import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import aiohttp

intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.guilds = True
intents.message_content = False

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_SECRET = os.getenv("TWITCH_SECRET")

ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
TARGET_GAME = "Star Citizen"

DATA_PATH = "/app/data"
LINKS_FILE = os.path.join(DATA_PATH, "twitch_links.json")
NICKS_FILE = os.path.join(DATA_PATH, "nicknames.json")

bot = commands.Bot(command_prefix="!", intents=intents)

if os.path.exists(LINKS_FILE):
    with open(LINKS_FILE, "r") as f:
        twitch_links = json.load(f)
else:
    twitch_links = {}

if os.path.exists(NICKS_FILE):
    with open(NICKS_FILE, "r") as f:
        original_nicks = json.load(f)
else:
    original_nicks = {}

def save_links():
    with open(LINKS_FILE, "w") as f:
        json.dump(twitch_links, f)

def save_nicks():
    with open(NICKS_FILE, "w") as f:
        json.dump(original_nicks, f)

def clean_nick(nick):
    return nick.lstrip("🔴 ").strip()

@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")
    await bot.change_presence(activity=discord.CustomActivity(name="/link"))
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Commandes synchronisées : {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"Erreur de synchronisation : {e}")
    check_streams.start()

@bot.event
async def on_presence_update(before, after):
    if not after.guild:
        return

    guild = after.guild
    member = guild.get_member(after.id)
    if not member:
        return

    stream_role = guild.get_role(ROLE_STREAM_ID)
    game_role = guild.get_role(ROLE_GAME_ID)

    is_streaming = any(
        activity.type == discord.ActivityType.streaming for activity in after.activities
    )

    playing_star_citizen = any(
        activity.type == discord.ActivityType.playing and
        activity.name and
        TARGET_GAME.lower() in activity.name.lower()
        for activity in after.activities
    )

    if stream_role:
        if is_streaming:
            await member.add_roles(stream_role)
            if str(member.id) not in original_nicks:
                original_nicks[str(member.id)] = clean_nick(member.display_name)
                save_nicks()
            new_nick = f"🔴 {clean_nick(member.display_name)}"
            if member.display_name != new_nick:
                try:
                    await member.edit(nick=new_nick)
                except discord.Forbidden:
                    pass
        else:
            await member.remove_roles(stream_role)
            if str(member.id) in original_nicks:
                try:
                    await member.edit(nick=original_nicks[str(member.id)])
                except discord.Forbidden:
                    pass
                del original_nicks[str(member.id)]
                save_nicks()

    if game_role:
        if playing_star_citizen:
            await member.add_roles(game_role)
        else:
            await member.remove_roles(game_role)

@bot.tree.command(name="link", description="Lier ton pseudo Twitch à ton compte Discord", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(twitch="Ton pseudo Twitch")
async def link(interaction: discord.Interaction, twitch: str):
    twitch_links[str(interaction.user.id)] = twitch
    save_links()
    live = await is_streaming_on_twitch(twitch)
    await interaction.response.send_message(
        f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True
    )

@bot.tree.command(name="unlink", description="Supprimer le lien avec ton compte Twitch", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    if str(interaction.user.id) in twitch_links:
        del twitch_links[str(interaction.user.id)]
        save_links()
        await interaction.response.send_message("Lien supprimé.", ephemeral=True)
    else:
        await interaction.response.send_message("Aucun lien trouvé.", ephemeral=True)

@bot.tree.command(name="statut", description="Afficher le statut du lien Twitch", guild=discord.Object(id=GUILD_ID))
async def statut(interaction: discord.Interaction):
    twitch = twitch_links.get(str(interaction.user.id))
    if not twitch:
        await interaction.response.send_message("Aucun pseudo Twitch lié.", ephemeral=True)
    else:
        live = await is_streaming_on_twitch(twitch)
        await interaction.response.send_message(
            f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True
        )

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
            print(f"Réponse de l'obtention du token Twitch: {data}")
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
            print(f"[DEBUG] Réponse Twitch pour {username}: {data}")
            if "data" in data and isinstance(data["data"], list) and data["data"]:
                return "🔴 En live"
            else:
                return "⚫ Hors ligne"

@tasks.loop(minutes=2)
async def check_streams():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    for discord_id, twitch_name in twitch_links.items():
        member = guild.get_member(int(discord_id))
        if not member:
            continue

        live_status = await is_streaming_on_twitch(twitch_name)
        stream_role = guild.get_role(ROLE_STREAM_ID)

        if live_status == "🔴 En live":
            if stream_role and stream_role not in member.roles:
                await member.add_roles(stream_role)
            if str(member.id) not in original_nicks:
                original_nicks[str(member.id)] = clean_nick(member.display_name)
                save_nicks()
            new_nick = f"🔴 {clean_nick(member.display_name)}"
            if member.display_name != new_nick:
                try:
                    await member.edit(nick=new_nick)
                except discord.Forbidden:
                    pass
        else:
            if stream_role and stream_role in member.roles:
                await member.remove_roles(stream_role)
            if str(member.id) in original_nicks:
                try:
                    await member.edit(nick=original_nicks[str(member.id)])
                except discord.Forbidden:
                    pass
                del original_nicks[str(member.id)]
                save_nicks()

bot.run(TOKEN)
