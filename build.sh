#!/usr/bin/env bash
# build.sh - Script d’installation et de configuration pour Render

# Arrête le script si une commande échoue
set -o errexit  

# Installer les dépendances Python
pip install -r requirements.txt

# Appliquer les migrations
python manage.py migrate

# Collecter les fichiers statiques
python manage.py collectstatic --noinput
