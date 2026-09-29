# JobSearch — Pipeline automatisé de recherche d'emploi — Documentation

Brain: JobSearch

## Vue d'ensemble

JobSearch est un pipeline Python générique et configurable de recherche d'emploi (stage, alternance, CDI…), utilisable pour n'importe quel secteur — pas seulement l'informatique : collecte des offres depuis des flux RSS et APIs, les filtre avec Claude Haiku (scoring 0-10) selon un sujet et un secteur configurables, génère un rapport Markdown, puis produit une lettre de motivation et un CV PDF adapté via Claude Sonnet pour les offres retenues. `jobsearch --setup` guide le choix du sujet (type de contrat, durée, géo) et du secteur (preset de mots-clés du pré-filtre parmi informatique/santé/droit/commerce/communication/ingénierie/enseignement, ou saisie libre) sans jamais toucher au code Python — voir « Changer de sujet de recherche » plus bas.

**Instance de référence de ce dépôt** : Nathan Albalat — Ingénieur INFO5 Polytech Grenoble, cherche un stage de 22 semaines minimum à l'étranger (priorité Pays-Bas / Europe), dispo février 2027, secteur informatique. C'est la config *personnelle* de ce déploiement (`~/.jobsearch/`), pas le positionnement générique de l'outil décrit ci-dessus.

**Documentation utilisateur** : `README.md` (anglais, version de référence) et `README_FR.md` (français) — contenu identique, à maintenir en parallèle en cas de modification.

**Suivi de projet (ObsidianBrain)** : `/Users/nathan/Document/ObsidianBrain/JobSearch/` — `STATE.md` (état courant du projet) et `CHANGELOG.md` (journal daté des sessions). Mis à jour via `/wrapup`.

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
  sources.yaml           Sources RSS et APIs à scraper — généré par `jobsearch --setup` depuis le secteur/
                        pays choisis (voir jobsearch/resources/source_presets.yaml), ou copié tel quel
                        depuis jobsearch/resources/examples/sources.example.yaml par setup.sh (exemple
                        générique, un type de source par entrée, à adapter à la main dans ce cas)
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
  duplicates.py         Annotations manuelles portées par le JSON d'une offre : doublons (`duplicate_of`),
                        offres écartées (`invalid`, motif parmi `INVALID_REASONS`) et consignes de
                        génération (`gen_notes`, champ {notes_block}) — stdlib uniquement
  sources_toggle.py      Activation/désactivation individuelle d'une source (`sources.yaml`, clé `enabled`) —
                        édition ligne à ligne (jamais `yaml.safe_dump`, préserve les commentaires du fichier),
                        même motif que `_set_scalar` dans `jobsearch/wizard.py` ; utilisé par `SourcesScreen`
                        (onglet Réglages de la TUI, ligne « Sources de scraping », `entrée`)
  migrate.py             `--migrate` : copie (jamais déplace) la config perso, les prompts/templates par défaut et les données du dépôt vers JOBSEARCH_HOME
  wizard.py              `--setup` : assistant d'installation Textual autonome (7 étapes, dont le choix d'un secteur/preset de mots-clés) — voir section dédiée ci-dessous
  resources/              [ressources du paquet, empaquetées via `importlib.resources` — défauts de référence]
    prompts/             Copie de référence des 3 prompts (mêmes fichiers que `config/prompts/` avant migration)
    templates/            Copie de référence des 2 templates HTML + fonts/*.ttf (polices DM Sans / DM Serif Display, non surchargeables)
    prefilter_presets.yaml  Jeux de mots-clés du pré-filtre par secteur, proposés à l'étape 4 de `jobsearch --setup` — pas dans `config/`, jamais lu depuis `JOBSEARCH_HOME` (contenu du paquet, pas config personnelle)
    source_presets.yaml     Catalogue de sources par secteur (requêtes de recherche `queries_en`/`queries_fr`,
                            flux à URL fixe `feeds`, table géographique `geo` : coordonnées WTTJ, code Adzuna,
                            `country_indeed` de jobspy) utilisé par `render_sources_yaml` (jobsearch/wizard.py)
                            pour générer `sources.yaml` à l'étape 4 de `jobsearch --setup`, sans nouvelle
                            question posée (dérivé du secteur choisi à cette étape et du type de contrat/pays
                            choisis à l'étape 3) — mêmes 7 secteurs que prefilter_presets.yaml, mêmes clés
    examples/                Les six `.example` (search.example.yaml, profile.example.yaml,
                            sources.example.yaml, cv_data.example.yaml, cv_master.example.md, .env.example) —
                            contenu de paquet (jamais lus depuis `JOBSEARCH_HOME`), résolus via
                            `Paths.example(name)` (jobsearch/config.py, même mécanique `importlib.resources`
                            que `package_prompts_dir`/`package_templates_dir`, mais sans repli : un `.example`
                            manquant est une anomalie d'installation). Vivaient auparavant à la racine du
                            dépôt (`config/*.example.yaml`, `cv_master/*.example*`, `.env.example`) — hors de
                            `jobsearch/`, donc jamais embarqués par setuptools sur une installation pipx :
                            `jobsearch --setup` écrivait un `search.yaml` dégradé (repli en dur, commentaires
                            perdus) et sautait `cv_data.yaml`/`cv_master.md` en silence, ce qui bouclait
                            `_check_setup` indéfiniment juste après un `--setup` pourtant complet

scripts/
  test_api.py            Smoke-test de la clé API Anthropic (hors paquet jobsearch/)

pyproject.toml            Métadonnées du paquet + entry point (`jobsearch = "jobsearch.cli:main"`)
LICENSE                   MIT — sans elle, le défaut légal serait « tous droits réservés »
setup.sh                  Amorçage : copie les `.example` de `jobsearch/resources/examples/` vers JOBSEARCH_HOME

data/                     [JOBSEARCH_HOME/data/]
  offers/               Un fichier JSON par offre (avec filter_result après filtrage ; champs optionnels
                        `duplicate_of` : id de l'offre candidatée dont celle-ci est un doublon manuel ;
                        `invalid` : `{"reason", "at"}` — offre écartée manuellement (fermée/annulée/
                        mauvais type de contrat/autre), posée via la touche `X` de l'onglet Offres ou
                        `--mark-invalid` ; `gen_notes` : consigne de génération ponctuelle, injectée dans
                        les prompts lettre et CV lors du prochain `g`/`L` (touche `N` dans l'onglet
                        Génération de la TUI) — les trois posés/retirés via `jobsearch/duplicates.py`)
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

# Consigne ponctuelle injectée dans les prompts lettre et CV (prime sur gen_notes de l'offre)
jobsearch --generate <offer_id> --notes "Insiste sur le projet X"

# Enregistrer une offre dans le suivi sans générer ni lettre ni CV (aucun appel API)
jobsearch --record <offer_id>

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
jobsearch --applications --json          # même sérialisation que l'API (jobsearch/serialize.py)

# Serveur HTTP : API JSON + vue web mobile (extra `jobsearch[server]`)
jobsearch --serve                        # 127.0.0.1:8000, lecture seule
jobsearch --serve --host 100.x.y.z --allow-write   # adresse Tailscale, écriture autorisée

# Serveur MCP : 4 outils pour un client MCP (Claude Code, app Claude) — extra `jobsearch[mcp]`
jobsearch --mcp                          # transport stdio, lecture seule
jobsearch --mcp --allow-write            # autorise aussi mark_application_status

jobsearch --mark <offer_id> sent --note "Candidature envoyée via le site"

# Migration config perso + données du dépôt vers JOBSEARCH_HOME (copie uniquement, jamais destructif)
jobsearch --migrate            # aperçu à blanc, n'écrit rien
jobsearch --migrate --apply    # copie réelle, vérifie fichiers + sommes de contrôle après coup

# Marquer une offre comme doublon manuel d'une candidature existante (voir section dédiée ci-dessous)
jobsearch --mark-duplicate <offer_id> <target_id>
jobsearch --unmark-duplicate <offer_id>

# Écarter une offre invalide (fermée, annulée, mauvais type de contrat...) du bucket "à candidater"
jobsearch --mark-invalid <offer_id> --reason closed|cancelled|contract|other
jobsearch --unmark-invalid <offer_id>

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

Cette installation de base fonctionne sur n'importe quel Python récent (3.11+, y compris 3.12/3.13/3.14).
`python-jobspy` (LinkedIn/Indeed) est une **dépendance optionnelle** (`jobsearch[jobspy]`), pas dans les
dépendances de base :

```bash
pipx install "jobsearch[jobspy] @ git+https://github.com/<toi>/jobsearch" --python python3.11
# ou, en développement local :
pip install -e '.[jobspy]'   # dans un venv Python 3.11
```

**Piège — version de Python imposée par `python-jobspy`.** Cette dépendance épingle `NUMPY==1.26.3`,
qui n'a de wheels que jusqu'à Python 3.11. Or `pipx` utilise par défaut le Python le plus récent de la
machine (3.14 chez l'auteur) : embarquer `python-jobspy` dans les dépendances de base ferait donc échouer
l'installation en tentant de **compiler** numpy, sur une erreur `ModuleNotFoundError: No module named
'genapi'` qui ne mentionne ni jobspy ni la version de Python — indiagnosticable pour un utilisateur
lambda. D'où l'extra optionnel : l'installation de base reste légère et compatible avec les Python
récents, seuls LinkedIn/Indeed exigeant l'extra et un Python ≤ 3.11. `fetch_jobspy`
(`jobsearch/scraper_jobspy.py`) intercepte l'`ImportError` et ignore la source avec un message
explicatif (pas une erreur — le reste du scrape continue), indiquant la commande d'installation correcte
selon le mode (pipx/venv local) et la contrainte de version.

Premier lancement : `jobsearch --scrape` (ou toute autre commande) détecte l'absence de config dans
`JOBSEARCH_HOME` (`~/.jobsearch/` par défaut) et propose de lancer `setup.sh`, qui copie les fichiers
`.example` du paquet vers `JOBSEARCH_HOME`. Une installation antérieure avec des données dans le dépôt
(avant packaging) se migre avec `jobsearch --migrate` (aperçu) puis `jobsearch --migrate --apply`.

---

## Interface TUI (`jobsearch --tui`)

Vue interactive sur les fichiers du pipeline — **les fichiers restent la source de vérité** : les rapports markdown, `data/offers/*.json` et `data/applications.json` restent lisibles et utilisables sans la TUI, qui ne persiste quasiment aucun état propre (seule exception : la file de génération de l'onglet Génération, dans `data/gen_queue.json`). On peut donc reprendre le workflow n'importe quand (scrape lundi, revue mercredi) : la TUI relit l'état courant au lancement et sur `r`.

6 onglets : **Offres** (tableau triable par score, panneau de détail, cocher pour ajouter à la file de génération — lignes cochées surlignées en vert, offres de la dernière session de scrape surlignées en bleu foncé, filtrage par période avec `d`/`D` ; colonne `✓` en cascade à 5 états : `▶` cochée > `⛔` invalide (marquée manuellement, motif au choix — offre fermée/annulée/mauvais type de contrat/autre) > `⧉` doublon manuel confirmé > `✓` candidatée > `?` doublon suspecté — entreprise déjà candidatée, non tranché — touche `m` pour marquer/démarquer un doublon, `X` pour marquer/démarquer invalide (retire aussi l'offre de la file de génération ; refusé si l'offre a déjà une candidature — utiliser le statut « annulée » de l'onglet Suivi dans ce cas), suit `h` comme une offre candidatée), **Génération** (file persistée entre deux lancements de la TUI dans `data/gen_queue.json` ; cocher des lignes avec `espace` restreint `g`/`L` à ce sous-ensemble — sinon toutes les offres en attente sont traitées ; `x` retire des lignes de la file ; lance lettre + CV en tâche de fond, ou lettre seule avec `L` ; `N` édite une consigne de génération par offre (`gen_notes`, colonne « Note »), injectée dans les prompts lettre et CV au prochain `g`/`L` ; `T` enregistre directement dans le suivi sans rien générer — aucun appel API, aucun dossier créé — pour les offres qui n'ont besoin ni de lettre ni de CV, ex. formulaire en ligne ; `o` ouvre l'offre dans le navigateur, `O` ouvre le dossier de la candidature générée), **Candidatures** (liste `output/candidatures/`, aperçu lettre/CV, régénération des PDF après retouche manuelle des fichiers — candidatures envoyées ou plus surlignées en vert foncé ; `o` ouvre l'offre dans le navigateur, `O` ouvre le dossier de la candidature), **Suivi** (statuts + notes de `applications.json` — envoyée surlignée en jaune, réponse reçue en ambre, entretien en violet, acceptée en vert, refusée en rouge, annulée en gris foncé et texte atténué ; panneau de droite : timeline de la candidature sous le curseur, reconstruite depuis `history` — écart en jours entre chaque événement, dates déduites suffixées d'un `~` quand l'historique est reconstitué depuis les anciennes dates ponctuelles), **Stats** (rendu texte : métriques de retour — taux d'acceptation/réponse, délai moyen —, répartition des statuts, localisation des stages, histogramme des candidatures au cours du temps ; les candidatures annulées sont exclues des taux et du délai moyen, comptées à part dans une catégorie « Annulé » ; parcours des candidatures sous forme d'arbre de statuts (`jobsearch/flow.py`) et répartition mensuelle des statuts atteints — `f` exporte le diagramme de Sankey correspondant en HTML autonome, `output/stats/flux.html`, ouvert dans le navigateur), **Réglages** (table listant les fichiers de configuration éditables — `.env`, `search.yaml`, `profile.yaml`, `sources.yaml`, les 3 prompts, les 2 templates HTML, `cv_data.yaml`, plus une ligne dédiée « Sources de scraping » — avec rôle, état [personnalisé / défaut du paquet / absent] et date de modification ; `entrée` sur une ligne de fichier l'ouvre dans l'éditeur, en copiant d'abord un prompt/template encore au défaut du paquet vers la config utilisateur avant édition ; `entrée` sur la ligne « Sources de scraping » ouvre une modale (`SourcesScreen`) listant individuellement les sources de `sources.yaml` — état actif/inactif, nom, type, pays, groupe pour les sources optionnelles — `espace` bascule la source sous le curseur avec écriture immédiate sur disque via `jobsearch/sources_toggle.py`, `échap` ferme ; `r` recharge ensuite `search.yaml` et invalide le cache de `get_search()`).

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
| Offres | `h` | Masquer/afficher les offres déjà candidatées, les doublons et les invalides marqués |
| Offres | `d` | Cycler la période affichée (tout · session · 7j · 30j) |
| Offres | `D` | Choisir une session de scrape précise |
| Offres | `g` | Aller à l'onglet Génération |
| Offres | `m` | Marquer/démarquer l'offre comme doublon d'une candidature existante (modale de sélection ; la machine suggère via l'entreprise, l'utilisateur confirme) |
| Offres | `X` | Marquer/démarquer l'offre comme invalide (modale de choix du motif : fermée/annulée/mauvais type de contrat/autre ; retire aussi l'offre de la file de génération ; refusé si l'offre a déjà une candidature) |
| Génération | `espace` | Cocher/décocher une offre de la file (filtre optionnel pour `g`/`L`) |
| Génération | `g` | Générer lettre + CV (lignes cochées, sinon toutes les offres en attente) |
| Génération | `L` | Générer la lettre seule (sans CV adapté — 1 appel Sonnet au lieu de 2) |
| Génération | `A` | Ajouter une offre manuelle (fichier HTML ou URL) à la liste, puis `g`/`L` |
| Génération | `x` | Retirer de la file (lignes cochées, sinon la ligne sous le curseur) |
| Génération | `N` | Éditer la note de génération de l'offre sous le curseur (`gen_notes`, injectée dans les prompts lettre et CV au prochain `g`/`L`) |
| Génération | `T` | Enregistrer dans le suivi SANS générer (aucun appel API, aucun dossier — offres qui n'ont besoin ni de lettre ni de CV) |
| Génération | `o` | Ouvrir l'offre dans le navigateur |
| Génération | `O` | Ouvrir le dossier de la candidature générée |
| Candidatures | `entrée` | Basculer l'aperçu lettre ↔ CV |
| Candidatures | `e` | Ouvrir lettre.md dans l'éditeur |
| Candidatures | `p` | Régénérer lettre.pdf depuis lettre.md |
| Candidatures | `u` | Régénérer cv_adapte.pdf depuis cv_adapte.html |
| Candidatures | `o` | Ouvrir l'offre dans le navigateur |
| Candidatures | `O` | Ouvrir le dossier de la candidature |
| Suivi | `s`/`a`/`i`/`j`/`v`/`z` | Statut → sent / answered / interview / rejected / accepted / cancelled |
| Suivi | `n` | Éditer la note |
| Suivi | `o` | Ouvrir l'offre dans le navigateur |
| Stats | `f` | Exporter le diagramme de Sankey du flux de statuts (HTML, ouvert dans le navigateur) |
| Réglages | `entrée` | Sur une ligne de fichier : l'ouvrir dans l'éditeur (`$VISUAL`/`$EDITOR` sinon `open`/`xdg-open`/`start`). Sur la ligne « Sources de scraping » : ouvrir la modale de gestion des sources (`SourcesScreen`) — activer/désactiver individuellement (`espace`), `échap` pour fermer |

---

## Accès distant : API HTTP et vue web (`jobsearch --serve`)

Extra optionnel `jobsearch[server]` (FastAPI + uvicorn), même motif que `[jobspy]` : l'installation de base reste légère.

- **`jobsearch/serialize.py`** (stdlib) — couche de sérialisation partagée entre `--applications --json`, l'API HTTP et le serveur MCP (`jobsearch/mcp_server.py`, voir plus bas) : `application_dict` (passe par `tracker.ensure_history`, jamais réimplémentée), `offer_dict`, `dossier_dict` et `offer_id_from_folder` (ce dernier factorise le motif `basename(f).rsplit("_", 1)[-1]`, auparavant dupliqué dans `jobsearch/tui.py`).
- **`dossier_dict`** est la ressource clé pour un agent : elle joint en un seul objet la candidature, l'offre et la lettre déjà produite. Sans elle, répondre à « écris-moi un mail à propos de la candidature X » exigerait de recoller trois sources.
- **`jobsearch/server.py`** — routes `/api/applications[/{id}]`, `/api/offers[/{id}]`, `/api/stats`, `/health`, et les pages web `/`, `/a/{id}`, `/offers`, `/stats`. **N'importe jamais `tui.py` (Textual) ni `generator.py` (Anthropic)** : le serveur doit démarrer vite et sans ces dépendances.
- **Les notes libres portent l'information qui n'a pas de champ** — échéances, dates d'entretien, noms de recruteurs. C'est un choix délibéré : on ne peut pas anticiper la liste des champs utiles, et un modèle extrait très bien « entretien le 07/08 » d'une phrase. Deux conséquences dans le code : le paramètre `q` de `/api/applications` **cherche aussi dans les notes** (sans quoi une question dont le critère y vit obligerait l'agent à rapatrier toutes les candidatures), et la note est éditable depuis la vue web (`POST /a/{id}/note`) — une échéance se repère en lisant un mail sur son téléphone, pas devant un terminal. Ne pas confondre `notes` (candidature, suivi) et `gen_notes` (offre, consigne de génération).
- **`tracker.set_note`** est le chemin d'écriture unique des notes : il **remplace** (édition), là où `mark_status(..., note=...)` **ajoute** au fil des changements de statut. La TUI (touche `n`) et la vue web passent toutes deux par lui.
- **Pagination obligatoire** : `limit` par défaut à 50, plafond dur à 200. Avec ~5400 offres et 44 Mo sur disque, aucun endpoint ne doit pouvoir tout renvoyer — c'est la contrainte n°1 face à un consommateur LLM.
- Les couleurs de statut des pages viennent de `tracker.STATUS_COLORS`, source unique déjà partagée avec la TUI et le SVG. La page `/stats` embarque `flow.render_sankey_svg`.
- Les templates vivent dans `jobsearch/resources/web/*.html` et **doivent rester déclarés dans `[tool.setuptools.package-data]`** — sinon elles disparaissent en installation pipx, exactement comme les `.example` (voir « Premier lancement (CLI) »).
- **Tri et filtrage — toujours côté serveur, jamais en JavaScript** (contrainte de rendu, page lue sur iPhone Safari) : `_sort_items`/`_APP_SORT_KEYS`/`_OFFER_SORT_KEYS`/`_scrape_sessions` (`jobsearch/server.py`) sont la source unique utilisée à la fois par l'API JSON et les pages HTML, pour rester cohérentes. `GET /api/applications` et `GET /` acceptent `sort` (`generated_at`/`company`/`status`) et `dir` (`asc`/`desc`) ; `GET /api/offers` et `GET /offers` acceptent en plus `sort=score|date_scraped|company` et `session=YYYY-MM-DD` (filtre sur `date_scraped`, sessions listées par `_scrape_sessions` — même calcul que `JobSearchApp._scrape_sessions` de `jobsearch/tui.py`, réimplémenté ici plutôt qu'importé pour ne jamais tirer Textual dans `server.py`). Les pages `index.html`/`offers.html` exposent ces paramètres via un `<select>` + bouton (pas d'`onchange` JS, un vrai `<button type="submit">` — le tri doit fonctionner même sans JS).
- **Changement de statut depuis `dossier.html`** : un `<select>` (une option par statut de `tracker.STATUSES`) + bouton « Enregistrer », POST sur la même route `/a/{id}/status` qu'avant (six boutons remplacés par un menu déroulant) — mêmes garde-fous (cookie, `--allow-write`), même passage unique par `tracker.mark_status`.
- **Page `/stats` affiche désormais l'intégralité de `tracker.compute_stats`** : `by_status`, `coarse` (regroupement acceptée/refusée/annulée/en attente), `rates` (envoyées, réponses, taux de réponse, taux d'acceptation, délai moyen et nombre de mesures), `by_location`, `by_month`, en plus du flux Sankey déjà présent — rien n'est omis, tout était déjà calculé côté `tracker.py`, l'affichage était simplement en retard sur le calcul.

### Extras manquants — message actionnable, jamais un ModuleNotFoundError brut

`pipx install jobsearch` n'embarque **aucun** extra : `--serve` et `--mcp` échouaient sur un `ModuleNotFoundError: No module named 'mcp'` que rien ne permettait de relier à `pipx inject`. `_missing_extra` (`jobsearch/cli.py`) affiche désormais la commande exacte pour les deux modes d'installation — même intention que `fetch_jobspy` pour python-jobspy.

**Piège** : `mcp_server.py` importe le SDK **à l'intérieur de `build_server`**, pas au niveau module. Le `try/except ImportError` doit donc englober l'**appel** et pas seulement l'import, sinon l'erreur surgit hors de sa portée et le message actionnable est contourné.

### Déclarer le serveur MCP à un client

Pour Claude Code, au périmètre utilisateur (disponible dans toutes les conversations, pas seulement ce dépôt) :

```bash
claude mcp add -s user jobsearch -- ~/.local/bin/jobsearch --mcp
claude mcp list                      # doit afficher « ✔ Connected »
```

Ajouter `--allow-write` à la fin pour autoriser les changements de statut depuis une conversation — absent par défaut, conformément au principe du reste du projet.

### Sécurité — ces règles sont le cœur de la fonctionnalité

- Écoute sur **`127.0.0.1` par défaut**.
- Jeton partagé `JOBSEARCH_API_TOKEN` dans `config/.env` : en-tête `X-API-Token` sur `/api/*`, cookie `HttpOnly` sur les pages. Le cookie est posé via `?token=...` à la première visite, puis **une redirection 303 retire le jeton de l'URL** — sans elle, le secret resterait dans l'historique du navigateur.
- **Refus de démarrer** (code retour 1) si l'écoute n'est pas sur la boucle locale et qu'aucun jeton n'est défini. Un échec bruyant au lancement vaut mieux qu'un suivi de candidatures ouvert sur le réseau.
- Écriture derrière `--allow-write`, absente par défaut (403 sinon). Seul `mark_status` est exposé, via `tracker.mark_status` — donc `ensure_history` est respectée.
- Aucune route ne prend un chemin de fichier : uniquement des `offer_id` validés.

### Déploiement

**Tailscale** : le Mac et l'iPhone rejoignent le même tailnet, le serveur écoute sur l'adresse Tailscale de la machine. Aucun port ouvert sur Internet, trafic chiffré de bout en bout.

**Limite à assumer** : la machine doit être allumée pour répondre. La séparation `JOBSEARCH_HOME` rend le déménagement vers un Raspberry Pi ou un petit VPS trivial si cette contrainte devient gênante.

### Service persistant (launchd, `jobsearch --service`)

Lancé à la main dans un terminal, `jobsearch --serve` ne survit ni à la fermeture de la session ni à un redémarrage — vécu en pratique : le serveur s'est arrêté au premier `Ctrl+C`/fermeture de fenêtre, laissant l'iPhone sans accès. `jobsearch/service.py` (stdlib uniquement — `plistlib`, `subprocess`, `os`, `shutil` — importable sans l'extra `[server]`, car ce module ne démarre jamais lui-même le serveur en process Python : il ne fait que fabriquer et piloter la commande `jobsearch --serve ...` que launchd invoquera en sous-processus) gère un agent launchd utilisateur, label `com.jobsearch.server`, plist dans `~/Library/LaunchAgents/com.jobsearch.server.plist`.

```bash
jobsearch --service install --host 100.x.y.z --port 8767 --allow-write   # écrit le plist + charge l'agent
jobsearch --service status     # plist présent ?, agent chargé ?, processus vivant ?, adresse d'écoute
jobsearch --service start | stop | restart
jobsearch --service logs       # fin du journal (JOBSEARCH_HOME/data/server.log)
jobsearch --service uninstall  # décharge puis supprime le plist
```

**Contenu du plist** (`build_plist`, `jobsearch/service.py`) :
- `ProgramArguments` : chemin absolu de l'exécutable `jobsearch` **résolu à l'installation** (`shutil.which`, jamais recalculé au démarrage — comportement attendu d'un agent launchd, la commande est figée dans le plist), suivi de `--serve --host <H> --port <P>` et `--allow-write` si demandé.
- `RunAtLoad` et `KeepAlive` à `true` — relance automatique si le processus meurt.
- `StandardOutPath`/`StandardErrorPath` vers `JOBSEARCH_HOME/data/server.log` — **c'est le point qui manquait avant cette fonctionnalité** : un service lancé à la main dans un terminal fermé ne laissait absolument aucune trace de son crash.
- `EnvironmentVariables.JOBSEARCH_HOME` **capturé à l'installation**, uniquement si `JOBSEARCH_HOME` diffère du défaut (`~/.jobsearch`) — sinon inutile de figer une variable que le process héritera de toute façon.
- `WorkingDirectory` : le home de l'utilisateur (répertoire stable, non lié au dépôt).

**`--service status` est la commande de diagnostic** (« ça ne marche plus ») : plist présent ou non, agent chargé (`launchctl print gui/$UID/<label>`) ou non, processus réellement vivant (`launchctl list <label>`, clé `PID`) ou non, et **sur quelle adresse il écoute** — lue directement dans le plist installé (la config réellement active), pas dans les arguments de la commande `status` elle-même.

**launchctl** : `install`/`start` utilisent `launchctl bootstrap gui/$UID <plist>` (forme moderne), avec repli sur `launchctl load -w` si `bootstrap` échoue (macOS ancien, ou agent déjà chargé autrement) ; `stop`/`uninstall` suivent le même patron avec `bootout`/`unload`. Codes de retour vérifiés à chaque appel, jamais de trace `subprocess` brute remontée à l'utilisateur — un message explicite à la place.

**Piège corrigé au passage : `run_server` passait `log_level="warning"` à uvicorn** (`jobsearch/server.py`), qui supprime à la fois la ligne de démarrage ET le journal d'accès (tous deux émis en `info`) — le fichier de journal du service restait vide en permanence, constaté à l'usage (0 octet même après une requête `/health`). `log_level` est désormais un paramètre de `run_server`/`jobsearch --serve --log-level` avec un défaut plus bavard (`info`) — un service qui tombe laisse maintenant une trace exploitable (`jobsearch --service logs`).

**Plateforme** : launchd est propre à macOS. `_is_macos()` (`jobsearch/service.py`) fait un premier test sur `sys.platform` — sur toute autre plateforme, chaque action de `--service` retourne un message explicatif pointant vers l'équivalent systemd `--user`, plutôt que d'échouer sur un `FileNotFoundError: launchctl` incompréhensible.

**`--service` est exempté de `_check_setup`** (même mécanisme que `--migrate`/`--setup`/`--help`, `jobsearch/cli.py`) : `--service status`/`logs` doivent pouvoir répondre même sur un `JOBSEARCH_HOME` pas encore configuré — c'est une commande de diagnostic, pas une commande du pipeline.

### Serveur MCP (`jobsearch --mcp`)

Extra optionnel `jobsearch[mcp]` (paquet `mcp`), même motif que `[jobspy]`/`[server]`. **`jobsearch/mcp_server.py`** appelle directement les fonctions Python de `tracker`/`report`/`duplicates`/`serialize` (pas l'API HTTP — pas de dépendance à un serveur qui tourne) et respecte la même règle structurante que `server.py` : **n'importe jamais `tui.py` (Textual) ni `generator.py` (Anthropic)**.

**Exactement 4 outils**, volontairement peu nombreux — chaque outil déclaré coûte du contexte dans *toutes* les conversations du client MCP, y compris celles sans rapport avec la recherche d'emploi :

| Outil | Rôle |
|---|---|
| `list_applications` | filtres `q`/`status`/`company`/`since`/`limit` — `q` cherche AUSSI dans les notes |
| `get_application` | le dossier joint : candidature + offre + lettre déjà envoyée (`dossier_dict`) |
| `search_offers` | `q`/`min_score`/`since`/`limit` ; exclut candidatées/doublons/invalides |
| `mark_application_status` | via `tracker.mark_status` (donc `ensure_history` respectée) ; refusé sans `--allow-write` |

**Le schéma d'usage attendu est en deux temps** : restreindre côté serveur (`list_applications(q=...)`), **puis** lire le petit sous-ensemble obtenu — jamais rapatrier toutes les candidatures. Les descriptions des 4 outils insistent explicitement là-dessus, et rappellent que **les échéances, dates d'entretien et noms de recruteurs vivent dans le champ de notes en texte libre**, pas dans des champs dédiés (même remarque que pour `/api/applications?q=` ci-dessus) — sans cette indication dans la description, un modèle conclut que l'information n'existe pas plutôt que de filtrer puis lire.

- `jobsearch --mcp` : transport **stdio** (cas principal — Claude Code, app Claude sur la même machine). Exemple de config client :
  ```json
  {"mcpServers": {"jobsearch": {"command": "jobsearch", "args": ["--mcp"]}}}
  ```
- `jobsearch --mcp --mcp-transport streamable-http --host H --port P` : agent distant. **Même garde-fou que `--serve`** (refus de démarrer sur une adresse non-loopback sans `JOBSEARCH_API_TOKEN`), mais **le SDK `mcp` 2.2.0 n'offre pas d'authentification par jeton partagé pour ce transport** (seul un `TokenVerifier` OAuth complet est prévu) — le jeton n'authentifie donc pas réellement les requêtes ici, contrairement à `--serve`. À n'exposer que derrière Tailscale, jamais nu sur Internet — documenté dans la docstring de `mcp_server.py` plutôt que de bricoler une pseudo-authentification.
- `--mcp` n'est **pas** exempté de `_check_setup` (contrairement à `--migrate`/`--setup`/`--help`) : il a besoin de la config comme toute autre commande.
- Pagination : mêmes bornes que l'API HTTP (`DEFAULT_LIMIT`/`MAX_LIMIT`, dupliquées dans `mcp_server.py` plutôt qu'importées de `server.py`, pour ne pas tirer FastAPI dans l'extra `[mcp]`).

---

## Alerte de mise à jour (`jobsearch/updates.py`)

Module **stdlib uniquement** (`urllib.request`, `json`, `os`, `time`) — aucune dépendance nouvelle, importable sans les extras `[server]`/`[mcp]`. Compare `jobsearch.__version__` à la dernière release du dépôt public `Nathan-Albalat/JobSearch` (`GET https://api.github.com/repos/Nathan-Albalat/JobSearch/releases/latest`, champ `tag_name`), normalisée en tuple d'entiers pour la comparaison (`1.10` > `1.9`, jamais une comparaison de chaînes).

- **Piège `importlib.metadata` — ne jamais l'utiliser ici.** En installation éditable, les métadonnées de distribution sont figées au moment de l'installation et ne suivent pas les changements de version du code (mesuré : le paquet déclarait 1.2.0 pendant que `importlib.metadata.version("jobsearch")` répondait encore 0.1.0) — une vérification de mise à jour fondée là-dessus annoncerait une mise à jour en permanence. Seule source de vérité : `jobsearch.__version__`.
- **Deux temporisations distinctes, dans `data/update_check.json` (`JOBSEARCH_HOME`), qui ne servent PAS la même chose** : `checked_at` limite l'appel réseau GitHub à une fois toutes les 24h (`check_for_update`) — sans lui, chaque commande ralentirait au démarrage et l'API anonyme finirait par renvoyer 403 (rate limit) ; `notified_at` limite l'affichage du message en CLI à une fois toutes les 15 minutes (`cli_notice`) — sans lui, une session de plusieurs commandes rapprochées republierait le message en boucle même avec le cache réseau déjà à jour. Ne jamais fusionner les deux : le cache réseau peut rester valide 24h pendant que la notification CLI, elle, doit réapparaître bien plus souvent.
- **Ne jamais nuire** : toute exception (hors ligne, API indisponible, quota 403, JSON inattendu, cache corrompu, disque plein) est avalée à l'intérieur de `check_for_update`/`cli_notice`/`status_notice` — ces trois fonctions ne lèvent jamais, retournent `None` en silence. Délai réseau court (`_TIMEOUT`, 3s).
- **Trois points d'affichage**, chacun avec sa propre fonction adaptée à son rythme d'appel : `cli_notice()` (CLI, `jobsearch/cli.py`, en fin de `main()`, avec la temporisation 15 min) ; `status_notice()` (TUI, `jobsearch/tui.py`, bannière persistante `#update-banner` peuplée une fois à `on_mount`, et vue web, `jobsearch/server.py`/`base.html`, pied de page peuplé à chaque rendu — pas de temporisation 15 min dans ces deux cas : ce sont des interfaces déjà ouvertes, pas une sortie répétée à chaque commande).
- Message : version disponible, version actuelle, commande de mise à jour (`pipx upgrade jobsearch`).

---

## Emplacement des données (`JOBSEARCH_HOME`)

Le pipeline se lance depuis **n'importe quel répertoire**, et sépare deux racines résolues en absolu par `jobsearch/config.py` (`PATHS`) :

- **Ressources du paquet** (`jobsearch/resources/`, accédées via `importlib.resources` — donc trouvées quel que soit le mode d'installation, éditable ou figée) : les **défauts de référence** des prompts et templates, plus les **polices** (`fonts/*.ttf`, jamais surchargeables). `PATHS.package_prompts_dir`/`PATHS.package_templates_dir` s'y résolvent toujours.
- **`JOBSEARCH_HOME`** : la **configuration personnelle** (`config/search.yaml`, `config/profile.yaml`, `config/sources.yaml`, `config/.env`, `cv_master/cv_data.yaml`, `cv_master/cv_master.md`) et les **données** (`data/`, `output/`). Vaut par défaut `~/.jobsearch/` — surchargeable via la variable d'environnement `JOBSEARCH_HOME` (utile pour tester sur une copie isolée, ou repointer vers le dépôt avec `JOBSEARCH_HOME=.` pour un usage « tout-en-un » historique).
- **Prompts et templates HTML** (`config/prompts/*.txt`, `config/templates/*.html`) sont du **contenu utilisateur éditable** : `PATHS.prompt(name)`/`PATHS.template(name)` cherchent d'abord dans `JOBSEARCH_HOME/config/`, et **replient automatiquement sur les défauts du paquet** si absents — aucune action requise pour une installation existante qui n'a encore rien personnalisé.
- **Les six `.example`** (`search.example.yaml`, `profile.example.yaml`, `sources.example.yaml`, `cv_data.example.yaml`, `cv_master.example.md`, `.env.example`) sont eux aussi des ressources du paquet, jamais de `JOBSEARCH_HOME` — résolus via `PATHS.example(name)`, sur `jobsearch/resources/examples/`. Contrairement à `prompt()`/`template()`, **aucun repli** : un `.example` manquant est une anomalie d'installation (paquet mal packagé), pas un cas nominal — `render_search_yaml`/`render_env_file`/`_build_write_items` (`jobsearch/wizard.py`) lèvent une erreur explicite plutôt que de dégrader silencieusement `search.yaml` (commentaires perdus) ou de sauter l'écriture de `cv_data.yaml`/`cv_master.md`.

`jobsearch --applications` fonctionne aussi bien depuis `Stage/Script` que depuis `/tmp` : les chemins ne dépendent jamais du répertoire courant. Voir `python -m jobsearch.config` pour afficher les chemins effectifs.

**Migration depuis une installation où tout vivait dans le dépôt** (avant cette séparation) : `jobsearch --migrate` (mode à blanc, n'écrit rien, affiche ce qui serait copié) puis `jobsearch --migrate --apply` (copie réelle, `jobsearch/migrate.py`). Ne fait QUE des copies — ne déplace ni ne supprime jamais les originaux du dépôt ; refuse d'écraser un fichier déjà présent à destination ; vérifie après coup le nombre de fichiers et les sommes de contrôle. Suppression des originaux laissée à l'utilisateur.

**Garde-fou** : si `JOBSEARCH_HOME` est vide/absent alors que le dépôt contient encore des offres dans `data/offers/` (ancien emplacement, pas encore migré), `_check_setup` (`jobsearch/cli.py`) et `_warn_if_empty_home` (`jobsearch/tui.py`, appelé par `run_tui()`) affichent un message explicite pointant vers `--migrate` plutôt que de laisser croire à « 0 offre » / une perte de données.

## Assistant d'installation (`jobsearch --setup`)

App Textual autonome (`jobsearch/wizard.py`, distincte de `jobsearch/tui.py`) qui automatise le parcours que `setup.sh` se contentait de décrire (checklist de 5 fichiers à éditer à la main). Exemptée de `_check_setup` (même mécanisme que `--migrate`, `jobsearch/cli.py`), donc utilisable sur une racine `JOBSEARCH_HOME` vierge. Étapes pilotées via `push_screen_wait` dans un worker (`@work` async) :

1. **Présentation** — porte d'entrée `jobsearch --tui`, coût API (Haiku au filtrage, Sonnet à la génération).
2. **Clés API** — toutes optionnelles à l'exception de la clé Anthropic (recommandée). Couvre, dans un seul écran (`ApiKeyScreen`) : Anthropic (`Input(password=True)`, console.anthropic.com/settings/keys), Adzuna (`ADZUNA_APP_ID`/`ADZUNA_APP_KEY`, gratuit sur developer.adzuna.com) et les notifications SMTP (`NOTIFY_EMAIL`/`SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` — pour Gmail, mot de passe d'application sur myaccount.google.com/apppasswords, pas le mot de passe habituel). Toute clé laissée vide est sautée. Validation optionnelle de la clé Anthropic par un appel minimal, non facturé (`client.models.list(limit=1)`), demandée par confirmation explicite avant tout appel réseau. `render_env_file` génère `.env` depuis `.env.example` (commentaires conservés) avec les valeurs saisies — **toujours écrit à l'étape finale, même si toutes les clés ont été sautées** : sinon `_check_setup` continue de le réclamer juste après un `--setup` pourtant complet.
3. **Sujet de recherche** — type de contrat, durée applicable ou non, pays prioritaires, région/ville, date de disponibilité, domaines, seuil. Génère `search.yaml` en substituant ces valeurs directement dans le texte de `config/search.example.yaml` du dépôt (jamais `yaml.safe_dump`, qui détruirait ses ~200 lignes de commentaires) : `_set_scalar`/`_set_list` repèrent la section top-level (`sujet:`, `duree:`, `disponibilite:`, `geo:`, `domaines:`, `scoring:`) puis remplacent la valeur scalaire ou le bloc de liste correspondant — `_set_list` accepte aussi bien la forme bloc (`liste:` suivi d'items indentés) que la forme mapping/liste vide en ligne (`liste: []`, celle du `.example.yaml` générique). **`geo.notes`/`geo.details` (le bloc réellement injecté dans le barème du prompt via `{geo_details}`), `disponibilite.*` et `scoring.bonus`/`malus` ne sont jamais recopiés depuis `search.example.yaml` : ils sont régénérés en entier depuis les réponses de l'utilisateur** (`render_geo_notes`/`render_geo_details`/`parse_disponibilite`/`render_scoring_bonus`/`render_scoring_malus`, `jobsearch/wizard.py`) — sinon le `.example.yaml`, même neutralisé, resterait la source du barème effectif d'un tiers. **De même pour `sujet.label`, `sujet.type_contrat_negatif`, `scoring.titre_bonus` et `domaines.ouverture`** (`render_sujet_label`/`derive_type_contrat_negatif`/`derive_titre_bonus`/`render_domaines_ouverture`, `jobsearch/wizard.py`) : troisième occurrence du même bug (après `geo.details`/`geo.notes`/`scoring.bonus`/`scoring.malus`, puis le prompt lui-même) — un `type_contrat_negatif` laissé sur la valeur de l'exemple (« une offre d'emploi ») contredit un `type_contrat` = « CDI » dans la même phrase du prompt de scoring. `geo.secondaire`/`geo.dernier_recours`, eux, ne sont pas demandés par cet assistant (seule `geo.priorite` l'est) : laissés explicitement vides plutôt que recopiés de l'exemple, à affiner depuis l'onglet Réglages. Voir « Garde-fou anti-régression » plus bas.
4. **Pré-filtre** — choix d'un jeu de mots-clés pré-rempli par secteur (`jobsearch/resources/prefilter_presets.yaml` : informatique, santé, droit, commerce/vente, communication/marketing, ingénierie, enseignement — le preset informatique conserve les 83 mots-clés d'origine, mais n'est plus le défaut universel) ou saisie libre (`preset: "libre"`, mots-clés séparés par des virgules + un intitulé court de secteur). Numéro, nom du preset ou `libre` accepté en saisie (`PrefilterScreen._resolve_preset`). Alimente deux endroits de `search.yaml` : `prefilter.keywords` (`_set_prefilter_keywords`, remplace intégralement le sous-bloc — supporte la forme bloc et la forme `keywords: {}` en ligne) et `sujet.expertise_recruteur` (`render_expertise_recruteur`, gabarit `"spécialisé dans les {type_contrat} en/dans …"` propre à chaque preset). Le groupe `contrat` de `prefilter.keywords` n'est **jamais** dérivé du preset : il vient uniquement du type de contrat choisi à l'étape 3 (`derive_contrat_keywords` — mapping stage/alternance/cdi/cdd/freelance vers des mots-clés dédiés, repli sur le type de contrat tel quel sinon), pour rester indépendant du secteur. **Ce même secteur pilote aussi `sources.yaml`** (écrit à l'étape 7, sans écran dédié ni question supplémentaire) : `render_sources_yaml` (`jobsearch/wizard.py`) associe au secteur choisi ici ses requêtes de recherche métier (`jobsearch/resources/source_presets.yaml`, clés `queries_en`/`queries_fr` — combinées à l'écriture avec un terme dérivé du type de contrat, `derive_contrat_en`/`derive_contrat_fr_label`) et sa géographie (le pays prioritaire de l'étape 3, résolu en code ISO par `resolve_country_code`, qui réutilise les tables de `jobsearch/tracker.py` plutôt que d'en dupliquer une troisième) pour générer les sources jobspy/Adzuna/WTTJ pertinentes — jamais les requêtes ni la géographie de l'auteur d'origine (stage informatique, Pays-Bas). Une géographie non reconnue (pays inconnu de la table, ou non couvert par Adzuna) fait sauter la source correspondante plutôt que d'en générer une cassée — consigné dans l'en-tête du fichier écrit.
5. **Profil** — formulaire structuré (`config/profile.example.yaml` est plat, court, sans commentaires → seul fichier qui s'y prête), rendu via `yaml.safe_dump` (pas de commentaires à préserver ici). Compétences saisies en paires libres catégorie/détail dans un `TextArea` (une ligne `Catégorie: item1, item2` par catégorie, `ctrl+s` pour valider tout le formulaire — un `TextArea` ne se soumet pas sur `entrée`, contrairement aux `Input` des autres champs). Collecté **avant** l'aperçu du prompt (étape 6, juste après) : celui-ci substitue `{profile}` avec ces réponses.
6. **Aperçu du prompt de scoring — non sautable.** Part du défaut générique livré (`jobsearch/resources/prompts/filter.txt`, neutralisé — plus aucun vocabulaire informatique/géographique/scolaire en dur, tout passe par des placeholders déjà connus de `Search.placeholders()`) et y insère un bloc littéral « Repères issus de la configuration initiale » avec le type de contrat et les domaines choisis à l'étape 3. L'écran affiche cet aperçu **avec tous les placeholders `{xxx}` de `search.yaml` et `{profile}` substitués** (`render_filter_prompt_preview`, qui construit une `Search` en mémoire depuis les réponses de l'assistant — jamais depuis le disque — et appelle `fill_template`), séparé visuellement des instructions par une ligne de titre « Aperçu du prompt (paramétré) ». Seuls `{title}`/`{company}`/`{location}`/`{url}`/`{description}` restent littéraux dans l'aperçu (propres à chaque offre, jamais connus à l'avance). **Le fichier réellement écrit sur le disque, lui, conserve tous ses placeholders** — `render_filter_prompt` (sans suffixe `_preview`) reste inchangé et sert à la fois à l'aperçu (comme gabarit avant substitution) et à l'écriture finale ; ne jamais confondre les deux fonctions. Écrit dans `JOBSEARCH_HOME/config/prompts/filter.txt`. Échap à cette étape revient à l'étape profil (pas d'abandon de l'assistant).
7. **Récapitulatif** — fichiers écrits/ignorés (dont `sources.yaml`, généré à cette étape à partir des réponses des étapes 3-4, voir point 4 ci-dessus), ce qui reste à personnaliser plus tard (CV — écrit avec un contenu générique, voir ci-dessous —, templates HTML, clés API laissées vides), commande suivante (`jobsearch --scrape` ou `jobsearch --tui`).

**Aperçu puis confirmation** (motif repris de `jobsearch/migrate.py`) : `_write_with_confirm` n'écrit jamais par-dessus un fichier déjà présent sans un écran de confirmation dédié (`OverwriteConfirmScreen`) — relancer `--setup` sur une racine déjà configurée n'écrase donc rien silencieusement ; un refus d'écraser est consigné et affiché au récapitulatif.

**`cv_master/cv_data.yaml`/`cv_master.md`** : `_check_setup` (`jobsearch/cli.py`) les réclame avant de laisser lancer n'importe quelle commande, y compris `--tui`/`--scrape`, alors qu'ils ne servent qu'à `--generate`. Plutôt que retirer cette exigence (au risque d'un `--generate` qui échoue plus tard sans repère clair) ou ajouter une étape de saisie du CV à l'assistant (hors scope — `--setup` configure le sujet/scoring, pas le contenu du CV), `_build_write_items` les initialise systématiquement depuis `cv_master/cv_data.example.yaml`/`cv_master.example.md` du paquet — un CV générique, explicitement signalé comme tel dans `ConfirmWriteScreen` et `SummaryScreen`, à personnaliser ensuite (onglet Réglages, touche 6). Choisi plutôt que d'exempter `--tui`/`--scrape` de cette exigence dans `_check_setup` : un `JOBSEARCH_HOME` fraîchement configuré reste ainsi immédiatement complet pour les 4 commandes principales, sans état intermédiaire « configuré mais incomplet » à documenter séparément.

### Garde-fou anti-régression : valeurs d'auteur héritées de `search.example.yaml` / `sources.yaml` / des exemples de CV

Bug récurrent (5 occurrences à ce jour : `geo.details`/`geo.notes`/`scoring.bonus`/`scoring.malus`, puis le prompt de scoring lui-même, puis `sujet.label`/`sujet.type_contrat_negatif`/`scoring.titre_bonus`/`domaines.ouverture`, puis `sources.yaml` en entier, puis `source_defaults`/les exemples de CV) — une valeur censée être dérivée des réponses de `jobsearch --setup` reste, par erreur, égale à celle de l'exemple livré avec le paquet (calibré sur le sujet de l'auteur d'origine : stage tech, Pays-Bas). Sans garde-fou reproductible, traiter les cas un par un ne suffit pas — c'est le cinquième bug de la même famille qu'il s'agit d'empêcher.

4e occurrence — `sources.yaml` en entier : `config/sources.yaml` était versionné tel quel avec les requêtes personnelles de l'auteur, `search_term: "software intern"`/`location: "Netherlands"`/`k: "développeur informatique"`, et n'était jamais régénéré par `jobsearch --setup` : un juriste cherchant un CDI en France héritait de ces requêtes, son pré-filtre rejetait alors 100 % des offres ramenées — zéro offre, aucun message d'erreur.

5e occurrence — `source_defaults` (`search.yaml`) et les exemples de CV, la plus grave à ce jour : `source_defaults.jobspy`/`source_defaults.adzuna` continuait d'être recopié tel quel depuis `search.example.yaml` par `render_search_yaml` (`search_term: "software intern"`, `country_indeed: "netherlands"`, `country: "nl"`, `what: "internship software"`) — **première fuite de cette famille active dès le scrape** plutôt qu'au scoring : `get_search().source_defaults(...)` est appelé dans `jobsearch/scraper.py` (Adzuna, HelloWork, WTTJ) et `jobsearch/scraper_jobspy.py` (jobspy), et sert de repli silencieux pour toute source qui omet un paramètre optionnel dans `sources.yaml`. Corrigé en factorisant la dérivation secteur/géographie déjà utilisée par `render_sources_yaml` (`jobsearch.wizard._source_context`) dans une nouvelle fonction `render_source_defaults`, appelée par `render_search_yaml` via `_set_source_defaults` (remplacement en bloc du sous-arbre, motif de `_set_prefilter_keywords`) — clé laissée vide si la géographie/le secteur ne permettent pas de la dériver, jamais héritée de l'exemple. Dans le même audit : `jobsearch/resources/examples/cv_data.example.yaml` (`mobility: "Netherlands (priority) · Europe"`) et `jobsearch/resources/examples/cv_master.example.md` (paragraphe entier du profil biographique de l'auteur — établissement, spécialité, disponibilité, mobilité) contenaient encore des éléments personnels de l'auteur, non couverts par `--check-wizard-defaults` faute d'être dérivés d'une entrée de l'assistant (`--setup` ne collecte pas le contenu du CV, seuls ces deux fichiers d'exemple l'initialisent — voir plus haut). **Le pire des trois : `cv_master.example.md` alimente `{cv_summary}` du prompt de lettre de motivation** (`jobsearch/generator.py`) — la lettre d'un juriste aurait été rédigée à partir du profil biographique d'un étudiant en informatique tant que l'utilisateur n'a pas personnalisé son CV. Les deux fichiers ont été réécrits en CV générique (placeholders `[Votre École]`/`[Month Year]`/`[Skill 1]`…), neutre quant au secteur et à la biographie.

`python -m jobsearch.config --check-wizard-defaults` (gratuit, sans appel API) rend `search.yaml` **et** `sources.yaml` pour deux profils simulés volontairement très contrastés (le sujet d'origine du paquet, puis CDI / France / droit) via `jobsearch.wizard.render_search_yaml`/`render_sources_yaml`, et échoue si :
- l'une des clés de `jobsearch.wizard.RISK_KEYS` (chemins à 2 ou 3 niveaux — `sujet.label`, `sujet.type_contrat_negatif`, `scoring.titre_bonus`, `geo.secondaire`, `geo.dernier_recours`, `domaines.ouverture`, `source_defaults.jobspy.search_term`, `source_defaults.jobspy.country_indeed`, `source_defaults.adzuna.country`, `source_defaults.adzuna.what`) reste égale à la valeur littérale de `config/search.example.yaml` pour le profil CDI/France/droit (le plus éloigné du sujet d'origine — une coïncidence y est beaucoup moins probable que pour le profil « stage », où certaines valeurs dérivées recoupent légitimement celles de l'exemple), ou reste identique entre les deux profils contrastés malgré des entrées très différentes (hors `geo.secondaire`/`geo.dernier_recours`, volontairement toujours vides — non demandés par cet assistant, voir plus haut) ;
- le `sources.yaml` rendu pour le profil CDI/France/droit contient encore un littéral de l'auteur d'origine (`jobsearch.wizard._SOURCES_AUTHOR_LITERALS` : « software intern », « netherlands », « développeur informatique », « stage développeur »), ou si les requêtes de recherche générées (`search_term`/`what`/`job_title`/`k`, tous types de sources confondus) sont identiques entre les deux profils contrastés ;
- `jobsearch/resources/examples/cv_data.example.yaml` ou `.../cv_master.example.md` contient encore un littéral biographique de l'auteur (`jobsearch.wizard._CV_AUTHOR_LITERALS` : « pays-bas », « netherlands », « polytech », « info5 », « février 2027 », « 22 semaines ») — seule vérification de cette commande qui ne dépend d'aucun profil simulé, ces fichiers ne variant pas selon les réponses de l'assistant.

`jobsearch.wizard.check_wizard_defaults()` est une fonction pure (aucune dépendance Textual), testable indépendamment de l'app. **À chaque nouvelle clé dérivée ajoutée à `render_search_yaml`, l'ajouter à `RISK_KEYS`** (`_lookup_path` gère aussi bien les chemins à 2 niveaux que les 3 niveaux de `source_defaults.<source>.<clé>`) ; à chaque nouveau littéral d'auteur identifié dans une source de `render_sources_yaml`, l'ajouter à `_SOURCES_AUTHOR_LITERALS` ; à chaque nouvel élément biographique de l'auteur identifié dans les exemples de CV, l'ajouter à `_CV_AUTHOR_LITERALS` — pour que ce garde-fou les couvre aussi.

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
6. **`config/sources.yaml`** — `jobsearch --setup` le régénère entièrement à l'étape 4 (voir plus haut, section wizard) à partir du secteur et de la géographie choisis : dans la plupart des cas, relancer l'assistant suffit, rien à éditer à la main. Pour un pivot manuel sur un `sources.yaml` déjà existant (ou un secteur sans flux RSS connu dans `jobsearch/resources/source_presets.yaml`, à ajouter soi-même) : pivoter les entrées `jobspy`/`adzuna` (`search_term`/`what`, `location`/`country`) vers le nouveau métier/pays, et les entrées `wttj` — éditer `job_title`, `locations` (mapping `country`/`country_code`/`label`/`latitude`/`longitude`) et `contract_types` de chaque entrée — rien d'autre. Même logique pour `hellowork` (`k`/`c`, FR uniquement).

Vérification sans consommer de crédit API : `python -m jobsearch.config --check-prompts` (échoue si un placeholder reste inconnu dans les 3 prompts) et `python -m jobsearch.config --check-wizard-defaults` (échoue si une valeur dérivée de l'entrée utilisateur retombe silencieusement sur `search.example.yaml` ou `sources.yaml` contient encore un littéral de l'auteur — voir « Garde-fou anti-régression » ci-dessus).

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
- **Activation individuelle** : chaque entrée de `sources.yaml` accepte une clé `enabled` (absente ou `true` = active par défaut, `false` = désactivée) — indépendante du mécanisme `optional`/`group` ci-dessous (collectif, réservé aux sources FR-only). Testée dans `scrape_all` (`jobsearch/scraper.py`) à côté du test de groupe, jamais à sa place. Éditable directement dans le fichier, ou depuis l'onglet Réglages de la TUI (ligne « Sources de scraping », `entrée`, `SourcesScreen`) — écriture ligne à ligne (`jobsearch/sources_toggle.py`) qui préserve les commentaires du fichier, jamais `yaml.safe_dump`.
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
- **Offres mortes ou hors-sujet, marquage manuel `invalid`** (`jobsearch/duplicates.py`, touche `X` dans la TUI, `--mark-invalid`/`--unmark-invalid`) : contrairement à `duplicate_of`, qui exige une cible déjà candidatée, `mark_invalid` couvre le cas d'une offre écartée sans lien avec une autre candidature (offre fermée, annulée, ou dont le type de contrat ne correspond finalement pas). Écrit `{"reason", "at"}` sous la clé `invalid` de l'offre — un dict plutôt qu'un booléen pour rester extensible et dater le marquage. Refuse une offre ayant déjà une candidature (utiliser le statut « annulée », touche `z` de l'onglet Suivi, dans ce cas — c'est là qu'une candidature devenue caduque se suit, pas dans ce marquage). Marqueur `⛔` dans la colonne `✓` de l'onglet Offres (cascade : `▶` cochée > `⛔` invalide > `⧉` doublon > `✓` candidatée > `?` suspectée), retire l'offre de la file de génération si elle y était, suit `h` comme un doublon/une candidature, et sort du bucket « à candidater » du rapport (nouvelle section « Offres écartées », `jobsearch/report.py`).

### Génération
- **Lettre PDF** : rendue via `jobsearch/letter_pdf.py` + `config/templates/cover_letter.html`. Le parser (`split_letter`) extrait salutation/closing de la lettre Sonnet (évite les doublons) et retire les artefacts de traduction en fin de lettre (ex. « Translated with DeepL.com… ») avant de chercher le mot de clôture. La ligne de séparation de l'en-tête dépend de `personal.accent_enabled` (`cv_data.yaml`, vert si activé) — placeholders `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}` à garder sur une seule ligne dans le template (un reformatage d'éditeur qui les casse sur plusieurs lignes rend le remplacement silencieusement inopérant → CSS invalide → plus de ligne du tout). `render_letter_pdf()` (nouvelle fonction, à utiliser à la place de `render_letter_html`+`write_letter_pdf`) essaie automatiquement 3 layouts de signature via un rendu d'essai WeasyPrint (comptage de pages) pour ne jamais déborder sur une 2e page à cause d'elle : empilée sous le nom (défaut) → compacte à côté du nom (`signoff-inline`) → sans signature. La signature `files/letter/signature.png` est insérée si présente. `--letter-pdf` permet le rendu à la demande hors pipeline.
- **cv_master.md est généré automatiquement** depuis `cv_data.yaml` (via `jobsearch/cv_sync.py`, à chaque `--generate` et via `files/cv/generate_cv.py`). Ne pas l'éditer à la main — modifier `cv_data.yaml`.
- **Langue du CV** : le template `cv_en.html` est en anglais uniquement. Pour un CV en français, il faudrait un second template avec les labels traduits.
- **WeasyPrint** : requiert des dépendances système (Pango, Cairo). Sur macOS : `brew install pango` si pas déjà installé. **Toujours l'importer via `import_weasyprint_html()` (`jobsearch/cv_pdf.py`)** : sans Pango/GTK (cas normal sous Windows), WeasyPrint lève une `OSError` à l'import, pas une `ImportError` — le `except ImportError` de `generator.py` la laissait passer, `--generate` plantait après les appels Sonnet sans enregistrer la candidature. Le helper la convertit en `ImportError` avec la commande d'installation propre à l'OS.
- **Polices locales** : DM Sans / DM Serif sont embarquées dans `config/templates/fonts/` — la génération PDF fonctionne hors-ligne. Le preview HTML (`cv_adapte.html`) référence ces polices en chemin relatif ; ouvert hors du dossier templates, il retombe sur les polices système (le PDF, lui, est correct).

### Mode manuel
- `--from-url` échoue sur les sites avec protection JS (CloudFront, Cloudflare). Utiliser `--from-file` dans ce cas.
- L'extraction automatique du titre/entreprise/lieu peut être imprécise sur des pages très obfusquées — utiliser `--title` et `--company` pour surcharger.

### Suivi / historique des statuts
- **`ensure_history()` doit être appelée AVANT toute mutation de `app`** (`jobsearch/tracker.py`). Elle déduit l'événement de réponse depuis `status` + `responded_at` : l'appeler après avoir écrit le nouveau statut réécrit le passé avec celui-ci. Le bug réel : un passage `interview` → `rejected` produisait `[generated, sent, rejected]` et effaçait purement et simplement l'étape « entretien » (la garde anti-doublon voyait alors un dernier événement déjà au bon statut et n'ajoutait rien). Même piège dans `record_generated` avec `generated_at`.
- **Historique reconstruit ≠ dates réelles** : pour les candidatures antérieures à l'introduction de `history`, les événements sont déduits des anciennes dates ponctuelles et marqués `inferred` (affichés avec un `~`). Ils datent du moment où le statut a été *changé*, pas de l'événement lui-même (le jour où l'on marque « entretien », pas le jour de l'entretien).
- **Sankey** : `jobsearch/flow.py` n'utilise que la stdlib (SVG généré par concaténation de chaînes) — ne pas y introduire de dépendance graphique. Le `<svg>` produit n'a volontairement pas de `xmlns` : valide dans un document HTML5, mais le bloc n'est donc pas extractible tel quel en fichier `.svg` autonome.

### Premier lancement (CLI)
- **`_check_setup()` s'exécute AVANT `parse_args()`** (`jobsearch/cli.py`), pour pouvoir proposer l'assistant quelle que soit la commande. Conséquence : toute commande qui doit fonctionner sans configuration doit être ajoutée au jeu d'exemptions `_EXEMPT` — c'est déjà le cas de `--migrate`, `--setup`, `--help` et `-h`. Sans l'exemption de `--help`, le tout premier réflexe d'un nouvel utilisateur tombait sur la question de configuration au lieu de l'aide.
- **`input()` n'est appelé que si `sys.stdin.isatty()`**, et sous `try/except EOFError`. Dans un pipe, un cron ou une CI, l'ancien appel nu produisait une trace Python à la place du message d'aide. L'assistant étant une app Textual, il exige de toute façon un vrai terminal.
- **Sortie redirigée sous Windows** : un `stdout` redirigé (fichier, pipe, tâche planifiée) y prend l'encodage ANSI (cp1252), et le premier émoji d'un `print` lève `UnicodeEncodeError`. `main()` (`jobsearch/cli.py`) reconfigure `stdout`/`stderr` en UTF-8 (`errors="replace"`) sous Windows — ne pas retirer ce bloc. Les `open()` en mode texte portent tous `encoding="utf-8"` : à conserver sur tout nouveau fichier lu ou écrit.
- **`setup.sh` n'est pas embarqué dans le paquet** : en installation pipx, `PACKAGE_ROOT` vaut `site-packages/` et le script n'y existe pas. Ne le proposer qu'après un `os.path.isfile`, sous peine de renvoyer l'utilisateur vers un fichier absent.
- **Même piège, sur les six `.example` eux-mêmes.** Ils vivaient à la racine du dépôt (`config/*.example.yaml`, `cv_master/*.example*`, `.env.example`), hors de `jobsearch/` — donc jamais inclus par setuptools sur une installation pipx (`PACKAGE_ROOT` = `site-packages/`). Deux points d'accès en souffraient en silence : `render_search_yaml` (`jobsearch/wizard.py`) retombait sur un repli en dur qui écrivait `search.yaml` sans ses ~200 lignes de commentaires, et `_build_write_items` lisait `cv_data.example.yaml`/`cv_master.example.md` sous un `if os.path.exists(...)` qui échouait sans bruit — l'assistant annonçait sa réussite mais `cv_master/cv_data.yaml`/`cv_master.md` n'étaient jamais écrits, et `_check_setup` les réclamait juste après, boucle infinie. Corrigé en déplaçant les six fichiers vers `jobsearch/resources/examples/` (embarqués via `package-data` dans `pyproject.toml`, résolus par `Paths.example(name)` avec `importlib.resources`) et en remplaçant les replis/gardes silencieuses par des `FileNotFoundError` explicites — un `.example` absent doit être visible immédiatement, pas neutralisé trois couches plus loin.

### Rendu terminal (TUI)
- **Échapper les crochets littéraux dans le balisage Rich** : `"[f]"` dans une chaîne passée à un widget est interprété comme une balise de style inconnue et **avalé silencieusement**. Écrire `"\\[f]"`. C'est ce qui faisait disparaître le raccourci de la ligne « exporter le diagramme de Sankey ».
- **Alignement en colonnes et émoji** : `f"{label:<40}"` pade en *caractères*, pas en cellules d'affichage — un émoji large compte 1 pour `len()` mais occupe 2 colonnes. Utiliser `_pad()`/`_visible_width()` (`jobsearch/tui.py`, basés sur `unicodedata.east_asian_width`). Corollaire : garder des émoji de largeur `W` dans `_STATUS_LABELS` (🎙 était `Neutral`, d'où le décalage d'une cellule de la ligne « entretien » — remplacé par 🎤).
- **Modale de confirmation : `enter` doit être lié explicitement.** `ConfirmScreen` (`jobsearch/tui.py`) n'avait que `y`/`n`/`échap` — sans binding `enter`, la modale paraît figée, puisque c'est la touche que l'on presse par réflexe. Combiné à l'avalement Rich des crochets ci-dessus (`\[y]` non échappé), l'écran n'affichait même plus quelle touche utiliser. Même défaut, même correctif que `OverwriteConfirmScreen` (`jobsearch/wizard.py`) — vérifier les deux en cas d'ajout d'une nouvelle modale.
- **Ouvrir un fichier/dossier : jamais `subprocess.run(["open", ...])` en dur.** `open` n'existe que sur macOS : sous Linux et Windows, il lève `FileNotFoundError` (que `check=False` ne rattrape pas) et fait planter la TUI — c'était le cas des touches `O` et `e`. Passer par `_open_with_system` (ouvreur de l'OS : `open`/`xdg-open`/`os.startfile`, sorties vers DEVNULL, erreur → message) ou `_open_in_editor` (`$VISUAL`/`$EDITOR` d'abord, puis `_open_with_system`) (`jobsearch/tui.py`). Même logique pour une URI de fichier : `Path(p).resolve().as_uri()`, jamais `"file://" + chemin` (malformée sous Windows).
- **Curseur préservé** : toute reconstruction de table (`clear()` + `add_row()`) ramène le curseur en haut. Passer par le gestionnaire de contexte `_keep_cursor(table_id)` — appliqué aux quatre tables (Offres, Génération, Candidatures, Suivi).
- **Une touche = un sens, partout dans la TUI.** `o` a longtemps ouvert tantôt l'offre (Offres/Génération/Suivi), tantôt le dossier de candidature (Candidatures, où `o`/`O` étaient inversés par rapport aux autres onglets) — piège vécu (« j'ai ouvert le Finder en croyant ouvrir une annonce »). Règle désormais figée : `o` ouvre toujours l'**offre** dans le navigateur (onglets Offres/Génération/Candidatures/Suivi), `O` ouvre toujours le **dossier** de candidature (onglets Génération/Candidatures) — ne jamais réutiliser ces deux touches pour un troisième sens sur un nouvel onglet. Même logique pour `i` : appartient exclusivement à la famille de statuts `s`/`a`/`i`/`j`/`v`/`z` de l'onglet Suivi (entretien) — le marquage invalide de l'onglet Offres vit sur `X`, pas sur `i`, pour ne pas dupliquer son sens selon l'onglet actif (l'ancien dispatcher `action_mark_invalid_or_interview` a été supprimé en même temps que ce piège). Avant d'assigner une touche à une nouvelle action, vérifier qu'elle n'est pas déjà prise ailleurs avec un sens différent.

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

pip install -e .            # lit pyproject.toml (recommandé, installe l'alias `jobsearch`)
pip install -e '.[jobspy]'  # + LinkedIn/Indeed — exige un venv Python 3.11 (voir Installation)
```

`requirements.txt` ne liste que les dépendances de base et **ne contient pas
`python-jobspy`**, volontairement : il épingle `NUMPY==1.26.3`, sans wheel au-delà de
Python 3.11 (voir le piège détaillé en section « Installation »). C'est un extra.

Toutes les commandes Python/pip doivent passer par le venv (`venv/`).
