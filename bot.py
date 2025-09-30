import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import aiohttp
import json
import firebase_admin
from firebase_admin import credentials, firestore
import sys
import base64

print("[DEBUG] Démarrage du script bot.py")

# --- Variables d'environnement ---
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", 0))
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_SECRET = os.getenv("TWITCH_SECRET")
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID", 0))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID", 0))
firebase_key_base64 = os.getenv("FIREBASE_KEY_JSON_BASE64")

if not all([TOKEN, GUILD_ID, TWITCH_CLIENT_ID, TWITCH_SECRET, ROLE_STREAM_ID, ROLE_GAME_ID, firebase_key_base64]):
    print("[CRITICAL ERROR] Une ou plusieurs variables d'environnement sont manquantes.")
    sys.exit(1)

# --- Initialisation Firebase ---
try:
    firebase_key = json.loads(base64.b64decode(firebase_key_base64).decode("utf-8"))
    cred = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"[CRITICAL ERROR] Erreur lors de l'initialisation de Firebase: {e}")
    sys.exit(1)

# --- Intents Discord ---
intents = discord.Intents.default()
intents.presences = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

TARGET_GAME = "Star Citizen"

# --- Fonctions Firebase ---
def save_link(user_id, twitch):
    db.collection("twitch_links").document(str(user_id)).set({"twitch": twitch})

def delete_link(user_id):
    db.collection("twitch_links").document(str(user_id)).delete()

def get_link(user_id):
    doc = db.collection("twitch_links").document(str(user_id)).get()
    return doc.to_dict()["twitch"] if doc.exists else None

def save_nick(user_id, nick):
    db.collection("nicknames").document(str(user_id)).set({"nick": nick})

def delete_nick(user_id):
    db.collection("nicknames").document(str(user_id)).delete()

def get_nick(user_id):
    doc = db.collection("nicknames").document(str(user_id)).get()
    return doc.to_dict()["nick"] if doc.exists else None

# --- Twitch ---
async def get_twitch_token():
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_SECRET,
        "grant_type": "client_credentials"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, params=params) as resp:
            data = await resp.json()
            return data.get("access_token")

async def is_streaming_on_twitch(username):
    try:
        token = await get_twitch_token()
        headers = {
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.twitch.tv/helix/streams?user_login={username}", headers=headers) as resp:
                data = await resp.json()
                if data.get("data"):
                    stream = data["data"][0]
                    if stream.get("game_name", "").lower() == TARGET_GAME.lower():
                        return "🔴 En live"
                    return "🟣 Autre live"
                return "⚫ Hors ligne"
    except:
        return "❌ Erreur"

# --- Discord events ---
@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")
    await bot.change_presence(activity=discord.CustomActivity(name="/link"))
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    check_streams.start()

# --- Commands ---
@bot.tree.command(name="link", description="Lier ton pseudo Twitch", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(twitch="Ton pseudo Twitch")
async def link(interaction: discord.Interaction, twitch: str):
    await interaction.response.defer(ephemeral=True)
    save_link(interaction.user.id, twitch)
    live = await is_streaming_on_twitch(twitch)
    await interaction.followup.send(f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True)

@bot.tree.command(name="unlink", description="Supprimer le lien Twitch", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    delete_link(interaction.user.id)
    await interaction.followup.send("Lien supprimé.", ephemeral=True)

@bot.tree.command(name="statut", description="Afficher le statut Twitch", guild=discord.Object(id=GUILD_ID))
async def statut(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    twitch = get_link(interaction.user.id)
    if not twitch:
        await interaction.followup.send("Aucun pseudo Twitch lié.", ephemeral=True)
    else:
        live = await is_streaming_on_twitch(twitch)
        await interaction.followup.send(f"Twitch : `{twitch}`\nStatut : {live}", ephemeral=True)

# --- Background task ---
@tasks.loop(minutes=2)
async def check_streams():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    stream_role = guild.get_role(ROLE_STREAM_ID)
    game_role = guild.get_role(ROLE_GAME_ID)

    # Récupère les utilisateurs ayant lié Twitch
    links = {doc.id: doc.to_dict()["twitch"] for doc in db.collection("twitch_links").stream()}

    for member in guild.members:
        if member.bot:
            continue

        # --- Gestion Twitch uniquement si le membre a lié son Twitch ---
        if str(member.id) in links:
            twitch_name = links[str(member.id)]
            status = await is_streaming_on_twitch(twitch_name)

            # Rôle "En stream"
            if stream_role:
                if status == "🔴 En live" and stream_role not in member.roles:
                    await member.add_roles(stream_role)
                elif status != "🔴 En live" and stream_role in member.roles:
                    await member.remove_roles(stream_role)

            # Pseudo modifié
            base_name = get_nick(member.id) or member.display_name
            if status == "🔴 En live" and not base_name.startswith("🔴"):
                save_nick(member.id, base_name)
                try:
                    await member.edit(nick=f"🔴 {base_name}")
                except:
                    pass
            elif status != "🔴 En live" and base_name.startswith("🔴"):
                try:
                    await member.edit(nick=base_name[2:].strip())
                    delete_nick(member.id)
                except:
                    pass

        # --- Gestion rôle "En jeu" pour TOUS les membres ---
        is_playing_game = any(
            act.type == discord.ActivityType.playing and act.name and act.name.lower() == TARGET_GAME.lower()
            for act in member.activities
        )

        if game_role:
            if is_playing_game and game_role not in member.roles:
                await member.add_roles(game_role)
            elif not is_playing_game and game_role in member.roles:
                await member.remove_roles(game_role)

# --- Run bot ---
bot.run(TOKEN)
