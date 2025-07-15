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

# Clé Firebase depuis base64
firebase_key_json_base64_str = os.getenv("FIREBASE_KEY_JSON_BASE64")
decoded_json_bytes = base64.b64decode(firebase_key_json_base64_str)
firebase_key = json.loads(decoded_json_bytes.decode('utf-8'))
cred = credentials.Certificate(firebase_key)
firebase_admin.initialize_app(cred)
db = firestore.client()
print("[DEBUG] Firebase initialisé")

# Discord bot setup
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.guilds = True
intents.message_content = False
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
    print("[DEBUG] Bot connecté en tant que", bot.user)
    await bot.change_presence(activity=discord.CustomActivity(name="/link"))
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"[DEBUG] Commandes synchronisées : {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"[ERROR] Échec de synchronisation : {e}")
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
                return "⚫ Hors ligne"
    except Exception as e:
        print(f"[ERROR] Twitch check failed: {e}")
        return "❌ Erreur"

@tasks.loop(minutes=2)
async def check_streams():
    print("[DEBUG] check_streams lancé")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("[ERROR] Guild introuvable.")
        return

    stream_role = guild.get_role(ROLE_STREAM_ID)
    game_role = guild.get_role(ROLE_GAME_ID)

    if not stream_role:
        print(f"[ERROR] Rôle stream ({ROLE_STREAM_ID}) introuvable")
    if not game_role:
        print(f"[ERROR] Rôle jeu ({ROLE_GAME_ID}) introuvable")

    try:
        users_ref = db.collection("twitch_links").stream()
    except Exception as e:
        print(f"[ERROR] Erreur Firestore : {e}")
        return

    for doc in users_ref:
        user_id = int(doc.id)
        twitch_name = doc.to_dict().get("twitch")
        member = guild.get_member(user_id)

        if not member or is_excluded(member):
            continue

        # Vérification Twitch
        live_status = await is_streaming_on_twitch(twitch_name)
        print(f"[DEBUG] Statut de {twitch_name} ({member.display_name}): {live_status}")

        if live_status == "🔴 En live":
            if stream_role and stream_role not in member.roles:
                try:
                    await member.add_roles(stream_role)
                    print(f"[INFO] Rôle stream ajouté à {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Ajout rôle stream: {e}")

            base_name = get_nick(member.id) or member.display_name
            if not base_name.startswith("🔴"):
                save_nick(member.id, base_name)
                try:
                    await member.edit(nick=f"🔴 {base_name}")
                except Exception as e:
                    print(f"[ERROR] Changement pseudo stream: {e}")
        else:
            if stream_role and stream_role in member.roles:
                try:
                    await member.remove_roles(stream_role)
                    print(f"[INFO] Rôle stream retiré à {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Suppression rôle stream: {e}")
            nick = get_nick(member.id)
            if nick:
                try:
                    await member.edit(nick=nick)
                except Exception as e:
                    print(f"[ERROR] Restauration pseudo: {e}")
                delete_nick(member.id)

        # Vérifie le jeu actuel
        if member.activity and member.activity.name and member.activity.name.lower() == TARGET_GAME.lower():
            if game_role and game_role not in member.roles:
                try:
                    await member.add_roles(game_role)
                    print(f"[INFO] Rôle 'en jeu' ajouté à {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Ajout rôle 'en jeu' : {e}")
        else:
            if game_role and game_role in member.roles:
                try:
                    await member.remove_roles(game_role)
                    print(f"[INFO] Rôle 'en jeu' retiré de {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Suppression rôle 'en jeu' : {e}")

print("[DEBUG] Bot en cours de lancement...")
bot.run(TOKEN)
