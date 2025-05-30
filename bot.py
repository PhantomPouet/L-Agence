import os
import json
import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

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
    print(f"{bot.user} connecté.")
    check_twitch_streams.start()

@bot.command()
async def register_twitch(ctx, twitch_username):
    data = load_data(TWITCH_DATA_FILE)
    user_id = str(ctx.author.id)
    data[user_id] = {"twitch": twitch_username, "is_live": False}
    save_data(TWITCH_DATA_FILE, data)
    await ctx.send(f"✅ Ton compte Twitch `{twitch_username}` a été enregistré.")

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
