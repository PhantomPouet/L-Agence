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
except (TypeError, ValueError): # Ajout de TypeError pour une meilleure gestion
    print("[CRITICAL ERROR] DISCORD_GUILD_ID invalide ou manquant")
    sys.exit(1)

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_SECRET = os.getenv("TWITCH_SECRET")
if not TWITCH_CLIENT_ID or not TWITCH_SECRET:
    print("[CRITICAL ERROR] Clés Twitch (CLIENT_ID ou SECRET) manquantes")
    sys.exit(1)

try:
    ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
    ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
except (TypeError, ValueError): # Ajout de TypeError pour une meilleure gestion
    print("[CRITICAL ERROR] ROLE_STREAM_ID ou ROLE_GAME_ID invalide ou manquant")
    sys.exit(1)

firebase_key_base64 = os.getenv("FIREBASE_KEY_JSON_BASE64")
if not firebase_key_base64:
    print("[CRITICAL ERROR] Clé Firebase (FIREBASE_KEY_JSON_BASE64) manquante")
    sys.exit(1)

try:
    firebase_key = json.loads(base64.b64decode(firebase_key_base64).decode("utf-8"))
    print("[DEBUG] Clé Firebase chargée pour", firebase_key.get("project_id"))
except Exception as e:
    print(f"[CRITICAL ERROR] Erreur décodage Firebase: {e}")
    sys.exit(1)

# --- Initialisation Firebase ---
try:
    cred = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("[DEBUG] Firebase initialisé")
except Exception as e:
    print(f"[CRITICAL ERROR] Firebase init échoué: {e}")
    sys.exit(1)

# --- Configuration Discord Bot ---
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.guilds = True # AJOUTÉ : Nécessaire pour bot.get_guild() et la gestion des membres
intents.message_content = False # AJOUTÉ : Bonne pratique si non utilisé

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
    # Ajout de la gestion d'erreur pour le cas où les secrets Twitch sont vides
    if not TWITCH_CLIENT_ID or not TWITCH_SECRET:
        print("[ERROR] TWITCH_CLIENT_ID ou TWITCH_SECRET est vide. Impossible d'obtenir le token Twitch.")
        raise ValueError("Clés Twitch manquantes ou vides.")

    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_SECRET,
        "grant_type": "client_credentials"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, params=params) as resp:
            data = await resp.json()
            if resp.status == 200 and "access_token" in data:
                return data["access_token"]
            else:
                print(f"[ERROR] Erreur lors de l'obtention du token Twitch: Statut={resp.status}, Réponse={data}")
                raise ValueError(f"Impossible d'obtenir le token Twitch. Statut: {resp.status}")

async def is_streaming_on_twitch(username):
    try:
        token = await get_twitch_token()
        if not token: # Vérifie si le token est vide après get_twitch_token
            return "❌ Erreur (token vide)"

        headers = {
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.twitch.tv/helix/streams?user_login={username}", headers=headers) as resp:
                data = await resp.json()
                print(f"[DEBUG] Twitch API response for {username}: {data}")
                if resp.status != 200:
                    print(f"[ERROR] Erreur HTTP lors de la requête Twitch: Statut={resp.status} - Réponse={data}")
                    return "❌ Erreur API"
                
                # Vérifie si 'data' est une liste non vide et contient des informations de stream
                if data.get("data") and isinstance(data["data"], list) and data["data"]:
                    stream = data["data"][0]
                    if stream.get("game_name", "").lower() == TARGET_GAME.lower():
                        return "🔴 En live"
                    return "🟣 Autre live" # En live, mais pas sur le jeu cible
                return "⚫ Hors ligne" # Pas de données de stream, donc hors ligne
    except Exception as e:
        print(f"[ERROR] Échec Twitch API pour {username}: {e}")
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
        print(f"[ERROR] Sync échouée: {e}")
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
    await interaction.response.defer(ephemeral=True) # AJOUTÉ : Defer pour éviter "Unknown interaction"
    twitch = get_link(interaction.user.id)
    if not twitch:
        await interaction.followup.send("Aucun Twitch lié.", ephemeral=True) # MODIFIÉ : followup.send
    else:
        live = await is_streaming_on_twitch(twitch)
        await interaction.followup.send(f"Twitch : `{twitch}`\nStatut : {live}", ephemeral=True) # MODIFIÉ : followup.send

# --- Tâche périodique ---
@tasks.loop(minutes=2)
async def check_streams():
    print("[DEBUG] check_streams lancé")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print(f"[ERROR] Guilde {GUILD_ID} introuvable. Vérifiez GUILD_ID et les intents.")
        return

    stream_role = guild.get_role(ROLE_STREAM_ID)
    game_role = guild.get_role(ROLE_GAME_ID)

    # Ajout de gestion d'erreur pour les rôles manquants
    if not stream_role:
        print(f"[WARNING] Rôle Stream (ID: {ROLE_STREAM_ID}) introuvable. La gestion du rôle de stream sera ignorée.")
    if not game_role:
        print(f"[WARNING] Rôle Jeu (ID: {ROLE_GAME_ID}) introuvable. La gestion du rôle de jeu sera ignorée.")


    try:
        docs = db.collection("twitch_links").stream()
        for doc in docs:
            user_id = int(doc.id)
            twitch_name = doc.to_dict().get("twitch")
            member = guild.get_member(user_id) # get_member est asynchrone, mais l'itération des docs est synchrone

            if not member:
                print(f"[DEBUG] Membre Discord {user_id} introuvable pour le lien Twitch {twitch_name}. Lien ignoré.")
                continue
            if is_excluded(member):
                print(f"[DEBUG] Membre {member.display_name} exclu. Ignoré.")
                continue

            # --- Statut Twitch ---
            status = await is_streaming_on_twitch(twitch_name)
            print(f"[DEBUG] {member.display_name} -> Statut Twitch: {status}")
            
            # Gestion du rôle de stream
            if stream_role: # Vérifie si le rôle existe avant d'essayer de l'ajouter/retirer
                if status == "🔴 En live":
                    if stream_role not in member.roles:
                        try:
                            await member.add_roles(stream_role)
                            print(f"[INFO] Rôle stream ajouté à {member.display_name}")
                        except discord.Forbidden:
                            print(f"[ERROR] Permissions insuffisantes pour ajouter le rôle Stream à {member.display_name}")
                        except Exception as e:
                            print(f"[ERROR] Erreur lors de l'ajout du rôle Stream à {member.display_name}: {e}")
                else: # Si le statut n'est pas "🔴 En live"
                    if stream_role in member.roles:
                        try:
                            await member.remove_roles(stream_role)
                            print(f"[INFO] Rôle stream retiré de {member.display_name}")
                        except discord.Forbidden:
                            print(f"[ERROR] Permissions insuffisantes pour retirer le rôle Stream de {member.display_name}")
                        except Exception as e:
                            print(f"[ERROR] Erreur lors de la suppression du rôle Stream de {member.display_name}: {e}")
            else:
                print(f"[DEBUG] Rôle Stream non défini, skipping rôle de stream pour {member.display_name}.")


            # Gestion du pseudo (si l'utilisateur est en live sur Star Citizen)
            base_name = get_nick(member.id) or member.display_name
            if status == "🔴 En live" and not base_name.startswith("🔴"):
                save_nick(member.id, base_name) # Sauvegarde le pseudo original
                try:
                    await member.edit(nick=f"🔴 {base_name}")
                    print(f"[INFO] Pseudo de {member.display_name} mis à jour en 🔴 {base_name}")
                except discord.Forbidden:
                    print(f"[ERROR] Permissions insuffisantes pour modifier le pseudo de {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Erreur lors de la modification du pseudo de {member.display_name}: {e}")
            elif status != "🔴 En live" and base_name.startswith("🔴"): # Si n'est plus en live sur Star Citizen et pseudo modifié
                nick_original = get_nick(member.id) # Récupère le pseudo original
                if nick_original:
                    try:
                        await member.edit(nick=nick_original)
                        print(f"[INFO] Pseudo de {member.display_name} restauré à {nick_original}")
                    except discord.Forbidden:
                        print(f"[ERROR] Permissions insuffisantes pour restaurer le pseudo de {member.display_name}")
                    except Exception as e:
                        print(f"[ERROR] Erreur lors de la restauration du pseudo de {member.display_name}: {e}")
                    delete_nick(member.id) # Supprime le pseudo enregistré après restauration
                else:
                    # Si pas de pseudo original enregistré mais le pseudo commence par 🔴
                    # Cela peut arriver si le bot a crashé avant d'enregistrer le nick
                    # ou si le pseudo a été modifié manuellement sur Discord.
                    # On tente de supprimer le 🔴
                    if member.display_name.startswith("🔴"):
                        try:
                            await member.edit(nick=member.display_name[2:].strip()) # Supprime le 🔴 et les espaces
                            print(f"[INFO] Pseudo de {member.display_name} nettoyé (🔴 retiré).")
                        except discord.Forbidden:
                            print(f"[ERROR] Permissions insuffisantes pour nettoyer le pseudo de {member.display_name}")
                        except Exception as e:
                            print(f"[ERROR] Erreur lors du nettoyage du pseudo de {member.display_name}: {e}")


            # --- Détection de jeu Star Citizen ---
            # Assurez-vous que member.activities est bien une liste d'activités
            # et que l'intent 'presences' est activé pour les obtenir.
            is_playing_game = False
            if member.activities:
                for act in member.activities:
                    # Vérifier si c'est une activité de type "Jeu" (Playing)
                    # et si le nom du jeu correspond à TARGET_GAME
                    if isinstance(act, discord.Game) and act.name and act.name.lower() == TARGET_GAME.lower():
                        is_playing_game = True
                        break # Pas besoin de vérifier d'autres activités

            print(f"[DEBUG] {member.display_name} -> Joue à {TARGET_GAME}: {is_playing_game}")

            # Gestion du rôle de jeu
            if game_role: # Vérifie si le rôle existe avant d'essayer de l'ajouter/retirer
                if is_playing_game:
                    if game_role not in member.roles:
                        try:
                            await member.add_roles(game_role)
                            print(f"[INFO] Rôle jeu ajouté à {member.display_name}")
                        except discord.Forbidden:
                            print(f"[ERROR] Permissions insuffisantes pour ajouter le rôle Jeu à {member.display_name}")
                        except Exception as e:
                            print(f"[ERROR] Erreur lors de l'ajout du rôle Jeu à {member.display_name}: {e}")
                else:
                    if game_role in member.roles:
                        try:
                            await member.remove_roles(game_role)
                            print(f"[INFO] Rôle jeu retiré de {member.display_name}")
                        except discord.Forbidden:
                            print(f"[ERROR] Permissions insuffisantes pour retirer le rôle Jeu de {member.display_name}")
                        except Exception as e:
                            print(f"[ERROR] Erreur lors de la suppression du rôle Jeu de {member.display_name}: {e}")
            else:
                print(f"[DEBUG] Rôle Jeu non défini, skipping rôle de jeu pour {member.display_name}.")

    except Exception as e:
        print(f"[ERROR] Erreur générale dans check_streams: {e}")
        # Ne pas retourner ici pour que la tâche continue de s'exécuter aux prochains intervalles
        # mais log l'erreur pour le diagnostic.

# --- Lancement ---
print("[DEBUG] Bot en cours de lancement...")
bot.run(TOKEN)
