# Pipeline automatisé de recherche de stage — Documentation

## Vue d'ensemble

Pipeline Python qui automatise la recherche de stage : collecte des offres depuis des flux RSS et APIs, les filtre avec Claude Haiku (scoring 0-10), génère un rapport Markdown, puis produit une lettre de motivation et un CV PDF adapté via Claude Sonnet pour les offres retenues.

**Candidat** : Nathan Albalat — Ingénieur INFO5 Polytech Grenoble, cherche un stage de 22 semaines minimum à l'étranger (priorité Pays-Bas / Europe), dispo février 2027.

**Documentation utilisateur** : `README.md` (anglais, version de référence) et `README_FR.md` (français) — contenu identique, à maintenir en parallèle en cas de modification.

---

## Architecture

```
Étape 1 — Scrape   src/scraper.py    RSS + JSON API + jobspy (LinkedIn/Indeed) + Adzuna → data/offers/*.json
Étape 2 — Filtre   src/filter.py     Pré-filtre mots-clés + Claude Haiku (0-10) — Message Batches par défaut (-50%)
Étape 3 — Rapport  src/report.py     data/offers/*.json → data/rapport_YYYY-MM-DD.md (+ section suivi)
Étape 4 — Candidature  src/generator.py  Claude Sonnet → lettre.md + lettre.pdf + cv_adapte.pdf + entrée dans applications.json
```

Les étapes 1-2-3 sont enchaînées via `python main.py --scrape`.
L'étape 4 est déclenchée manuellement : `python main.py --generate <offer_id>`.

---

## Structure des fichiers

```
config/
  search.yaml            LE SUJET de recherche (stage/CDI, durée, géo, scoring, mots-clés, modèles…)
  profile.yaml           L'IDENTITÉ du candidat (formation, compétences, langues — indépendant du sujet)
  sources.yaml           Sources RSS et APIs à scraper
  prompts/
    filter.txt          Prompt de scoring Claude Haiku (barème + 14 exemples few-shot)
    cover_letter.txt    Prompt de génération de lettre de motivation
    cv_adapt.txt        Prompt d'adaptation du CV → sort un JSON structuré
  templates/
    cv_en.html          Template HTML du CV (DM Sans / DM Serif, layout 2 colonnes)
    cover_letter.html   Template HTML de la lettre de motivation (même charte graphique)

cv_master/
  cv_data.yaml          SOURCE DE VÉRITÉ pour le CV PDF (données structurées)
  cv_master.md          Résumé textuel du CV (utilisé par le prompt cover_letter)

src/
  config.py              Socle de configuration : chemins (`Paths`/`PATHS`) + valeurs (`Search`/`get_search()`) — lit config/search.yaml fusionné au-dessus de DEFAULTS, jamais rien au niveau module
  tui.py                Interface TUI Textual (--tui) : revue des offres, génération, suivi
  scraper.py            Collecte RSS (feedparser) + API JSON (arbeitnow) + Adzuna + dédup fuzzy inter-sources
  scraper_jobspy.py     Source type "jobspy" : LinkedIn / Indeed via python-jobspy
  filter.py             Pré-filtre mots-clés (configurable) + Claude Haiku (Message Batches par défaut, séquentiel en fallback)
  report.py             Génération du rapport Markdown (+ section suivi des candidatures)
  generator.py          Génération lettre + CV adapté (Claude Sonnet) + enregistrement dans le suivi
  tracker.py            Suivi des candidatures (data/applications.json, statuts, tableau CLI)
  cv_sync.py            Régénère cv_master.md depuis cv_data.yaml (source de vérité unique)
  cv_pdf.py             Rendu HTML→PDF via WeasyPrint (partagé avec files/)
  letter_pdf.py         Rendu PDF de la lettre (extraction salutation/closing, markdown→HTML)
  manual.py             Mode manuel : extraction depuis HTML/URL + génération directe
  notifier.py           Envoi email optionnel (SMTP)

config/templates/fonts/  Polices locales DM Sans / DM Serif Display (.ttf) — PDF sans réseau

data/
  offers/               Un fichier JSON par offre (avec filter_result après filtrage)
  seen_offers.json      Hachés des offres déjà vues (déduplication)
  applications.json     Suivi des candidatures (statuts : generated/sent/answered/interview/rejected/accepted/cancelled)
  gen_queue.json         File de génération de l'onglet 2 de la TUI (persistée entre deux lancements)
  rapport_*.md          Rapports générés

output/
  candidatures/         Un dossier par candidature : lettre.md + lettre.pdf + cv_adapte.html + cv_adapte.pdf

files/
  generate_cv.py        Génère le CV PDF "master" (sans adaptation) depuis cv_data.yaml
  cv_nathan_albala_en.html  Preview HTML du CV master
  cv_nathan_albala_en.pdf   PDF du CV master
  template_en.html      (obsolète — remplacé par config/templates/cv_en.html)
```

---

## Commandes principales

```bash
# Pipeline complet (scrape + filtre + rapport)
python main.py --scrape

# Avec seuil personnalisé
python main.py --scrape --threshold 7

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

5 onglets : **Offres** (tableau triable par score, panneau de détail, cocher pour ajouter à la file de génération — lignes cochées surlignées en vert, offres de la dernière session de scrape surlignées en bleu foncé, filtrage par période avec `d`/`D`), **Génération** (file persistée entre deux lancements de la TUI dans `data/gen_queue.json` ; cocher des lignes avec `espace` restreint `g`/`L` à ce sous-ensemble — sinon toutes les offres en attente sont traitées ; `x` retire des lignes de la file ; lance lettre + CV en tâche de fond, ou lettre seule avec `L`), **Candidatures** (liste `output/candidatures/`, aperçu lettre/CV, régénération des PDF après retouche manuelle des fichiers — candidatures envoyées ou plus surlignées en vert foncé), **Suivi** (statuts + notes de `applications.json` — envoyée surlignée en jaune, réponse reçue en ambre, entretien en violet, acceptée en vert, refusée en rouge, annulée en gris foncé et texte atténué), **Stats** (rendu texte : métriques de retour — taux d'acceptation/réponse, délai moyen —, répartition des statuts, localisation des stages, histogramme des candidatures au cours du temps ; les candidatures annulées sont exclues des taux et du délai moyen, comptées à part dans une catégorie « Annulé »).

Workflow de retouche : ouvrir la lettre avec `e` (éditeur par défaut), modifier le `.md`, revenir dans la TUI et presser `p` — la colonne PDF affiche « ⚠ à régénérer » quand le `.md` est plus récent que le PDF.

### Raccourcis clavier (aussi via `?` dans la TUI)

| Contexte | Touche | Action |
|---|---|---|
| Global | `1`/`2`/`3`/`4`/`5` | Onglets Offres / Génération / Candidatures / Suivi / Stats |
| Global | `r` | Recharger les données depuis le disque |
| Global | `S` / `s` / `Ctrl+S` | Lancer scrape + filtre + rapport (spinner + étape + chrono dans la barre de statut). `s` ne déclenche le scrape qu'en dehors de l'onglet Suivi, où il vaut « envoyée » ; les alias existent car certains terminaux ne transmettent pas `shift`+lettre |
| Global | `c` | Ouvrir la Claude Console (recharger le crédit API) |
| Global | `?` | Aide (liste des raccourcis) |
| Global | `q` | Quitter |
| Offres | `espace` | Cocher/décocher l'offre pour génération |
| Offres | `o` | Ouvrir l'offre dans le navigateur |
| Offres | `+`/`-` | Ajuster le seuil de score affiché |
| Offres | `h` | Masquer/afficher les offres déjà candidatées |
| Offres | `d` | Cycler la période affichée (tout · session · 7j · 30j) |
| Offres | `D` | Choisir une session de scrape précise |
| Offres | `g` | Aller à l'onglet Génération |
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

---

## Configuration du candidat

Modifier `config/search.yaml` pour changer le **sujet** de recherche : type de contrat, durée, disponibilité, préférences géographiques, domaines, seuil de scoring, mots-clés du pré-filtre, modèles Claude et défauts des sources. Voir aussi `config/search.example.yaml` (commenté) et la section « Changer de sujet de recherche » ci-dessous.

Modifier `config/profile.yaml` pour changer l'**identité** du candidat : formation, compétences techniques, langues. Ce fichier ne dépend pas du sujet recherché.

Modifier `cv_master/cv_data.yaml` pour mettre à jour le CV :
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
- **WTTJ (Welcome to the Jungle)** : non-scrappable automatiquement (JS côté client + CloudFront 403). Workaround : `--from-file` avec la page sauvegardée depuis le navigateur.
- **HelloWork** : pas d'API publique — non couvert.

### Filtrage
- **Pré-filtre : recherche par mot entier** — `is_tech_offer()` compile `_TECH_KEYWORDS` en une regex à limites de mots (`_TECH_KEYWORDS_RE`). Ne jamais revenir à un `kw in text` : le substring brut provoquait des faux positifs massifs (`"git"` dans « diGITal », `"rust"` dans « tRUST », `"intern"` dans « INTERNational », et surtout l'ancien `"r "` — le langage R — qui matchait « manageR », « senioR », « foR »). Mesure sur l'historique : **43 % des offres envoyées à l'API l'étaient pour rien**, sans qu'aucune offre pertinente (score ≥ 6) ne dépende de ces correspondances partielles.
- **Ajouter un mot-clé court demande de la prudence** : un terme de 1-2 caractères (le langage R, « c ») génère du bruit même en mot entier (« R&D », initiales). R est volontairement absent — ces offres sont captées par `data`/`analyst`.
- **Faux positifs du pré-filtre** : le filtre à mots-clés peut encore laisser passer des offres non-tech contenant des termes génériques ("stage", "intern", "data"). L'appel API Claude corrige ensuite — c'est le compromis voulu, un faux positif coûte un appel Haiku alors qu'un faux négatif perd une offre.
- **Faux négatifs du pré-filtre** : offres tech dans des langues non couvertes (néerlandais, allemand sans termes anglais) peuvent être écartées avant l'appel API. Quelques termes NL/DE sont couverts (`digitaal`, `coderen`, `informatica`, `werkstudent`).
- **Cache TTL 5 min (mode séquentiel uniquement)** : si le filtrage séquentiel dure plus de 5 minutes sans appel API, le cache expire. Non applicable en mode batch (défaut).
- **Dédup fuzzy** : la détection de doublons inter-sources (entreprise identique + titre ≈ identique via SequenceMatcher ≥ 0.9) peut rater des doublons si l'entreprise est orthographiée différemment selon les boards.

### Génération
- **Lettre PDF** : rendue via `src/letter_pdf.py` + `config/templates/cover_letter.html`. Le parser (`split_letter`) extrait salutation/closing de la lettre Sonnet (évite les doublons) et retire les artefacts de traduction en fin de lettre (ex. « Translated with DeepL.com… ») avant de chercher le mot de clôture. La ligne de séparation de l'en-tête dépend de `personal.accent_enabled` (`cv_data.yaml`, vert si activé) — placeholders `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}` à garder sur une seule ligne dans le template (un reformatage d'éditeur qui les casse sur plusieurs lignes rend le remplacement silencieusement inopérant → CSS invalide → plus de ligne du tout). `render_letter_pdf()` (nouvelle fonction, à utiliser à la place de `render_letter_html`+`write_letter_pdf`) essaie automatiquement 3 layouts de signature via un rendu d'essai WeasyPrint (comptage de pages) pour ne jamais déborder sur une 2e page à cause d'elle : empilée sous le nom (défaut) → compacte à côté du nom (`signoff-inline`) → sans signature. La signature `files/letter/signature.png` est insérée si présente. `--letter-pdf` permet le rendu à la demande hors pipeline.
- **cv_master.md est généré automatiquement** depuis `cv_data.yaml` (via `src/cv_sync.py`, à chaque `--generate` et via `files/cv/generate_cv.py`). Ne pas l'éditer à la main — modifier `cv_data.yaml`.
- **Langue du CV** : le template `cv_en.html` est en anglais uniquement. Pour un CV en français, il faudrait un second template avec les labels traduits.
- **WeasyPrint** : requiert des dépendances système (Pango, Cairo). Sur macOS : `brew install pango` si pas déjà installé.
- **Polices locales** : DM Sans / DM Serif sont embarquées dans `config/templates/fonts/` — la génération PDF fonctionne hors-ligne. Le preview HTML (`cv_adapte.html`) référence ces polices en chemin relatif ; ouvert hors du dossier templates, il retombe sur les polices système (le PDF, lui, est correct).

### Mode manuel
- `--from-url` échoue sur les sites avec protection JS (CloudFront, Cloudflare). Utiliser `--from-file` dans ce cas.
- L'extraction automatique du titre/entreprise/lieu peut être imprécise sur des pages très obfusquées — utiliser `--title` et `--company` pour surcharger.

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
