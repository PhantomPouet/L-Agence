# Utilise une image Python allégée
FROM python:3.11-slim

# Crée un dossier pour le bot
WORKDIR /app

# Copie le fichier requirements.txt et installe les dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie tout le reste de votre projet dans le dossier /app du conteneur
COPY . .

# Lance le bot au démarrage du conteneur
CMD ["python", "bot.py"]
