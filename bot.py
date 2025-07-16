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
# Chaque variable est vérifiée pour s'assurer qu'elle existe et est au bon format.
# Si une erreur est détectée, un message d'erreur critique est imprimé et le script s'arrête.

# DISCORD_TOKEN
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("[CRITICAL ERROR] DISCORD_TOKEN manquant. Le bot ne peut pas se connecter.")
    sys.exit(1)

# DISCORD_GUILD_ID
try:
    GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
except (TypeError, ValueError): # Gère les cas où la variable est manquante ou non numérique
    print("[CRITICAL ERROR] DISCORD_GUILD_ID invalide ou manquant. Assurez-vous qu'il est défini et est un nombre entier.")
    sys.exit(1)

# TWITCH_CLIENT_ID et TWITCH_SECRET
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_SECRET = os.getenv("TWITCH_SECRET")
if not TWITCH_CLIENT_ID or not TWITCH_SECRET:
    print("[CRITICAL ERROR] Clés Twitch (TWITCH_CLIENT_ID ou TWITCH_SECRET) manquantes. Le bot ne pourra pas interroger l'API Twitch.")
    sys.exit(1)

# ROLE_STREAM_ID et ROLE_GAME_ID
try:
    ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))
    ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
except (TypeError, ValueError): # Gère les cas où les variables sont manquantes ou non numériques
    print("[CRITICAL ERROR] ROLE_STREAM_ID ou ROLE_GAME_ID invalide ou manquant. Assurez-vous qu'ils sont définis et sont des nombres entiers.")
    sys.exit(1)

# FIREBASE_KEY_JSON_BASE64 (Clé de service Firebase encodée en Base64)
firebase_key_base64 = os.getenv("FIREBASE_KEY_JSON_BASE64")
if not firebase_key_base64:
    print("[CRITICAL ERROR] Clé Firebase (FIREBASE_KEY_JSON_BASE64) manquante. Impossible d'initialiser Firebase.")
    sys.exit(1)

try:
    # Décoder la chaîne Base64 en bytes, puis en string UTF-8, puis parser le JSON
    firebase_key = json.loads(base64.b64decode(firebase_key_base64).decode("utf-8"))
    print("[DEBUG] Clé Firebase chargée pour le projet :", firebase_key.get("project_id"))
except (base64.binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
    print(f"[CRITICAL ERROR] Erreur de décodage (Base64 ou JSON) pour FIREBASE_KEY_JSON_BASE64: {e}")
    # Affiche le début de la chaîne pour aider au débogage, évite d'afficher toute la clé
    print(f"La valeur de FIREBASE_KEY_JSON_BASE64 (début): {firebase_key_base64[:100]}...")
    sys.exit(1)
except Exception as e:
    print(f"[CRITICAL ERROR] Erreur inattendue lors du chargement de la clé Firebase: {e}")
    sys.exit(1)

# --- Initialisation Firebase ---
try:
    cred = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("[DEBUG] Firebase initialisé avec succès.")
except Exception as e:
    print(f"[CRITICAL ERROR] Erreur lors de l'initialisation de l'application Firebase: {e}")
    sys.exit(1)

# --- Configuration Discord Bot ---
intents = discord.Intents.default()
intents.presences = True # Nécessaire pour détecter les activités (jeux, streams)
intents.members = True   # Nécessaire pour accéder aux membres et leurs rôles/pseudos
intents.guilds = True    # Nécessaire pour obtenir l'objet guilde et synchroniser les commandes
intents.message_content = False # Non nécessaire pour les commandes slash, bonne pratique de désactiver si non utilisé

bot = commands.Bot(command_prefix="!", intents=intents)

# Jeu cible pour la détection
TARGET_GAME = "Star Citizen"
# Rôles Discord à exclure de la gestion automatique (par exemple, les administrateurs)
EXCLUDED_ROLE_IDS = [1363632614556041417] # Remplacez par les IDs de vos rôles exclus

# --- Fonctions Firebase pour la persistance des données ---
def is_excluded(member):
    """Vérifie si un membre possède un rôle exclu."""
    return any(role.id in EXCLUDED_ROLE_IDS for role in member.roles)

def save_link(user_id, twitch):
    """Sauvegarde le pseudo Twitch d'un utilisateur dans Firestore."""
    db.collection("twitch_links").document(str(user_id)).set({"twitch": twitch})

def delete_link(user_id):
    """Supprime le pseudo Twitch d'un utilisateur de Firestore."""
    db.collection("twitch_links").document(str(user_id)).delete()

def get_link(user_id):
    """Récupère le pseudo Twitch lié à un utilisateur depuis Firestore."""
    doc = db.collection("twitch_links").document(str(user_id)).get()
    return doc.to_dict()["twitch"] if doc.exists else None

def save_nick(user_id, nick):
    """Sauvegarde le pseudo original d'un utilisateur avant modification."""
    db.collection("nicknames").document(str(user_id)).set({"nick": nick})

def delete_nick(user_id):
    """Supprime le pseudo original enregistré d'un utilisateur."""
    db.collection("nicknames").document(str(user_id)).delete()

def get_nick(user_id):
    """Récupère le pseudo original d'un utilisateur depuis Firestore."""
    doc = db.collection("nicknames").document(str(user_id)).get()
    return doc.to_dict()["nick"] if doc.exists else None

# --- Fonctions d'interaction avec l'API Twitch ---
async def get_twitch_token():
    """Obtient un token d'accès OAuth de Twitch."""
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
    """Vérifie si un utilisateur Twitch stream et sur quel jeu."""
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
                        return "🔴 En live" # Stream sur le jeu cible
                    return "🟣 Autre live" # Stream sur un autre jeu
                return "⚫ Hors ligne" # Pas de données de stream, donc hors ligne
    except Exception as e:
        print(f"[ERROR] Échec Twitch API pour {username}: {e}")
        return "❌ Erreur"

# --- Événements Discord ---
@bot.event
async def on_ready():
    """Se déclenche lorsque le bot est connecté à Discord."""
    print("[DEBUG] Événement on_ready déclenché")
    print(f"Connecté en tant que {bot.user}")
    await bot.change_presence(activity=discord.CustomActivity(name="/link")) # Définit le statut du bot
    try:
        # Synchronise les commandes slash avec la guilde spécifiée
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"[DEBUG] Commandes synchronisées : {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"[ERROR] Erreur de synchronisation des commandes : {e}")
    check_streams.start() # Démarre la tâche de vérification des streams

# --- Commandes Slash ---
@bot.tree.command(name="link", description="Lier ton pseudo Twitch", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(twitch="Ton pseudo Twitch")
async def link(interaction: discord.Interaction, twitch: str):
    """Commande pour lier un pseudo Twitch à l'utilisateur Discord."""
    # Acquitte l'interaction immédiatement pour éviter les timeouts Discord
    await interaction.response.defer(ephemeral=True) 
    save_link(interaction.user.id, twitch) # Sauvegarde le lien Twitch dans Firestore
    live = await is_streaming_on_twitch(twitch) # Vérifie le statut Twitch
    # Envoie la réponse réelle via followup.send après le defer
    await interaction.followup.send(f"Twitch lié : `{twitch}`\nStatut : {live}", ephemeral=True)

@bot.tree.command(name="unlink", description="Supprimer le lien Twitch", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    """Commande pour supprimer le lien Twitch de l'utilisateur Discord."""
    await interaction.response.defer(ephemeral=True)
    delete_link(interaction.user.id) # Supprime le lien Twitch de Firestore
    await interaction.followup.send("Lien supprimé.", ephemeral=True)

@bot.tree.command(name="statut", description="Afficher le statut Twitch", guild=discord.Object(id=GUILD_ID))
async def statut(interaction: discord.Interaction):
    """Commande pour afficher le statut Twitch de l'utilisateur lié."""
    await interaction.response.defer(ephemeral=True) # Acquitte l'interaction
    twitch = get_link(interaction.user.id) # Récupère le lien Twitch
    if not twitch:
        await interaction.followup.send("Aucun pseudo Twitch lié.", ephemeral=True)
    else:
        live = await is_streaming_on_twitch(twitch) # Vérifie le statut Twitch
        await interaction.followup.send(f"Twitch : `{twitch}`\nStatut : {live}", ephemeral=True)

# --- Tâche périodique pour vérifier les streams et les jeux ---
@tasks.loop(minutes=2) # La tâche s'exécute toutes les 2 minutes
async def check_streams():
    print("[DEBUG] check_streams lancé")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print(f"[ERROR] Guilde {GUILD_ID} introuvable. Vérifiez GUILD_ID et les intents du bot.")
        return # Quitte la tâche si la guilde n'est pas trouvée

    stream_role = guild.get_role(ROLE_STREAM_ID)
    game_role = guild.get_role(ROLE_GAME_ID)

    # Avertissements si les rôles ne sont pas trouvés (mais ne bloque pas la tâche)
    if not stream_role:
        print(f"[WARNING] Rôle Stream (ID: {ROLE_STREAM_ID}) introuvable. La gestion du rôle de stream sera ignorée.")
    if not game_role:
        print(f"[WARNING] Rôle Jeu (ID: {ROLE_GAME_ID}) introuvable. La gestion du rôle de jeu sera ignorée.")

    try:
        # Itère sur les documents Firestore de manière synchrone (comme attendu par .stream())
        docs = db.collection("twitch_links").stream()
        for doc in docs:
            user_id = int(doc.id)
            twitch_name = doc.to_dict().get("twitch")
            member = guild.get_member(user_id) # Récupère l'objet membre Discord

            if not member:
                print(f"[DEBUG] Membre Discord {user_id} introuvable pour le lien Twitch {twitch_name}. Lien ignoré.")
                continue # Passe au prochain document si le membre n'est plus sur le serveur
            if is_excluded(member):
                print(f"[DEBUG] Membre {member.display_name} exclu. Ignoré.")
                continue # Passe au prochain document si le membre est exclu

            # --- Statut Twitch ---
            status = await is_streaming_on_twitch(twitch_name)
            print(f"[DEBUG] {member.display_name} -> Statut Twitch: {status}")
            
            # Gestion du rôle de stream (si le rôle existe)
            if stream_role: 
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
                print(f"[DEBUG] Rôle Stream non défini, skipping gestion du rôle de stream pour {member.display_name}.")


            # Gestion du pseudo (si l'utilisateur est en live sur le jeu cible)
            # Récupère le pseudo de base (original ou actuel si non enregistré)
            base_name = get_nick(member.id) or member.display_name 
            if status == "🔴 En live" and not base_name.startswith("🔴"):
                save_nick(member.id, base_name) # Sauvegarde le pseudo original avant de le modifier
                try:
                    await member.edit(nick=f"🔴 {base_name}")
                    print(f"[INFO] Pseudo de {member.display_name} mis à jour en 🔴 {base_name}")
                except discord.Forbidden:
                    print(f"[ERROR] Permissions insuffisantes pour modifier le pseudo de {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Erreur lors de la modification du pseudo de {member.display_name}: {e}")
            elif status != "🔴 En live" and base_name.startswith("🔴"): # Si n'est plus en live sur le jeu cible et pseudo modifié
                nick_original = get_nick(member.id) # Tente de récupérer le pseudo original enregistré
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
                    # Cas où le pseudo original n'a pas été enregistré (ex: bot a crashé, ou pseudo modifié manuellement)
                    # On tente de supprimer le 🔴 si le pseudo actuel commence par 🔴
                    if member.display_name.startswith("🔴"):
                        try:
                            # Supprime le "🔴 " et les espaces éventuels
                            await member.edit(nick=member.display_name[2:].strip()) 
                            print(f"[INFO] Pseudo de {member.display_name} nettoyé (🔴 retiré).")
                        except discord.Forbidden:
                            print(f"[ERROR] Permissions insuffisantes pour nettoyer le pseudo de {member.display_name}")
                        except Exception as e:
                            print(f"[ERROR] Erreur lors du nettoyage du pseudo de {member.display_name}: {e}")


            # --- Détection de jeu Star Citizen (activité Discord) ---
            is_playing_game = False
            if member.activities: # S'assure qu'il y a des activités à vérifier
                for act in member.activities:
                    # MODIFIÉ : Vérifie si l'activité est de type 'Playing' (discord.ActivityType.playing)
                    # et si le nom du jeu correspond à TARGET_GAME (insensible à la casse)
                    if isinstance(act, discord.Activity) and act.type == discord.ActivityType.playing and act.name and act.name.lower() == TARGET_GAME.lower():
                        is_playing_game = True
                        break # Sort de la boucle dès que le jeu cible est trouvé

            print(f"[DEBUG] {member.display_name} -> Joue à {TARGET_GAME}: {is_playing_game}")

            # Gestion du rôle de jeu (si le rôle existe)
            if game_role:
                if is_playing_game:
                    if game_role not in member.roles:
                        try:
                            await member.add_roles(game_role)
                            print(f"[INFO] Rôle jeu ajouté à {member.display_name}")
                        except discord.Forbidden:
                            print(f"[ERROR] Permissions insuffisantes pour ajouter le rôle Jeu à {member.display_name}")
                        except Exception as e:
                            print(f"[ERROR] Erreur lors de l'ajout du rôle Jeu à {member.display_name}: {e}")
                else: # Si ne joue plus au jeu cible
                    if game_role in member.roles:
                        try:
                            await member.remove_roles(game_role)
                            print(f"[INFO] Rôle jeu retiré de {member.display_name}")
                        except discord.Forbidden:
                            print(f"[ERROR] Permissions insuffisantes pour retirer le rôle Jeu de {member.display_name}")
                        except Exception as e:
                            print(f"[ERROR] Erreur lors de la suppression du rôle Jeu de {member.display_name}: {e}")
            else:
                print(f"[DEBUG] Rôle Jeu non défini, skipping gestion du rôle de jeu pour {member.display_name}.")

    except Exception as e:
        print(f"[ERROR] Erreur générale dans check_streams: {e}")
        # Ne pas sys.exit(1) ici pour ne pas faire planter le bot entier,
        # la tâche se relancera au prochain intervalle.

# --- Lancement du bot ---
print("[DEBUG] Bot en cours de lancement...")
bot.run(TOKEN)
