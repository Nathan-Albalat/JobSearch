# Pipeline automatisé de recherche de stage — Documentation

## Vue d'ensemble

Pipeline Python qui automatise la recherche de stage : collecte des offres depuis des flux RSS et APIs, les filtre avec Claude Haiku (scoring 0-10), génère un rapport Markdown, puis produit une lettre de motivation et un CV PDF adapté via Claude Sonnet pour les offres retenues.

**Candidat** : Nathan Albalat — Ingénieur INFO5 Polytech Grenoble, cherche un stage de 22 semaines minimum à l'étranger (priorité Pays-Bas / Europe), dispo février 2027.

**Documentation utilisateur** : `README.md` (anglais, version de référence) et `README_FR.md` (français) — contenu identique, à maintenir en parallèle en cas de modification.

**Suivi de projet (ObsidianBrain)** : `/Users/nathan/Document/ObsidianBrain/Année INFO5/Script/` — `STATE.md` (état courant du projet) et `CHANGELOG.md` (journal daté des sessions). Mis à jour via `/wrapup`.

---

## Architecture

```
Étape 1 — Scrape   src/scraper.py    RSS + JSON API + jobspy (LinkedIn/Indeed) + Adzuna + WTTJ (NL active, groupe `fr` optionnel) + HelloWork (groupe `fr`, optionnel) → data/offers/*.json
Étape 2 — Filtre   src/filter.py     Pré-filtre mots-clés + Claude Haiku (0-10) — Message Batches par défaut (-50%)
Étape 3 — Rapport  src/report.py     data/offers/*.json → data/rapport_YYYY-MM-DD.md (+ section suivi)
Étape 4 — Candidature  src/generator.py  Claude Sonnet → lettre.md + lettre.pdf + cv_adapte.pdf + entrée dans applications.json
```

Les étapes 1-2-3 sont enchaînées via `python main.py --scrape`.
L'étape 4 est déclenchée manuellement : `python main.py --generate <offer_id>`.

---

## Structure des fichiers

Deux racines distinctes (voir « Emplacement des données » ci-dessous) : `config/prompts/` et
`config/templates/` sont des **ressources livrées avec le code**, elles restent dans le dépôt/paquet
(`PACKAGE_ROOT`) quel que soit `JOBSEARCH_HOME`. Tout le reste ci-dessous (`config/search.yaml`,
`config/profile.yaml`, `config/sources.yaml`, `config/.env`, `cv_master/`, `data/`, `output/`) est de la
**config personnelle et des données**, résolues sous `JOBSEARCH_HOME` (`~/.jobsearch/` par défaut). Les
chemins présentés ici sont relatifs à leur racine respective pour rester lisibles.

```
config/                  [PACKAGE_ROOT/config/prompts+templates ; le reste sous JOBSEARCH_HOME/config/]
  search.yaml            LE SUJET de recherche (stage/CDI, durée, géo, scoring, mots-clés, modèles…)
  profile.yaml           L'IDENTITÉ du candidat (formation, compétences, langues — indépendant du sujet)
  sources.yaml           Sources RSS et APIs à scraper
  .env                   Clés API et secrets (ANTHROPIC_API_KEY, ADZUNA_*, SMTP_*…)
  prompts/               [ressource du paquet — PACKAGE_ROOT, jamais JOBSEARCH_HOME]
    filter.txt          Prompt de scoring Claude Haiku (barème + 14 exemples few-shot)
    cover_letter.txt    Prompt de génération de lettre de motivation
    cv_adapt.txt        Prompt d'adaptation du CV → sort un JSON structuré
  templates/              [ressource du paquet — PACKAGE_ROOT, jamais JOBSEARCH_HOME]
    cv_en.html          Template HTML du CV (DM Sans / DM Serif, layout 2 colonnes)
    cover_letter.html   Template HTML de la lettre de motivation (même charte graphique)

cv_master/                [JOBSEARCH_HOME/cv_master/]
  cv_data.yaml          SOURCE DE VÉRITÉ pour le CV PDF (données structurées)
  cv_master.md          Résumé textuel du CV (utilisé par le prompt cover_letter)

src/
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
  migrate.py             `--migrate` : copie (jamais déplace) la config perso et les données du dépôt vers JOBSEARCH_HOME

scripts/
  test_api.py            Smoke-test de la clé API Anthropic (hors paquet src/)

config/templates/fonts/  Polices locales DM Sans / DM Serif Display (.ttf) — PDF sans réseau ; ressource du paquet (PACKAGE_ROOT)

data/                     [JOBSEARCH_HOME/data/]
  offers/               Un fichier JSON par offre (avec filter_result après filtrage ; champ optionnel
                        `duplicate_of` : id de l'offre candidatée dont celle-ci est un doublon manuel,
                        posé/retiré via `src/duplicates.py`)
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
# Pipeline complet (scrape + filtre + rapport)
python main.py --scrape

# Avec seuil personnalisé
python main.py --scrape --threshold 7

# Inclure les sources FR-only (groupe `fr`, ex: HelloWork, WTTJ FR), désactivées par défaut
python main.py --scrape --with fr

# Désactiver un groupe même s'il est activé par défaut dans search.yaml (sources.enabled_groups)
python main.py --scrape --without fr

# Générer candidature pour une offre (lettre + CV adapté)
python main.py --generate <offer_id>

# Lettre seule (pas de CV adapté — 1 appel Sonnet au lieu de 2)
python main.py --generate <offer_id> --letter-only

# Mode manuel (offre non-scrappable)
python main.py --from-file ~/Downloads/offre.html --url-source "https://..." --company "Criteo"
python main.py --from-url https://example.com/jobs/stage-dev-python

# Modules en isolation (debug)
python -m src.scraper
python -m src.filter
python -m src.report
python -m src.generator <offer_id>

# Forcer le filtrage séquentiel (sans Message Batches)
python main.py --scrape --no-batch

# Lettre PDF à la demande (hors pipeline)
python main.py --letter-pdf lettre.md --company "ASML" --title "Software Intern" --location "Eindhoven, NL" [--dept "..."] [--output out.pdf]
python main.py --letter-pdf offre.json   # format files/letter/offer_data_example.json

# Interface interactive (revue → génération → suivi)
python main.py --tui

# Suivi des candidatures
python main.py --applications
python main.py --mark <offer_id> sent --note "Candidature envoyée via le site"

# Migration config perso + données du dépôt vers JOBSEARCH_HOME (copie uniquement, jamais destructif)
python main.py --migrate            # aperçu à blanc, n'écrit rien
python main.py --migrate --apply    # copie réelle, vérifie fichiers + sommes de contrôle après coup

# Marquer une offre comme doublon manuel d'une candidature existante (voir section dédiée ci-dessous)
python main.py --mark-duplicate <offer_id> <target_id>
python main.py --unmark-duplicate <offer_id>

# Générer le CV master (sans adaptation, depuis cv_data.yaml) — resynchronise aussi cv_master.md
python files/cv/generate_cv.py

# Régénérer cv_master.md seul depuis cv_data.yaml
python -m src.cv_sync

# Afficher les chemins et valeurs de config effectifs (débug config/search.yaml)
python -m src.config
python -m src.config --check-prompts   # échoue si un placeholder reste inconnu dans les 3 prompts
```

---

## Interface TUI (`python main.py --tui`)

Vue interactive sur les fichiers du pipeline — **les fichiers restent la source de vérité** : les rapports markdown, `data/offers/*.json` et `data/applications.json` restent lisibles et utilisables sans la TUI, qui ne persiste quasiment aucun état propre (seule exception : la file de génération de l'onglet Génération, dans `data/gen_queue.json`). On peut donc reprendre le workflow n'importe quand (scrape lundi, revue mercredi) : la TUI relit l'état courant au lancement et sur `r`.

5 onglets : **Offres** (tableau triable par score, panneau de détail, cocher pour ajouter à la file de génération — lignes cochées surlignées en vert, offres de la dernière session de scrape surlignées en bleu foncé, filtrage par période avec `d`/`D` ; colonne `✓` en cascade à 4 états : `▶` cochée > `⧉` doublon manuel confirmé > `✓` candidatée > `?` doublon suspecté — entreprise déjà candidatée, non tranché — touche `m` pour marquer/démarquer, suit `h` comme une offre candidatée), **Génération** (file persistée entre deux lancements de la TUI dans `data/gen_queue.json` ; cocher des lignes avec `espace` restreint `g`/`L` à ce sous-ensemble — sinon toutes les offres en attente sont traitées ; `x` retire des lignes de la file ; lance lettre + CV en tâche de fond, ou lettre seule avec `L`), **Candidatures** (liste `output/candidatures/`, aperçu lettre/CV, régénération des PDF après retouche manuelle des fichiers — candidatures envoyées ou plus surlignées en vert foncé), **Suivi** (statuts + notes de `applications.json` — envoyée surlignée en jaune, réponse reçue en ambre, entretien en violet, acceptée en vert, refusée en rouge, annulée en gris foncé et texte atténué ; panneau de droite : timeline de la candidature sous le curseur, reconstruite depuis `history` — écart en jours entre chaque événement, dates déduites suffixées d'un `~` quand l'historique est reconstitué depuis les anciennes dates ponctuelles), **Stats** (rendu texte : métriques de retour — taux d'acceptation/réponse, délai moyen —, répartition des statuts, localisation des stages, histogramme des candidatures au cours du temps ; les candidatures annulées sont exclues des taux et du délai moyen, comptées à part dans une catégorie « Annulé » ; parcours des candidatures sous forme d'arbre de statuts (`src/flow.py`) et répartition mensuelle des statuts atteints — `f` exporte le diagramme de Sankey correspondant en HTML autonome, `output/stats/flux.html`, ouvert dans le navigateur).

Workflow de retouche : ouvrir la lettre avec `e` (éditeur par défaut), modifier le `.md`, revenir dans la TUI et presser `p` — la colonne PDF affiche « ⚠ à régénérer » quand le `.md` est plus récent que le PDF.

### Raccourcis clavier (aussi via `?` dans la TUI)

| Contexte | Touche | Action |
|---|---|---|
| Global | `1`/`2`/`3`/`4`/`5` | Onglets Offres / Génération / Candidatures / Suivi / Stats |
| Global | `r` | Recharger les données depuis le disque |
| Global | `S` / `s` | Lancer scrape + filtre + rapport (spinner + étape + chrono dans la barre de statut). `s` ne déclenche le scrape qu'en dehors de l'onglet Suivi, où il vaut « envoyée » ; `s` est un alias de `S` (certains terminaux ne transmettent pas `shift`+lettre) |
| Global | `Ctrl+S` | Idem, en incluant aussi les sources FR-only (groupe `fr`, ex. HelloWork, WTTJ FR) — équivalent de `python main.py --scrape --with fr` |
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

---

## Emplacement des données (`JOBSEARCH_HOME`)

Le pipeline se lance depuis **n'importe quel répertoire**, et sépare deux racines résolues en absolu par `src/config.py` (`PATHS`) :

- **`PACKAGE_ROOT`** (= racine du dépôt) : le code et les **ressources livrées avec lui** — `config/prompts/`, `config/templates/` (et les polices) — jamais rien d'autre. `PATHS.prompts_dir`/`PATHS.templates_dir` (et les raccourcis `PATHS.prompt()`/`PATHS.template()`) s'y résolvent toujours, quel que soit `JOBSEARCH_HOME`.
- **`JOBSEARCH_HOME`** : la **configuration personnelle** (`config/search.yaml`, `config/profile.yaml`, `config/sources.yaml`, `config/.env`, `cv_master/cv_data.yaml`, `cv_master/cv_master.md`) et les **données** (`data/`, `output/`). Vaut par défaut `~/.jobsearch/` — surchargeable via la variable d'environnement `JOBSEARCH_HOME` (utile pour tester sur une copie isolée, ou repointer vers le dépôt avec `JOBSEARCH_HOME=.` pour un usage « tout-en-un » historique).

`python main.py --applications` fonctionne aussi bien depuis `Stage/Script` que depuis `/tmp` : les chemins ne dépendent jamais du répertoire courant. Voir `python -m src.config` pour afficher les chemins effectifs.

**Migration depuis une installation où tout vivait dans le dépôt** (avant cette séparation) : `python main.py --migrate` (mode à blanc, n'écrit rien, affiche ce qui serait copié) puis `python main.py --migrate --apply` (copie réelle, `src/migrate.py`). Ne fait QUE des copies — ne déplace ni ne supprime jamais les originaux du dépôt ; refuse d'écraser un fichier déjà présent à destination ; vérifie après coup le nombre de fichiers et les sommes de contrôle. Suppression des originaux laissée à l'utilisateur.

**Garde-fou** : si `JOBSEARCH_HOME` est vide/absent alors que le dépôt contient encore des offres dans `data/offers/` (ancien emplacement, pas encore migré), `_check_setup` (`main.py`) et `_warn_if_empty_home` (`src/tui.py`, appelé par `run_tui()`) affichent un message explicite pointant vers `--migrate` plutôt que de laisser croire à « 0 offre » / une perte de données.

## Configuration du candidat

Modifier `config/search.yaml` (sous `JOBSEARCH_HOME`) pour changer le **sujet** de recherche : type de contrat, durée, disponibilité, préférences géographiques, domaines, seuil de scoring, mots-clés du pré-filtre, modèles Claude et défauts des sources. Voir aussi `config/search.example.yaml` (commenté, dans le dépôt) et la section « Changer de sujet de recherche » ci-dessous.

Modifier `config/profile.yaml` (sous `JOBSEARCH_HOME`) pour changer l'**identité** du candidat : formation, compétences techniques, langues. Ce fichier ne dépend pas du sujet recherché.

Modifier `cv_master/cv_data.yaml` (sous `JOBSEARCH_HOME`) pour mettre à jour le CV :
- Expériences, projets, compétences, infos personnelles
- Ce fichier alimente à la fois `files/cv/generate_cv.py` (CV master) et `src/generator.py` (CV adapté)

---

## Changer de sujet de recherche

Le pipeline est conçu pour être repris par n'importe quel utilisateur, pour n'importe quel sujet (stage, alternance, CDI, dans n'importe quel pays), en ne touchant que la config, les prompts et le CV — **jamais le code Python**. Dans l'ordre :

1. **`config/search.yaml`** — le cœur du pivot. Ajuster `sujet.type_contrat` (ex: `"CDI"`), `sujet.type_contrat_negatif`, `duree.applicable` (`false` pour un contrat sans durée minimale), `geo.priorite`/`secondaire`/`dernier_recours`/`details`, `domaines.liste`, `scoring.seuil`/`bonus`/`malus`, et surtout `prefilter.keywords.contrat` — c'est le levier n°1 : retirer `stage`/`internship`/`werkstudent` et ajouter les termes du nouveau contrat (`cdi`, `permanent`, `full-time`…) change immédiatement quelles offres passent le pré-filtre gratuit avant même l'appel API.
2. **Les 3 prompts** (`config/prompts/`) — la plupart du texte est déjà paramétré via les placeholders (`{sujet}`, `{type_contrat}`, `{duree}`, `{pays_prioritaires}`, `{seuil}`…) et se met à jour automatiquement depuis `search.yaml`. **Seule exception, à réécrire à la main : les 14 exemples few-shot A→N de `filter.txt`** — ce sont des offres réelles calibrées sur le sujet actuel (stage tech, Pays-Bas). C'est l'étape la plus coûteuse en temps humain d'un pivot, mais elle est nécessaire : sans exemples représentatifs du nouveau sujet, le scoring reste ancré sur l'ancien.
3. **`config/profile.yaml`** — mettre à jour si la formation ou les compétences techniques ont changé (indépendant du sujet, à ajuster seulement si le candidat change).
4. **`cv_master/cv_data.yaml`** — adapter le CV master si nécessaire (expériences, projets à mettre en avant pour le nouveau sujet).
5. **Repartir d'un `data/` vierge** — il n'existe pas de mécanisme de re-filtrage : les offres déjà scorées dans `data/offers/*.json` restent telles quelles avec leurs anciens critères. Après un changement de sujet, vider (ou déplacer) `data/offers/`, `data/seen_offers.json` et relancer `python main.py --scrape`.
6. **`config/sources.yaml` — pivoter les entrées `wttj`** : éditer `job_title`, `locations` (mapping `country`/`country_code`/`label`/`latitude`/`longitude`) et `contract_types` de chaque entrée — rien d'autre. Même logique pour `hellowork` (`k`/`c`).

Vérification sans consommer de crédit API : `python -m src.config --check-prompts` (échoue si un placeholder reste inconnu dans les 3 prompts).

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
  - l'ordre des blocs de `description` (durée/début en tête, `company_description` en dernier) est volontaire : `strip_html` (`src/filter.py`) tronque à 2000 caractères, le signal utile doit passer avant la troncature
- **HelloWork** : type `hellowork`, source FR-only (groupe `fr`, `optional: true` — désactivée par défaut, activer avec `--with fr` ou `sources.enabled_groups` dans `search.yaml`). Server-rendered : une requête GET (User-Agent navigateur) sur la recherche paginée (`?p=N`), puis une requête détail par offre pour extraire le bloc JSON-LD `schema.org/JobPosting` (titre, entreprise, lieu, description, date). **Le JSON-LD est le contrat stable — ne jamais basculer sur des sélecteurs CSS**, qui cassent à chaque refonte front. Un délai (`source_defaults.hellowork.delay`, 0.3 s par défaut) est appliqué entre chaque requête détail par politesse réseau ; une offre détail illisible est loguée et ignorée sans interrompre la source.

### Filtrage
- **Pré-filtre : recherche par mot entier** — `is_tech_offer()` compile `_TECH_KEYWORDS` en une regex à limites de mots (`_TECH_KEYWORDS_RE`). Ne jamais revenir à un `kw in text` : le substring brut provoquait des faux positifs massifs (`"git"` dans « diGITal », `"rust"` dans « tRUST », `"intern"` dans « INTERNational », et surtout l'ancien `"r "` — le langage R — qui matchait « manageR », « senioR », « foR »). Mesure sur l'historique : **43 % des offres envoyées à l'API l'étaient pour rien**, sans qu'aucune offre pertinente (score ≥ 6) ne dépende de ces correspondances partielles.
- **Ajouter un mot-clé court demande de la prudence** : un terme de 1-2 caractères (le langage R, « c ») génère du bruit même en mot entier (« R&D », initiales). R est volontairement absent — ces offres sont captées par `data`/`analyst`.
- **Faux positifs du pré-filtre** : le filtre à mots-clés peut encore laisser passer des offres non-tech contenant des termes génériques ("stage", "intern", "data"). L'appel API Claude corrige ensuite — c'est le compromis voulu, un faux positif coûte un appel Haiku alors qu'un faux négatif perd une offre.
- **Faux négatifs du pré-filtre** : offres tech dans des langues non couvertes (néerlandais, allemand sans termes anglais) peuvent être écartées avant l'appel API. Quelques termes NL/DE sont couverts (`digitaal`, `coderen`, `informatica`, `werkstudent`).
- **Cache TTL 5 min (mode séquentiel uniquement)** : si le filtrage séquentiel dure plus de 5 minutes sans appel API, le cache expire. Non applicable en mode batch (défaut).
- **Dédup fuzzy** : la détection de doublons inter-sources (entreprise identique + titre ≈ identique via SequenceMatcher ≥ 0.9) peut rater des doublons si l'entreprise est orthographiée différemment selon les boards.
- **La dédup automatique ne peut pas tout attraper — cas Farmless, d'où le marquage manuel** (`src/duplicates.py`, touche `m` dans la TUI, `--mark-duplicate`/`--unmark-duplicate`) : l'offre `c52e7b97` (« Software Engineering Intern (Full-Stack) », LinkedIn, candidature envoyée puis entretien puis refusée) est ressortie sous `dcd4359a` via WTTJ (« Fullstack Software Engineering Intern ») avec une similarité mesurée de **0.392**, très loin du seuil de 0.9 — impossible à rattraper en baissant le seuil. Pire : Farmless publie aussi un « Software Engineering Intern (Rust) », poste réellement différent, dont le recouvrement de mots avec l'offre WTTJ est **exactement le même (0.667)** que le vrai doublon. Aucun seuil ne peut départager ces deux cas : il faut comprendre que « Fullstack » ≠ « Rust », donc du sens, pas de la comparaison de chaînes. D'où le principe « la machine suggère, l'utilisateur confirme » : l'indicateur `?` de la colonne `✓` signale une entreprise déjà candidatée (pas forcément un doublon — la plupart ne le sont pas), la touche `m` ouvre une modale listant les candidatures de cette entreprise (celles-ci en tête) pour confirmer en une pression. Le marquage écrit `duplicate_of` (id de l'offre candidatée) dans le JSON de l'offre doublon ; celle-ci disparaît alors du bucket « pertinent »/« à candidater » du rapport (`src/report.py`) et suit la touche `h` (masquage) comme une offre déjà candidatée.

### Génération
- **Lettre PDF** : rendue via `src/letter_pdf.py` + `config/templates/cover_letter.html`. Le parser (`split_letter`) extrait salutation/closing de la lettre Sonnet (évite les doublons) et retire les artefacts de traduction en fin de lettre (ex. « Translated with DeepL.com… ») avant de chercher le mot de clôture. La ligne de séparation de l'en-tête dépend de `personal.accent_enabled` (`cv_data.yaml`, vert si activé) — placeholders `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}` à garder sur une seule ligne dans le template (un reformatage d'éditeur qui les casse sur plusieurs lignes rend le remplacement silencieusement inopérant → CSS invalide → plus de ligne du tout). `render_letter_pdf()` (nouvelle fonction, à utiliser à la place de `render_letter_html`+`write_letter_pdf`) essaie automatiquement 3 layouts de signature via un rendu d'essai WeasyPrint (comptage de pages) pour ne jamais déborder sur une 2e page à cause d'elle : empilée sous le nom (défaut) → compacte à côté du nom (`signoff-inline`) → sans signature. La signature `files/letter/signature.png` est insérée si présente. `--letter-pdf` permet le rendu à la demande hors pipeline.
- **cv_master.md est généré automatiquement** depuis `cv_data.yaml` (via `src/cv_sync.py`, à chaque `--generate` et via `files/cv/generate_cv.py`). Ne pas l'éditer à la main — modifier `cv_data.yaml`.
- **Langue du CV** : le template `cv_en.html` est en anglais uniquement. Pour un CV en français, il faudrait un second template avec les labels traduits.
- **WeasyPrint** : requiert des dépendances système (Pango, Cairo). Sur macOS : `brew install pango` si pas déjà installé.
- **Polices locales** : DM Sans / DM Serif sont embarquées dans `config/templates/fonts/` — la génération PDF fonctionne hors-ligne. Le preview HTML (`cv_adapte.html`) référence ces polices en chemin relatif ; ouvert hors du dossier templates, il retombe sur les polices système (le PDF, lui, est correct).

### Mode manuel
- `--from-url` échoue sur les sites avec protection JS (CloudFront, Cloudflare). Utiliser `--from-file` dans ce cas.
- L'extraction automatique du titre/entreprise/lieu peut être imprécise sur des pages très obfusquées — utiliser `--title` et `--company` pour surcharger.

### Suivi / historique des statuts
- **`ensure_history()` doit être appelée AVANT toute mutation de `app`** (`src/tracker.py`). Elle déduit l'événement de réponse depuis `status` + `responded_at` : l'appeler après avoir écrit le nouveau statut réécrit le passé avec celui-ci. Le bug réel : un passage `interview` → `rejected` produisait `[generated, sent, rejected]` et effaçait purement et simplement l'étape « entretien » (la garde anti-doublon voyait alors un dernier événement déjà au bon statut et n'ajoutait rien). Même piège dans `record_generated` avec `generated_at`.
- **Historique reconstruit ≠ dates réelles** : pour les candidatures antérieures à l'introduction de `history`, les événements sont déduits des anciennes dates ponctuelles et marqués `inferred` (affichés avec un `~`). Ils datent du moment où le statut a été *changé*, pas de l'événement lui-même (le jour où l'on marque « entretien », pas le jour de l'entretien).
- **Sankey** : `src/flow.py` n'utilise que la stdlib (SVG généré par concaténation de chaînes) — ne pas y introduire de dépendance graphique. Le `<svg>` produit n'a volontairement pas de `xmlns` : valide dans un document HTML5, mais le bloc n'est donc pas extractible tel quel en fichier `.svg` autonome.

### Rendu terminal (TUI)
- **Échapper les crochets littéraux dans le balisage Rich** : `"[f]"` dans une chaîne passée à un widget est interprété comme une balise de style inconnue et **avalé silencieusement**. Écrire `"\\[f]"`. C'est ce qui faisait disparaître le raccourci de la ligne « exporter le diagramme de Sankey ».
- **Alignement en colonnes et émoji** : `f"{label:<40}"` pade en *caractères*, pas en cellules d'affichage — un émoji large compte 1 pour `len()` mais occupe 2 colonnes. Utiliser `_pad()`/`_visible_width()` (`src/tui.py`, basés sur `unicodedata.east_asian_width`). Corollaire : garder des émoji de largeur `W` dans `_STATUS_LABELS` (🎙 était `Neutral`, d'où le décalage d'une cellule de la ligne « entretien » — remplacé par 🎤).
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
