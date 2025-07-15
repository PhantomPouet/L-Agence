import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import aiohttp
import json
import firebase_admin
from firebase_admin import credentials, firestore
import base64
import sys

# --- Chargement des variables d’environnement ---
def get_env_var(name, cast=str, required=True):
    value = os.getenv(name)
    if required and (value is None or value == ""):
        print(f"[CRITICAL] Variable d'environnement {name} manquante.")
        sys.exit(1)
    return cast(value) if value is not None else None

TOKEN = get_env_var("DISCORD_TOKEN")
GUILD_ID = get_env_var("DISCORD_GUILD_ID", int)
TWITCH_CLIENT_ID = get_env_var("TWITCH_CLIENT_ID")
TWITCH_SECRET = get_env_var("TWITCH_SECRET")
ROLE_STREAM_ID = get_env_var("ROLE_STREAM_ID", int)
ROLE_GAME_ID = get_env_var("ROLE_GAME_ID", int)
FIREBASE_KEY_BASE64 = get_env_var("FIREBASE_KEY_JSON_BASE64")

# --- Initialisation Firebase ---
try:
    decoded = base64.b64decode(FIREBASE_KEY_BASE64).decode("utf-8")
    cred_dict = json.loads(decoded)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("[INFO] Firebase initialisé avec succès.")
except Exception as e:
    print(f"[CRITICAL] Erreur lors de l'initialisation Firebase : {e}")
    sys.exit(1)

# --- Discord setup ---
intents = discord.Intents.default()
intents.presences = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

TARGET_GAME = "Star Citizen"
EXCLUDED_ROLE_IDS = [1363632614556041417]

# --- Fonctions Firestore ---
def save_link(user_id, twitch):
    db.collection("twitch_links").document(str(user_id)).set({"twitch": twitch})

def delete_link(user_id):
    db.collection("twitch_links").document(str(user_id)).delete()

def get_link(user_id):
    doc = db.collection("twitch_links").document(str(user_id)).get()
    return doc.to_dict()["twitch"] if doc.exists else None

def save_nick(user_id, nick):
    db.collection("nicknames").document(str(user_id)).set({"nick": nick})

def get_nick(user_id):
    doc = db.collection("nicknames").document(str(user_id)).get()
    return doc.to_dict()["nick"] if doc.exists else None

def delete_nick(user_id):
    db.collection("nicknames").document(str(user_id)).delete()

def is_excluded(member):
    return any(role.id in EXCLUDED_ROLE_IDS for role in member.roles)

# --- Twitch ---
async def get_twitch_token():
    async with aiohttp.ClientSession() as session:
        async with session.post("https://id.twitch.tv/oauth2/token", params={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_SECRET,
            "grant_type": "client_credentials"
        }) as resp:
            return (await resp.json()).get("access_token")

async def is_streaming(username):
    try:
        token = await get_twitch_token()
        headers = {"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.twitch.tv/helix/streams?user_login={username}", headers=headers
            ) as resp:
                data = await resp.json()
                if data.get("data") and isinstance(data["data"], list) and data["data"]:
                    stream = data["data"][0]
                    if stream.get("game_name", "").lower() == TARGET_GAME.lower():
                        return True
        return False
    except Exception as e:
        print(f"[ERROR] Twitch check failed: {e}")
        return False

# --- Events ---
@bot.event
async def on_ready():
    print(f"[INFO] Connecté en tant que {bot.user}")
    try:
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print("[INFO] Commandes slash synchronisées.")
    except Exception as e:
        print(f"[ERROR] Sync commandes : {e}")
    check_streams.start()

# --- Commandes Slash ---
@bot.tree.command(name="link", description="Associe ton pseudo Twitch", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(twitch="Ton pseudo Twitch")
async def link(interaction: discord.Interaction, twitch: str):
    await interaction.response.defer(ephemeral=True)
    save_link(interaction.user.id, twitch)
    live = await is_streaming(twitch)
    await interaction.followup.send(f"Twitch lié : `{twitch}`\nStatut : {'🔴 En live' if live else '⚫ Hors ligne'}", ephemeral=True)

@bot.tree.command(name="unlink", description="Supprime ton lien Twitch", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    delete_link(interaction.user.id)
    await interaction.followup.send("Lien supprimé.", ephemeral=True)

@bot.tree.command(name="statut", description="Affiche ton statut Twitch", guild=discord.Object(id=GUILD_ID))
async def statut(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    twitch = get_link(interaction.user.id)
    if not twitch:
        await interaction.followup.send("Aucun Twitch lié.", ephemeral=True)
        return
    live = await is_streaming(twitch)
    await interaction.followup.send(f"Twitch lié : `{twitch}`\nStatut : {'🔴 En live' if live else '⚫ Hors ligne'}", ephemeral=True)

# --- Vérification périodique ---
@tasks.loop(minutes=2)
async def check_streams():
    print("[INFO] Vérification des streams en cours...")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print(f"[ERROR] Guild {GUILD_ID} non trouvée.")
        return

    role_stream = guild.get_role(ROLE_STREAM_ID)
    role_game = guild.get_role(ROLE_GAME_ID)

    async for doc in db.collection("twitch_links").stream():
        user_id = int(doc.id)
        twitch_name = doc.to_dict().get("twitch")
        member = guild.get_member(user_id)

        if not member or is_excluded(member):
            continue

        # Rôle Stream
        if await is_streaming(twitch_name):
            if role_stream and role_stream not in member.roles:
                await member.add_roles(role_stream)
                save_nick(member.id, member.display_name)
                await member.edit(nick=f"🔴 {member.display_name}")
        else:
            if role_stream and role_stream in member.roles:
                await member.remove_roles(role_stream)
            old_nick = get_nick(member.id)
            if old_nick:
                await member.edit(nick=old_nick)
                delete_nick(member.id)

        # Rôle "En jeu"
        playing = any(
            activity.type == discord.ActivityType.playing and TARGET_GAME.lower() in activity.name.lower()
            for activity in member.activities
        )
        if playing:
            if role_game and role_game not in member.roles:
                await member.add_roles(role_game)
        else:
            if role_game and role_game in member.roles:
                await member.remove_roles(role_game)

print("[INFO] Lancement du bot...")
bot.run(TOKEN)
