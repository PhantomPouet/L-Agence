import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from discord import app_commands

load_dotenv()

intents = discord.Intents.default()
intents.presences = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))

@bot.event
async def on_ready():
    print(f"{bot.user} est connecté à Discord !")
@bot.event
async def on_ready():
    await bot.wait_until_ready()
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"{bot.user} connecté et commandes slash synchronisées.")

@bot.tree.command(name="register_twitch", description="Associe ton pseudo Twitch")
@app_commands.describe(twitch_username="Ton pseudo Twitch (sans https)")
async def register_twitch(interaction: discord.Interaction, twitch_username: str):
    user_id = str(interaction.user.id)
    data = load_data(TWITCH_DATA_FILE)
    data[user_id] = {"twitch": twitch_username, "is_live": False}
    save_data(TWITCH_DATA_FILE, data)
    await interaction.response.send_message(f"✅ Ton Twitch `{twitch_username}` a été enregistré.", ephemeral=True)    
@bot.command()
async def register_twitch(ctx, twitch_username):
    user_id = str(ctx.author.id)
    data = load_twitch_links()
    data[user_id] = {
        "twitch": twitch_username,
        "is_live": False
    }
    save_twitch_links(data)
    await ctx.send(f"Ton compte Twitch `{twitch_username}` a été enregistré ✅")

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

    # Vérifie les activités
    is_streaming = any(a.type == discord.ActivityType.streaming for a in after.activities)
    is_playing_star_citizen = any(
        a.type == discord.ActivityType.playing and a.name == "Star Citizen"
        for a in after.activities
    )
    
@tasks.loop(minutes=2)
async def check_twitch_streams():
    data = load_twitch_links()
    token = get_twitch_token()
    headers = {"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}
    
    for user_id, info in data.items():
        twitch_name = info["twitch"]
        discord_user = bot.get_user(int(user_id))
        if not discord_user:
            continue

        is_live = twitch_user_is_live(twitch_name, headers)
        if is_live and not info.get("is_live", False):
            # Passé en live
            data[user_id]["is_live"] = True
            await apply_stream_changes(discord_user)
        elif not is_live and info.get("is_live", False):
            # Fin de stream
            data[user_id]["is_live"] = False
            await revert_stream_changes(discord_user)
    
    save_twitch_links(data)

    
    # === GESTION STREAMING ===
    if is_streaming:
        if role_stream not in member.roles:
            await member.add_roles(role_stream)
        if not member.nick or not member.nick.startswith("🔴"):
            try:
                original = member.nick if member.nick else member.name
                await member.edit(nick=f"🔴 {original}")
            except discord.Forbidden:
                print(f"⚠️ Pas les permissions pour changer le pseudo de {member.display_name}")
    else:
        if role_stream in member.roles:
            await member.remove_roles(role_stream)
        if member.nick and member.nick.startswith("🔴"):
            try:
                await member.edit(nick=member.nick[2:].strip())
            except discord.Forbidden:
                print(f"⚠️ Pas les permissions pour restaurer le pseudo de {member.display_name}")

    # === GESTION STAR CITIZEN ===
    if is_playing_star_citizen:
        if role_game not in member.roles:
            await member.add_roles(role_game)
    else:
        if role_game in member.roles:
            await member.remove_roles(role_game)

bot.run(TOKEN)
