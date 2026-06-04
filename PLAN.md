# Plan — Automatisation Recherche de Stage

> Guide de développement à utiliser avec Claude Code.  
> Objectif : construire un pipeline qui tourne toutes les 48h, filtre les offres pertinentes via l'API Claude, et génère lettre + CV adapté à la demande.

---

## Architecture cible

```
projet-stage/
├── config/
│   ├── profile.yaml          # Ton profil (formation, compétences, préférences)
│   ├── sources.yaml          # Liste des flux RSS / URLs à scraper
│   └── prompts/
│       ├── filter.txt        # Prompt de pertinence offre ↔ profil
│       ├── cover_letter.txt  # Prompt génération lettre de motivation
│       └── cv_adapt.txt      # Prompt adaptation CV
├── data/
│   ├── seen_offers.json      # Historique des offres déjà vues (évite les doublons)
│   └── offers/               # Offres validées, une par fichier JSON
├── output/
│   └── candidatures/         # Dossiers générés par candidature (lettre + CV)
├── cv_master/
│   └── cv_master.md          # Ton CV "source de vérité" en Markdown structuré
├── src/
│   ├── scraper.py            # Collecte des offres (RSS + scraping léger)
│   ├── filter.py             # Appel API Claude pour scorer la pertinence
│   ├── report.py             # Génération du rapport de validation (HTML/Markdown)
│   └── generator.py          # Génération lettre + CV adapté
├── main.py                   # Point d'entrée principal
├── requirements.txt
└── .env                      # ANTHROPIC_API_KEY (jamais commité)
```

---

## Étapes de développement

### Phase 1 — Fondations (à faire en premier)

**Objectif** : avoir un projet qui tourne sans erreur, même avec des données fictives.

- [ ] **1.1** Initialiser le projet Python (`venv`, `requirements.txt`)
  - Dépendances : `anthropic`, `feedparser`, `requests`, `beautifulsoup4`, `pyyaml`, `python-dotenv`, `jinja2`
- [ ] **1.2** Créer la structure de dossiers
- [ ] **1.3** Rédiger `config/profile.yaml` — ton profil complet
  - Formation (INFO4, spécialités), compétences techniques, langues, préférences géographiques, types de stage recherchés
- [ ] **1.4** Rédiger `cv_master/cv_master.md` — CV en Markdown structuré
  - Sections : Résumé, Formation, Expériences, Compétences techniques, Projets, Langues
- [ ] **1.5** Configurer `.env` + vérifier que la clé API fonctionne avec un appel test

---

### Phase 2 — Scraper RSS (cœur du projet)

**Analogie** : le scraper est comme un facteur qui fait sa tournée toutes les 48h — il collecte le courrier (offres), trie ce qu'il a déjà livré (doublons), et pose le reste sur le bureau.

- [ ] **2.1** Identifier et lister les sources RSS dans `config/sources.yaml`
  - Cibles prioritaires : Indeed (RSS par recherche), APEC, HelloWork, RegionsJob
  - Format exemple :
    ```yaml
    sources:
      - name: "Indeed - Stage Informatique Lyon"
        url: "https://fr.indeed.com/rss?q=stage+informatique&l=Lyon"
        type: rss
      - name: "APEC - Stage Dev"
        url: "https://..."
        type: rss
    ```
- [ ] **2.2** Coder `src/scraper.py`
  - Lire chaque source RSS avec `feedparser`
  - Extraire : titre, entreprise, lieu, description, URL, date
  - Comparer avec `data/seen_offers.json` pour filtrer les doublons
  - Retourner la liste des nouvelles offres
- [ ] **2.3** Tester le scraper en isolation (`python -m src.scraper`)

---

### Phase 3 — Filtre par pertinence (API Claude)

**Analogie** : Claude joue le rôle d'un conseiller d'orientation qui lit chaque offre et te dit "ça colle avec ton profil ? Score : 7/10, parce que..."

- [ ] **3.1** Rédiger `config/prompts/filter.txt`
  - Le prompt doit inclure : ton profil injecté dynamiquement + l'offre + demande de score JSON
  - Format de réponse attendu :
    ```json
    {
      "score": 8,
      "pertinent": true,
      "raison": "Correspond à la formation INFO4, stack technique alignée...",
      "points_forts": ["Python", "Stage 6 mois", "Lyon"],
      "points_faibles": ["Exige 2 ans d'expérience"]
    }
    ```
- [ ] **3.2** Coder `src/filter.py`
  - Appel API Claude (modèle : `claude-haiku-4-5-20251001` pour réduire les coûts sur cette étape)
  - Parser la réponse JSON
  - Seuil de pertinence configurable (ex: score ≥ 6)
- [ ] **3.3** Tester sur 5 offres fictives

> ⚠️ **Point facturation** : utiliser Haiku pour le filtrage (moins cher), garder Sonnet pour la génération de documents.

---

### Phase 4 — Rapport de validation

**Objectif** : te présenter les offres filtrées de façon lisible pour que ta validation manuelle prenne < 10 minutes.

- [ ] **4.1** Coder `src/report.py`
  - Générer un fichier `rapport_YYYY-MM-DD.md` dans `data/`
  - Pour chaque offre retenue : titre, entreprise, lieu, score, raison, lien
  - Ajouter une case à cocher Markdown `- [ ]` par offre
- [ ] **4.2** (Optionnel) Générer une version HTML avec `jinja2` pour lecture plus confortable

---

### Phase 5 — Générateur de candidature

**Analogie** : c'est l'atelier de personnalisation — on prend le CV maître (la planche de bois brute) et on le taille précisément pour chaque offre (la mortaise).

- [ ] **5.1** Rédiger `config/prompts/cover_letter.txt`
  - Contexte : profil + offre complète → lettre de motivation en Markdown
  - Contraintes dans le prompt : ton (professionnel mais pas rigide), longueur (~350 mots), structure (accroche / pourquoi eux / pourquoi moi / conclusion)
- [ ] **5.2** Rédiger `config/prompts/cv_adapt.txt`
  - Contexte : CV maître + offre → CV réordonné/reformulé selon les mots-clés de l'offre
  - Sortie : Markdown avec les sections prioritaires remontées
- [ ] **5.3** Coder `src/generator.py`
  - Lire une offre depuis `data/offers/`
  - Générer lettre + CV adapté via API (Sonnet)
  - Sauvegarder dans `output/candidatures/<entreprise>_<date>/`
    - `lettre.md`
    - `cv_adapte.md`
- [ ] **5.4** (Optionnel) Convertir les `.md` en PDF avec `pandoc` ou `weasyprint`

---

### Phase 6 — Orchestration & automatisation

- [ ] **6.1** Coder `main.py`
  - Mode `--scrape` : Phase 2 + 3 + 4 (collecte et rapport)
  - Mode `--generate <offer_id>` : Phase 5 pour une offre spécifique
- [ ] **6.2** Planifier l'exécution automatique toutes les 48h
  - **Linux/macOS** : `cron` (`0 8 */2 * * cd /path/projet && python main.py --scrape`)
  - **Windows** : Planificateur de tâches Windows
- [ ] **6.3** Tester le pipeline complet de bout en bout

---

### Phase 7 — Améliorations optionnelles

> À traiter seulement si les phases 1-6 fonctionnent bien.

- [ ] **7.1** Ajouter d'autres sources (scraping direct WTTJ si pas de RSS, LinkedIn Jobs via URL publique)
- [ ] **7.2** Notifications par email quand de nouvelles offres sont trouvées (`smtplib`)
- [ ] **7.3** Interface web légère (Flask) pour valider les offres depuis le navigateur plutôt qu'un fichier Markdown
- [ ] **7.4** Historique et stats (combien de candidatures, taux de réponse si suivi manuel)

---

## Notes importantes

### Facturation API
- **Filtrage** (Phase 3) → `claude-haiku-4-5-20251001` : rapide et très bon marché
- **Génération** (Phase 5) → `claude-sonnet-4-6` : meilleure qualité pour les documents
- Estimation coût : ~0.01€ par offre filtrée, ~0.05€ par candidature générée

### Sécurité
- Ne jamais commiter `.env` dans Git
- Ajouter `.env` et `data/` au `.gitignore`

### Workflow avec Claude Code
- Utiliser Claude Code phase par phase, en lui donnant ce fichier comme contexte
- Commencer chaque session par : *"Voici le plan.md du projet, on travaille sur la Phase X"*
- Tester chaque phase en isolation avant de passer à la suivante

---

## Ordre de démarrage recommandé

```
Phase 1 → Phase 2 → test scraper seul
→ Phase 3 → test filtre seul  
→ Phase 4 → test rapport complet
→ Phase 5 → test générateur sur 1 offre
→ Phase 6 → automatisation
```