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

# 4. Créer les fichiers de configuration depuis les exemples
bash setup.sh
# → Copie automatiquement les .example vers leurs noms définitifs
# → Indique quels fichiers éditer ensuite
```

> **Note :** `setup.sh` ne remplace jamais un fichier existant.
> Le pipeline le propose aussi automatiquement si un fichier de config est manquant au premier lancement.

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

## Interface interactive (TUI)

Plutôt que d'enchaîner les commandes, tu peux tout piloter depuis une interface dans le terminal :

```bash
python main.py --tui
```

**Principe important :** la TUI n'est qu'une **vue** sur les fichiers du pipeline. Elle ne crée aucun état caché : les rapports, `data/offers/*.json` et `data/applications.json` restent lisibles et modifiables sans elle. Tu peux donc reprendre le travail n'importe quand (scraper lundi, faire la revue mercredi) — la TUI relit l'état courant à l'ouverture et avec la touche `r`.

### Les 4 onglets

| Onglet | Rôle |
|---|---|
| **1 · Offres** | Tableau des offres filtrées, triable par score, avec panneau de détail. Tu coches les offres à traiter. |
| **2 · Génération** | Lance la génération (lettre + CV, ou lettre seule) des offres cochées, en tâche de fond. |
| **3 · Candidatures** | Liste des dossiers `output/candidatures/`, aperçu de la lettre/CV, **régénération des PDF après retouche manuelle**. |
| **4 · Suivi** | Statuts et notes des candidatures (`applications.json`). |

### Workflow de retouche d'une lettre

C'est l'usage principal de l'onglet Candidatures :

1. Onglet **3 · Candidatures**, sélectionne la candidature.
2. Touche `e` → `lettre.md` s'ouvre dans ton éditeur par défaut.
3. Retouche le texte, enregistre.
4. Reviens dans la TUI, touche `p` → le PDF est régénéré à partir du `.md`.

La colonne « PDF » affiche **« ⚠ à régénérer »** quand le `.md` est plus récent que le PDF, pour repérer d'un coup d'œil ce qui reste à refaire.

### Raccourcis clavier (aussi accessibles via `?` dans la TUI)

| Contexte | Touche | Action |
|---|---|---|
| Global | `1`/`2`/`3`/`4` | Changer d'onglet |
| Global | `r` | Recharger les données depuis le disque |
| Global | `S` | Lancer scrape + filtre + rapport |
| Global | `c` | Ouvrir la Claude Console (recharger le crédit API) |
| Global | `?` | Afficher l'aide (liste des raccourcis) |
| Global | `q` | Quitter |
| Offres | `espace` | Cocher/décocher l'offre pour génération |
| Offres | `o` | Ouvrir l'offre dans le navigateur |
| Offres | `+`/`-` | Ajuster le seuil de score affiché |
| Offres | `h` | Masquer/afficher les offres déjà candidatées |
| Offres | `g` | Aller à l'onglet Génération |
| Génération | `g` | Générer lettre + CV des offres cochées |
| Génération | `L` | Générer la **lettre seule** (sans CV — 1 appel Sonnet au lieu de 2) |
| Génération | `o` | Ouvrir le dossier de la candidature |
| Candidatures | `entrée` | Basculer l'aperçu lettre ↔ CV |
| Candidatures | `e` | Ouvrir `lettre.md` dans l'éditeur |
| Candidatures | `p` | Régénérer `lettre.pdf` depuis `lettre.md` |
| Candidatures | `u` | Régénérer `cv_adapte.pdf` depuis `cv_adapte.html` |
| Candidatures | `o` | Ouvrir le dossier de la candidature |
| Suivi | `s`/`a`/`i`/`j`/`v` | Statut → sent / answered / interview / rejected / accepted |
| Suivi | `n` | Éditer la note |
| Suivi | `o` | Ouvrir l'offre dans le navigateur |

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
0 8 */2 * * cd "path/to/Script" && source venv/bin/activate && python main.py --scrape >> data/cron.log 2>&1
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
Édite `cv_master/cv_data.yaml` — c'est **la** source de vérité (données structurées : expériences, projets, compétences, infos perso). Ce fichier alimente à la fois le CV « master » et le CV adapté généré par Claude. Le fichier `cv_master/cv_master.md` est **régénéré automatiquement** depuis le YAML (via `python -m src.cv_sync`, et à chaque génération de candidature) — ne l'édite pas à la main.

### Ajuster les prompts
- `config/prompts/filter.txt` — modifie les critères de scoring
- `config/prompts/cover_letter.txt` — ajuste le style/structure de la lettre (à modifier pour ajuster les dates mentionnées dans la lettre)
- `config/prompts/cv_adapt.txt` — modifie comment le CV est réorganisé

### Personnaliser les templates PDF (CV & lettre)

L'apparence des PDF est contrôlée par deux templates HTML dans `config/templates/` :

| Template | Utilisé pour | Rendu par |
|---|---|---|
| `cv_en.html` | Le CV (master et adapté) | `src/cv_pdf.py` |
| `cover_letter.html` | La lettre de motivation | `src/letter_pdf.py` |

**Comment ça marche :** chaque template contient des marqueurs `{{NOM}}` que le code remplace par les vraies valeurs, puis WeasyPrint convertit le HTML en PDF. Tu peux modifier librement le CSS (couleurs, polices, marges, disposition) tant que tu **conserves les marqueurs** `{{...}}` là où le contenu doit apparaître.

**Marqueurs disponibles — CV (`cv_en.html`) :**
`{{FULL_NAME}}`, `{{HEADLINE}}`, `{{LOCATION}}`, `{{EMAIL}}`, `{{GITHUB}}`, `{{EXPERIENCE_ENTRIES}}`, `{{PROJECT_ENTRIES}}`, `{{EDU_SCHOOL}}`, `{{EDU_DATES}}`, `{{EDU_DETAIL}}`, `{{START_DATE}}`, `{{DURATION}}`, `{{MOBILITY}}`, `{{SKILLS_PROFICIENT}}`, `{{SKILLS_FAMILIAR}}`, `{{SKILLS_TOOLS}}`, `{{SKILLS_DOMAINS}}`, `{{LANG1_NAME}}`/`{{LANG1_LEVEL}}`, `{{LANG2_NAME}}`/`{{LANG2_LEVEL}}`, `{{OTHER_INFO}}`, `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}`.

**Marqueurs disponibles — Lettre (`cover_letter.html`) :**
`{{FULL_NAME}}`, `{{SENDER_LOCATION}}`, `{{EMAIL}}`, `{{COMPANY_NAME}}`, `{{COMPANY_LOCATION}}`, `{{COMPANY_DEPT_LINE}}`, `{{POSITION_TITLE}}`, `{{DATE}}`, `{{SALUTATION}}`, `{{BODY}}`, `{{CLOSING_WORD}}`, `{{SIGNATURE_IMG}}`, `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}`.

**Couleur d'accent :** contrôlée par `personal.accent_enabled` dans `cv_data.yaml` (vert si `true`, gris si `false`). Les valeurs hex sont injectées dans `{{ACCENT_COLOR}}` / `{{ACCENT_LIGHT}}` — pour changer la teinte, modifie-les dans `src/cv_pdf.py` / `src/letter_pdf.py`.

**Polices :** les fichiers `.ttf` (DM Sans, DM Serif Display) sont dans `config/templates/fonts/` et chargés via `@font-face`. La génération PDF fonctionne donc hors-ligne. Pour changer de police, dépose le `.ttf` dans ce dossier et ajuste les règles `@font-face` en tête de template.

**Signature de la lettre :** l'image `files/letter/signature.png` est insérée automatiquement via `{{SIGNATURE_IMG}}` (si le fichier existe). Sa taille se règle avec la règle CSS `.cl-signature` (propriété `height`) dans `cover_letter.html`. ⚠️ Garde-la assez petite pour que la lettre **tienne sur une seule page**.

**Tester une modif de template** (sans consommer d'API) :
- Lettre : `python main.py --letter-pdf <une lettre.md> --company "Test"` — ou touche `p` dans la TUI sur une candidature existante.
- CV master : `python files/generate_cv.py` → `files/cv_nathan_albala_en.pdf`.
- CV adapté d'une candidature existante : touche `u` dans la TUI.

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
NOTIFY_EMAIL=your_email@gmail.com
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
