# JobSearch

Un pipeline automatisé qui collecte des offres d'emploi/de stage depuis des flux RSS et des APIs de job boards, score chacune avec Claude Haiku selon tes critères de recherche, génère un rapport Markdown, puis — pour les offres que tu choisis — rédige une lettre de motivation adaptée et un CV PDF avec Claude Sonnet.

**JobSearch est généraliste**, pas réservé à l'informatique : l'assistant d'installation propose 7 presets sectoriels (informatique, santé, droit, commerce, communication, ingénierie, enseignement) plus une saisie libre pour tout le reste. Utilisable pour un stage, un CDI, ou tout autre type de contrat, dans n'importe quel pays.

---

## Installation

```bash
pipx install git+https://github.com/Nathan-Albalat/JobSearch
jobsearch --setup
jobsearch --tui
```

C'est tout — `jobsearch --setup` te guide dans un assistant interactif (clé API, sujet de recherche, prompt de scoring, profil de candidat), et `jobsearch --tui` ouvre l'interface interactive.

Ça fonctionne sur n'importe quel Python 3.11+ (y compris 3.12/3.13/3.14) et couvre toutes les sources sauf LinkedIn/Indeed. Quelques précisions utiles :

- **LinkedIn / Indeed** passent par l'extra optionnel `[jobspy]`, qui requiert un Python ≤ 3.11 (`python-jobspy` épingle un vieux numpy sans wheels au-delà) :
  ```bash
  pipx install "jobsearch[jobspy] @ git+https://github.com/Nathan-Albalat/JobSearch" --python python3.11
  ```
  Sans lui, ces deux sources sont simplement ignorées avec un message explicatif — pas une erreur, le reste du scrape tourne normalement.
- **WeasyPrint** (rendu PDF) requiert Pango au niveau système : `brew install pango` sur macOS ; `apt install libpango-1.0-0 libpangoft2-1.0-0` sur Debian/Ubuntu (généralement déjà présent sur un poste de bureau) ; sous Windows, installer [MSYS2](https://www.msys2.org), lancer `pacman -S mingw-w64-x86_64-pango`, puis définir `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin`. Sans elle, `--generate` écrit quand même la lettre (`.md`) et l'aperçu du CV (`.html`) et enregistre la candidature, mais saute les PDF avec un message explicatif.
- **Windows** : utiliser `jobsearch --setup` (`setup.sh` exige bash), et de préférence Windows Terminal pour la TUI. `--service` (démarrage automatique) est propre à macOS (launchd).

Pour du développement local plutôt que `pipx` : `pip install -e .` (ou `pip install -e '.[jobspy]'`) dans le venv de ce dépôt.

### Mettre à jour

```bash
jobsearch --upgrade      # lance `pipx upgrade jobsearch` pour toi
```

`--upgrade` détecte le mode d'installation : avec pipx, il lance la mise à jour lui-même (sauf sous Windows, où un `jobsearch.exe` en cours d'exécution ne peut pas se remplacer — il affiche la commande à lancer depuis un nouveau terminal) ; pour un dépôt cloné ou une installation pip classique, il affiche la bonne commande. `pipx upgrade jobsearch` fonctionne toujours directement.

Ta configuration et tes données sont préservées : elles vivent sous `~/.jobsearch/`, en dehors du paquet. Un prompt ou un template que tu as personnalisé garde ta version ; ceux que tu n'as jamais touchés récupèrent automatiquement les améliorations. L'extra `[jobspy]` est conservé — pipx mémorise la commande d'installation d'origine, tu n'as rien à repréciser.

### À propos de l'assistant d'installation

`jobsearch --setup` est une app Textual autonome en 7 étapes : présentation de l'outil, ta clé API Anthropic (facultative à ce stade), ton sujet de recherche (type de contrat, durée minimale, pays prioritaires, domaines, seuil de score), le pré-filtre (jeu de mots-clés pré-rempli pour ton secteur, ou saisie libre), le prompt de scoring (pré-rempli avec tes réponses pour que ton premier scrape ne soit pas calibré sur la recherche de quelqu'un d'autre), ton profil de candidat, puis un récapitulatif de ce qui est configuré. Il n'écrase jamais un fichier existant sans demander, et se relance sans risque. `sources.yaml` (les sources de scraping elles-mêmes — requêtes de recherche, flux RSS) est aussi généré, à partir de ce même secteur et de ces mêmes pays — sans question supplémentaire, sans qu'aucune source ne reste calée sur la recherche d'origine de l'auteur (un stage informatique aux Pays-Bas).

**Fortement recommandé après l'installation :** personnalise encore le prompt de scoring et le pré-filtre depuis l'onglet Réglages de la TUI (touche `6`) avec quelques exemples réels de ta recherche — ça améliore nettement la précision du classement.

**Emplacement des données :** ta config et tes données vivent sous `JOBSEARCH_HOME`, qui vaut par défaut `~/.jobsearch/` (surchargeable via la variable d'environnement `JOBSEARCH_HOME`). `jobsearch` fonctionne de la même façon depuis n'importe quel répertoire. `python -m jobsearch.config` affiche les chemins effectifs, `python -m jobsearch.config --check-prompts` / `--check-wizard-defaults` sont des vérifications hors-ligne.

**Tu mets à jour une installation antérieure où les données vivaient dans le dépôt ?** Lance `jobsearch --migrate` pour un aperçu à blanc, puis `jobsearch --migrate --apply` pour copier réellement ta config et tes données vers `JOBSEARCH_HOME` (n'écrase jamais, ne supprime jamais les originaux).

---

## La TUI

```bash
jobsearch --tui
```

La TUI n'est qu'une **vue** sur les fichiers du pipeline, pas une base de données à part : les rapports, `data/offers/*.json` et `data/applications.json` restent lisibles et modifiables sans elle, et elle relit l'état courant à l'ouverture et avec `r`. Elle ne persiste que la file de l'onglet Génération (`data/gen_queue.json`) — tu peux donc reprendre le travail n'importe quand : scraper lundi, faire la revue mercredi.

### Les 6 onglets

| Onglet | Rôle |
|---|---|
| **1 · Offres** | Tableau des offres scorées, triable, avec panneau de détail. Coche les offres (`espace`) pour les mettre en file de génération — lignes cochées surlignées en vert, offres de la dernière session en bleu foncé. La colonne `✓` est une cascade à 5 états : `▶` cochée > `⛔` marquée invalide (offre morte/fermée/mauvais type de contrat — `X` pour marquer, motif au choix) > `⧉` doublon manuel confirmé > `✓` déjà candidatée > `?` doublon suspecté (même entreprise, non tranché — `m` pour confirmer). |
| **2 · Génération** | La file de génération, persistée dans `data/gen_queue.json`. Coche des lignes pour restreindre `g`/`L` à ce sous-ensemble — sinon toutes les offres en attente sont traitées. Lance la génération (lettre + CV, ou lettre seule) en tâche de fond. `N` attache une note par offre (`gen_notes`) injectée dans les prompts lettre et CV ; `T` enregistre directement dans le suivi sans rien générer — aucun appel API, aucun dossier — pour les offres qui n'ont besoin ni de lettre ni de CV (formulaire en ligne, CV générique). `o` ouvre l'offre dans le navigateur, `O` ouvre le dossier de la candidature générée. |
| **3 · Candidatures** | Parcours `output/candidatures/`, aperçu lettre/CV, régénération des PDF après retouche manuelle. `o` ouvre l'offre dans le navigateur, `O` ouvre le dossier de la candidature. |
| **4 · Suivi** | Statuts et notes de `applications.json` (envoyée / réponse reçue / entretien / acceptée / refusée / annulée, chacun colorisé). Le panneau de droite affiche la timeline de la candidature sélectionnée, reconstruite depuis son historique — dates déduites d'anciennes données suffixées d'un `~`. |
| **5 · Stats** | Métriques de retour (taux d'acceptation/réponse, délai moyen), répartition des statuts, localisation des offres en arbre pays → villes, heatmap d'activité quotidienne façon GitHub, histogramme des candidatures au cours du temps et candidatures par pays (courbes), ces deux derniers sur une échelle commune jour/semaine/mois (`t` la change), et arbre des parcours de statuts réellement suivis. `f` exporte le flux en diagramme de Sankey HTML autonome. |
| **6 · Réglages** | Tous les fichiers de config éditables (`.env`, `search.yaml`, `profile.yaml`, `sources.yaml`, les 3 prompts, les 2 templates HTML, `cv_data.yaml`), plus deux lignes dédiées, « Sources de scraping » et « Modèles Claude », avec leur état (personnalisé / défaut du paquet / absent). `entrée` sur une ligne de fichier l'ouvre dans l'éditeur ; un bandeau rappelle de personnaliser le prompt de scoring et le pré-filtre. `entrée` sur la ligne « Sources de scraping » ouvre une modale pour activer/désactiver individuellement une source de scraping (`espace` pour basculer, `échap` pour fermer) — écrit immédiatement dans `sources.yaml`, indépendamment du groupe `fr` basculé avec `--with`/`--without`. `entrée` sur la ligne « Modèles Claude » ouvre une modale listant le modèle de chaque tâche (filtre, lettre, CV) : `entrée` sur une tâche propose « Dernier Haiku/Sonnet/Opus (auto) » ou un modèle épinglé, `R` rafraîchit la liste, `échap` ferme — écrit immédiatement dans `search.yaml`. |

### Workflow de retouche d'une lettre

L'usage principal de l'onglet Candidatures : `e` pour ouvrir `lettre.md` dans ton éditeur, retoucher et enregistrer, revenir et presser `p` pour régénérer le PDF. La colonne « PDF » affiche **« ⚠ à régénérer »** dès que le `.md` est plus récent que le PDF.

### Raccourcis clavier (aussi via `?` dans la TUI)

| Contexte | Touche | Action |
|---|---|---|
| Global | `1`-`6` | Changer d'onglet |
| Global | `r` | Recharger les données depuis le disque |
| Global | `S` / `s` | Lancer scrape + filtre + rapport |
| Global | `Ctrl+S` | Idem, en incluant aussi les sources FR-only (groupe `fr`) |
| Global | `c` | Ouvrir la Claude Console (recharge crédit API) |
| Global | `?` | Aide |
| Global | `q` | Quitter |
| Offres | `espace` | Cocher/décocher pour génération |
| Offres | `o` | Ouvrir dans le navigateur |
| Offres | `+`/`-` | Ajuster le seuil de score |
| Offres | `h` | Masquer/afficher les offres déjà candidatées, les doublons et les invalides marqués |
| Offres | `d` / `D` | Cycler / choisir la période affichée |
| Offres | `g` | Aller à l'onglet Génération |
| Offres | `m` | Marquer/démarquer comme doublon d'une candidature existante |
| Offres | `X` | Marquer/démarquer comme invalide (motif au choix : fermée/annulée/mauvais type de contrat/autre ; retire aussi l'offre de la file de génération ; refusé si l'offre a déjà une candidature) |
| Génération | `espace` | Cocher/décocher une ligne de la file |
| Génération | `g` | Générer lettre + CV |
| Génération | `L` | Générer la lettre seule |
| Génération | `A` | Ajouter une offre manuelle (fichier HTML ou URL) à la file |
| Génération | `x` | Retirer de la file |
| Génération | `N` | Éditer la note de génération de l'offre sous le curseur (`gen_notes`, injectée dans les prompts lettre et CV au prochain `g`/`L`) |
| Génération | `T` | Enregistrer dans le suivi SANS générer (aucun appel API, aucun dossier — offres qui n'ont besoin ni de lettre ni de CV) |
| Génération | `o` | Ouvrir l'offre dans le navigateur |
| Génération | `O` | Ouvrir le dossier de la candidature générée |
| Candidatures | `entrée` | Basculer l'aperçu lettre ↔ CV |
| Candidatures | `e` | Ouvrir `lettre.md` dans l'éditeur |
| Candidatures | `p` | Régénérer `lettre.pdf` |
| Candidatures | `u` | Régénérer `cv_adapte.pdf` |
| Candidatures | `o` | Ouvrir l'offre dans le navigateur |
| Candidatures | `O` | Ouvrir le dossier de la candidature |
| Suivi | `s`/`a`/`i`/`j`/`v`/`z` | Statut → sent / answered / interview / rejected / accepted / cancelled |
| Suivi | `n` | Éditer la note |
| Suivi | `o` | Ouvrir l'offre dans le navigateur |
| Stats | `f` | Exporter le diagramme de Sankey du flux de statuts |
| Stats | `t` | Changer l'échelle de temps de l'histogramme et des courbes par pays (jour · semaine · mois) |
| Réglages | `entrée` | Sur une ligne de fichier : l'ouvrir dans l'éditeur. Sur la ligne « Sources de scraping » : ouvrir la modale de gestion des sources. Sur la ligne « Modèles Claude » : ouvrir la modale des modèles par tâche (`R` rafraîchit la liste) |

### Offres en doublon

La déduplication automatique ne peut pas attraper tous les doublons inter-sources. Le pipeline suit le principe « la machine suggère, l'utilisateur confirme » : les offres d'une entreprise déjà candidatée sont signalées `?`, et `m` ouvre une modale pour confirmer en une pression.

### Offres invalides

Marquer un doublon exige une candidature cible existante. Certaines offres meurent tout simplement d'elles-mêmes — fermées, annulées, ou dont le type de contrat ne convient finalement pas — sans rien à quoi les rattacher. `i` (onglet Offres) les marque comme invalides à la place : choisis un motif dans la modale, l'offre sort alors du bucket « à candidater » du rapport et de la file de génération, tout en restant visible avec un marqueur `⛔` (masquable aussi avec `h`). Refusé pour une offre ayant déjà une candidature — utilise plutôt le statut « annulée » (`z`, onglet Suivi) dans ce cas.

---

## La CLI

```bash
jobsearch --setup                          # assistant d'installation interactif
jobsearch --scrape                         # collecte + filtre + rapport
jobsearch --scrape --threshold 7           # seuil de pertinence personnalisé
jobsearch --scrape --with fr               # inclut aussi les sources FR-only (HelloWork, WTTJ FR)
jobsearch --scrape --without fr            # désactive un groupe activé par défaut
jobsearch --generate <offer_id>            # génère lettre + CV adapté pour une offre
jobsearch --generate <offer_id> --letter-only  # lettre seule (1 appel Sonnet au lieu de 2)
jobsearch --generate <offer_id> --notes "..."  # consigne ponctuelle injectée dans les prompts lettre et CV
jobsearch --record <offer_id>              # enregistre dans le suivi sans rien générer (aucun appel API)
jobsearch --tui                            # interface interactive
jobsearch --applications --json            # suivi en JSON (même forme que l'API)
jobsearch --serve                          # API HTTP + vue web mobile (extra `jobsearch[server]`)
jobsearch --mcp                            # serveur MCP pour Claude Code / app Claude (extra `jobsearch[mcp]`)
jobsearch --applications                   # tableau de suivi
jobsearch --mark <offer_id> <status>       # met à jour le statut d'une candidature
jobsearch --mark-duplicate <offer_id> <target_id>  # marque comme doublon d'une candidature existante
jobsearch --unmark-duplicate <offer_id>
jobsearch --mark-invalid <offer_id> --reason closed|cancelled|contract|other  # écarte une offre morte/hors-sujet
jobsearch --unmark-invalid <offer_id>
jobsearch --migrate [--apply]              # copie la config/données d'une install dans le dépôt vers JOBSEARCH_HOME
jobsearch --from-file <chemin.html> [--url-source URL] [--company NOM] [--title TITRE]
jobsearch --from-url <url>
jobsearch --letter-pdf <lettre.md> --company "Nom" [--title ...] [--location ...] [--dept ...] [--output out.pdf]
```

Liste complète des options et exemples : `jobsearch --help`.

### Modules en isolation (debug)

```bash
python -m jobsearch.scraper     # collecte uniquement
python -m jobsearch.filter      # filtre les offres déjà collectées
python -m jobsearch.report      # génère le rapport depuis les offres déjà filtrées
python -m jobsearch.generator <offer_id>
```

### Candidature manuelle (WTTJ, LinkedIn, etc.)

Pour les offres sur des sites non-scrappables automatiquement, enregistre la page et génère la candidature directement :

```bash
# Enregistrer sous "Page web complète" depuis ton navigateur, puis :
jobsearch --from-file ~/Downloads/offre.html --url-source "https://..." --company "Nom Entreprise"

# Ou, pour les sites sans JavaScript :
jobsearch --from-url https://example.com/jobs/offre

# Surcharger l'extraction si elle est incorrecte :
jobsearch --from-file offre.html --title "Stage Développeur" --company "Criteo"
```

---

## Accès distant — téléphone et agents

Installe l'extra optionnel, puis lance le serveur :

```bash
pipx install 'jobsearch[server]' ...        # ou : pip install -e '.[server]'
jobsearch --serve                           # 127.0.0.1:8000, lecture seule
```

Le même processus sert deux choses :

- **Une vue web mobile** — candidatures, statuts et chronologie, offres récentes, et le diagramme de flux des statuts. HTML simple, une colonne, sans JavaScript : utilisable dans Safari sur un téléphone, là où aucun terminal n'est accessible. Candidatures et offres se trient côté serveur (`?sort=`/`?dir=` — entreprise, statut, date de génération pour les candidatures ; score, date de collecte, entreprise pour les offres) et les offres se filtrent par session de scrape (`?session=YYYY-MM-DD`) — un `<select>` + bouton, toujours sans JS. Le changement de statut sur la page d'une candidature passe par un menu déroulant + bouton « Enregistrer » plutôt que six boutons séparés. La page Stats affiche tout ce que le suivi calcule déjà : répartition par statut, catégories larges, taux de réponse/acceptation et délai moyen, localisation, candidatures par mois — en plus du diagramme de flux Sankey.
- **Une API JSON** (`/api/applications`, `/api/offers`, `/api/stats`) pour qu'un agent puisse répondre aux questions sur ta recherche. `?q=` cherche dans l'entreprise, le titre **et les notes libres** — échéances, dates d'entretien et noms de recruteurs y vivent plutôt que dans des champs dédiés, ce qui permet à l'agent de restreindre côté serveur au lieu de tout rapatrier. Mêmes paramètres de tri/filtre que les pages web.
- **Notes éditables depuis le téléphone** — noter une échéance ou une date d'entretien depuis la page d'une candidature (nécessite `--allow-write`). `GET /api/applications/{id}` renvoie un objet unique joignant la candidature, l'offre et la lettre déjà envoyée — exactement ce qu'il faut pour rédiger une relance sans recoller trois sources.

### Sécurité

- Écoute sur `127.0.0.1` par défaut.
- Définis `JOBSEARCH_API_TOKEN` dans `config/.env` pour exiger un jeton (en-tête `X-API-Token` pour l'API, cookie pour les pages — ouvre `http://<hôte>:8000/?token=<ton jeton>` une fois, le jeton est aussitôt retiré de l'URL).
- **Le serveur refuse de démarrer** sur une adresse non locale si aucun jeton n'est défini.
- L'écriture (changement de statut) exige `--allow-write` ; sans lui, la route répond 403.

### L'exposer à ton téléphone

Utilise **Tailscale** : mets la machine et le téléphone sur le même tailnet, puis écoute sur l'adresse Tailscale de la machine :

```bash
jobsearch --serve --host 100.x.y.z --allow-write
```

Aucun port n'est ouvert sur Internet et le trafic est chiffré de bout en bout. Attention : la machine doit être allumée pour répondre — `JOBSEARCH_HOME` rend le déménagement vers un Raspberry Pi toujours allumé ou un petit VPS trivial si la contrainte devient gênante.

### Le rendre persistant — service launchd (macOS)

Lancer `jobsearch --serve` à la main dans un terminal ne survit ni à la fermeture de la session ni à un redémarrage. `jobsearch --service` gère un agent launchd utilisateur (`com.jobsearch.server`) qui le maintient actif, le démarre à la connexion, et le relance s'il plante :

```bash
jobsearch --service install --host 100.x.y.z --port 8767 --allow-write   # écrit et charge le plist
jobsearch --service status     # plist présent ?, agent chargé ?, processus vivant ?, adresse d'écoute
jobsearch --service logs       # fin du journal du service (JOBSEARCH_HOME/data/server.log)
jobsearch --service restart    # après une modif de config, ou si ça semble bloqué
jobsearch --service uninstall  # décharge et supprime le plist
```

`install` reprend les mêmes options `--host`/`--port`/`--allow-write` que `--serve`. `status` est la commande à lancer quand « ça ne marche plus » — elle indique le chemin du plist, si l'agent est chargé, si le processus est réellement vivant, et l'adresse sur laquelle il est configuré pour écouter. Le plist pointe vers l'exécutable `jobsearch` résolu à l'installation, et journalise stdout comme stderr dans `JOBSEARCH_HOME/data/server.log` — sans ça, un service qui plantait ne laissait aucune trace. Ce mode est propre à macOS (launchd) ; sur une autre plateforme, `--service` explique la situation et indique l'équivalent systemd `--user`, plutôt que d'échouer sur une trace brute.

### Serveur MCP — Claude Code / app Claude

**Installer l'extra et déclarer le serveur** (périmètre utilisateur — disponible dans toutes tes conversations, pas seulement ce dépôt) :

```bash
pipx inject jobsearch mcp          # ou : pip install -e '.[mcp]'
claude mcp add -s user jobsearch -- ~/.local/bin/jobsearch --mcp
claude mcp list                    # doit afficher « Connected »
```

Ajouter `--allow-write` à la fin pour autoriser les changements de statut depuis une conversation ; absent par défaut.


Pour qu'un client MCP (Claude Code, app Claude sur la machine) réponde directement aux questions sur ta recherche, sans passer par l'API HTTP :

```bash
pipx install 'jobsearch[mcp]' ...        # ou : pip install -e '.[mcp]'
jobsearch --mcp                          # transport stdio, lecture seule
jobsearch --mcp --allow-write            # autorise aussi le changement de statut
```

Exactement **4 outils**, volontairement peu nombreux (chaque outil déclaré coûte du contexte dans toutes les conversations du client, pas seulement celles sur la recherche d'emploi) :

| Outil | Rôle |
|---|---|
| `list_applications` | filtre `q`/`status`/`company`/`since`/`limit` — `q` cherche AUSSI dans les notes libres |
| `get_application` | dossier complet : candidature + offre + lettre déjà envoyée |
| `search_offers` | offres collectées non candidatées — `q`/`min_score`/`since`/`limit` |
| `mark_application_status` | change le statut ; refusé sans `--allow-write` |

Même principe que l'API HTTP : les échéances, dates d'entretien et noms de recruteurs vivent dans le champ de notes en texte libre, pas dans des champs dédiés — `q` y cherche aussi. Le schéma d'usage attendu est en deux temps : restreindre côté serveur (`list_applications(q=...)`), puis lire le petit sous-ensemble obtenu — jamais rapatrier toutes les candidatures.

Exemple de configuration client (`claude_desktop_config.json` ou équivalent Claude Code, transport stdio) :

```json
{
  "mcpServers": {
    "jobsearch": {
      "command": "jobsearch",
      "args": ["--mcp"]
    }
  }
}
```

Un transport `streamable-http` existe aussi (`--mcp --mcp-transport streamable-http --host H --port P`) pour un agent distant, avec le même garde-fou que `--serve` (refus de démarrer sur une adresse non-loopback sans `JOBSEARCH_API_TOKEN`) — mais **le SDK MCP 2.2.0 n'authentifie pas réellement les requêtes de ce transport par un jeton partagé** (seul un mécanisme OAuth complet est prévu). Ne l'expose donc jamais nu sur Internet : uniquement derrière un tunnel de confiance (Tailscale), comme `--serve --host <adresse Tailscale>`.

## Informations supplémentaires

### Le pipeline, étape par étape

```
Étape 1 — Scrape      : collecte les offres depuis les flux RSS et APIs
Étape 2 — Filtre      : Claude Haiku score chaque offre (0-10) selon tes critères
Étape 3 — Rapport     : rapport Markdown des offres pertinentes

            ↓ Tu lis le rapport, tu notes les offres qui t'intéressent

Étape 4 — Candidature : Claude Sonnet rédige lettre de motivation + CV adapté
```

### Coût API

Le filtrage utilise Claude Haiku via l'API Message Batches par défaut (−50% sur les tokens). La génération (lettre + CV) utilise Claude Sonnet, un appel par offre. Fermer le terminal pendant le filtrage ne fait rien perdre : le batch en cours est enregistré et repris (sans être refacturé) au prochain scrape. Recharge des crédits sur [console.anthropic.com](https://console.anthropic.com) si besoin — le scraping lui-même fonctionne sans crédit.

### Automatisation toutes les 48h (macOS/Linux)

```bash
crontab -e
```

```
0 8 */2 * * jobsearch --scrape >> ~/.jobsearch/data/cron.log 2>&1
```

### Personnalisation

- **Sujet de recherche** — `search.yaml` (type de contrat, durée, géographie, domaines, seuil de score, mots-clés du pré-filtre). Pour un vrai changement de sujet (ex. stage → CDI), vide aussi `data/offers/` pour ne pas garder d'offres scorées sur un ancien sujet.
- **Profil de candidat** — `profile.yaml` (formation, compétences, langues). Les compétences sont des paires libres catégorie/détail, pas forcément techniques.
- **Données du CV** — `cv_data.yaml`, source de vérité unique pour le CV master et le CV adapté ; `cv_master.md` est régénéré automatiquement — ne pas l'éditer à la main.
- **Prompts** — `config/prompts/{filter,cover_letter,cv_adapt}.txt`.
- **Templates PDF** — `config/templates/{cv_en,cover_letter}.html`, avec des marqueurs `{{NOM}}` remplacés avant le rendu WeasyPrint. Les polices sont embarquées localement (rendu hors-ligne) ; une image de signature `files/letter/signature.png` est insérée automatiquement si présente.
- **Sources** — `sources.yaml` est généré par l'assistant d'installation à partir de ton secteur et de tes pays prioritaires (requêtes de recherche, flux RSS, géographie LinkedIn/Indeed/Adzuna/WTTJ/HelloWork — voir `jobsearch/resources/source_presets.yaml`) ; relancer `jobsearch --setup` le régénère. Pour pivoter à la main à la place : ajoute des flux RSS, ou édite les entrées `jobspy`/`adzuna`/`wttj`/`hellowork` (pays, requête/intitulé, types de contrat) dans `sources.yaml`. Chaque source peut aussi être activée/désactivée individuellement via `enabled: false` (active par défaut) — soit directement dans le fichier, soit depuis le gestionnaire de sources de l'onglet Réglages (`6`, puis `o`).

Tout ceci s'édite directement, ou depuis l'onglet Réglages de la TUI (`6`).

### Notifications email (optionnel)

Ajoute dans `.env` pour recevoir un résumé email après chaque `--scrape` :

```
NOTIFY_EMAIL=your_email@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=ton_compte@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # App Password Gmail
```

### Modèles Claude

Chaque tâche (filtre, lettre, CV) a son propre modèle dans `search.yaml` (`models.<tâche>.model`), modifiable depuis l'onglet Réglages de la TUI (ligne « Modèles Claude ») :

- `auto:haiku` / `auto:sonnet` / `auto:opus` suit le dernier modèle de la famille. La liste vient de `client.models.list()` (gratuit), mise en cache 24 h dans `data/models_cache.json` ; quand le modèle résolu change, un avis s'affiche une seule fois dans le bandeau de la TUI et en sortie de la CLI. Hors ligne ou sans clé API, la dernière résolution connue (puis un défaut intégré) est utilisée — jamais bloquant.
- Un identifiant exact (ex. `claude-sonnet-5-5`) épingle le modèle.
- `local:<modèle>` est réservé à une future phase « modèle local » et refusé pour l'instant.
- `max_tokens` inclut les jetons de réflexion des modèles récents (Sonnet 5.5 réfléchit par défaut) : garder les 16000 par défaut pour la lettre et le CV. `effort` est optionnel et absent par défaut, car Haiku 4.5 le rejette.
- Les appels lettre et CV sur un modèle non Haiku utilisent le repli serveur en cas de refus (retour à un appel simple si l'API le rejette). Jamais utilisé pour le batch du filtre.

### Alerte de mise à jour

`jobsearch` vérifie la dernière release du dépôt public (`api.github.com`, anonyme, stdlib uniquement) et prévient quand une version plus récente existe — dans la sortie de la CLI (limité à une fois toutes les 15 minutes), dans la barre de statut de la TUI, et dans le pied de page de la vue web. Entièrement non-bloquant : la vérification réseau est mise en cache 24h dans `data/update_check.json`, et tout échec (hors ligne, quota dépassé, réponse inattendue) est ignoré en silence — jamais de ralentissement ni de commande cassée. Le message pointe vers `jobsearch --upgrade`.

### Résolution de problèmes

| Symptôme | Solution |
|---|---|
| "ANTHROPIC_API_KEY manquant" | Vérifie que `.env` contient `ANTHROPIC_API_KEY=sk-ant-...` |
| "Your credit balance is too low" | Recharge sur [console.anthropic.com](https://console.anthropic.com) — le scraping fonctionne sans crédits |
| "0 nouvelle(s) offre(s) collectée(s)" | Normal si le pipeline a tourné récemment (dédup) ; attends 48h ou ajoute des sources |
| "Aucune offre filtrée trouvée" au moment du rapport | Lance `jobsearch --scrape` en entier, le filtre doit tourner en premier |
| Peu d'offres pertinentes dans le rapport | Baisse le seuil : `jobsearch --scrape --threshold 5` |
| `offer_id` introuvable pour `--generate` | C'est le nom du fichier JSON sans `.json` dans `data/offers/` |

### Licence

MIT — voir [LICENSE](LICENSE).
