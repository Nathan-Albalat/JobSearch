# JobSearch

JobSearch collecte des offres d'emploi depuis des flux RSS et des API de sites d'annonces, note chacune par rapport à ta recherche avec Claude, et écrit un rapport des offres les plus pertinentes. Pour les offres que tu choisis, il rédige une lettre de motivation et un CV adaptés en PDF, puis t'aide à suivre tes candidatures.

Il fonctionne pour n'importe quel secteur (7 préréglages : informatique, santé, droit, commerce, marketing, ingénierie, enseignement, ou tes propres mots-clés), n'importe quel type de contrat et n'importe quel pays.

---

## Installation

```bash
pipx install git+https://github.com/Nathan-Albalat/JobSearch
jobsearch --setup     # configuration guidée
jobsearch --tui       # interface interactive
```

Nécessite Python 3.11 ou plus récent. Options :

- **LinkedIn / Indeed** : l'extra `[jobspy]`, qui exige Python 3.11 exactement. Sans lui, ces deux sources sont ignorées avec un message.
  ```bash
  pipx install "jobsearch[jobspy] @ git+https://github.com/Nathan-Albalat/JobSearch" --python python3.11
  ```
- **Sortie PDF** : nécessite la bibliothèque Pango.
  - macOS : `brew install pango`
  - Debian/Ubuntu : `apt install libpango-1.0-0 libpangoft2-1.0-0`
  - Windows : installer [MSYS2](https://www.msys2.org), lancer `pacman -S mingw-w64-x86_64-pango`, puis définir `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin`

  Sans Pango, la lettre est quand même écrite (en Markdown) et la candidature enregistrée ; seuls les PDF sont sautés.
- **Windows** : utilise Windows Terminal pour la TUI. Le service en arrière-plan (`--service`) n'existe que sur macOS.

Pour le développement : `pip install -e .` dans l'environnement virtuel du dépôt.

### Mettre à jour

```bash
jobsearch --upgrade
```

Avec pipx, la commande lance la mise à jour pour toi. Sous Windows, ou pour un dépôt cloné ou une installation pip classique, elle affiche la commande à lancer. Ta config, tes données et les extras installés sont conservés.

### Première configuration

`jobsearch --setup` te demande ta clé API Anthropic, ta recherche (contrat, durée, pays, domaines, seuil de score), ton secteur et ton profil. Il écrit ensuite les fichiers de config, dont les sources de scraping adaptées à ton secteur et à ton pays. Il n'écrase jamais un fichier sans demander : tu peux le relancer sans risque.

Ensuite, ajoute quelques exemples d'offres réelles de ta recherche dans le prompt de scoring (onglet Réglages, touche `6`) : le classement s'en trouve nettement meilleur.

La config et les données sont dans `~/.jobsearch/` (modifiable avec la variable d'environnement `JOBSEARCH_HOME`). `python -m jobsearch.config` affiche les chemins utilisés.

---

## Fonctionnement

```
1. Collecte       récupère les offres des flux RSS et des API d'annonces
2. Filtrage       Claude Haiku note chaque offre de 0 à 10
3. Rapport        rapport Markdown des offres pertinentes
                  ↓ tu choisis les offres qui t'intéressent
4. Candidature    Claude Sonnet rédige une lettre de motivation et un CV adapté
```

Les étapes 1 à 3 se lancent avec `jobsearch --scrape` (ou `S` dans la TUI). L'étape 4 se lance offre par offre.

---

## La TUI

```bash
jobsearch --tui
```

La TUI lit et écrit les mêmes fichiers que la CLI (`data/offers/`, `data/applications.json`) : tu peux t'arrêter et reprendre quand tu veux.

| Onglet | À quoi il sert |
|---|---|
| **1 · Offres** | Les offres notées, avec un panneau de détail. Coche des offres avec `espace` pour les mettre en file de génération. |
| **2 · Génération** | La file de génération : lettre + CV (`g`) ou lettre seule (`L`), consignes propres à une offre (`N`), ou enregistrement d'une candidature sans rien générer (`T`). |
| **3 · Candidatures** | Les lettres et CV générés. Modifie une lettre (`e`), puis régénère son PDF (`p`). |
| **4 · Suivi** | Statuts, notes et chronologie de chaque candidature. |
| **5 · Stats** | Taux de réponse, carte d'activité, localisation par pays et par ville, candidatures dans le temps, et parcours des candidatures. |
| **6 · Réglages** | Ouvre n'importe quel fichier de config dans ton éditeur, active ou désactive les sources, et choisis le modèle Claude de chaque tâche. |

### Raccourcis clavier

`?` dans la TUI affiche la liste complète.

| Onglet | Touche | Action |
|---|---|---|
| Tous | `1`–`6` | Changer d'onglet |
| Tous | `r` | Recharger les données depuis le disque |
| Tous | `S` / `s` | Collecte + filtrage + rapport (`Ctrl+S` inclut aussi les sources françaises) |
| Tous | `c` | Ouvrir la Claude Console (crédits API) |
| Tous | `q` | Quitter |
| Offres | `espace` | Mettre en file de génération |
| Offres | `o` | Ouvrir l'offre dans le navigateur |
| Offres | `+` / `-` | Monter / baisser le seuil de score |
| Offres | `h` | Masquer / afficher les offres candidatées, doublons et invalides |
| Offres | `d` / `D` | Changer la période affichée / choisir une session de collecte |
| Offres | `m` | Marquer comme doublon d'une candidature existante |
| Offres | `X` | Marquer comme invalide (fermée, annulée, mauvais contrat…) |
| Génération | `g` / `L` | Générer lettre + CV / lettre seule |
| Génération | `A` | Ajouter une offre à la main (fichier HTML ou URL) |
| Génération | `N` | Ajouter une consigne pour la lettre et le CV de cette offre |
| Génération | `T` | Enregistrer la candidature sans rien générer |
| Génération | `x` | Retirer de la file |
| Candidatures | `entrée` | Basculer l'aperçu entre lettre et CV |
| Candidatures | `e` / `p` | Modifier la lettre / régénérer son PDF |
| Candidatures | `u` | Régénérer le PDF du CV |
| Suivi | `s` `a` `i` `j` `v` `z` | Statut : envoyée, réponse reçue, entretien, refusée, acceptée, annulée |
| Suivi | `retour arrière` | Annuler le dernier changement de statut |
| Suivi | `n` | Modifier la note |
| Stats | `t` | Échelle de temps : jour, semaine, mois |
| Stats | `f` | Exporter le diagramme des parcours (Sankey) |
| Réglages | `entrée` | Ouvrir le fichier, ou le gestionnaire de sources / de modèles |

`o` ouvre toujours l'offre dans le navigateur, et `O` toujours le dossier de la candidature.

### Offres en double et offres invalides

Une même offre est parfois publiée sur plusieurs sites. Quand une offre vient d'une entreprise où tu as déjà candidaté, elle est signalée par `?` : appuie sur `m` pour confirmer que c'est un doublon.

Une offre fermée, annulée ou qui n'a pas le bon contrat peut être marquée invalide avec `X`. Elle sort alors de la liste « à candidater ». Pour une offre où tu as déjà candidaté, utilise plutôt le statut « annulée » (`z`).

---

## La CLI

```bash
jobsearch --scrape                          # collecte + filtrage + rapport
jobsearch --scrape --threshold 7            # seuil de score personnalisé
jobsearch --scrape --with fr                # utilise aussi les sources françaises
jobsearch --generate <offer_id>             # lettre + CV adapté
jobsearch --generate <offer_id> --letter-only
jobsearch --generate <offer_id> --notes "Insiste sur le projet X"
jobsearch --record <offer_id>               # enregistre la candidature, sans rien générer
jobsearch --applications                    # tableau des candidatures (--json pour du JSON)
jobsearch --mark <offer_id> <statut>        # change un statut
jobsearch --undo-status <offer_id>          # annule le dernier changement de statut
jobsearch --mark-duplicate <offer_id> <target_id>
jobsearch --mark-invalid <offer_id> --reason closed|cancelled|contract|other
jobsearch --letter-pdf <lettre.md> --company "Nom"    # rend le PDF d'une lettre à la demande
```

`offer_id` est le nom du fichier de l'offre dans `data/offers/`, sans `.json`. `jobsearch --help` liste toutes les options.

### Offres d'autres sites

Pour une offre que la collecte ne peut pas atteindre, enregistre la page depuis ton navigateur (« Page web, complète ») et génère à partir d'elle :

```bash
jobsearch --from-file ~/Downloads/offre.html --url-source "https://..." --company "Entreprise"
jobsearch --from-url https://example.com/jobs/offre     # sites qui fonctionnent sans JavaScript
```

Ajoute `--title` ou `--company` si les valeurs extraites sont fausses.

---

## Accès à distance

### Vue web et API

```bash
pipx inject jobsearch fastapi uvicorn python-multipart
jobsearch --serve                  # http://127.0.0.1:8000, lecture seule
```

La commande sert une vue web adaptée au téléphone (candidatures, chronologies, offres, stats) et une API JSON (`/api/applications`, `/api/offers`, `/api/stats`). Le paramètre de recherche `q` cherche aussi dans tes notes, là où vivent en général les échéances, dates d'entretien et noms de recruteurs.

**Sécurité :**
- Par défaut, le serveur n'écoute que sur ta propre machine.
- Pour y accéder depuis un autre appareil, définis `JOBSEARCH_API_TOKEN` dans `config/.env`, puis ouvre une fois `http://<hôte>:8000/?token=<jeton>`. Le serveur refuse de démarrer sur une adresse réseau sans jeton.
- Modifier des statuts ou des notes exige `--allow-write`.

**Depuis ton téléphone**, le plus simple est [Tailscale](https://tailscale.com) : mets l'ordinateur et le téléphone sur le même tailnet, puis lance

```bash
jobsearch --serve --host 100.x.y.z --allow-write
```

Aucun port n'est ouvert sur Internet. L'ordinateur doit rester allumé pour répondre.

### Le garder actif (macOS)

`jobsearch --service` fait tourner le serveur en arrière-plan, le démarre à l'ouverture de session et le relance s'il plante :

```bash
jobsearch --service install --host 100.x.y.z --port 8767 --allow-write
jobsearch --service status      # tourne-t-il, et sur quelle adresse ?
jobsearch --service logs
jobsearch --service restart     # par ex. après une mise à jour
jobsearch --service uninstall
```

### Serveur MCP (Claude Code, app Claude)

Il permet à Claude de répondre directement à des questions sur tes candidatures :

```bash
pipx inject jobsearch mcp
claude mcp add -s user jobsearch -- ~/.local/bin/jobsearch --mcp
```

Il expose quatre outils : `list_applications`, `get_application` (la candidature, l'offre et la lettre envoyée), `search_offers` et `mark_application_status`. Les changements de statut sont désactivés, sauf si tu ajoutes `--allow-write` à la commande.

Un transport réseau existe (`--mcp-transport streamable-http`), mais il ne vérifie pas le jeton lui-même : ne l'utilise que derrière Tailscale.

---

## Configuration

Chaque fichier s'ouvre depuis l'onglet Réglages (`6`) :

| Fichier | Contenu |
|---|---|
| `config/search.yaml` | Ta recherche : contrat, durée, pays, domaines, seuil, mots-clés du pré-filtre, modèles |
| `config/profile.yaml` | Ton profil : formation, compétences, langues |
| `config/sources.yaml` | Sources de scraping (générées par `--setup` pour ton secteur et ton pays) |
| `config/prompts/*.txt` | Prompts de scoring, de lettre et de CV |
| `config/templates/*.html` | Mises en page PDF du CV et de la lettre |
| `cv_master/cv_data.yaml` | Le contenu de ton CV, utilisé pour le CV de référence et les CV adaptés |
| `config/.env` | Clés API |

Si tu changes complètement de recherche (par ex. d'un stage à un CDI), vide aussi `data/offers/`, pour que les anciennes offres ne soient pas notées avec l'ancienne recherche.

### Modèles Claude

Chaque tâche (filtrage, lettre, CV) a son propre modèle, réglable depuis l'onglet Réglages ou dans `search.yaml` :

- `auto:haiku`, `auto:sonnet` ou `auto:opus` suit le modèle le plus récent de la famille, avec un avis quand il change.
- Un nom exact comme `claude-sonnet-5-5` épingle le modèle.

Les modèles récents comptent leur réflexion dans `max_tokens` : garde la valeur par défaut de 16000 pour la lettre et le CV.

### Coût

Le filtrage envoie les offres par lots (« batch »), ce qui divise le prix par deux. Un lot peut prendre jusqu'à une heure. Si tu fermes le terminal entre-temps, rien n'est perdu : il est repris à la collecte suivante, sans être refacturé. La génération coûte quelques centimes par candidature. La collecte des offres est gratuite.

### Résumé par e-mail (optionnel)

Ajoute dans `config/.env` pour recevoir un e-mail après chaque collecte :

```
NOTIFY_EMAIL=toi@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=toi@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # mot de passe d'application Gmail
```

### Collecte automatique (macOS/Linux)

Tous les deux jours à 8 h, avec `crontab -e` :

```
0 8 */2 * * jobsearch --scrape >> ~/.jobsearch/data/cron.log 2>&1
```

---

## Dépannage

| Problème | Solution |
|---|---|
| « ANTHROPIC_API_KEY manquant dans .env » | Ajoute `ANTHROPIC_API_KEY=sk-ant-...` dans `config/.env` |
| « Your credit balance is too low » | Recharge des crédits sur [console.anthropic.com](https://console.anthropic.com). La collecte des offres fonctionne toujours. |
| La collecte semble bloquée au filtrage | Elle attend le lot, ce qui peut prendre jusqu'à une heure. La barre de statut montre sa progression. |
| « 0 nouvelle(s) offre(s) collectée(s) » | Normal si tu as collecté récemment : les offres déjà vues sont ignorées. |
| Peu d'offres pertinentes | Baisse le seuil : `jobsearch --scrape --threshold 5` |

## Licence

MIT, voir [LICENSE](LICENSE).
