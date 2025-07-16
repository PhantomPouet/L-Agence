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
if not TOKEN:
    print("[CRITICAL ERROR] DISCORD_TOKEN manquant")
    sys.exit(1)

try:
    GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
except:
    print("[CRITICAL ERROR] DISCORD_GUILD_ID invalide")
    sys.exit(1)

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_SECRET = os.getenv("TWITCH_SECRET")
if not TWITCH_CLIENT_ID or not TWITCH_SECRET:
    print("[CRITICAL ERROR] Clés Twitch manquantes")
    sys.exit(1)

try:
    ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
    ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
except:
    print("[CRITICAL ERROR] ROLE_STREAM_ID ou ROLE_GAME_ID invalide")
    sys.exit(1)

firebase_key_base64 = os.getenv("FIREBASE_KEY_JSON_BASE64")
if not firebase_key_base64:
    print("[CRITICAL ERROR] Clé Firebase manquante")
    sys.exit(1)

try:
    firebase_key = json.loads(base64.b64decode(firebase_key_base64).decode("utf-8"))
    print("[DEBUG] Clé Firebase chargée pour", firebase_key.get("project_id"))
except Exception as e:
    print("[CRITICAL ERROR] Erreur décodage Firebase:", e)
    sys.exit(1)

# --- Initialisation Firebase ---
try:
    cred = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("[DEBUG] Firebase initialisé")
except Exception as e:
    print("[CRITICAL ERROR] Firebase init échoué:", e)
    sys.exit(1)

# --- Configuration Discord Bot ---
intents = discord.Intents.default()
intents.presences = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

TARGET_GAME = "Star Citizen"
EXCLUDED_ROLE_IDS = [1363632614556041417]

# --- Fonctions Firebase ---
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

# --- Twitch API ---
async def get_twitch_token():
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": TWITCH_CLIENT_ID,
                "client_secret": TWITCH_SECRET,
                "grant_type": "client_credentials"
            }
        ) as resp:
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
                print(f"[DEBUG] Twitch API response for {username}: {data}")
                if data.get("data"):
                    stream = data["data"][0]
                    if stream.get("game_name", "").lower() == TARGET_GAME.lower():
                        return "🔴 En live"
                    return "🟣 Autre live"
                return "⚫ Hors ligne"
    except Exception as e:
        print(f"[ERROR] Échec Twitch API: {e}")
        return "❌ Erreur"

# --- Slash Commands ---
@bot.event
async def on_ready():
    print("[DEBUG] Bot connecté en tant que", bot.user)
    await bot.change_presence(activity=discord.CustomActivity(name="/link"))
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print("[DEBUG] Commandes synchronisées :", [cmd.name for cmd in synced])
    except Exception as e:
        print("[ERROR] Sync échouée:", e)
    check_streams.start()

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
    twitch = get_link(interaction.user.id)
    if not twitch:
        await interaction.response.send_message("Aucun Twitch lié.", ephemeral=True)
    else:
        live = await is_streaming_on_twitch(twitch)
        await interaction.response.send_message(f"Twitch : `{twitch}`\nStatut : {live}", ephemeral=True)

# --- Tâche périodique ---
@tasks.loop(minutes=2)
async def check_streams():
    print("[DEBUG] check_streams lancé")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print(f"[ERROR] Guilde {GUILD_ID} introuvable")
        return

    stream_role = guild.get_role(ROLE_STREAM_ID)
    game_role = guild.get_role(ROLE_GAME_ID)

    docs = db.collection("twitch_links").stream()
    for doc in docs:
        user_id = int(doc.id)
        twitch_name = doc.to_dict().get("twitch")
        member = guild.get_member(user_id)
        if not member or is_excluded(member):
            continue

        # --- Statut Twitch ---
        status = await is_streaming_on_twitch(twitch_name)
        print(f"[DEBUG] {member.display_name} -> {status}")
        if status == "🔴 En live":
            if stream_role and stream_role not in member.roles:
                await member.add_roles(stream_role)
                print(f"[INFO] Rôle stream ajouté à {member.display_name}")
            base_name = get_nick(member.id) or member.display_name
            if not base_name.startswith("🔴"):
                save_nick(member.id, base_name)
                await member.edit(nick=f"🔴 {base_name}")
        else:
            if stream_role and stream_role in member.roles:
                await member.remove_roles(stream_role)
                print(f"[INFO] Rôle stream retiré de {member.display_name}")
            nick = get_nick(member.id)
            if nick:
                await member.edit(nick=nick)
                delete_nick(member.id)

        # --- Détection de jeu Star Citizen ---
        is_playing_game = any(
            isinstance(act, discord.Game) and act.name and act.name.lower() == TARGET_GAME.lower()
            for act in member.activities
        )
        if is_playing_game:
            if game_role and game_role not in member.roles:
                await member.add_roles(game_role)
                print(f"[INFO] Rôle jeu ajouté à {member.display_name}")
        else:
            if game_role and game_role in member.roles:
                await member.remove_roles(game_role)
                print(f"[INFO] Rôle jeu retiré de {member.display_name}")

# --- Lancement ---
print("[DEBUG] Bot en cours de lancement...")
bot.run(TOKEN)
