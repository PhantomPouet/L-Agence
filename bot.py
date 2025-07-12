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

print("[DEBUG] Lancement du bot")

# --- ENV ---
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_SECRET = os.getenv("TWITCH_SECRET")
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
TARGET_GAME = "Star Citizen"
EXCLUDED_ROLE_IDS = [1363632614556041417]

# --- FIREBASE ---
firebase_key_base64 = os.getenv("FIREBASE_KEY_JSON_BASE64")
if not firebase_key_base64:
    print("[ERROR] FIREBASE_KEY_JSON_BASE64 non défini")
    sys.exit(1)

try:
    firebase_key_json = json.loads(base64.b64decode(firebase_key_base64).decode("utf-8"))
    cred = credentials.Certificate(firebase_key_json)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("[DEBUG] Firebase initialisé")
except Exception as e:
    print(f"[ERROR] Erreur Firebase : {e}")
    sys.exit(1)

# --- DISCORD BOT ---
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Firebase helpers ---
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
            return data["access_token"]

async def is_streaming_on_twitch(username):
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
            return "⚫ Hors ligne"

# --- Discord Events ---
@bot.event
async def on_ready():
    print(f"[DEBUG] Connecté en tant que {bot.user}")
    await bot.change_presence(activity=discord.Game(name="/link"))
    try:
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print("[DEBUG] Commandes slash synchronisées")
    except Exception as e:
        print(f"[ERROR] Synchronisation des commandes : {e}")
    check_streams.start()

@bot.event
async def on_presence_update(before, after):
    if not after.guild:
        return

    member = after.guild.get_member(after.id)
    if not member:
        return

    game_role = after.guild.get_role(ROLE_GAME_ID)
    playing_target_game = any(
        a.type == discord.ActivityType.playing and a.name and TARGET_GAME.lower() in a.name.lower()
        for a in after.activities
    )

    if game_role:
        if playing_target_game:
            await member.add_roles(game_role)
        else:
            await member.remove_roles(game_role)

# --- Slash Commands ---
@bot.tree.command(name="link", description="Associer ton pseudo Twitch", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(twitch="Ton pseudo Twitch")
async def link(interaction: discord.Interaction, twitch: str):
    await interaction.response.defer(ephemeral=True)
    save_link(interaction.user.id, twitch)
    live = await is_streaming_on_twitch(twitch)
    await interaction.followup.send(f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True)

@bot.tree.command(name="unlink", description="Dissocier ton Twitch", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    delete_link(interaction.user.id)
    await interaction.followup.send("Lien supprimé.", ephemeral=True)

@bot.tree.command(name="statut", description="Voir ton statut Twitch", guild=discord.Object(id=GUILD_ID))
async def statut(interaction: discord.Interaction):
    twitch = get_link(interaction.user.id)
    if not twitch:
        await interaction.response.send_message("Aucun Twitch lié.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    live = await is_streaming_on_twitch(twitch)
    await interaction.followup.send(f"Twitch : `{twitch}`\nStatut : {live}", ephemeral=True)

# --- Stream checker ---
@tasks.loop(minutes=2)
async def check_streams():
    print("[DEBUG] Vérification des lives Twitch")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("[ERROR] Guild introuvable")
        return

    stream_role = guild.get_role(ROLE_STREAM_ID)
    if not stream_role:
        print("[ERROR] Rôle stream introuvable")
        return

    users = db.collection("twitch_links").stream()
    for doc in users:
        user_id = int(doc.id)
        twitch_name = doc.to_dict().get("twitch")
        member = guild.get_member(user_id)

        if not member or is_excluded(member):
            continue

        status = await is_streaming_on_twitch(twitch_name)

        if status == "🔴 En live":
            if stream_role not in member.roles:
                await member.add_roles(stream_role)
            base_nick = get_nick(member.id) or member.display_name
            if not base_nick.startswith("🔴"):
                save_nick(member.id, base_nick)
                try:
                    await member.edit(nick=f"🔴 {base_nick}")
                except discord.Forbidden:
                    print(f"[WARN] Impossible de changer le pseudo de {member.display_name}")
        else:
            if stream_role in member.roles:
                await member.remove_roles(stream_role)
            nick = get_nick(member.id)
            if nick:
                try:
                    await member.edit(nick=nick)
                except discord.Forbidden:
                    print(f"[WARN] Impossible de rétablir le pseudo de {member.display_name}")
                delete_nick(member.id)

# --- Lancement du bot ---
print("[DEBUG] Lancement du bot")
bot.run(TOKEN)
