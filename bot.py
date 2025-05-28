import discord
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))

intents = discord.Intents.default()
intents.presences = True
intents.members = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Bot connecté en tant que {client.user}")

@client.event
async def on_presence_update(before, after):
    guild = client.get_guild(GUILD_ID)
    role_stream = guild.get_role(ROLE_STREAM_ID)
    role_game = guild.get_role(ROLE_GAME_ID)

    if not isinstance(after, discord.Member):
        return

    is_streaming = any(a.type == discord.ActivityType.streaming for a in after.activities)
    is_playing_star_citizen = any(
        a.type == discord.ActivityType.playing and a.name.lower() == "star citizen"
        for a in after.activities
    )

    # Gestion du rôle "En stream"
    if is_streaming and role_stream not in after.roles:
        await after.add_roles(role_stream)
        print(f"Ajout du rôle En stream à {after.display_name}")
    elif not is_streaming and role_stream in after.roles:
        await after.remove_roles(role_stream)
        print(f"Retrait du rôle En stream à {after.display_name}")

    # Gestion du rôle "En train de jouer"
    if is_playing_star_citizen and role_game not in after.roles:
        await after.add_roles(role_game)
        print(f"Ajout du rôle En train de jouer à {after.display_name}")
    elif not is_playing_star_citizen and role_game in after.roles:
        await after.remove_roles(role_game)
        print(f"Retrait du rôle En train de jouer à {after.display_name}")

client.run(TOKEN)
