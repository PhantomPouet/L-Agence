# Utilise une image Python allégée
FROM python:3.11-slim

# Crée un dossier pour le bot
WORKDIR /app

# Copie uniquement les fichiers nécessaires
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copie le reste du code
COPY . .

# Lancement du bot
CMD ["python", "bot.py"]
