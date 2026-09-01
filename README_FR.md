# Guide d'utilisation — Pipeline de recherche de stage

## Prérequis

- Python 3.11+
- Une clé API Anthropic (pour les phases de filtrage et génération)
- macOS / Linux (les commandes cron sont pour macOS/Linux)

---

## Installation

```bash
# Recommandé : installation comme outil CLI isolé
pipx install git+https://github.com/<toi>/jobsearch

# Ou, pour du développement local (dans le venv de ce dépôt) :
pip install -e .
```

WeasyPrint (rendu PDF) requiert une dépendance système : Pango/Cairo — `brew install pango` sur macOS. C'est le principal écueil d'installation : sans elle, `--letter-pdf` / `--generate` échouent à l'étape PDF alors que le reste fonctionne.

Dans les deux cas, tu obtiens une commande globale `jobsearch`. Lance ensuite l'assistant d'installation interactif :

```bash
jobsearch --setup
```

C'est une app Textual autonome qui te guide en 6 étapes : présentation de l'outil, ta clé API Anthropic (facultative à ce stade, avec une validation minimale optionnelle — confirmée avant d'être lancée), ton sujet de recherche (type de contrat, durée minimale, pays prioritaires, domaines, seuil de score), le prompt de scoring (**étape non sautable** : le paquet livre un défaut générique et paramétré, sans exemple d'offre réelle — l'assistant le complète avec ton sujet pour que ton tout premier scrape ne soit pas calibré sur la recherche de quelqu'un d'autre, affinable ensuite depuis l'onglet Réglages de la TUI, touche `6`), ton profil de candidat, puis un récapitulatif de ce qui est configuré et de ce qui reste optionnel (données du CV, templates, Adzuna, SMTP). Il n'écrase jamais un fichier déjà présent sans le demander, et montre le contenu avant de l'écrire — se relance sans risque.

Le premier lancement d'une autre commande (`jobsearch --scrape`, `--tui`, …) avant `--setup` détecte que `JOBSEARCH_HOME` (`~/.jobsearch/` par défaut) n'a pas encore de configuration et propose de lancer `setup.sh` à la place, un repli plus sommaire qui copie les fichiers `.example` du paquet vers leurs noms définitifs et indique quels fichiers éditer à la main.

> **Note :** ni `--setup` ni `setup.sh` ne remplacent jamais un fichier existant.

**Se lance depuis n'importe quel répertoire.** Deux racines distinctes : le code et ses ressources par défaut (prompts, templates HTML, polices) vivent dans le paquet (`jobsearch/resources/`, accédées via `importlib.resources` pour être trouvées quel que soit le mode d'installation) ; ta config personnelle (`search.yaml`, `profile.yaml`, `sources.yaml`, `cv_data.yaml`, `.env`) et tes données (`data/`, `output/`) vivent sous `JOBSEARCH_HOME`, qui vaut par défaut `~/.jobsearch/` — `jobsearch --applications` fonctionne de la même façon depuis le dépôt ou depuis n'importe où ailleurs. Surchargeable via la variable d'environnement `JOBSEARCH_HOME`. `python -m jobsearch.config` affiche les chemins effectifs.

**Les prompts et templates HTML sont du contenu éditable, pas des ressources figées.** Les 3 prompts de scoring/génération (`config/prompts/*.txt`) et les 2 templates HTML (`config/templates/cover_letter.html`, `cv_en.html`) sont résolus en priorité depuis `JOBSEARCH_HOME/config/`, avec repli automatique sur les défauts du paquet si tu n'as encore rien personnalisé — aucune action requise sur une installation existante. Pour les personnaliser, copie les défauts dans `JOBSEARCH_HOME/config/prompts/` / `.../templates/` (ou lance `jobsearch --migrate --apply`, qui les y copie sans jamais écraser un fichier existant) puis édite les copies. Seules les polices restent toujours dans le paquet.

**Tu mets à jour une installation antérieure où les données vivaient dans le dépôt ?** Lance `jobsearch --migrate` pour un aperçu (affiche ce qui serait copié, n'écrit rien), puis `jobsearch --migrate --apply` pour copier réellement ta config et tes données vers `JOBSEARCH_HOME`. Cette commande ne fait QUE des copies — elle ne déplace ni ne supprime jamais les originaux du dépôt ; elle vérifie le nombre de fichiers et les sommes de contrôle après la copie, et te dit où sont les originaux pour que tu puisses les supprimer toi-même une fois satisfait.

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
| `jobsearch --setup` | Assistant d'installation interactif (clé API, sujet de recherche, prompt de scoring, profil) |
| `jobsearch --scrape` | Lance les étapes 1-2-3 (collecte + filtre + rapport) |
| `jobsearch --scrape --threshold 7` | Idem avec un seuil de pertinence de 7/10 |
| `jobsearch --scrape --with fr` | Inclut aussi les sources FR-only (groupe `fr`, ex. HelloWork, WTTJ FR), désactivées par défaut |
| `jobsearch --scrape --without fr` | Désactive un groupe même s'il est activé par défaut dans `search.yaml` (`sources.enabled_groups`) |
| `jobsearch --generate <offer_id>` | Génère la candidature pour une offre spécifique |
| `jobsearch --mark-duplicate <offer_id> <target_id>` | Marque `<offer_id>` comme doublon manuel de `<target_id>` (une offre déjà candidatée) |
| `jobsearch --unmark-duplicate <offer_id>` | Annule un marquage de doublon |
| `jobsearch --migrate` | Aperçu à blanc : affiche ce qui serait copié du dépôt vers `JOBSEARCH_HOME`, n'écrit rien |
| `jobsearch --migrate --apply` | Copie réellement la config perso et les données vers `JOBSEARCH_HOME` (n'écrase jamais, ne supprime jamais les originaux) |

### Modules en isolation (pour debug)

| Commande | Description |
|---|---|
| `python -m jobsearch.scraper` | Collecte uniquement (sans filtre ni rapport) |
| `python -m jobsearch.filter` | Filtre les offres déjà collectées |
| `python -m jobsearch.report` | Génère le rapport depuis les offres déjà filtrées |
| `python -m jobsearch.generator <offer_id>` | Génère la candidature pour une offre |

---

## Interface interactive (TUI)

Plutôt que d'enchaîner les commandes, tu peux tout piloter depuis une interface dans le terminal :

```bash
jobsearch --tui
```

**Principe important :** la TUI n'est qu'une **vue** sur les fichiers du pipeline. Elle ne crée quasiment aucun état caché : les rapports, `data/offers/*.json` et `data/applications.json` restent lisibles et modifiables sans elle. Tu peux donc reprendre le travail n'importe quand (scraper lundi, faire la revue mercredi) — la TUI relit l'état courant à l'ouverture et avec la touche `r`. Seule exception : la file de l'onglet Génération, persistée dans `data/gen_queue.json`.

### Les 6 onglets

| Onglet | Rôle |
|---|---|
| **1 · Offres** | Tableau des offres filtrées, triable par score, avec panneau de détail. Tu coches les offres à ajouter à la file de génération — les lignes cochées sont **surlignées en vert**. Les offres de la dernière session de scrape sont **surlignées en bleu foncé** ; la période affichée (tout / dernière session / 7 derniers jours / 30 derniers jours) se filtre avec `d`/`D`. La colonne `✓` est une cascade à 4 états : `▶` cochée > `⧉` doublon manuel confirmé > `✓` déjà candidatée > `?` doublon suspecté (même entreprise déjà candidatée, non tranché) — `m` pour confirmer/annuler via une modale. |
| **2 · Génération** | La file de génération, persistée entre deux lancements de la TUI dans `data/gen_queue.json`. Coche des lignes avec `espace` pour restreindre `g`/`L` à ce sous-ensemble — sans rien coché, `g`/`L` traitent toutes les offres en attente. `x` retire des lignes de la file. Lance la génération (lettre + CV, ou lettre seule) en tâche de fond. |
| **3 · Candidatures** | Liste des dossiers `output/candidatures/`, aperçu de la lettre/CV, **régénération des PDF après retouche manuelle**. |
| **4 · Suivi** | Statuts et notes des candidatures (`applications.json`). Chaque statut a sa propre couleur : **envoyée** en jaune, **réponse reçue** en ambre, **entretien** en violet, **acceptée** en vert, **refusée** en rouge, **annulée** en gris foncé avec texte atténué. Le panneau de droite affiche la timeline individuelle de la candidature sous le curseur, reconstruite depuis son `history` (journal statut → date/heure), avec l'écart en jours entre chaque événement — les dates déduites des anciens champs ponctuels (avant l'existence de `history`) sont suffixées d'un `~`. |
| **5 · Stats** | Métriques de retour (taux d'acceptation, taux de réponse, délai moyen), répartition des statuts, localisation des stages, et histogramme des candidatures au cours du temps. Les candidatures annulées sont exclues des taux et du délai moyen, comptées à part dans une catégorie « Annulé ». Aussi : un rendu arborescent du parcours des candidatures (flux de statuts) et une répartition mensuelle des statuts atteints. `f` exporte le diagramme de Sankey correspondant en HTML autonome (`output/stats/flux.html`), ouvert dans le navigateur. |
| **6 · Réglages** | Table listant tous les fichiers de config éditables — `.env`, `search.yaml`, `profile.yaml`, `sources.yaml`, les 3 prompts, les 2 templates HTML, `cv_data.yaml` — avec leur rôle, leur **état** (personnalisé / défaut du paquet / absent) et leur date de modification. `entrée` sur une ligne ouvre le fichier dans ton éditeur : pour un prompt/template encore au défaut du paquet, la copie vers la config utilisateur se fait **avant** l'ouverture, pour ne jamais éditer les fichiers livrés avec le paquet. Utilise `$VISUAL`/`$EDITOR` s'ils sont définis (en suspendant la TUI pour un éditeur terminal), sinon retombe sur `open`/`xdg-open`/`start` selon l'OS. `r` ensuite pour recharger — vide aussi le cache de `search.yaml`, un seuil de scoring modifié prend effet immédiatement. |

### Offres en doublon

La déduplication automatique ne peut pas attraper tous les doublons inter-sources (ex. la même offre republiée sous un titre légèrement différent sur un autre board). Plutôt que de deviner, le pipeline suit le principe « **la machine suggère, l'utilisateur confirme** » : les offres d'une entreprise déjà candidatée sont signalées par un `?` dans l'onglet Offres, et la touche `m` ouvre une liste des candidatures à cette entreprise (ou d'autres) pour confirmer en une pression — rien n'est jamais marqué automatiquement.

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
| Global | `1`/`2`/`3`/`4`/`5`/`6` | Changer d'onglet (Offres / Génération / Candidatures / Suivi / Stats / Réglages) |
| Global | `r` | Recharger les données depuis le disque |
| Global | `S` / `s` | Lancer scrape + filtre + rapport (spinner + étape + chrono dans la barre de statut). `s` ne déclenche le scrape qu'en dehors de l'onglet Suivi, où il vaut « envoyée » ; `s` est un alias de `S` |
| Global | `Ctrl+S` | Idem, en incluant aussi les sources FR-only (groupe `fr`, ex. HelloWork, WTTJ FR) — équivalent de `jobsearch --scrape --with fr` |
| Global | `c` | Ouvrir la Claude Console (recharger le crédit API) |
| Global | `?` | Afficher l'aide (liste des raccourcis) |
| Global | `q` | Quitter |
| Offres | `espace` | Cocher/décocher l'offre pour génération |
| Offres | `o` | Ouvrir l'offre dans le navigateur |
| Offres | `+`/`-` | Ajuster le seuil de score affiché |
| Offres | `h` | Masquer/afficher les offres déjà candidatées (et les doublons marqués) |
| Offres | `d` | Cycler la période affichée (tout · session · 7j · 30j) |
| Offres | `D` | Choisir une session de scrape précise |
| Offres | `g` | Aller à l'onglet Génération |
| Offres | `m` | Marquer/démarquer l'offre comme doublon d'une candidature existante (modale ; la machine suggère via l'entreprise, l'utilisateur confirme) |
| Génération | `espace` | Cocher/décocher une offre de la file (filtre optionnel pour `g`/`L`) |
| Génération | `g` | Générer lettre + CV (lignes cochées, sinon toutes les offres en attente) |
| Génération | `L` | Générer la **lettre seule** (sans CV — 1 appel Sonnet au lieu de 2) |
| Génération | `x` | Retirer de la file (lignes cochées, sinon la ligne sous le curseur) |
| Génération | `o` | Ouvrir le dossier de la candidature |
| Candidatures | `entrée` | Basculer l'aperçu lettre ↔ CV |
| Candidatures | `e` | Ouvrir `lettre.md` dans l'éditeur |
| Candidatures | `p` | Régénérer `lettre.pdf` depuis `lettre.md` |
| Candidatures | `u` | Régénérer `cv_adapte.pdf` depuis `cv_adapte.html` |
| Candidatures | `o` | Ouvrir le dossier de la candidature |
| Suivi | `s`/`a`/`i`/`j`/`v`/`z` | Statut → sent / answered / interview / rejected / accepted / cancelled |
| Suivi | `n` | Éditer la note |
| Suivi | `o` | Ouvrir l'offre dans le navigateur |
| Stats | `f` | Exporter le diagramme de Sankey du flux de statuts (HTML, ouvert dans le navigateur) |
| Réglages | `entrée` | Ouvrir le fichier de config sous le curseur dans l'éditeur (`$VISUAL`/`$EDITOR`, sinon `open`/`xdg-open`/`start`) |

---

## Exemple de session type

```bash
# Lancer le pipeline complet
jobsearch --scrape

# → Le rapport est généré dans data/rapport_YYYY-MM-DD.md
# → Lire le rapport, noter l'ID des offres intéressantes

# Générer la candidature pour une offre
jobsearch --generate 0151e52f

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
0 8 */2 * * cd "path/to/Script" && source venv/bin/activate (seulement si vous utilisez une venv) && jobsearch --scrape >> data/cron.log 2>&1
```

Ce que ça fait : tous les 2 jours à 8h00, le pipeline tourne et les résultats sont ajoutés à `data/cron.log`.

---

## Personnalisation

### Modifier ton sujet de recherche
Édite `config/search.yaml` (voir `config/search.example.yaml`, commenté) :
- Type de contrat (stage, CDI…), durée minimum, date de disponibilité
- Préférences géographiques, domaines, seuil de score, bonus/malus de scoring
- Mots-clés du pré-filtre (le levier le plus rapide pour pivoter vers un autre type de contrat — ex. remplacer `intern`/`internship` par `cdi`/`permanent`)
- Modèles/tokens Claude et paramètres par défaut du scraping

Pour un vrai changement de sujet (ex. passer d'une recherche de stage à une recherche de CDI), il faut aussi repartir d'un dossier `data/` vierge — le prompt de scoring livré (`filter.txt`) contient désormais des exemples génériques qui s'adaptent à n'importe quel sujet via les placeholders, donc rien à réécrire à la main pour démarrer. Pour un scoring plus fin, remplacer ces exemples par 8-14 offres réelles représentatives de ta recherche dans `config/prompts/filter.txt` (prend le pas sur le défaut du paquet). Voir la section « Changer de sujet de recherche » dans `CLAUDE.md` pour la procédure complète.

### Modifier ton profil de candidat
Édite `config/profile.yaml` — ce fichier ne contient que ton identité (formation, compétences techniques, langues), indépendante du sujet de recherche.

### Ajouter des sources RSS
Édite `config/sources.yaml` — ajoute un bloc :
```yaml
- name: "Nom descriptif"
  url: "https://lien-vers-le-flux.rss"
  type: rss
  country: "NL"  # Code pays ISO
```

### Welcome to the Jungle (WTTJ)
Type `wttj` — pas de clé API nécessaire (API publique v3). Les entrées NL sont actives par défaut, les entrées FR vivent dans le groupe `fr` (`--with fr`). Pivoter vers un autre pays/une autre requête ne touche que la config :
```yaml
- name: "WTTJ - Intern NL"
  type: wttj
  country: "NL"
  params:
    job_title: "intern"
    locations:
      country: "Netherlands"
      country_code: "NL"
      label: "Pays-Bas"
      latitude: 52.25
      longitude: 5.75
    contract_types: [internship, apprenticeship]
    pages: 3
```

### Mettre à jour ton CV
Édite `cv_master/cv_data.yaml` — c'est **la** source de vérité (données structurées : expériences, projets, compétences, infos perso). Ce fichier alimente à la fois le CV « master » et le CV adapté généré par Claude. Le fichier `cv_master/cv_master.md` est **régénéré automatiquement** depuis le YAML (via `python -m jobsearch.cv_sync`, et à chaque génération de candidature) — ne l'édite pas à la main.

### Ajuster les prompts
- `config/prompts/filter.txt` — modifie les critères de scoring
- `config/prompts/cover_letter.txt` — ajuste le style/structure de la lettre (à modifier pour ajuster les dates mentionnées dans la lettre)
- `config/prompts/cv_adapt.txt` — modifie comment le CV est réorganisé

### Personnaliser les templates PDF (CV & lettre)

L'apparence des PDF est contrôlée par deux templates HTML dans `config/templates/` :

| Template | Utilisé pour | Rendu par |
|---|---|---|
| `cv_en.html` | Le CV (master et adapté) | `jobsearch/cv_pdf.py` |
| `cover_letter.html` | La lettre de motivation | `jobsearch/letter_pdf.py` |

**Comment ça marche :** chaque template contient des marqueurs `{{NOM}}` que le code remplace par les vraies valeurs, puis WeasyPrint convertit le HTML en PDF. Tu peux modifier librement le CSS (couleurs, polices, marges, disposition) tant que tu **conserves les marqueurs** `{{...}}` là où le contenu doit apparaître.

**Marqueurs disponibles — CV (`cv_en.html`) :**
`{{FULL_NAME}}`, `{{HEADLINE}}`, `{{LOCATION}}`, `{{EMAIL}}`, `{{GITHUB}}`, `{{EXPERIENCE_ENTRIES}}`, `{{PROJECT_ENTRIES}}`, `{{EDU_SCHOOL}}`, `{{EDU_DATES}}`, `{{EDU_DETAIL}}`, `{{START_DATE}}`, `{{DURATION}}`, `{{MOBILITY}}`, `{{SKILLS_PROFICIENT}}`, `{{SKILLS_FAMILIAR}}`, `{{SKILLS_TOOLS}}`, `{{SKILLS_DOMAINS}}`, `{{LANG1_NAME}}`/`{{LANG1_LEVEL}}`, `{{LANG2_NAME}}`/`{{LANG2_LEVEL}}`, `{{OTHER_INFO}}`, `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}`.

**Marqueurs disponibles — Lettre (`cover_letter.html`) :**
`{{FULL_NAME}}`, `{{SENDER_LOCATION}}`, `{{EMAIL}}`, `{{COMPANY_NAME}}`, `{{COMPANY_LOCATION}}`, `{{COMPANY_DEPT_LINE}}`, `{{POSITION_TITLE}}`, `{{DATE}}`, `{{SALUTATION}}`, `{{BODY}}`, `{{CLOSING_WORD}}`, `{{SIGNATURE_IMG}}`, `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}`.

**Couleur d'accent :** contrôlée par `personal.accent_enabled` dans `cv_data.yaml` (vert si `true`, gris si `false`). Les valeurs hex sont injectées dans `{{ACCENT_COLOR}}` / `{{ACCENT_LIGHT}}` — pour changer la teinte, modifie-les dans `jobsearch/cv_pdf.py` / `jobsearch/letter_pdf.py`.

**Polices :** les fichiers `.ttf` (DM Sans, DM Serif Display) sont dans `config/templates/fonts/` et chargés via `@font-face`. La génération PDF fonctionne donc hors-ligne. Pour changer de police, dépose le `.ttf` dans ce dossier et ajuste les règles `@font-face` en tête de template.

**Signature de la lettre :** l'image `files/letter/signature.png` est insérée automatiquement via `{{SIGNATURE_IMG}}` (si le fichier existe). Sa taille se règle avec la règle CSS `.cl-signature` (propriété `height`) dans `cover_letter.html`. ⚠️ Garde-la assez petite pour que la lettre **tienne sur une seule page**.

**Tester une modif de template** (sans consommer d'API) :
- Lettre : `jobsearch --letter-pdf <une lettre.md> --company "Test"` — ou touche `p` dans la TUI sur une candidature existante.
- CV master : `python files/cv/generate_cv.py` → `files/cv/cv_nathan_albalat_en.pdf`.
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
jobsearch --from-file ~/Downloads/offre_wttj.html \
               --url-source "https://www.welcometothejungle.com/fr/companies/..." \
               --company "Nom Entreprise"
```

L'argument `--url-source` est optionnel mais utile — il est inclus dans la lettre générée pour référencer l'offre.

### Depuis une URL directe (sites simples)

Pour les sites accessibles sans JavaScript (pas de CloudFront) :

```bash
jobsearch --from-url https://www.somesite.com/jobs/stage-dev-python
```

### Surcharges si l'extraction échoue

Si le titre ou l'entreprise n'est pas bien extrait automatiquement :

```bash
jobsearch --from-file offre.html \
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
→ Le filtre doit avoir tourné en premier. Lance `jobsearch --scrape` dans l'ordre complet.

**Le rapport contient peu d'offres pertinentes**
→ Baisse le seuil : `jobsearch --scrape --threshold 5`

**L'offre_id est introuvable pour --generate**
→ L'ID est le nom du fichier JSON sans `.json` dans `data/offers/` (ex: `0151e52f`)

---

## Licence

MIT — voir [LICENSE](LICENSE).
