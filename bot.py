
import os
import json
import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

intents = discord.Intents.default()
intents.presences = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

TWITCH_DATA_FILE = "twitch_links.json"
NICKNAME_FILE = "nicknames.json"

def load_data(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            return json.load(f)
    return {}

def save_data(file, data):
    with open(file, "w") as f:
        json.dump(data, f)

@bot.event
async def on_ready():
    await bot.wait_until_ready()
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"{bot.user} connecté et commandes slash synchronisées.")
    check_twitch_streams.start()

@tree.command(name="link", description="Associe ton pseudo Twitch")
@app_commands.describe(twitch_username="Ton pseudo Twitch (sans https)")
async def link(interaction: discord.Interaction, twitch_username: str):
    user_id = str(interaction.user.id)
    data = load_data(TWITCH_DATA_FILE)
    data[user_id] = {"twitch": twitch_username, "is_live": False}
    save_data(TWITCH_DATA_FILE, data)
    await interaction.response.send_message(f"✅ Ton Twitch `{twitch_username}` a été enregistré.", ephemeral=True)

@tree.command(name="unlink", description="Supprime l'association avec ton compte Twitch")
async def unlink(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    data = load_data(TWITCH_DATA_FILE)
    if user_id in data:
        del data[user_id]
        save_data(TWITCH_DATA_FILE, data)
        await interaction.response.send_message("❌ Ton compte Twitch a été dissocié.", ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ Aucun compte Twitch enregistré.", ephemeral=True)

@tree.command(name="twitch_status", description="Affiche l'état du lien Twitch")
async def twitch_status(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    data = load_data(TWITCH_DATA_FILE)
    if user_id in data:
        twitch = data[user_id]["twitch"]
        live = "🔴 En live" if data[user_id].get("is_live") else "⚫ Hors ligne"
await interaction.response.send_message(f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True)

Statut : {live}", ephemeral=True)
    else:
        await interaction.response.send_message("🚫 Aucun compte Twitch lié.", ephemeral=True)

async def get_twitch_token():
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, params=params) as resp:
            data = await resp.json()
            return data["access_token"]

async def twitch_user_is_live(username, headers):
    url = f"https://api.twitch.tv/helix/streams?user_login={username}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return len(data["data"]) > 0

@tasks.loop(minutes=2)
async def check_twitch_streams():
    data = load_data(TWITCH_DATA_FILE)
    nick_data = load_data(NICKNAME_FILE)
    guild = bot.get_guild(GUILD_ID)
    role_stream = guild.get_role(ROLE_STREAM_ID)
    token = await get_twitch_token()
    headers = {"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}

    for user_id, info in data.items():
        member = guild.get_member(int(user_id))
        if not member:
            continue

        is_live = await twitch_user_is_live(info["twitch"], headers)
        was_live = info.get("is_live", False)

        if is_live and not was_live:
            if role_stream not in member.roles:
                await member.add_roles(role_stream)
            if str(member.id) not in nick_data:
                nick_data[str(member.id)] = member.nick or ""
                save_data(NICKNAME_FILE, nick_data)
            try:
                await member.edit(nick=f"🔴 {member.nick or member.name}")
            except discord.Forbidden:
                print(f"❌ Pas de permission pour modifier le pseudo de {member}")
            data[user_id]["is_live"] = True

        elif not is_live and was_live:
            if role_stream in member.roles:
                await member.remove_roles(role_stream)
            try:
                old_nick = nick_data.get(str(member.id), "")
                await member.edit(nick=old_nick or None)
                if str(member.id) in nick_data:
                    del nick_data[str(member.id)]
                    save_data(NICKNAME_FILE, nick_data)
            except discord.Forbidden:
                print(f"❌ Pas de permission pour restaurer le pseudo de {member}")
            data[user_id]["is_live"] = False

    save_data(TWITCH_DATA_FILE, data)

@bot.event
async def on_presence_update(before, after):
    guild = bot.get_guild(GUILD_ID)
    role_game = guild.get_role(ROLE_GAME_ID)
    member = guild.get_member(after.id)

    if member is None:
        return

    is_playing_star_citizen = any(
        activity.type == discord.ActivityType.playing and activity.name.lower() == "star citizen"
        for activity in after.activities
    )

    if is_playing_star_citizen:
        if role_game not in member.roles:
            await member.add_roles(role_game)
    else:
        if role_game in member.roles:
            await member.remove_roles(role_game)
