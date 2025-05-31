# database.py
import sqlite3
import logging
import os # Ajoutez cette importation

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Chemin de base de données ajusté pour Railway /app/data
DATABASE_DIR = os.getenv("DATABASE_PATH", "/app/data") # Définit le chemin par défaut à /app/data
DATABASE_NAME = os.path.join(DATABASE_DIR, 'bot_data.db')

def init_db():
    """Initialise la base de données et crée la table si elle n'existe pas."""
    os.makedirs(DATABASE_DIR, exist_ok=True) # Crée le répertoire /app/data si nécessaire
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS twitch_links (
                discord_id INTEGER PRIMARY KEY,
                twitch_username TEXT NOT NULL,
                original_nickname TEXT, -- Pour stocker le pseudo avant modification par le bot
                UNIQUE(twitch_username)
            )
        ''')
        conn.commit()
        logging.info(f"Base de données initialisée avec succès à {DATABASE_NAME}.")
    except sqlite3.Error as e:
        logging.error(f"Erreur lors de l'initialisation de la base de données : {e}")
    finally:
        if conn:
            conn.close()

def link_twitch_account(discord_id: int, twitch_username: str, original_nickname: str = None):
    """Lie un compte Discord à un compte Twitch."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO twitch_links (discord_id, twitch_username, original_nickname)
            VALUES (?, ?, ?)
        ''', (discord_id, twitch_username, original_nickname))
        conn.commit()
        logging.info(f"Liaison Twitch enregistrée: Discord ID {discord_id} -> Twitch '{twitch_username}'")
        return True
    except sqlite3.Error as e:
        logging.error(f"Erreur lors de la liaison du compte Twitch pour Discord ID {discord_id} : {e}")
        return False
    finally:
        if conn:
            conn.close()

def unlink_twitch_account(discord_id: int):
    """Délie un compte Discord d'un compte Twitch."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM twitch_links WHERE discord_id = ?', (discord_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logging.info(f"Liaison Twitch supprimée pour Discord ID {discord_id}.")
            return True
        else:
            logging.info(f"Aucune liaison Twitch trouvée pour Discord ID {discord_id}.")
            return False
    except sqlite3.Error as e:
        logging.error(f"Erreur lors de la suppression de la liaison Twitch pour Discord ID {discord_id} : {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_twitch_link(discord_id: int):
    """Récupère le nom d'utilisateur Twitch lié à un ID Discord."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT twitch_username, original_nickname FROM twitch_links WHERE discord_id = ?', (discord_id,))
        result = cursor.fetchone()
        return result if result else (None, None)
    except sqlite3.Error as e:
        logging.error(f"Erreur lors de la récupération de la liaison Twitch pour Discord ID {discord_id} : {e}")
        return None, None
    finally:
        if conn:
            conn.close()

def get_all_twitch_links():
    """Récupère toutes les liaisons Discord -> Twitch."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT discord_id, twitch_username, original_nickname FROM twitch_links')
        return cursor.fetchall()
    except sqlite3.Error as e:
        logging.error(f"Erreur lors de la récupération de toutes les liaisons Twitch : {e}")
        return []
    finally:
        if conn:
            conn.close()

def update_original_nickname(discord_id: int, nickname: str):
    """Met à jour le pseudo original enregistré pour un utilisateur."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE twitch_links
            SET original_nickname = ?
            WHERE discord_id = ?
        ''', (nickname, discord_id))
        conn.commit()
        if cursor.rowcount > 0:
            logging.info(f"Pseudo original de Discord ID {discord_id} mis à jour : '{nickname}'")
            return True
        else:
            logging.warning(f"Aucune liaison trouvée pour Discord ID {discord_id} pour mettre à jour le pseudo original.")
            return False
    except sqlite3.Error as e:
        logging.error(f"Erreur lors de la mise à jour du pseudo original pour Discord ID {discord_id} : {e}")
        return False
    finally:
        if conn:
            conn.close()