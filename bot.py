import os
import json
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.presences = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))

NICKNAME_FILE = "nicknames.json"

def load_nicknames():
    if os.path.exists(NICKNAME_FILE):
        with open(NICKNAME_FILE, "r") as f:
            return json.load(f)
    return {}

def save_nicknames(data):
    with open(NICKNAME_FILE, "w") as f:
        json.dump(data, f)

original_nicknames = load_nicknames()

@bot.event
async def on_ready():
    print(f"{bot.user} est connecté à Discord !")

@bot.event
async def on_presence_update(before, after):
    guild = after.guild
    if not guild:
        return

    role_game = guild.get_role(ROLE_GAME_ID)
    role_stream = guild.get_role(ROLE_STREAM_ID)

    if not role_game or not role_stream:
        print("Les rôles sont introuvables.")
        return

    member = after

    is_streaming = any(a.type == discord.ActivityType.streaming for a in after.activities)
    is_playing_star_citizen = any(
        a.type == discord.ActivityType.playing and a.name == "Star Citizen"
        for a in after.activities
    )

    if is_streaming:
        if role_stream not in member.roles:
            await member.add_roles(role_stream)
        if not member.nick or not member.nick.startswith("🔴"):
            try:
                if str(member.id) not in original_nicknames:
                    original_nicknames[str(member.id)] = member.nick or ""
                    save_nicknames(original_nicknames)
                base_nick = member.nick if member.nick else member.name
                await member.edit(nick=f"🔴 {base_nick}")
            except discord.Forbidden:
                print(f"⚠️ Pas les permissions pour changer le pseudo de {member.display_name}")
    else:
        if role_stream in member.roles:
            await member.remove_roles(role_stream)
        if member.nick and member.nick.startswith("🔴"):
            try:
                user_id_str = str(member.id)
                if user_id_str in original_nicknames:
                    old_nick = original_nicknames[user_id_str]
                    await member.edit(nick=old_nick or None)
                    del original_nicknames[user_id_str]
                    save_nicknames(original_nicknames)
                else:
                    await member.edit(nick=member.name)
            except discord.Forbidden:
                print(f"⚠️ Pas les permissions pour restaurer le pseudo de {member.display_name}")

    if is_playing_star_citizen:
        if role_game not in member.roles:
            await member.add_roles(role_game)
    else:
        if role_game in member.roles:
            await member.remove_roles(role_game)

bot.run(TOKEN)
