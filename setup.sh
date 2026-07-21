#!/usr/bin/env bash
# setup.sh — Initialisation du pipeline de recherche de stage
# Copie les fichiers .example vers leurs noms définitifs (si absents).
# Ne remplace jamais un fichier existant.

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
GRAY='\033[0;37m'
NC='\033[0m'

copy_if_missing() {
    local src="$1"
    local dst="$2"
    if [ -f "$dst" ]; then
        echo -e "  ${GRAY}Déjà présent${NC}   $dst"
    else
        cp "$src" "$dst"
        echo -e "  ${GREEN}Copié${NC}          $dst"
    fi
}

echo ""
echo "=== Setup — Pipeline de recherche de stage ==="
echo ""
echo "Création des fichiers de configuration depuis les exemples :"
echo ""

copy_if_missing ".env.example"                        ".env"
copy_if_missing "config/profile.example.yaml"         "config/profile.yaml"
copy_if_missing "cv_master/cv_data.example.yaml"      "cv_master/cv_data.yaml"
copy_if_missing "cv_master/cv_master.example.md"      "cv_master/cv_master.md"

echo ""
echo -e "${YELLOW}À faire maintenant :${NC}"
echo "  1. Édite .env             → ajoute ta clé ANTHROPIC_API_KEY"
echo "  2. Édite config/profile.yaml         → ton profil de recherche"
echo "  3. Édite cv_master/cv_data.yaml      → tes données CV (nom, expériences, etc.)"
echo "  4. Édite cv_master/cv_master.md      → résumé textuel du CV (pour les lettres)"
echo ""
echo "Ensuite : python main.py --scrape"
echo ""
