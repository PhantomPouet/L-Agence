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

# --- CHARGEMENT ET VÉRIFICATION DES VARIABLES D'ENVIRONNEMENT ---
# ... (Vos vérifications de variables d'environnement restent inchangées ici) ...

# DISCORD_TOKEN
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("[CRITICAL ERROR] DISCORD_TOKEN n'est pas défini. Le bot ne peut pas se connecter.")
    sys.exit(1)

# DISCORD_GUILD_ID
try:
    GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
except (TypeError, ValueError):
    print("[CRITICAL ERROR] DISCORD_GUILD_ID n'est pas défini ou n'est pas un nombre valide.")
    sys.exit(1)

# TWITCH_CLIENT_ID
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
if not TWITCH_CLIENT_ID:
    print("[CRITICAL ERROR] TWITCH_CLIENT_ID n'est pas défini.")
    sys.exit(1)

TWITCH_SECRET = os.getenv("TWITCH_SECRET")
if not TWITCH_SECRET:
    print("[CRITICAL ERROR] TWITCH_SECRET n'est pas défini.")
    sys.exit(1)

# ROLE_STREAM_ID
try:
    ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
except (TypeError, ValueError):
    print("[CRITICAL ERROR] ROLE_STREAM_ID n'est pas défini ou n'est pas un nombre valide.")
    sys.exit(1)

# ROLE_GAME_ID
try:
    ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
except (TypeError, ValueError):
    print("[CRITICAL ERROR] ROLE_GAME_ID n'est pas défini ou n'est pas un nombre valide.")
    sys.exit(1)

# FIREBASE_KEY_JSON_BASE64
firebase_key_json_base64_str = os.getenv("FIREBASE_KEY_JSON_BASE64")
if not firebase_key_json_base64_str:
    print("[CRITICAL ERROR] FIREBASE_KEY_JSON_BASE64 n'est pas défini. Impossible d'initialiser Firebase.")
    sys.exit(1)

try:
    decoded_json_bytes = base64.b64decode(firebase_key_json_base64_str)
    decoded_json_str = decoded_json_bytes.decode('utf-8')
    firebase_key = json.loads(decoded_json_str)
    print("[DEBUG] Clé Firebase chargée pour le projet :", firebase_key.get("project_id"))
except (base64.binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
    print(f"[CRITICAL ERROR] Erreur de décodage (Base64 ou JSON) pour FIREBASE_KEY_JSON_BASE64: {e}")
    print(f"La valeur de FIREBASE_KEY_JSON_BASE64 (début): {firebase_key_json_base64_str[:100]}...")
    sys.exit(1)
except Exception as e:
    print(f"[CRITICAL ERROR] Erreur inattendue lors du chargement de la clé Firebase: {e}")
    sys.exit(1)

# INITIALISATION DE FIREBASE
try:
    cred = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("[DEBUG] Firebase initialisé avec succès.")
except Exception as e:
    print(f"[CRITICAL ERROR] Erreur lors de l'initialisation de l'application Firebase: {e}")
    sys.exit(1)


# --- DÉBUT DU CODE DU BOT ---

TARGET_GAME = "Star Citizen"
EXCLUDED_ROLE_IDS = [1363632614556041417]

intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.guilds = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

print("[DEBUG] Instance du bot créée")

# Fonctions Firebase
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

# Événements Discord
@bot.event
async def on_ready():
    print("[DEBUG] Événement on_ready déclenché")
    print(f"Connecté en tant que {bot.user}")
    await bot.change_presence(activity=discord.CustomActivity(name="/link"))
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Commandes synchronisées : {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"[ERROR] Erreur de synchronisation des commandes : {e}")
    check_streams.start()

# Commandes Slash (MODIFIÉES AVEC DEFER ET FOLLOWUP.SEND)
@bot.tree.command(name="link", description="Lier ton pseudo Twitch", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(twitch="Ton pseudo Twitch")
async def link(interaction: discord.Interaction, twitch: str):
    await interaction.response.defer(ephemeral=True) # <-- AJOUTÉ : Acquitte l'interaction immédiatement
    save_link(interaction.user.id, twitch)
    live = await is_streaming_on_twitch(twitch)
    await interaction.followup.send( # <-- MODIFIÉ : Utilise followup.send
        f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True
    )

@bot.tree.command(name="unlink", description="Supprimer le lien Twitch", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True) # <-- AJOUTÉ
    delete_link(interaction.user.id)
    await interaction.followup.send("Lien supprimé.", ephemeral=True) # <-- MODIFIÉ

@bot.tree.command(name="statut", description="Afficher le statut Twitch", guild=discord.Object(id=GUILD_ID))
async def statut(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True) # <-- AJOUTÉ
    twitch = get_link(interaction.user.id)
    if not twitch:
        await interaction.followup.send("Aucun pseudo Twitch lié.", ephemeral=True) # <-- MODIFIÉ
    else:
        live = await is_streaming_on_twitch(twitch)
        await interaction.followup.send( # <-- MODIFIÉ
            f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True
        )

# Fonctions Twitch API (inchangées, mais les erreurs sont gérées plus haut)
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
            if "access_token" in data:
                return data["access_token"]
            else:
                print(f"[ERROR] Erreur lors de l'obtention du token Twitch: {data}")
                raise ValueError("Impossible d'obtenir le token Twitch")

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
                if resp.status != 200:
                    print(f"[ERROR] Erreur HTTP lors de la requête Twitch: {resp.status} - {data}")
                    return "❌ Erreur API"
                if data.get("data") and isinstance(data["data"], list):
                    if data["data"]:
                        stream = data["data"][0]
                        if stream.get("game_name", "").lower() == TARGET_GAME.lower():
                            return "🔴 En live"
                    return "⚫ Hors ligne"
                else:
                    print(f"[WARNING] Réponse inattendue de l'API Twitch: {data}")
                    return "❌ Erreur"
    except Exception as e:
        print(f"[ERROR] Échec de la vérification Twitch: {e}")
        return "❌ Erreur"

# Tâche en boucle pour vérifier les streams
@tasks.loop(minutes=2)
async def check_streams():
    print("[DEBUG] Exécution de check_streams")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print(f"[ERROR] Guilde introuvable (ID: {GUILD_ID}). Assurez-vous que le bot est sur le serveur et que GUILD_ID est correct.")
        return

    stream_role = guild.get_role(ROLE_STREAM_ID)
    if not stream_role:
        print(f"[ERROR] Rôle Stream introuvable (ID: {ROLE_STREAM_ID}).")

    try:
        users_ref = db.collection("twitch_links").stream()
    except Exception as e:
        print(f"[ERROR] Impossible de récupérer les liens Twitch de Firestore: {e}")
        return

    for doc in users_ref:
        user_id = int(doc.id)
        twitch_name = doc.to_dict().get("twitch")
        member = guild.get_member(user_id)

        if not member:
            print(f"[DEBUG] Membre Discord {user_id} introuvable pour le lien Twitch {twitch_name}. Lien ignoré.")
            continue
        if is_excluded(member):
            print(f"[DEBUG] Membre {member.display_name} exclu. Ignoré.")
            continue

        live_status = await is_streaming_on_twitch(twitch_name)
        print(f"[DEBUG] Statut de {twitch_name} ({member.display_name}): {live_status}")

        if live_status == "🔴 En live":
            if stream_role and stream_role not in member.roles:
                try:
                    await member.add_roles(stream_role)
                    print(f"[INFO] Ajout du rôle Stream à {member.display_name}")
                except discord.Forbidden:
                    print(f"[ERROR] Permissions insuffisantes pour ajouter le rôle Stream à {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Erreur lors de l'ajout du rôle Stream à {member.display_name}: {e}")

            base_name = get_nick(member.id) or member.display_name
            if not base_name.startswith("🔴"):
                save_nick(member.id, base_name)
                try:
                    await member.edit(nick=f"🔴 {base_name}")
                    print(f"[INFO] Pseudo de {member.display_name} mis à jour en 🔴 {base_name}")
                except discord.Forbidden:
                    print(f"[ERROR] Permissions insuffisantes pour modifier le pseudo de {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Erreur lors de la modification du pseudo de {member.display_name}: {e}")
        else:
            if stream_role and stream_role in member.roles:
                try:
                    await member.remove_roles(stream_role)
                    print(f"[INFO] Suppression du rôle Stream de {member.display_name}")
                except discord.Forbidden:
                    print(f"[ERROR] Permissions insuffisantes pour supprimer le rôle Stream de {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Erreur lors de la suppression du rôle Stream de {member.display_name}: {e}")
            
            nick = get_nick(member.id)
            if nick:
                try:
                    await member.edit(nick=nick)
                    print(f"[INFO] Pseudo de {member.display_name} restauré à {nick}")
                except discord.Forbidden:
                    print(f"[ERROR] Permissions insuffisantes pour restaurer le pseudo de {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Erreur lors de la restauration du pseudo de {member.display_name}: {e}")
                delete_nick(member.id)

# Démarrage du bot
print("[DEBUG] Bot is starting...")
bot.run(TOKEN)
