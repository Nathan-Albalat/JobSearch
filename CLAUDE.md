# JobSearch — Pipeline automatisé de recherche d'emploi — Documentation

## Vue d'ensemble

JobSearch est un pipeline Python générique et configurable de recherche d'emploi (stage, alternance, CDI…), utilisable pour n'importe quel secteur — pas seulement l'informatique : collecte des offres depuis des flux RSS et APIs, les filtre avec Claude Haiku (scoring 0-10) selon un sujet et un secteur configurables, génère un rapport Markdown, puis produit une lettre de motivation et un CV PDF adapté via Claude Sonnet pour les offres retenues. `jobsearch --setup` guide le choix du sujet (type de contrat, durée, géo) et du secteur (preset de mots-clés du pré-filtre parmi informatique/santé/droit/commerce/communication/ingénierie/enseignement, ou saisie libre) sans jamais toucher au code Python — voir « Changer de sujet de recherche » plus bas.

**Instance de référence de ce dépôt** : Nathan Albalat — Ingénieur INFO5 Polytech Grenoble, cherche un stage de 22 semaines minimum à l'étranger (priorité Pays-Bas / Europe), dispo février 2027, secteur informatique. C'est la config *personnelle* de ce déploiement (`~/.jobsearch/`), pas le positionnement générique de l'outil décrit ci-dessus.

**Documentation utilisateur** : `README.md` (anglais, version de référence) et `README_FR.md` (français) — contenu identique, à maintenir en parallèle en cas de modification.

**Suivi de projet (ObsidianBrain)** : `/Users/nathan/Document/ObsidianBrain/Année INFO5/Script/` — `STATE.md` (état courant du projet) et `CHANGELOG.md` (journal daté des sessions). Mis à jour via `/wrapup`.

---

## Architecture

```
Étape 1 — Scrape   jobsearch/scraper.py    RSS + JSON API + jobspy (LinkedIn/Indeed) + Adzuna + WTTJ (NL active, groupe `fr` optionnel) + HelloWork (groupe `fr`, optionnel) → data/offers/*.json
Étape 2 — Filtre   jobsearch/filter.py     Pré-filtre mots-clés + Claude Haiku (0-10) — Message Batches par défaut (-50%)
Étape 3 — Rapport  jobsearch/report.py     data/offers/*.json → data/rapport_YYYY-MM-DD.md (+ section suivi)
Étape 4 — Candidature  jobsearch/generator.py  Claude Sonnet → lettre.md + lettre.pdf + cv_adapte.pdf + entrée dans applications.json
```

Les étapes 1-2-3 sont enchaînées via `jobsearch --scrape`.
L'étape 4 est déclenchée manuellement : `jobsearch --generate <offer_id>`.

---

## Structure des fichiers

Deux racines distinctes (voir « Emplacement des données » ci-dessous) : les prompts (`config/prompts/`)
et les templates HTML (`config/templates/*.html`) sont du **contenu utilisateur éditable** — résolus en
priorité depuis `JOBSEARCH_HOME/config/` (`~/.jobsearch/config/prompts/`, `.../templates/`) avec **repli
automatique sur les défauts livrés dans le paquet** (`jobsearch/resources/`, accédés via
`importlib.resources`) si l'utilisateur n'a rien personnalisé — voir `Paths.prompt()`/`Paths.template()`
dans `jobsearch/config.py`. Seules les polices (`jobsearch/resources/templates/fonts/*.ttf`) restent
exclusivement dans le paquet, jamais surchargeables. Tout le reste ci-dessous (`config/search.yaml`,
`config/profile.yaml`, `config/sources.yaml`, `config/.env`, `cv_master/`, `data/`, `output/`) est de la
**config personnelle et des données**, résolues sous `JOBSEARCH_HOME` (`~/.jobsearch/` par défaut). Les
chemins présentés ici sont relatifs à leur racine respective pour rester lisibles.

```
config/                  [JOBSEARCH_HOME/config/ ; prompts/ et templates/ optionnels ici, sinon repli sur le paquet]
  search.yaml            LE SUJET de recherche (stage/CDI, durée, géo, scoring, mots-clés, modèles…)
  profile.yaml           L'IDENTITÉ du candidat (formation, compétences, langues — indépendant du sujet)
  sources.yaml           Sources RSS et APIs à scraper
  .env                   Clés API et secrets (ANTHROPIC_API_KEY, ADZUNA_*, SMTP_*…)
  prompts/               [optionnel — si absent, repli sur jobsearch/resources/prompts/ du paquet]
    filter.txt          Prompt de scoring Claude Haiku (barème + exemples few-shot — 14 exemples réels calibrés sur le sujet actuel, personnels à ce dossier ; le défaut du paquet `jobsearch/resources/prompts/filter.txt` reste générique)
    cover_letter.txt    Prompt de génération de lettre de motivation
    cv_adapt.txt        Prompt d'adaptation du CV → sort un JSON structuré
  templates/              [optionnel — si absent, repli sur jobsearch/resources/templates/ du paquet]
    cv_en.html          Template HTML du CV (DM Sans / DM Serif, layout 2 colonnes)
    cover_letter.html   Template HTML de la lettre de motivation (même charte graphique)

cv_master/                [JOBSEARCH_HOME/cv_master/]
  cv_data.yaml          SOURCE DE VÉRITÉ pour le CV PDF (données structurées)
  cv_master.md          Résumé textuel du CV (utilisé par le prompt cover_letter)

jobsearch/                [paquet installable — code + ressources par défaut, alias CLI `jobsearch`]
  cli.py                  Point d'entrée (`jobsearch = "jobsearch.cli:main"` dans pyproject.toml), ex-`main.py`
  config.py              Socle de configuration : chemins (`Paths`/`PATHS`, résolus en absolu depuis `JOBSEARCH_HOME`) + valeurs (`Search`/`get_search()`) — lit config/search.yaml fusionné au-dessus de DEFAULTS, jamais rien au niveau module
  tui.py                Interface TUI Textual (--tui) : revue des offres, génération, suivi
  scraper.py            Collecte RSS (feedparser) + API JSON (arbeitnow) + Adzuna + dédup fuzzy inter-sources
  scraper_jobspy.py     Source type "jobspy" : LinkedIn / Indeed via python-jobspy
  filter.py             Pré-filtre mots-clés (configurable) + Claude Haiku (Message Batches par défaut, séquentiel en fallback)
  report.py             Génération du rapport Markdown (+ section suivi des candidatures)
  generator.py          Génération lettre + CV adapté (Claude Sonnet) + enregistrement dans le suivi
  tracker.py            Suivi des candidatures (data/applications.json, statuts, tableau CLI, historique, couleurs de statut)
  flow.py                Agrégation + rendu du flux de statuts (arbre texte pour la TUI, export SVG/HTML Sankey autonome)
  cv_sync.py            Régénère cv_master.md depuis cv_data.yaml (source de vérité unique)
  cv_pdf.py             Rendu HTML→PDF via WeasyPrint (partagé avec files/)
  letter_pdf.py         Rendu PDF de la lettre (extraction salutation/closing, markdown→HTML)
  manual.py             Mode manuel : extraction depuis HTML/URL + génération directe
  notifier.py           Envoi email optionnel (SMTP)
  duplicates.py         Marquage manuel des offres en doublon (champ `duplicate_of`) — stdlib uniquement
  migrate.py             `--migrate` : copie (jamais déplace) la config perso, les prompts/templates par défaut et les données du dépôt vers JOBSEARCH_HOME
  wizard.py              `--setup` : assistant d'installation Textual autonome (7 étapes, dont le choix d'un secteur/preset de mots-clés) — voir section dédiée ci-dessous
  resources/              [ressources du paquet, empaquetées via `importlib.resources` — défauts de référence]
    prompts/             Copie de référence des 3 prompts (mêmes fichiers que `config/prompts/` avant migration)
    templates/            Copie de référence des 2 templates HTML + fonts/*.ttf (polices DM Sans / DM Serif Display, non surchargeables)
    prefilter_presets.yaml  Jeux de mots-clés du pré-filtre par secteur, proposés à l'étape 4 de `jobsearch --setup` — pas dans `config/`, jamais lu depuis `JOBSEARCH_HOME` (contenu du paquet, pas config personnelle)

scripts/
  test_api.py            Smoke-test de la clé API Anthropic (hors paquet jobsearch/)

pyproject.toml            Métadonnées du paquet + entry point (`jobsearch = "jobsearch.cli:main"`)
LICENSE                   MIT — sans elle, le défaut légal serait « tous droits réservés »
setup.sh                  Amorçage : copie les `.example` du dépôt vers JOBSEARCH_HOME

data/                     [JOBSEARCH_HOME/data/]
  offers/               Un fichier JSON par offre (avec filter_result après filtrage ; champ optionnel
                        `duplicate_of` : id de l'offre candidatée dont celle-ci est un doublon manuel,
                        posé/retiré via `jobsearch/duplicates.py`)
  seen_offers.json      Hachés des offres déjà vues (déduplication)
  applications.json     Suivi des candidatures (statuts : generated/sent/answered/interview/rejected/accepted/cancelled)
                        Chaque candidature porte un champ `history` : liste ordonnée `{"status", "at"}` (datetime ISO)
                        des transitions de statut — se matérialise au premier changement de statut (pas de script de
                        migration), reconstruit à la volée sinon (`ensure_history`, événements marqués `inferred`)
  gen_queue.json         File de génération de l'onglet 2 de la TUI (persistée entre deux lancements)
  rapport_*.md          Rapports générés

output/                   [JOBSEARCH_HOME/output/]
  candidatures/         Un dossier par candidature : lettre.md + lettre.pdf + cv_adapte.html + cv_adapte.pdf
  stats/
    flux.html            Export du diagramme de Sankey du flux de statuts (`f` dans l'onglet Stats de la TUI)

files/
  generate_cv.py        Génère le CV PDF "master" (sans adaptation) depuis cv_data.yaml
  cv_nathan_albala_en.html  Preview HTML du CV master
  cv_nathan_albala_en.pdf   PDF du CV master
```

---

## Commandes principales

```bash
# Assistant d'installation interactif (première utilisation)
jobsearch --setup

# Pipeline complet (scrape + filtre + rapport)
jobsearch --scrape

# Avec seuil personnalisé
jobsearch --scrape --threshold 7

# Inclure les sources FR-only (groupe `fr`, ex: HelloWork, WTTJ FR), désactivées par défaut
jobsearch --scrape --with fr

# Désactiver un groupe même s'il est activé par défaut dans search.yaml (sources.enabled_groups)
jobsearch --scrape --without fr

# Générer candidature pour une offre (lettre + CV adapté)
jobsearch --generate <offer_id>

# Lettre seule (pas de CV adapté — 1 appel Sonnet au lieu de 2)
jobsearch --generate <offer_id> --letter-only

# Mode manuel (offre non-scrappable)
jobsearch --from-file ~/Downloads/offre.html --url-source "https://..." --company "Criteo"
jobsearch --from-url https://example.com/jobs/stage-dev-python

# Modules en isolation (debug)
python -m jobsearch.scraper
python -m jobsearch.filter
python -m jobsearch.report
python -m jobsearch.generator <offer_id>

# Forcer le filtrage séquentiel (sans Message Batches)
jobsearch --scrape --no-batch

# Lettre PDF à la demande (hors pipeline)
jobsearch --letter-pdf lettre.md --company "ASML" --title "Software Intern" --location "Eindhoven, NL" [--dept "..."] [--output out.pdf]
jobsearch --letter-pdf offre.json   # format files/letter/offer_data_example.json

# Interface interactive (revue → génération → suivi)
jobsearch --tui

# Suivi des candidatures
jobsearch --applications
jobsearch --mark <offer_id> sent --note "Candidature envoyée via le site"

# Migration config perso + données du dépôt vers JOBSEARCH_HOME (copie uniquement, jamais destructif)
jobsearch --migrate            # aperçu à blanc, n'écrit rien
jobsearch --migrate --apply    # copie réelle, vérifie fichiers + sommes de contrôle après coup

# Marquer une offre comme doublon manuel d'une candidature existante (voir section dédiée ci-dessous)
jobsearch --mark-duplicate <offer_id> <target_id>
jobsearch --unmark-duplicate <offer_id>

# Générer le CV master (sans adaptation, depuis cv_data.yaml) — resynchronise aussi cv_master.md
python files/cv/generate_cv.py

# Régénérer cv_master.md seul depuis cv_data.yaml
python -m jobsearch.cv_sync

# Afficher les chemins et valeurs de config effectifs (débug config/search.yaml)
python -m jobsearch.config
python -m jobsearch.config --check-prompts   # échoue si un placeholder reste inconnu dans les 3 prompts
python -m jobsearch.config --check-wizard-defaults   # échoue si une valeur d'auteur fuite de search.example.yaml dans un setup simulé
```

---

## Installation

```bash
pipx install git+https://github.com/<toi>/jobsearch   # commande globale `jobsearch`, dépendances isolées
# ou, en développement local (dans le venv du dépôt) :
pip install -e .
```

Dépendance système requise par WeasyPrint (rendu PDF) : Pango/Cairo — `brew install pango` sur macOS.

**Piège — version de Python imposée par `python-jobspy`.** Cette dépendance épingle `NUMPY==1.26.3`,
qui n'a de wheels que jusqu'à Python 3.11. Or `pipx` utilise par défaut le Python le plus récent de la
machine (3.14 chez l'auteur) : l'installation échoue alors en tentant de **compiler** numpy, sur une
erreur `ModuleNotFoundError: No module named 'genapi'` qui ne mentionne ni jobspy ni la version de
Python. Contournement immédiat :

```bash
pipx install -e . --python /chemin/vers/python3.11
```

Correctif de fond à faire : passer `python-jobspy` en **dépendance optionnelle** (`jobsearch[jobspy]`).
`fetch_jobspy` (`jobsearch/scraper_jobspy.py`) intercepte déjà l'`ImportError` et ignore la source avec
un message, donc le reste du pipeline fonctionne sans elle — l'installation de base deviendrait légère
et compatible avec les Python récents, seuls LinkedIn/Indeed exigeant l'extra et un Python ≤ 3.11.

Premier lancement : `jobsearch --scrape` (ou toute autre commande) détecte l'absence de config dans
`JOBSEARCH_HOME` (`~/.jobsearch/` par défaut) et propose de lancer `setup.sh`, qui copie les fichiers
`.example` du paquet vers `JOBSEARCH_HOME`. Une installation antérieure avec des données dans le dépôt
(avant packaging) se migre avec `jobsearch --migrate` (aperçu) puis `jobsearch --migrate --apply`.

---

## Interface TUI (`jobsearch --tui`)

Vue interactive sur les fichiers du pipeline — **les fichiers restent la source de vérité** : les rapports markdown, `data/offers/*.json` et `data/applications.json` restent lisibles et utilisables sans la TUI, qui ne persiste quasiment aucun état propre (seule exception : la file de génération de l'onglet Génération, dans `data/gen_queue.json`). On peut donc reprendre le workflow n'importe quand (scrape lundi, revue mercredi) : la TUI relit l'état courant au lancement et sur `r`.

6 onglets : **Offres** (tableau triable par score, panneau de détail, cocher pour ajouter à la file de génération — lignes cochées surlignées en vert, offres de la dernière session de scrape surlignées en bleu foncé, filtrage par période avec `d`/`D` ; colonne `✓` en cascade à 4 états : `▶` cochée > `⧉` doublon manuel confirmé > `✓` candidatée > `?` doublon suspecté — entreprise déjà candidatée, non tranché — touche `m` pour marquer/démarquer, suit `h` comme une offre candidatée), **Génération** (file persistée entre deux lancements de la TUI dans `data/gen_queue.json` ; cocher des lignes avec `espace` restreint `g`/`L` à ce sous-ensemble — sinon toutes les offres en attente sont traitées ; `x` retire des lignes de la file ; lance lettre + CV en tâche de fond, ou lettre seule avec `L`), **Candidatures** (liste `output/candidatures/`, aperçu lettre/CV, régénération des PDF après retouche manuelle des fichiers — candidatures envoyées ou plus surlignées en vert foncé), **Suivi** (statuts + notes de `applications.json` — envoyée surlignée en jaune, réponse reçue en ambre, entretien en violet, acceptée en vert, refusée en rouge, annulée en gris foncé et texte atténué ; panneau de droite : timeline de la candidature sous le curseur, reconstruite depuis `history` — écart en jours entre chaque événement, dates déduites suffixées d'un `~` quand l'historique est reconstitué depuis les anciennes dates ponctuelles), **Stats** (rendu texte : métriques de retour — taux d'acceptation/réponse, délai moyen —, répartition des statuts, localisation des stages, histogramme des candidatures au cours du temps ; les candidatures annulées sont exclues des taux et du délai moyen, comptées à part dans une catégorie « Annulé » ; parcours des candidatures sous forme d'arbre de statuts (`jobsearch/flow.py`) et répartition mensuelle des statuts atteints — `f` exporte le diagramme de Sankey correspondant en HTML autonome, `output/stats/flux.html`, ouvert dans le navigateur), **Réglages** (table listant les fichiers de configuration éditables — `.env`, `search.yaml`, `profile.yaml`, `sources.yaml`, les 3 prompts, les 2 templates HTML, `cv_data.yaml` — avec rôle, état [personnalisé / défaut du paquet / absent] et date de modification ; `entrée` ouvre le fichier sous le curseur dans l'éditeur, en copiant d'abord un prompt/template encore au défaut du paquet vers la config utilisateur avant édition ; `r` recharge ensuite `search.yaml` et invalide le cache de `get_search()`).

Workflow de retouche : ouvrir la lettre avec `e` (éditeur par défaut), modifier le `.md`, revenir dans la TUI et presser `p` — la colonne PDF affiche « ⚠ à régénérer » quand le `.md` est plus récent que le PDF.

### Raccourcis clavier (aussi via `?` dans la TUI)

| Contexte | Touche | Action |
|---|---|---|
| Global | `1`/`2`/`3`/`4`/`5`/`6` | Onglets Offres / Génération / Candidatures / Suivi / Stats / Réglages |
| Global | `r` | Recharger les données depuis le disque |
| Global | `S` / `s` | Lancer scrape + filtre + rapport (spinner + étape + chrono dans la barre de statut). `s` ne déclenche le scrape qu'en dehors de l'onglet Suivi, où il vaut « envoyée » ; `s` est un alias de `S` (certains terminaux ne transmettent pas `shift`+lettre) |
| Global | `Ctrl+S` | Idem, en incluant aussi les sources FR-only (groupe `fr`, ex. HelloWork, WTTJ FR) — équivalent de `jobsearch --scrape --with fr` |
| Global | `c` | Ouvrir la Claude Console (recharger le crédit API) |
| Global | `?` | Aide (liste des raccourcis) |
| Global | `q` | Quitter |
| Offres | `espace` | Cocher/décocher l'offre pour génération |
| Offres | `o` | Ouvrir l'offre dans le navigateur |
| Offres | `+`/`-` | Ajuster le seuil de score affiché |
| Offres | `h` | Masquer/afficher les offres déjà candidatées (et les doublons marqués) |
| Offres | `d` | Cycler la période affichée (tout · session · 7j · 30j) |
| Offres | `D` | Choisir une session de scrape précise |
| Offres | `g` | Aller à l'onglet Génération |
| Offres | `m` | Marquer/démarquer l'offre comme doublon d'une candidature existante (modale de sélection ; la machine suggère via l'entreprise, l'utilisateur confirme) |
| Génération | `espace` | Cocher/décocher une offre de la file (filtre optionnel pour `g`/`L`) |
| Génération | `g` | Générer lettre + CV (lignes cochées, sinon toutes les offres en attente) |
| Génération | `L` | Générer la lettre seule (sans CV adapté — 1 appel Sonnet au lieu de 2) |
| Génération | `A` | Ajouter une offre manuelle (fichier HTML ou URL) à la liste, puis `g`/`L` |
| Génération | `x` | Retirer de la file (lignes cochées, sinon la ligne sous le curseur) |
| Génération | `o` | Ouvrir le dossier de la candidature |
| Candidatures | `entrée` | Basculer l'aperçu lettre ↔ CV |
| Candidatures | `e` | Ouvrir lettre.md dans l'éditeur |
| Candidatures | `p` | Régénérer lettre.pdf depuis lettre.md |
| Candidatures | `u` | Régénérer cv_adapte.pdf depuis cv_adapte.html |
| Candidatures | `o` | Ouvrir le dossier de la candidature |
| Candidatures | `O` | Ouvrir l'offre dans le navigateur |
| Suivi | `s`/`a`/`i`/`j`/`v`/`z` | Statut → sent / answered / interview / rejected / accepted / cancelled |
| Suivi | `n` | Éditer la note |
| Suivi | `o` | Ouvrir l'offre dans le navigateur |
| Stats | `f` | Exporter le diagramme de Sankey du flux de statuts (HTML, ouvert dans le navigateur) |
| Réglages | `entrée` | Ouvrir le fichier de config sous le curseur dans l'éditeur (`$VISUAL`/`$EDITOR` sinon `open`/`xdg-open`/`start`) |

---

## Emplacement des données (`JOBSEARCH_HOME`)

Le pipeline se lance depuis **n'importe quel répertoire**, et sépare deux racines résolues en absolu par `jobsearch/config.py` (`PATHS`) :

- **Ressources du paquet** (`jobsearch/resources/`, accédées via `importlib.resources` — donc trouvées quel que soit le mode d'installation, éditable ou figée) : les **défauts de référence** des prompts et templates, plus les **polices** (`fonts/*.ttf`, jamais surchargeables). `PATHS.package_prompts_dir`/`PATHS.package_templates_dir` s'y résolvent toujours.
- **`JOBSEARCH_HOME`** : la **configuration personnelle** (`config/search.yaml`, `config/profile.yaml`, `config/sources.yaml`, `config/.env`, `cv_master/cv_data.yaml`, `cv_master/cv_master.md`) et les **données** (`data/`, `output/`). Vaut par défaut `~/.jobsearch/` — surchargeable via la variable d'environnement `JOBSEARCH_HOME` (utile pour tester sur une copie isolée, ou repointer vers le dépôt avec `JOBSEARCH_HOME=.` pour un usage « tout-en-un » historique).
- **Prompts et templates HTML** (`config/prompts/*.txt`, `config/templates/*.html`) sont du **contenu utilisateur éditable** : `PATHS.prompt(name)`/`PATHS.template(name)` cherchent d'abord dans `JOBSEARCH_HOME/config/`, et **replient automatiquement sur les défauts du paquet** si absents — aucune action requise pour une installation existante qui n'a encore rien personnalisé.

`jobsearch --applications` fonctionne aussi bien depuis `Stage/Script` que depuis `/tmp` : les chemins ne dépendent jamais du répertoire courant. Voir `python -m jobsearch.config` pour afficher les chemins effectifs.

**Migration depuis une installation où tout vivait dans le dépôt** (avant cette séparation) : `jobsearch --migrate` (mode à blanc, n'écrit rien, affiche ce qui serait copié) puis `jobsearch --migrate --apply` (copie réelle, `jobsearch/migrate.py`). Ne fait QUE des copies — ne déplace ni ne supprime jamais les originaux du dépôt ; refuse d'écraser un fichier déjà présent à destination ; vérifie après coup le nombre de fichiers et les sommes de contrôle. Suppression des originaux laissée à l'utilisateur.

**Garde-fou** : si `JOBSEARCH_HOME` est vide/absent alors que le dépôt contient encore des offres dans `data/offers/` (ancien emplacement, pas encore migré), `_check_setup` (`jobsearch/cli.py`) et `_warn_if_empty_home` (`jobsearch/tui.py`, appelé par `run_tui()`) affichent un message explicite pointant vers `--migrate` plutôt que de laisser croire à « 0 offre » / une perte de données.

## Assistant d'installation (`jobsearch --setup`)

App Textual autonome (`jobsearch/wizard.py`, distincte de `jobsearch/tui.py`) qui automatise le parcours que `setup.sh` se contentait de décrire (checklist de 5 fichiers à éditer à la main). Exemptée de `_check_setup` (même mécanisme que `--migrate`, `jobsearch/cli.py`), donc utilisable sur une racine `JOBSEARCH_HOME` vierge. Étapes pilotées via `push_screen_wait` dans un worker (`@work` async) :

1. **Présentation** — porte d'entrée `jobsearch --tui`, coût API (Haiku au filtrage, Sonnet à la génération).
2. **Clés API** — toutes optionnelles à l'exception de la clé Anthropic (recommandée). Couvre, dans un seul écran (`ApiKeyScreen`) : Anthropic (`Input(password=True)`, console.anthropic.com/settings/keys), Adzuna (`ADZUNA_APP_ID`/`ADZUNA_APP_KEY`, gratuit sur developer.adzuna.com) et les notifications SMTP (`NOTIFY_EMAIL`/`SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` — pour Gmail, mot de passe d'application sur myaccount.google.com/apppasswords, pas le mot de passe habituel). Toute clé laissée vide est sautée. Validation optionnelle de la clé Anthropic par un appel minimal, non facturé (`client.models.list(limit=1)`), demandée par confirmation explicite avant tout appel réseau. `render_env_file` génère `.env` depuis `.env.example` (commentaires conservés) avec les valeurs saisies — **toujours écrit à l'étape finale, même si toutes les clés ont été sautées** : sinon `_check_setup` continue de le réclamer juste après un `--setup` pourtant complet.
3. **Sujet de recherche** — type de contrat, durée applicable ou non, pays prioritaires, région/ville, date de disponibilité, domaines, seuil. Génère `search.yaml` en substituant ces valeurs directement dans le texte de `config/search.example.yaml` du dépôt (jamais `yaml.safe_dump`, qui détruirait ses ~200 lignes de commentaires) : `_set_scalar`/`_set_list` repèrent la section top-level (`sujet:`, `duree:`, `disponibilite:`, `geo:`, `domaines:`, `scoring:`) puis remplacent la valeur scalaire ou le bloc de liste correspondant — `_set_list` accepte aussi bien la forme bloc (`liste:` suivi d'items indentés) que la forme mapping/liste vide en ligne (`liste: []`, celle du `.example.yaml` générique). **`geo.notes`/`geo.details` (le bloc réellement injecté dans le barème du prompt via `{geo_details}`), `disponibilite.*` et `scoring.bonus`/`malus` ne sont jamais recopiés depuis `search.example.yaml` : ils sont régénérés en entier depuis les réponses de l'utilisateur** (`render_geo_notes`/`render_geo_details`/`parse_disponibilite`/`render_scoring_bonus`/`render_scoring_malus`, `jobsearch/wizard.py`) — sinon le `.example.yaml`, même neutralisé, resterait la source du barème effectif d'un tiers. **De même pour `sujet.label`, `sujet.type_contrat_negatif`, `scoring.titre_bonus` et `domaines.ouverture`** (`render_sujet_label`/`derive_type_contrat_negatif`/`derive_titre_bonus`/`render_domaines_ouverture`, `jobsearch/wizard.py`) : troisième occurrence du même bug (après `geo.details`/`geo.notes`/`scoring.bonus`/`scoring.malus`, puis le prompt lui-même) — un `type_contrat_negatif` laissé sur la valeur de l'exemple (« une offre d'emploi ») contredit un `type_contrat` = « CDI » dans la même phrase du prompt de scoring. `geo.secondaire`/`geo.dernier_recours`, eux, ne sont pas demandés par cet assistant (seule `geo.priorite` l'est) : laissés explicitement vides plutôt que recopiés de l'exemple, à affiner depuis l'onglet Réglages. Voir « Garde-fou anti-régression » plus bas.
4. **Pré-filtre** — choix d'un jeu de mots-clés pré-rempli par secteur (`jobsearch/resources/prefilter_presets.yaml` : informatique, santé, droit, commerce/vente, communication/marketing, ingénierie, enseignement — le preset informatique conserve les 83 mots-clés d'origine, mais n'est plus le défaut universel) ou saisie libre (`preset: "libre"`, mots-clés séparés par des virgules + un intitulé court de secteur). Numéro, nom du preset ou `libre` accepté en saisie (`PrefilterScreen._resolve_preset`). Alimente deux endroits de `search.yaml` : `prefilter.keywords` (`_set_prefilter_keywords`, remplace intégralement le sous-bloc — supporte la forme bloc et la forme `keywords: {}` en ligne) et `sujet.expertise_recruteur` (`render_expertise_recruteur`, gabarit `"spécialisé dans les {type_contrat} en/dans …"` propre à chaque preset). Le groupe `contrat` de `prefilter.keywords` n'est **jamais** dérivé du preset : il vient uniquement du type de contrat choisi à l'étape 3 (`derive_contrat_keywords` — mapping stage/alternance/cdi/cdd/freelance vers des mots-clés dédiés, repli sur le type de contrat tel quel sinon), pour rester indépendant du secteur.
5. **Profil** — formulaire structuré (`config/profile.example.yaml` est plat, court, sans commentaires → seul fichier qui s'y prête), rendu via `yaml.safe_dump` (pas de commentaires à préserver ici). Compétences saisies en paires libres catégorie/détail dans un `TextArea` (une ligne `Catégorie: item1, item2` par catégorie, `ctrl+s` pour valider tout le formulaire — un `TextArea` ne se soumet pas sur `entrée`, contrairement aux `Input` des autres champs). Collecté **avant** l'aperçu du prompt (étape 6, juste après) : celui-ci substitue `{profile}` avec ces réponses.
6. **Aperçu du prompt de scoring — non sautable.** Part du défaut générique livré (`jobsearch/resources/prompts/filter.txt`, neutralisé — plus aucun vocabulaire informatique/géographique/scolaire en dur, tout passe par des placeholders déjà connus de `Search.placeholders()`) et y insère un bloc littéral « Repères issus de la configuration initiale » avec le type de contrat et les domaines choisis à l'étape 3. L'écran affiche cet aperçu **avec tous les placeholders `{xxx}` de `search.yaml` et `{profile}` substitués** (`render_filter_prompt_preview`, qui construit une `Search` en mémoire depuis les réponses de l'assistant — jamais depuis le disque — et appelle `fill_template`), séparé visuellement des instructions par une ligne de titre « Aperçu du prompt (paramétré) ». Seuls `{title}`/`{company}`/`{location}`/`{url}`/`{description}` restent littéraux dans l'aperçu (propres à chaque offre, jamais connus à l'avance). **Le fichier réellement écrit sur le disque, lui, conserve tous ses placeholders** — `render_filter_prompt` (sans suffixe `_preview`) reste inchangé et sert à la fois à l'aperçu (comme gabarit avant substitution) et à l'écriture finale ; ne jamais confondre les deux fonctions. Écrit dans `JOBSEARCH_HOME/config/prompts/filter.txt`. Échap à cette étape revient à l'étape profil (pas d'abandon de l'assistant).
7. **Récapitulatif** — fichiers écrits/ignorés, ce qui reste à personnaliser plus tard (CV — écrit avec un contenu générique, voir ci-dessous —, templates HTML, clés API laissées vides), commande suivante (`jobsearch --scrape` ou `jobsearch --tui`).

**Aperçu puis confirmation** (motif repris de `jobsearch/migrate.py`) : `_write_with_confirm` n'écrit jamais par-dessus un fichier déjà présent sans un écran de confirmation dédié (`OverwriteConfirmScreen`) — relancer `--setup` sur une racine déjà configurée n'écrase donc rien silencieusement ; un refus d'écraser est consigné et affiché au récapitulatif.

**`cv_master/cv_data.yaml`/`cv_master.md`** : `_check_setup` (`jobsearch/cli.py`) les réclame avant de laisser lancer n'importe quelle commande, y compris `--tui`/`--scrape`, alors qu'ils ne servent qu'à `--generate`. Plutôt que retirer cette exigence (au risque d'un `--generate` qui échoue plus tard sans repère clair) ou ajouter une étape de saisie du CV à l'assistant (hors scope — `--setup` configure le sujet/scoring, pas le contenu du CV), `_build_write_items` les initialise systématiquement depuis `cv_master/cv_data.example.yaml`/`cv_master.example.md` du paquet — un CV générique, explicitement signalé comme tel dans `ConfirmWriteScreen` et `SummaryScreen`, à personnaliser ensuite (onglet Réglages, touche 6). Choisi plutôt que d'exempter `--tui`/`--scrape` de cette exigence dans `_check_setup` : un `JOBSEARCH_HOME` fraîchement configuré reste ainsi immédiatement complet pour les 4 commandes principales, sans état intermédiaire « configuré mais incomplet » à documenter séparément.

### Garde-fou anti-régression : valeurs d'auteur héritées de `search.example.yaml`

Bug récurrent (3 occurrences à ce jour : `geo.details`/`geo.notes`/`scoring.bonus`/`scoring.malus`, puis le prompt de scoring lui-même, puis `sujet.label`/`sujet.type_contrat_negatif`/`scoring.titre_bonus`/`domaines.ouverture`) — une clé de `search.yaml` censée être dérivée des réponses de `jobsearch --setup` reste, par erreur, égale à la valeur de l'exemple livré avec le paquet (calibré sur le sujet de l'auteur d'origine : stage tech, Pays-Bas). Sans garde-fou reproductible, traiter les cas un par un ne suffit pas — c'est le quatrième bug de la même famille qu'il s'agit d'empêcher.

`python -m jobsearch.config --check-wizard-defaults` (gratuit, sans appel API) rend `search.yaml` pour deux profils simulés volontairement très contrastés (le sujet d'origine du paquet, puis CDI / France / droit) via `jobsearch.wizard.render_search_yaml`, et échoue si l'une des clés de `jobsearch.wizard.RISK_KEYS` (`sujet.label`, `sujet.type_contrat_negatif`, `scoring.titre_bonus`, `geo.secondaire`, `geo.dernier_recours`, `domaines.ouverture`) :
- reste égale à la valeur littérale de `config/search.example.yaml` pour le profil CDI/France/droit (le plus éloigné du sujet d'origine — une coïncidence y est beaucoup moins probable que pour le profil « stage », où certaines valeurs dérivées recoupent légitimement celles de l'exemple) ;
- ou reste identique entre les deux profils contrastés, malgré des entrées très différentes (hors `geo.secondaire`/`geo.dernier_recours`, volontairement toujours vides — non demandés par cet assistant, voir plus haut).

`jobsearch.wizard.check_wizard_defaults()` est une fonction pure (aucune dépendance Textual), testable indépendamment de l'app. **À chaque nouvelle clé dérivée ajoutée à `render_search_yaml`, l'ajouter à `RISK_KEYS`** pour que ce garde-fou la couvre aussi.

## Configuration du candidat

Modifier `config/search.yaml` (sous `JOBSEARCH_HOME`) pour changer le **sujet** de recherche : type de contrat, durée, disponibilité, préférences géographiques, domaines, seuil de scoring, mots-clés du pré-filtre, modèles Claude et défauts des sources. Voir aussi `config/search.example.yaml` (commenté, dans le dépôt) et la section « Changer de sujet de recherche » ci-dessous.

Modifier `config/profile.yaml` (sous `JOBSEARCH_HOME`) pour changer l'**identité** du candidat : formation, compétences, langues. Ce fichier ne dépend pas du sujet recherché. Compétences en paires libres catégorie/détail sous `competences` (l'utilisateur nomme lui-même ses catégories, pas forcément techniques — ex. `Langages: [Python, C++]` ou `Domaines du droit: [Droit des affaires]`), format écrit par l'écran profil de `jobsearch --setup`. L'ancien format à clés fixes (`competences_techniques` avec `langages_maitrise`/`langages_pratiques`/`frameworks`/`outils`/`domaines_ml`, hérité du sujet historique stage tech) reste lu par rétrocompatibilité si `competences` est absent — voir `jobsearch.filter.render_competences_lines`.

Modifier `cv_master/cv_data.yaml` (sous `JOBSEARCH_HOME`) pour mettre à jour le CV :
- Expériences, projets, compétences, infos personnelles
- Ce fichier alimente à la fois `files/cv/generate_cv.py` (CV master) et `jobsearch/generator.py` (CV adapté)

---

## Changer de sujet de recherche

Le pipeline est conçu pour être repris par n'importe quel utilisateur, pour n'importe quel sujet (stage, alternance, CDI, dans n'importe quel pays) **et n'importe quel secteur** (pas seulement l'informatique), en ne touchant que la config, les prompts et le CV — **jamais le code Python**. `jobsearch --setup` couvre déjà l'essentiel de ce qui suit (étapes 3-4) ; cette section décrit l'équivalent manuel, pour un pivot sur une config déjà existante. Dans l'ordre :

1. **`config/search.yaml`** — le cœur du pivot. Ajuster `sujet.type_contrat` (ex: `"CDI"`), `sujet.type_contrat_negatif`, `sujet.expertise_recruteur` (le secteur, ex: `"spécialisé dans les CDI en droit des affaires"`), `duree.applicable` (`false` pour un contrat sans durée minimale), `geo.priorite`/`secondaire`/`dernier_recours`/`details`, `domaines.liste`, `scoring.seuil`/`bonus`/`malus`, et surtout `prefilter.keywords` — c'est le levier n°1 : ni les 83 mots-clés informatiques ni aucun autre jeu de mots-clés n'est le défaut universel du dépôt (`config/search.example.yaml` livre `prefilter.keywords: {}` vide). Choisir un secteur (voir `jobsearch/resources/prefilter_presets.yaml` : informatique, santé, droit, commerce/vente, communication/marketing, ingénierie, enseignement — ou dupliquer une entrée pour un nouveau secteur, aucune modification de code requise) via `jobsearch --setup` (étape 4) ou en copiant directement les mots-clés du preset choisi dans `search.yaml`. Le sous-groupe `prefilter.keywords.contrat`, lui, doit toujours être dérivé du type de contrat (retirer `stage`/`internship`/`werkstudent` et ajouter les termes du nouveau contrat, ex. `cdi`/`permanent`/`full-time`) — c'est ce sous-groupe précis qui change immédiatement quelles offres passent le pré-filtre gratuit avant même l'appel API.
2. **Les 3 prompts** (`config/prompts/`) — la plupart du texte est déjà paramétré via les placeholders (`{sujet}`, `{type_contrat}`, `{duree}`, `{pays_prioritaires}`, `{seuil}`…) et se met à jour automatiquement depuis `search.yaml`. Le défaut du paquet (`jobsearch/resources/prompts/filter.txt`) contient déjà des exemples de scoring **génériques**, adaptés à n'importe quel sujet via les placeholders — rien à réécrire à la main pour démarrer. Pour affiner le scoring, remplacer ces exemples par 8-14 offres réelles représentatives du sujet dans `config/prompts/filter.txt` (repli prioritaire sur le défaut du paquet, voir `Paths.prompt()`) reste la meilleure amélioration possible : un jeu d'exemples calibrés bat toujours des exemples génériques, mais ce n'est plus un prérequis au pivot.
2bis. **Règles de durée conditionnelles dans `filter.txt`** — les bullets de durée du barème (`{duree_bullet_8_10}`, `{duree_bullet_6_7}`, `{duree_bullet_0_2}`, `{duree_regle_defaut}`) et les clauses inline des exemples (`{duree_clause_exemple_a/b}`) sont rendus vides par `Search.placeholders()` (`jobsearch/config.py`) quand `duree.applicable` est faux (ex: un CDI) — s'adapte automatiquement si l'utilisateur bascule ce réglage depuis l'onglet Réglages, sans prompt figé à maintenir manuellement. `fill_template` retire en bloc toute ligne du template ne contenant qu'un seul placeholder résolu à une chaîne vide (pas de ligne creuse ni de tiret orphelin) — ne pas mettre du texte littéral sur la même ligne qu'un de ces placeholders, sous peine de casser cette suppression.
3. **`config/profile.yaml`** — mettre à jour si la formation ou les compétences techniques ont changé (indépendant du sujet, à ajuster seulement si le candidat change).
4. **`cv_master/cv_data.yaml`** — adapter le CV master si nécessaire (expériences, projets à mettre en avant pour le nouveau sujet).
5. **Repartir d'un `data/` vierge** — il n'existe pas de mécanisme de re-filtrage : les offres déjà scorées dans `data/offers/*.json` restent telles quelles avec leurs anciens critères. Après un changement de sujet, vider (ou déplacer) `data/offers/`, `data/seen_offers.json` et relancer `jobsearch --scrape`.
6. **`config/sources.yaml` — pivoter les entrées `wttj`** : éditer `job_title`, `locations` (mapping `country`/`country_code`/`label`/`latitude`/`longitude`) et `contract_types` de chaque entrée — rien d'autre. Même logique pour `hellowork` (`k`/`c`).

Vérification sans consommer de crédit API : `python -m jobsearch.config --check-prompts` (échoue si un placeholder reste inconnu dans les 3 prompts) et `python -m jobsearch.config --check-wizard-defaults` (échoue si une clé dérivée de l'entrée utilisateur retombe silencieusement sur `search.example.yaml` — voir « Garde-fou anti-régression » ci-dessus).

---

## Coûts API : Message Batches + prompt caching (Claude Haiku)

Le filtrage passe par l'**API Message Batches** dès qu'il y a ≥ 6 offres à scorer (`-50%` sur tous les tokens, résultats sous ~1h max, généralement quelques minutes) :
- Une requête par offre (`custom_id` = offer_id), `system` partagé avec `cache_control: ephemeral` → caching intra-batch
- Poll toutes les 30 s jusqu'à `processing_status == "ended"`, résultats appariés par `custom_id`
- Requêtes en erreur → offre non marquée, re-tentée au run suivant
- `--no-batch` force le mode séquentiel (debug, petits volumes)

Mode séquentiel : prompt (~4100 tokens) en bloc `system` avec `cache_control: ephemeral`, TTL 5 min, stats de cache affichées en fin de run.

Le générateur (Claude Sonnet) n'utilise pas le caching — chaque appel est one-shot avec un prompt différent par offre.

---

## Limitations actuelles

### Sources
- **LinkedIn / Indeed** : couverts via `python-jobspy` (type `jobspy` dans sources.yaml). Rate-limiting possible côté LinkedIn — une source en erreur est loggée et n'interrompt pas le run. Zone grise CGU (volume faible, pas d'authentification).
- **Adzuna** : type `adzuna`, requiert `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` (gratuits sur developer.adzuna.com) dans `.env` — sinon la source est ignorée avec un warning.
- **WTTJ (Welcome to the Jungle)** : type `wttj`, API publique v3 (`https://api.welcometothejungle.com/api/v3/public/jobs`), **aucune authentification** requise (ni cookie, ni CSRF, ni User-Agent) — ne pas en réintroduire par précaution inutile. `search/jobs` est une méthode JS interne du front, pas une route (→ 403 si appelée telle quelle). Entrées NL actives par défaut (le HTML de la page reste, lui, inexploitable côté scraping : JS côté client + CloudFront 403), volet FR dans le groupe `fr` (`--with fr`), même patron que HelloWork. Pièges vérifiés :
  - **le piège des bureaux** : l'API répète chaque offre une fois par bureau de l'entreprise (mesure réelle : 10 résultats bruts → 5 couples `(organization.slug, name)` uniques) — la dédup se fait sur cette clé, **jamais** sur `reference` ni `slug` (tous deux uniques par bureau), et **avant** la boucle de fetch détail (sinon on double les requêtes HTTP)
  - `per_page` est **verrouillé à 10** côté serveur ; `contract_type`/`query`/`per_page` sont refusés en query (422) → `contract_type` est filtré côté client via `contract_types` dans `sources.yaml`
  - `remote` est une **chaîne** (`unknown`/`partial`/`no`/`punctual`), pas un booléen — un `job.get("remote", False)` naïf marquerait le champ `"no"` comme truthy ; mappé explicitement dans `normalize_wttj`
  - `contract_duration_min/max` est exprimé en **mois** (confirmé sur mesure), directement pertinent vu la contrainte de 22 semaines (~5,5 mois)
  - l'URL publique de l'offre (`.../fr/companies/{org_slug}/jobs/{slug}`) n'est jamais fetchée par le code (403 CloudFront sans UA complet) ; elle sert de lien et de base à `make_offer_id`
  - l'ordre des blocs de `description` (durée/début en tête, `company_description` en dernier) est volontaire : `strip_html` (`jobsearch/filter.py`) tronque à 2000 caractères, le signal utile doit passer avant la troncature
- **HelloWork** : type `hellowork`, source FR-only (groupe `fr`, `optional: true` — désactivée par défaut, activer avec `--with fr` ou `sources.enabled_groups` dans `search.yaml`). Server-rendered : une requête GET (User-Agent navigateur) sur la recherche paginée (`?p=N`), puis une requête détail par offre pour extraire le bloc JSON-LD `schema.org/JobPosting` (titre, entreprise, lieu, description, date). **Le JSON-LD est le contrat stable — ne jamais basculer sur des sélecteurs CSS**, qui cassent à chaque refonte front. Un délai (`source_defaults.hellowork.delay`, 0.3 s par défaut) est appliqué entre chaque requête détail par politesse réseau ; une offre détail illisible est loguée et ignorée sans interrompre la source.

### Filtrage
- **Pré-filtre : recherche par mot entier** — `is_tech_offer()` compile `_TECH_KEYWORDS` en une regex à limites de mots (`_TECH_KEYWORDS_RE`). Ne jamais revenir à un `kw in text` : le substring brut provoquait des faux positifs massifs (`"git"` dans « diGITal », `"rust"` dans « tRUST », `"intern"` dans « INTERNational », et surtout l'ancien `"r "` — le langage R — qui matchait « manageR », « senioR », « foR »). Mesure sur l'historique : **43 % des offres envoyées à l'API l'étaient pour rien**, sans qu'aucune offre pertinente (score ≥ 6) ne dépende de ces correspondances partielles.
- **Ajouter un mot-clé court demande de la prudence** : un terme de 1-2 caractères (le langage R, « c ») génère du bruit même en mot entier (« R&D », initiales). R est volontairement absent — ces offres sont captées par `data`/`analyst`.
- **Faux positifs du pré-filtre** : le filtre à mots-clés peut encore laisser passer des offres non-tech contenant des termes génériques ("stage", "intern", "data"). L'appel API Claude corrige ensuite — c'est le compromis voulu, un faux positif coûte un appel Haiku alors qu'un faux négatif perd une offre.
- **Faux négatifs du pré-filtre** : offres tech dans des langues non couvertes (néerlandais, allemand sans termes anglais) peuvent être écartées avant l'appel API. Quelques termes NL/DE sont couverts (`digitaal`, `coderen`, `informatica`, `werkstudent`).
- **Cache TTL 5 min (mode séquentiel uniquement)** : si le filtrage séquentiel dure plus de 5 minutes sans appel API, le cache expire. Non applicable en mode batch (défaut).
- **Dédup fuzzy** : la détection de doublons inter-sources (entreprise identique + titre ≈ identique via SequenceMatcher ≥ 0.9) peut rater des doublons si l'entreprise est orthographiée différemment selon les boards.
- **La dédup automatique ne peut pas tout attraper — cas Farmless, d'où le marquage manuel** (`jobsearch/duplicates.py`, touche `m` dans la TUI, `--mark-duplicate`/`--unmark-duplicate`) : l'offre `c52e7b97` (« Software Engineering Intern (Full-Stack) », LinkedIn, candidature envoyée puis entretien puis refusée) est ressortie sous `dcd4359a` via WTTJ (« Fullstack Software Engineering Intern ») avec une similarité mesurée de **0.392**, très loin du seuil de 0.9 — impossible à rattraper en baissant le seuil. Pire : Farmless publie aussi un « Software Engineering Intern (Rust) », poste réellement différent, dont le recouvrement de mots avec l'offre WTTJ est **exactement le même (0.667)** que le vrai doublon. Aucun seuil ne peut départager ces deux cas : il faut comprendre que « Fullstack » ≠ « Rust », donc du sens, pas de la comparaison de chaînes. D'où le principe « la machine suggère, l'utilisateur confirme » : l'indicateur `?` de la colonne `✓` signale une entreprise déjà candidatée (pas forcément un doublon — la plupart ne le sont pas), la touche `m` ouvre une modale listant les candidatures de cette entreprise (celles-ci en tête) pour confirmer en une pression. Le marquage écrit `duplicate_of` (id de l'offre candidatée) dans le JSON de l'offre doublon ; celle-ci disparaît alors du bucket « pertinent »/« à candidater » du rapport (`jobsearch/report.py`) et suit la touche `h` (masquage) comme une offre déjà candidatée.

### Génération
- **Lettre PDF** : rendue via `jobsearch/letter_pdf.py` + `config/templates/cover_letter.html`. Le parser (`split_letter`) extrait salutation/closing de la lettre Sonnet (évite les doublons) et retire les artefacts de traduction en fin de lettre (ex. « Translated with DeepL.com… ») avant de chercher le mot de clôture. La ligne de séparation de l'en-tête dépend de `personal.accent_enabled` (`cv_data.yaml`, vert si activé) — placeholders `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}` à garder sur une seule ligne dans le template (un reformatage d'éditeur qui les casse sur plusieurs lignes rend le remplacement silencieusement inopérant → CSS invalide → plus de ligne du tout). `render_letter_pdf()` (nouvelle fonction, à utiliser à la place de `render_letter_html`+`write_letter_pdf`) essaie automatiquement 3 layouts de signature via un rendu d'essai WeasyPrint (comptage de pages) pour ne jamais déborder sur une 2e page à cause d'elle : empilée sous le nom (défaut) → compacte à côté du nom (`signoff-inline`) → sans signature. La signature `files/letter/signature.png` est insérée si présente. `--letter-pdf` permet le rendu à la demande hors pipeline.
- **cv_master.md est généré automatiquement** depuis `cv_data.yaml` (via `jobsearch/cv_sync.py`, à chaque `--generate` et via `files/cv/generate_cv.py`). Ne pas l'éditer à la main — modifier `cv_data.yaml`.
- **Langue du CV** : le template `cv_en.html` est en anglais uniquement. Pour un CV en français, il faudrait un second template avec les labels traduits.
- **WeasyPrint** : requiert des dépendances système (Pango, Cairo). Sur macOS : `brew install pango` si pas déjà installé.
- **Polices locales** : DM Sans / DM Serif sont embarquées dans `config/templates/fonts/` — la génération PDF fonctionne hors-ligne. Le preview HTML (`cv_adapte.html`) référence ces polices en chemin relatif ; ouvert hors du dossier templates, il retombe sur les polices système (le PDF, lui, est correct).

### Mode manuel
- `--from-url` échoue sur les sites avec protection JS (CloudFront, Cloudflare). Utiliser `--from-file` dans ce cas.
- L'extraction automatique du titre/entreprise/lieu peut être imprécise sur des pages très obfusquées — utiliser `--title` et `--company` pour surcharger.

### Suivi / historique des statuts
- **`ensure_history()` doit être appelée AVANT toute mutation de `app`** (`jobsearch/tracker.py`). Elle déduit l'événement de réponse depuis `status` + `responded_at` : l'appeler après avoir écrit le nouveau statut réécrit le passé avec celui-ci. Le bug réel : un passage `interview` → `rejected` produisait `[generated, sent, rejected]` et effaçait purement et simplement l'étape « entretien » (la garde anti-doublon voyait alors un dernier événement déjà au bon statut et n'ajoutait rien). Même piège dans `record_generated` avec `generated_at`.
- **Historique reconstruit ≠ dates réelles** : pour les candidatures antérieures à l'introduction de `history`, les événements sont déduits des anciennes dates ponctuelles et marqués `inferred` (affichés avec un `~`). Ils datent du moment où le statut a été *changé*, pas de l'événement lui-même (le jour où l'on marque « entretien », pas le jour de l'entretien).
- **Sankey** : `jobsearch/flow.py` n'utilise que la stdlib (SVG généré par concaténation de chaînes) — ne pas y introduire de dépendance graphique. Le `<svg>` produit n'a volontairement pas de `xmlns` : valide dans un document HTML5, mais le bloc n'est donc pas extractible tel quel en fichier `.svg` autonome.

### Rendu terminal (TUI)
- **Échapper les crochets littéraux dans le balisage Rich** : `"[f]"` dans une chaîne passée à un widget est interprété comme une balise de style inconnue et **avalé silencieusement**. Écrire `"\\[f]"`. C'est ce qui faisait disparaître le raccourci de la ligne « exporter le diagramme de Sankey ».
- **Alignement en colonnes et émoji** : `f"{label:<40}"` pade en *caractères*, pas en cellules d'affichage — un émoji large compte 1 pour `len()` mais occupe 2 colonnes. Utiliser `_pad()`/`_visible_width()` (`jobsearch/tui.py`, basés sur `unicodedata.east_asian_width`). Corollaire : garder des émoji de largeur `W` dans `_STATUS_LABELS` (🎙 était `Neutral`, d'où le décalage d'une cellule de la ligne « entretien » — remplacé par 🎤).
- **Curseur préservé** : toute reconstruction de table (`clear()` + `add_row()`) ramène le curseur en haut. Passer par le gestionnaire de contexte `_keep_cursor(table_id)` — appliqué aux quatre tables (Offres, Génération, Candidatures, Suivi).

---

## Variables d'environnement (.env)

```
ANTHROPIC_API_KEY=sk-ant-...      # Requis pour filtre et génération
ADZUNA_APP_ID=...                  # Optionnel : API Adzuna (gratuit, developer.adzuna.com)
ADZUNA_APP_KEY=...
NOTIFY_EMAIL=...                   # Optionnel : email de notification après scrape
SMTP_HOST=smtp.gmail.com           # Optionnel : config SMTP pour les notifications
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...                  # App Password Gmail (16 caractères)
```

---

## Environnement Python

```bash
venvinit    # Créer le venv (une seule fois)
venvon      # Activer le venv
venvoff     # Désactiver

pip install -r requirements.txt
```

Toutes les commandes Python/pip doivent passer par le venv (`venv/`).
