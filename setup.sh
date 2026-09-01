#!/usr/bin/env bash
# setup.sh — Initialisation du pipeline de recherche de stage
# Copie les fichiers .example (ressources du dépôt) vers JOBSEARCH_HOME
# (~/.jobsearch/ par défaut, surchargeable via la variable d'environnement).
# Ne remplace jamais un fichier existant.

set -e

# Racine du dépôt (ressources : .example, prompts, templates) — indépendante
# du répertoire courant depuis lequel setup.sh est appelé.
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

JOBSEARCH_HOME="${JOBSEARCH_HOME:-$HOME/.jobsearch}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
GRAY='\033[0;37m'
NC='\033[0m'

copy_if_missing() {
    local src="$1"
    local dst="$2"
    mkdir -p "$(dirname "$dst")"
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
echo "Racine des données/config personnelles (JOBSEARCH_HOME) : $JOBSEARCH_HOME"
echo ""
echo "Création de l'arborescence et des fichiers de configuration depuis les exemples du dépôt :"
echo ""

mkdir -p "$JOBSEARCH_HOME/config" "$JOBSEARCH_HOME/data/offers" "$JOBSEARCH_HOME/output" "$JOBSEARCH_HOME/cv_master"

copy_if_missing "$PACKAGE_ROOT/.env.example"                    "$JOBSEARCH_HOME/config/.env"
copy_if_missing "$PACKAGE_ROOT/config/profile.example.yaml"     "$JOBSEARCH_HOME/config/profile.yaml"
copy_if_missing "$PACKAGE_ROOT/config/search.example.yaml"      "$JOBSEARCH_HOME/config/search.yaml"
copy_if_missing "$PACKAGE_ROOT/config/sources.yaml"              "$JOBSEARCH_HOME/config/sources.yaml"
copy_if_missing "$PACKAGE_ROOT/cv_master/cv_data.example.yaml"  "$JOBSEARCH_HOME/cv_master/cv_data.yaml"
copy_if_missing "$PACKAGE_ROOT/cv_master/cv_master.example.md"  "$JOBSEARCH_HOME/cv_master/cv_master.md"

echo ""
echo -e "${YELLOW}À faire maintenant :${NC}"
echo "  1. Édite $JOBSEARCH_HOME/config/.env              → ajoute ta clé ANTHROPIC_API_KEY"
echo "  2. Édite $JOBSEARCH_HOME/config/search.yaml        → ton sujet de recherche (stage, pays, durée...)"
echo "  3. Édite $JOBSEARCH_HOME/config/profile.yaml       → ton profil (formation, compétences)"
echo "  4. Édite $JOBSEARCH_HOME/cv_master/cv_data.yaml    → tes données CV (nom, expériences, etc.)"
echo "  5. Édite $JOBSEARCH_HOME/cv_master/cv_master.md    → résumé textuel du CV (pour les lettres)"
echo ""
echo "Une installation antérieure avec des données dans le dépôt (data/, output/, config perso) ?"
echo "  python main.py --migrate          (aperçu, n'écrit rien)"
echo "  python main.py --migrate --apply  (copie réelle vers $JOBSEARCH_HOME)"
echo ""
echo "Ensuite : python main.py --scrape"
echo ""
