# Guide d'utilisation — Pipeline de recherche de stage

## Prérequis

- Python 3.11+
- Une clé API Anthropic (pour les phases de filtrage et génération)
- macOS / Linux (les commandes cron sont pour macOS/Linux)

---

## Installation

```bash
# 1. Créer l'environnement virtuel (une seule fois)
venvinit

# 2. Activer l'environnement
venvon

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer la clé API
cp .env.example .env
# Éditer .env et remplacer "your_key_here" par ta clé Anthropic
```

---

## Workflow complet

Le pipeline se déroule en 3 étapes :

```
Étape 1 — Scrape      : Collecte les offres depuis les flux RSS
Étape 2 — Filtre      : Claude score chaque offre (0-10) selon ton profil
Étape 3 — Rapport     : Génère un rapport Markdown avec les offres pertinentes

            ↓ Tu lis le rapport, tu coches les offres qui t'intéressent

Étape 4 — Candidature : Claude génère lettre de motivation + CV adapté
```

---

## Commandes principales

| Commande | Description |
|---|---|
| `python main.py --scrape` | Lance les étapes 1-2-3 (collecte + filtre + rapport) |
| `python main.py --scrape --threshold 7` | Idem avec un seuil de pertinence de 7/10 |
| `python main.py --generate <offer_id>` | Génère la candidature pour une offre spécifique |

### Modules en isolation (pour debug)

| Commande | Description |
|---|---|
| `python -m src.scraper` | Collecte uniquement (sans filtre ni rapport) |
| `python -m src.filter` | Filtre les offres déjà collectées |
| `python -m src.report` | Génère le rapport depuis les offres déjà filtrées |
| `python -m src.generator <offer_id>` | Génère la candidature pour une offre |

---

## Exemple de session type

```bash
# Activer l'environnement
venvon

# Lancer le pipeline complet
python main.py --scrape

# → Le rapport est généré dans data/rapport_YYYY-MM-DD.md
# → Lire le rapport, noter l'ID des offres intéressantes

# Générer la candidature pour une offre
python main.py --generate 0151e52f

# → Les fichiers sont dans output/candidatures/<nom-entreprise>_<id>/
#   - lettre.md     : lettre de motivation prête à relire/adapter
#   - cv_adapte.md  : CV réorganisé selon les mots-clés de l'offre
```

---

## Automatisation toutes les 48h (macOS/Linux)

Pour que le scraping et le filtrage tournent automatiquement toutes les 48h :

```bash
# Ouvrir l'éditeur cron
crontab -e
```

Ajouter cette ligne (adapter le chemin si nécessaire) :

```
0 8 */2 * * cd "/Users/nathan/Document/Année INFO5/Stage/Script" && source venv/bin/activate && python main.py --scrape >> data/cron.log 2>&1
```

Ce que ça fait : tous les 2 jours à 8h00, le pipeline tourne et les résultats sont ajoutés à `data/cron.log`.

---

## Personnalisation

### Modifier ton profil de recherche
Édite `config/profile.yaml` :
- Changer les domaines recherchés, les préférences géographiques, le seuil de score
- Modifier les critères de bonus/malus utilisés par Claude pour scorer les offres

### Ajouter des sources RSS
Édite `config/sources.yaml` — ajoute un bloc :
```yaml
- name: "Nom descriptif"
  url: "https://lien-vers-le-flux.rss"
  type: rss
  country: "NL"  # Code pays ISO
```

### Mettre à jour ton CV
Édite `cv_master/cv_master.md` — c'est la "source de vérité". Le CV adapté généré par Claude pour chaque offre est basé sur ce fichier.

### Ajuster les prompts
- `config/prompts/filter.txt` — modifie les critères de scoring
- `config/prompts/cover_letter.txt` — ajuste le style/structure de la lettre
- `config/prompts/cv_adapt.txt` — modifie comment le CV est réorganisé

---

## Candidature manuelle (WTTJ, LinkedIn, etc.)

Pour les offres sur des sites non-scrappables automatiquement (WTTJ, LinkedIn, Jobteaser…), deux modes permettent de générer lettre + CV directement depuis ta session de navigation.

### Depuis un fichier HTML (recommandé pour WTTJ)

WTTJ est bloqué pour le scraping direct. Workflow :

1. Ouvre l'offre dans ton navigateur
2. **Fichier → Enregistrer sous → "Page web complète"** → sauvegarde en `.html`
3. Lance la commande :

```bash
python main.py --from-file ~/Downloads/offre_wttj.html \
               --url-source "https://www.welcometothejungle.com/fr/companies/..." \
               --company "Nom Entreprise"
```

L'argument `--url-source` est optionnel mais utile — il est inclus dans la lettre générée pour référencer l'offre.

### Depuis une URL directe (sites simples)

Pour les sites accessibles sans JavaScript (pas de CloudFront) :

```bash
python main.py --from-url https://www.somesite.com/jobs/stage-dev-python
```

### Surcharges si l'extraction échoue

Si le titre ou l'entreprise n'est pas bien extrait automatiquement :

```bash
python main.py --from-file offre.html \
               --title "Stage Développeur Python" \
               --company "Criteo"
```

### Ce que ça fait

Le programme extrait le titre, l'entreprise, le lieu et la description de la page (via JSON-LD, `__NEXT_DATA__`, ou heuristiques HTML), puis génère directement `lettre.md` + `cv_adapte.md` dans `output/candidatures/` — sans étape de filtrage.

---

## Notifications email (optionnel)

Pour recevoir un résumé par email à chaque fois que de nouvelles offres pertinentes sont trouvées :

1. Active la validation en 2 étapes sur ton compte Google
2. Génère un **App Password** sur [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Ajoute ces lignes dans ton `.env` :

```
NOTIFY_EMAIL=albalatnathan.etu@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=ton_compte@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # App Password 16 caractères
```

Le pipeline envoie automatiquement l'email après chaque `--scrape` si `NOTIFY_EMAIL` est renseigné.  
Si la variable est vide ou absente, aucune notification n'est envoyée.

---

## Résolution de problèmes

**"ANTHROPIC_API_KEY manquant"**
→ Vérifie que le fichier `.env` existe à la racine et contient `ANTHROPIC_API_KEY=sk-ant-...`

**"Your credit balance is too low"**
→ Recharge des crédits sur [console.anthropic.com](https://console.anthropic.com). Le scraping (étape 1) fonctionne sans crédits.

**"0 nouvelle(s) offre(s) collectée(s)"**
→ Normal si le pipeline a déjà tourné récemment — les offres sont dédupliquées. Attends 48h ou ajoute de nouvelles sources dans `sources.yaml`.

**"Aucune offre filtrée trouvée" au moment du rapport**
→ Le filtre doit avoir tourné en premier. Lance `python main.py --scrape` dans l'ordre complet.

**Le rapport contient peu d'offres pertinentes**
→ Baisse le seuil : `python main.py --scrape --threshold 5`

**L'offre_id est introuvable pour --generate**
→ L'ID est le nom du fichier JSON sans `.json` dans `data/offers/` (ex: `0151e52f`)
