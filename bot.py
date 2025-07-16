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

# Chargement des variables d'environnement
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_SECRET = os.getenv("TWITCH_SECRET")
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
TARGET_GAME = "Star Citizen"
EXCLUDED_ROLE_IDS = [1363632614556041417]

firebase_key_json_base64_str = os.getenv("FIREBASE_KEY_JSON_BASE64")
decoded_json = base64.b64decode(firebase_key_json_base64_str).decode("utf-8")
firebase_key = json.loads(decoded_json)
cred = credentials.Certificate(firebase_key)
firebase_admin.initialize_app(cred)
db = firestore.client()

intents = discord.Intents.default()
intents.presences = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_excluded(member):
    return any(role.id in EXCLUDED_ROLE_IDS for role in member.roles)

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

@bot.event
async def on_ready():
    print(f"[DEBUG] Bot connecté en tant que {bot.user}")
    await bot.change_presence(activity=discord.CustomActivity(name="/link"))
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    check_streams.start()

@bot.event
async def on_presence_update(before, after):
    if after.guild.id != GUILD_ID:
        return
    if after.bot:
        return

    print(f"[DEBUG] Nouvelle présence détectée pour {after.display_name}")
    for activity in after.activities:
        print(f" - Activity: {activity.name} ({type(activity)})")

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
        await interaction.followup.send(f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True)

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
            return data["access_token"]

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
                if data.get("data") and isinstance(data["data"], list):
                    stream = data["data"][0] if data["data"] else None
                    if stream and stream.get("game_name", "").lower() == TARGET_GAME.lower():
                        return "🔴 En live"
                return "⚫ Hors ligne"
    except Exception as e:
        print(f"[ERROR] Twitch check failed: {e}")
        return "❌ Erreur"

@tasks.loop(minutes=2)
async def check_streams():
    print("[DEBUG] check_streams lancé")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print(f"[ERROR] Guild ID {GUILD_ID} introuvable")
        return

    stream_role = guild.get_role(ROLE_STREAM_ID)
    game_role = guild.get_role(ROLE_GAME_ID)

    async for doc in db.collection("twitch_links").stream():
        user_id = int(doc.id)
        twitch_name = doc.to_dict().get("twitch")
        member = guild.get_member(user_id)
        if not member or is_excluded(member):
            continue

        live_status = await is_streaming_on_twitch(twitch_name)
        print(f"[DEBUG] Statut de {twitch_name} ({member.display_name}): {live_status}")

        if live_status == "🔴 En live":
            if stream_role and stream_role not in member.roles:
                await member.add_roles(stream_role)
            base_name = get_nick(member.id) or member.display_name
            if not base_name.startswith("\ud83d\udd34"):
                save_nick(member.id, base_name)
                try:
                    await member.edit(nick=f"\ud83d\udd34 {base_name}")
                except discord.Forbidden:
                    pass
        else:
            if stream_role and stream_role in member.roles:
                await member.remove_roles(stream_role)
            nick = get_nick(member.id)
            if nick:
                try:
                    await member.edit(nick=nick)
                except discord.Forbidden:
                    pass
                delete_nick(member.id)

        # --- AJOUT pour rôle "en jeu" ---
        playing_star_citizen = any(
            activity.type == discord.ActivityType.playing and TARGET_GAME.lower() in activity.name.lower()
            for activity in member.activities
        )

        if playing_star_citizen:
            if game_role and game_role not in member.roles:
                await member.add_roles(game_role)
                print(f"[INFO] Rôle 'En jeu' ajouté à {member.display_name}")
        else:
            if game_role and game_role in member.roles:
                await member.remove_roles(game_role)
                print(f"[INFO] Rôle 'En jeu' retiré de {member.display_name}")

bot.run(TOKEN)
