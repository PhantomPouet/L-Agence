# twitch_api.py
import aiohttp
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

class TwitchAPI:
    def __init__(self):
        self.client_id = TWITCH_CLIENT_ID
        self.client_secret = TWITCH_CLIENT_SECRET
        self.access_token = None
        self.session = aiohttp.ClientSession() # Utiliser une session aiohttp pour de meilleures performances

    async def _get_access_token(self):
        """Récupère ou rafraîchit le jeton d'accès OAuth2 de Twitch."""
        if not self.client_id or not self.client_secret:
            logging.error("TWITCH_CLIENT_ID ou TWITCH_CLIENT_SECRET non définis dans .env")
            return None

        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"
        }
        try:
            async with self.session.post(url, data=params) as response:
                response.raise_for_status()
                data = await response.json()
                self.access_token = data.get("access_token")
                # Twitch access tokens expire in 60 days usually, no need for refresh token flow for client_credentials
                logging.info("Jeton d'accès Twitch obtenu.")
                return self.access_token
        except aiohttp.ClientError as e:
            logging.error(f"Erreur lors de l'obtention du jeton d'accès Twitch : {e}")
            self.access_token = None # S'assurer que le jeton est effacé en cas d'erreur
            return None

    async def get_stream_status(self, twitch_username: str):
        """
        Vérifie si un utilisateur Twitch est en direct.
        Retourne True si en direct, False sinon, ou None en cas d'erreur irrécupérable.
        """
        if not self.access_token:
            await self._get_access_token()
            if not self.access_token:
                return None # Impossible d'obtenir le jeton d'accès

        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}"
        }

        # Première requête pour obtenir l'ID de l'utilisateur Twitch
        user_url = f"https://api.twitch.tv/helix/users?login={twitch_username}"
        try:
            async with self.session.get(user_url, headers=headers) as response:
                response.raise_for_status()
                user_data = await response.json()
                users = user_data.get("data")
                if not users:
                    logging.warning(f"Utilisateur Twitch '{twitch_username}' introuvable.")
                    return False # Utilisateur introuvable ou erreur
                user_id = users[0]["id"]
                
                # Deuxième requête pour vérifier l'état du stream
                stream_url = f"https://api.twitch.tv/helix/streams?user_id={user_id}"
                async with self.session.get(stream_url, headers=headers) as stream_response:
                    stream_response.raise_for_status()
                    stream_data = await stream_response.json()
                    streams = stream_data.get("data")
                    
                    if streams:
                        stream = streams[0]
                        # Si tu voulais une vérification plus stricte sur le jeu spécifique (ex: "Star Citizen")
                        # tu pourrais ajouter: and stream["game_name"] == "Star Citizen"
                        logging.info(f"Stream de '{twitch_username}' détecté. Titre: '{stream['title']}', Jeu: '{stream['game_name']}'")
                        return True # Le streamer est en direct
                    else:
                        logging.info(f"'{twitch_username}' n'est pas en direct.")
                        return False # Pas en direct

        except aiohttp.ClientResponseError as e:
            if e.status == 401: # Unauthorized - token likely expired or invalid
                logging.warning("Jeton d'accès Twitch expiré ou invalide, tentera de le rafraîchir.")
                self.access_token = None # Invalide le jeton pour qu'il soit rafraîchi
                return await self.get_stream_status(twitch_username) # Retenter la requête
            logging.error(f"Erreur HTTP lors de la vérification du stream pour '{twitch_username}': {e}")
            return None # Indique une erreur non gérée
        except aiohttp.ClientError as e:
            logging.error(f"Erreur de connexion lors de la vérification du stream pour '{twitch_username}': {e}")
            return None
        except Exception as e:
            logging.error(f"Erreur inattendue lors de la vérification du stream pour '{twitch_username}': {e}")
            return None

    async def close(self):
        """Ferme la session aiohttp."""
        if not self.session.closed:
            await self.session.close()
            logging.info("Session aiohttp fermée.")

# Exemple d'utilisation (pour tests)
async def main():
    twitch_api = TwitchAPI()
    is_live = await twitch_api.get_stream_status("montwitchusername") # Remplace par un vrai username
    if is_live is True:
        print("L'utilisateur est en direct.")
    elif is_live is False:
        print("L'utilisateur n'est pas en direct.")
    else:
        print("Impossible de vérifier le statut du stream (erreur).")
    await twitch_api.close()

if __name__ == "__main__":
    asyncio.run(main())