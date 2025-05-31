import os
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import logging
import asyncio

# Importe nos modules personnalisés
import database
import twitch_api

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

intents = discord.Intents.default()
intents.presences = True # Nécessaire pour on_presence_update
intents.members = True   # Nécessaire pour accéder aux membres et leurs rôles/pseudos

bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ROLE_GAME_ID = int(os.getenv("ROLE_GAME_ID"))
ROLE_STREAM_ID = int(os.getenv("ROLE_STREAM_ID"))

# Variables globales pour les rôles et la guilde (cache)
target_guild = None
role_game_cached = None
role_stream_cached = None
twitch_api_client = None # Instance du client Twitch API

# --- Fonctions utilitaires ---

async def update_member_roles_and_nickname(member: discord.Member, is_streaming_on_twitch: bool, is_playing_star_citizen_discord: bool, original_nickname: str = None):
    """Gère l'ajout/retrait de rôles et la modification du pseudo."""
    global role_game_cached, role_stream_cached, target_guild

    if not target_guild or not role_game_cached or not role_stream_cached:
        logging.warning("Rôles ou guilde non initialisés. Impossible de mettre à jour le membre.")
        return

    # Vérification des permissions avant toute opération
    if not target_guild.me.guild_permissions.manage_roles:
        logging.warning(f"Le bot n'a pas la permission de gérer les rôles. Impossible de gérer les rôles de {member.display_name}")
    if not target_guild.me.guild_permissions.manage_nicknames:
        logging.warning(f"Le bot n'a pas la permission de gérer les pseudos. Impossible de modifier le pseudo de {member.display_name}")

    # GESTION DU RÔLE DE STREAMING ET DU PSEUDO
    if is_streaming_on_twitch:
        if role_stream_cached not in member.roles:
            if target_guild.me.guild_permissions.manage_roles:
                try:
                    await member.add_roles(role_stream_cached)
                    logging.info(f"Rôle '{role_stream_cached.name}' ajouté à {member.display_name} (via Twitch stream).")
                except discord.Forbidden:
                    logging.warning(f"Le bot n'a pas les permissions pour ajouter le rôle {role_stream_cached.name} à {member.display_name}.")
                except Exception as e:
                    logging.error(f"Erreur lors de l'ajout du rôle {role_stream_cached.name} à {member.display_name}: {e}")
        
        # Mettre à jour le pseudo avec le préfixe "🔴"
        if target_guild.me.guild_permissions.manage_nicknames:
            current_nick = member.nick if member.nick else member.name
            if not current_nick.startswith("🔴"):
                try:
                    # Stocker le pseudo original si ce n'est pas déjà fait ou si c'est la première fois
                    # Ou si l'original stocké est différent de l'actuel (au cas où un admin l'aurait changé)
                    _, stored_original_nickname = database.get_twitch_link(member.id)
                    if stored_original_nickname is None or stored_original_nickname != current_nick:
                        database.update_original_nickname(member.id, current_nick)
                        logging.info(f"Pseudo original de {member.display_name} enregistré: '{current_nick}'")

                    new_nick = f"🔴 {current_nick}"
                    await member.edit(nick=new_nick)
                    logging.info(f"Pseudo de {member.display_name} mis à jour : '{new_nick}'")
                except discord.Forbidden:
                    logging.warning(f"Le bot n'a pas les permissions pour changer le pseudo de {member.display_name} (Forbidden).")
                except Exception as e:
                    logging.error(f"Erreur inattendue lors de la mise à jour du pseudo de {member.display_name}: {e}")
    else: # Plus en stream sur Twitch
        if role_stream_cached in member.roles:
            if target_guild.me.guild_permissions.manage_roles:
                try:
                    await member.remove_roles(role_stream_cached)
                    logging.info(f"Rôle '{role_stream_cached.name}' retiré de {member.display_name} (plus de Twitch stream).")
                except discord.Forbidden:
                    logging.warning(f"Le bot n'a pas les permissions pour retirer le rôle {role_stream_cached.name} de {member.display_name}.")
                except Exception as e:
                    logging.error(f"Erreur lors du retrait du rôle {role_stream_cached.name} de {member.display_name}: {e}")
        
        # Restaurer le pseudo si le préfixe "🔴" est présent et qu'il y a un original
        if target_guild.me.guild_permissions.manage_nicknames:
            current_nick = member.nick
            if current_nick and current_nick.startswith("🔴"):
                try:
                    # Récupérer le pseudo original de la DB
                    _, stored_original_nickname = database.get_twitch_link(member.id)
                    
                    if stored_original_nickname:
                        new_nick = stored_original_nickname
                    else: # Fallback si l'original n'a pas été stocké (cas rare)
                        new_nick = current_nick[len("🔴 "):].strip() 
                        if not new_nick: new_nick = None # Si pseudo vide après retrait du prefix, remettre None pour le nom d'utilisateur

                    await member.edit(nick=new_nick)
                    logging.info(f"Pseudo de {member.display_name} restauré : '{new_nick}' (depuis Twitch stream).")
                    # database.update_original_nickname(member.id, None) # Optionnel: pour nettoyer l'original après restauration
                except discord.Forbidden:
                    logging.warning(f"Le bot n'a pas les permissions pour restaurer le pseudo de {member.display_name}.")
                except Exception as e:
                    logging.error(f"Erreur inattendue lors de la restauration du pseudo de {member.display_name}: {e}")

    # GESTION DU RÔLE DE JEU (Star Citizen)
    # Ce rôle peut être activé soit par l'activité Discord, soit par le stream Twitch
    # IMPORTANT: Si le stream Twitch n'est pas sur Star Citizen mais qu'ils jouent à Star Citizen
    # sur Discord, le rôle de jeu doit quand même être activé.
    # Pour l'instant, on suppose que si quelqu'un stream SC sur Twitch et l'a lié, le rôle jeu s'applique.
    should_have_game_role = is_playing_star_citizen_discord or is_streaming_on_twitch # Twitch stream = aussi un jeu

    if should_have_game_role:
        if role_game_cached not in member.roles:
            if target_guild.me.guild_permissions.manage_roles:
                try:
                    await member.add_roles(role_game_cached)
                    logging.info(f"Rôle '{role_game_cached.name}' ajouté à {member.display_name} (Star Citizen).")
                except discord.Forbidden:
                    logging.warning(f"Le bot n'a pas les permissions pour ajouter le rôle {role_game_cached.name} à {member.display_name}.")
                except Exception as e:
                    logging.error(f"Erreur lors de l'ajout du rôle {role_game_cached.name} à {member.display_name}: {e}")
    else:
        if role_game_cached in member.roles:
            if target_guild.me.guild_permissions.manage_roles:
                try:
                    await member.remove_roles(role_game_cached)
                    logging.info(f"Rôle '{role_game_cached.name}' retiré de {member.display_name} (ne joue plus à Star Citizen).")
                except discord.Forbidden:
                    logging.warning(f"Le bot n'a pas les permissions pour retirer le rôle {role_game_cached.name} de {member.display_name}.")
                except Exception as e:
                    logging.error(f"Erreur lors du retrait du rôle {role_game_cached.name} de {member.display_name}: {e}")


# --- Événements du bot ---

@bot.event
async def on_ready():
    global target_guild, role_game_cached, role_stream_cached, twitch_api_client
    logging.info(f"{bot.user} est connecté à Discord !")

    # Initialiser la base de données
    database.init_db()

    # Initialiser le client Twitch API
    twitch_api_client = twitch_api.TwitchAPI()
    await twitch_api_client._get_access_token() # Obtient le premier jeton

    target_guild = bot.get_guild(GUILD_ID)
    if not target_guild:
        logging.error(f"La guilde avec l'ID {GUILD_ID} est introuvable. Veuillez vérifier GUILD_ID dans .env")
        return

    role_game_cached = target_guild.get_role(ROLE_GAME_ID)
    if not role_game_cached:
        logging.warning(f"Le rôle de jeu avec l'ID {ROLE_GAME_ID} est introuvable dans la guilde {target_guild.name}. Vérifiez ROLE_GAME_ID.")

    role_stream_cached = target_guild.get_role(ROLE_STREAM_ID)
    if not role_stream_cached:
        logging.warning(f"Le rôle de stream avec l'ID {ROLE_STREAM_ID} est introuvable dans la guilde {target_guild.name}. Vérifiez ROLE_STREAM_ID.")

    # Démarrer la tâche en arrière-plan pour vérifier les streams Twitch
    check_twitch_streams.start()

@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    # Ignorer si ce n'est pas la guilde cible ou si les rôles ne sont pas définis
    if after.guild != target_guild or not role_game_cached or not role_stream_cached:
        return

    member = after

    is_streaming_discord = False
    is_playing_star_citizen_discord = False

    for activity in after.activities:
        if activity.type == discord.ActivityType.streaming:
            is_streaming_discord = True # Note: Ceci est la détection Discord de streaming
            # Si le stream Discord détecté est Star Citizen, c'est suffisant pour le rôle de jeu
            if activity.game and activity.game.name == "Star Citizen":
                 is_playing_star_citizen_discord = True
        if activity.type == discord.ActivityType.playing and activity.name == "Star Citizen":
            is_playing_star_citizen_discord = True
        
        # Optimisation: si les deux sont trouvés, on peut sortir de la boucle
        if is_streaming_discord and is_playing_star_citizen_discord:
            break

    # Vérifier si l'utilisateur a un compte Twitch lié
    twitch_username, original_nickname = database.get_twitch_link(member.id)
    is_streaming_on_twitch = False # Statut initial de streaming Twitch

    if twitch_username:
        logging.info(f"Vérification du stream Twitch pour {member.display_name} ({twitch_username}) via on_presence_update.")
        twitch_status = await twitch_api_client.get_stream_status(twitch_username)
        
        # Si le stream est détecté via Twitch, il prime sur l'activité Discord pour le rôle de stream et de jeu
        if twitch_status is True:
            is_streaming_on_twitch = True
            is_playing_star_citizen_discord = True # Si stream Twitch, on considère qu'il joue à SC pour le rôle
        elif twitch_status is False:
            is_streaming_on_twitch = False
        else: # Erreur ou impossible de vérifier
            logging.error(f"Impossible de vérifier le statut du stream Twitch pour {twitch_username}. Bascule sur l'activité Discord.")
            # Si erreur, on ne modifie pas is_streaming_on_twitch, donc on se base sur Discord uniquement
            is_streaming_on_twitch = is_streaming_discord # Fallback sur la détection Discord

    # Appel de la fonction utilitaire avec les deux sources d'information
    await update_member_roles_and_nickname(member, is_streaming_on_twitch, is_playing_star_citizen_discord, original_nickname)


# --- Commandes du bot ---

@bot.command(name="link_twitch", help="Lie votre compte Discord à un nom d'utilisateur Twitch. Utilisation: !link_twitch <nom_utilisateur_twitch>")
async def link_twitch(ctx: commands.Context, twitch_username: str):
    member = ctx.author
    # Obtenir le pseudo actuel pour le stocker comme original
    original_nickname = member.nick if member.nick else member.name
    
    if database.link_twitch_account(member.id, twitch_username, original_nickname):
        await ctx.send(f"Votre compte Discord est maintenant lié à Twitch : **{twitch_username}**.")
        logging.info(f"{member.display_name} a lié son compte à Twitch: {twitch_username}")
        
        # Vérifier immédiatement le statut du stream Twitch après la liaison
        is_streaming = await twitch_api_client.get_stream_status(twitch_username)
        if is_streaming is True:
            await update_member_roles_and_nickname(member, True, True, original_nickname)
        elif is_streaming is False:
            # Si pas en stream Twitch, vérifier aussi l'activité Discord pour le rôle de jeu
            is_playing_star_citizen_discord = any(
                a.type == discord.ActivityType.playing and a.name == "Star Citizen"
                for a in member.activities
            )
            await update_member_roles_and_nickname(member, False, is_playing_star_citizen_discord, original_nickname)
        else:
            await ctx.send(f"⚠️ Impossible de vérifier le statut de votre stream Twitch actuellement. Le lien est enregistré, mais la mise à jour des rôles/pseudo peut être retardée.")

    else:
        await ctx.send("Une erreur est survenue lors de la liaison de votre compte Twitch. Veuillez réessayer.")

@bot.command(name="unlink_twitch", help="Délie votre compte Discord de Twitch.")
async def unlink_twitch(ctx: commands.Context):
    member = ctx.author
    # Avant de délier, récupérer le pseudo original pour le restaurer
    _, original_nickname = database.get_twitch_link(member.id)

    if database.unlink_twitch_account(member.id):
        await ctx.send("Votre compte Discord a été délié de Twitch.")
        logging.info(f"{member.display_name} a délié son compte Twitch.")
        
        # S'assurer que le rôle de stream est retiré et le pseudo restauré
        # Le second False indique qu'il ne joue PAS à Star Citizen via Discord après le délink
        await update_member_roles_and_nickname(member, False, False, original_nickname)
    else:
        await ctx.send("Vous n'avez pas de compte Twitch lié.")


# --- Tâche en arrière-plan pour vérifier les streams Twitch ---

@tasks.loop(minutes=5) # Vérifie toutes les 5 minutes
async def check_twitch_streams():
    global target_guild, twitch_api_client
    if not target_guild or not twitch_api_client:
        logging.warning("Guilde ou client Twitch API non initialisé. Skipping Twitch stream check.")
        return

    logging.info("Démarrage de la vérification périodique des streams Twitch liés.")
    all_links = database.get_all_twitch_links()
    
    for discord_id, twitch_username, original_nickname in all_links:
        member = target_guild.get_member(discord_id)
        if not member:
            logging.warning(f"Membre Discord avec ID {discord_id} introuvable dans la guilde. Suppression de la liaison Twitch.")
            database.unlink_twitch_account(discord_id) # Nettoyer les entrées orphelines
            continue

        logging.info(f"Vérification du stream Twitch pour {member.display_name} ({twitch_username})...")
        is_streaming_on_twitch = await twitch_api_client.get_stream_status(twitch_username)
        
        if is_streaming_on_twitch is True:
            # Si un stream Twitch est détecté, on suppose que c'est Star Citizen pour le rôle de jeu
            await update_member_roles_and_nickname(member, True, True, original_nickname)
        elif is_streaming_on_twitch is False:
            # Si pas en stream Twitch, vérifier aussi l'activité Discord (pour le rôle de jeu)
            is_playing_star_citizen_discord = any(
                a.type == discord.ActivityType.playing and a.name == "Star Citizen"
                for a in member.activities
            )
            await update_member_roles_and_nickname(member, False, is_playing_star_citizen_discord, original_nickname)
        else: # Erreur lors de la vérification du stream
            logging.error(f"Impossible de vérifier le statut du stream pour {member.display_name} ({twitch_username}).")


# --- Démarrage et arrêt du bot ---

@bot.event
async def on_disconnect():
    logging.info("Bot déconnecté de Discord.")
    if twitch_api_client:
        await twitch_api_client.close()

@bot.event
async def on_error(event, *args, **kwargs):
    logging.exception(f"Unhandled error in event {event}: {args}, {kwargs}")

try:
    bot.run(TOKEN)
except discord.LoginFailure:
    logging.critical("Erreur: Le token Discord est invalide. Vérifiez votre .env")
except Exception as e:
    logging.critical(f"Erreur fatale lors du démarrage du bot: {e}")
finally:
    if twitch_api_client and not twitch_api_client.session.closed:
        asyncio.run(twitch_api_client.close()) # S'assurer que la session aiohttp est fermée même en cas d'erreur