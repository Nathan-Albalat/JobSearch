"""Socle de configuration du pipeline : chemins (`Paths`) et valeurs (`Search`).

Ce module ne lit RIEN au niveau module (aucun `SEARCH = load_search()` global) :
un `import jobsearch.config` doit toujours réussir, même si `config/search.yaml`
n'existe pas ou contient une faute de frappe. Les valeurs ne sont résolues
qu'à l'appel explicite de `load_search()` / `get_search()`.
"""
from __future__ import annotations

import functools
import importlib.resources
import os
import re
import sys
from dataclasses import dataclass

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _package_resource_dir(*parts: str) -> str:
    """Chemin d'une ressource livrée avec le paquet `jobsearch` (prompts, templates, polices).

    Résolu via `importlib.resources` pour fonctionner aussi bien en mode
    éditable (`pip install -e .`) qu'installé réellement — un chemin construit
    à la main depuis `__file__` ne survivrait pas à une installation figée.
    """
    ref = importlib.resources.files("jobsearch")
    for part in parts:
        ref = ref / part
    return str(ref)


# Racine du paquet (code + ressources livrées : prompts, templates, polices).
# Ces ressources voyagent avec le code, jamais avec les données utilisateur.
PACKAGE_ROOT = PROJECT_ROOT

# Racine des données/config PERSONNELLES de l'utilisateur (search.yaml,
# profile.yaml, sources.yaml, cv_data.yaml, .env, data/, output/). Par défaut
# `~/.jobsearch/` — surchargeable pour pointer vers un autre emplacement, ou
# repointer vers le dépôt (`JOBSEARCH_HOME=.`) pour un usage "tout-en-un".
JOBSEARCH_HOME = os.path.abspath(
    os.environ.get("JOBSEARCH_HOME") or os.path.join(os.path.expanduser("~"), ".jobsearch")
)


@dataclass(frozen=True)
class Paths:
    """Regroupe les chemins du pipeline.

    Les prompts et templates HTML sont du CONTENU UTILISATEUR : ils sont
    éditables (calibrage du scoring, charte graphique de la lettre/CV), donc
    résolus en priorité depuis `JOBSEARCH_HOME/config/` — avec repli sur les
    défauts livrés dans le paquet (`jobsearch/resources/`, via
    `importlib.resources`) si l'utilisateur n'a encore rien personnalisé.
    Seules les polices restent exclusivement dans le paquet (personne
    n'édite une police). La configuration personnelle et les données se
    résolvent depuis `JOBSEARCH_HOME` (`~/.jobsearch/` par défaut).
    """

    config_dir: str = os.path.join(JOBSEARCH_HOME, "config")
    data_dir: str = os.path.join(JOBSEARCH_HOME, "data")
    cv_dir: str = os.path.join(JOBSEARCH_HOME, "cv_master")
    output_dir: str = os.path.join(JOBSEARCH_HOME, "output")

    @property
    def user_prompts_dir(self) -> str:
        """Prompts personnalisés par l'utilisateur (prioritaires s'ils existent)."""
        return os.path.join(self.config_dir, "prompts")

    @property
    def user_templates_dir(self) -> str:
        """Templates HTML personnalisés par l'utilisateur (prioritaires s'ils existent)."""
        return os.path.join(self.config_dir, "templates")

    @property
    def package_prompts_dir(self) -> str:
        """Défauts de référence livrés avec le paquet — jamais dans `JOBSEARCH_HOME`."""
        return _package_resource_dir("resources", "prompts")

    @property
    def package_templates_dir(self) -> str:
        """Défauts de référence livrés avec le paquet (dont les polices, non surchargeables)."""
        return _package_resource_dir("resources", "templates")

    # Alias conservés pour compatibilité (`cv_pdf.py` : base_url WeasyPrint,
    # les polices restant toujours dans le paquet quel que soit le template utilisé).
    @property
    def prompts_dir(self) -> str:
        return self.package_prompts_dir

    @property
    def templates_dir(self) -> str:
        return self.package_templates_dir

    @property
    def offers_dir(self) -> str:
        return os.path.join(self.data_dir, "offers")

    @property
    def env_path(self) -> str:
        """Emplacement de `.env` — config personnelle, dans `JOBSEARCH_HOME/config/`."""
        return os.path.join(self.config_dir, ".env")

    def prompt(self, name: str) -> str:
        """Résout un prompt : priorité à `JOBSEARCH_HOME/config/prompts/`, repli sur le paquet."""
        user_path = os.path.join(self.user_prompts_dir, name)
        if os.path.exists(user_path):
            return user_path
        return os.path.join(self.package_prompts_dir, name)

    def template(self, name: str) -> str:
        """Résout un template HTML : priorité à `JOBSEARCH_HOME/config/templates/`, repli sur le paquet."""
        user_path = os.path.join(self.user_templates_dir, name)
        if os.path.exists(user_path):
            return user_path
        return os.path.join(self.package_templates_dir, name)

    def offer(self, offer_id: str) -> str:
        return os.path.join(self.offers_dir, f"{offer_id}.json")

    def absolute(self) -> "Paths":
        """Conservée pour compatibilité (`jobsearch/cv_pdf.py`) — les chemins sont déjà
        absolus depuis `JOBSEARCH_HOME`/le paquet, donc simple no-op."""
        return self


PATHS = Paths()


def settings_files() -> list[dict]:
    """Liste des fichiers de configuration éditables, pour l'onglet Réglages de la TUI.

    Chaque entrée porte : `role` (description humaine), `path` (chemin résolu — priorité
    utilisateur, repli paquet pour prompts/templates), `kind` ("prompt"/"template"/"plain",
    utile pour savoir s'il faut copier avant édition), `state` ("personnalisé" / "défaut du
    paquet" / "absent") et `mtime` (datetime de dernière modification, ou None si absent).
    """
    import datetime as _dt

    entries: list[dict] = []

    def _mtime(path: str) -> _dt.datetime | None:
        try:
            return _dt.datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            return None

    def _plain(role: str, path: str) -> dict:
        exists = os.path.exists(path)
        return {
            "role": role,
            "path": path,
            "kind": "plain",
            "state": "personnalisé" if exists else "absent",
            "mtime": _mtime(path) if exists else None,
        }

    entries.append(_plain("Clés API", PATHS.env_path))
    entries.append(_plain("Sujet de recherche, filtres, scoring", os.path.join(PATHS.config_dir, "search.yaml")))
    entries.append(_plain("Profil du candidat", os.path.join(PATHS.config_dir, "profile.yaml")))
    entries.append(_plain("Sources à scraper", os.path.join(PATHS.config_dir, "sources.yaml")))

    for name, role in (
        ("filter.txt", "Prompt de scoring (filtrage)"),
        ("cover_letter.txt", "Prompt de génération de lettre"),
        ("cv_adapt.txt", "Prompt d'adaptation du CV"),
    ):
        user_path = os.path.join(PATHS.user_prompts_dir, name)
        package_path = os.path.join(PATHS.package_prompts_dir, name)
        path = PATHS.prompt(name)
        is_user = os.path.exists(user_path)
        is_package = os.path.exists(package_path)
        state = "personnalisé" if is_user else ("défaut du paquet" if is_package else "absent")
        entries.append({
            "role": role,
            "path": path,
            "kind": "prompt",
            "name": name,
            "state": state,
            "mtime": _mtime(path) if (is_user or is_package) else None,
        })

    for name, role in (
        ("cv_en.html", "Template HTML du CV"),
        ("cover_letter.html", "Template HTML de la lettre"),
    ):
        user_path = os.path.join(PATHS.user_templates_dir, name)
        package_path = os.path.join(PATHS.package_templates_dir, name)
        path = PATHS.template(name)
        is_user = os.path.exists(user_path)
        is_package = os.path.exists(package_path)
        state = "personnalisé" if is_user else ("défaut du paquet" if is_package else "absent")
        entries.append({
            "role": role,
            "path": path,
            "kind": "template",
            "name": name,
            "state": state,
            "mtime": _mtime(path) if (is_user or is_package) else None,
        })

    entries.append(_plain("Données du CV", os.path.join(PATHS.cv_dir, "cv_data.yaml")))

    return entries


# ---------------------------------------------------------------------------
# Valeurs de recherche (config/search.yaml)
# ---------------------------------------------------------------------------

DEFAULTS: dict = {
    "sujet": {
        "label": "stage en informatique et ingénierie logicielle",
        "type_contrat": "stage",
        "type_contrat_negatif": "une offre d'emploi",
        "expertise_recruteur": "spécialisé dans les stages en informatique et ingénierie logicielle",
    },
    "duree": {
        "min_semaines": 22,
        "texte": "22 semaines minimum",
        "applicable": True,
    },
    "disponibilite": {
        "debut_iso": "2027-03-15",
        "texte_fr": "15 mars 2027",
        "texte_en": "March 15th 2027",
    },
    "geo": {
        "priorite": ["Pays-Bas", "Belgique", "Suisse", "Allemagne", "Italie"],
        "secondaire": ["Europe (hors pays frontaliers)"],
        "dernier_recours": ["Hors Europe"],
        "notes": "Stage à l'étranger obligatoire. France possible seulement si aucune option étrangère viable.",
        "details": [
            "**Pays-Bas** : priorité absolue — Amsterdam, Eindhoven, Delft, Rotterdam, Utrecht sont toutes des villes tech majeures avec de nombreuses entreprises internationales",
            "**Belgique** : Bruxelles, Gand, Louvain — bonne densité tech, proche géographiquement, stage facilité par la francophonie partielle",
            "**Suisse** : Zurich, Lausanne, Bâle — excellent niveau technique, salaires de stage élevés, mais coût de la vie important",
            "**Allemagne** : Berlin, Munich, Hambourg, Francfort — vaste marché, nombreuses offres tech, langue souvent pas requise pour les postes internationaux",
            "**Remote Europe** : acceptable, mais préférer les offres avec un ancrage géographique européen (pas uniquement \"global remote\" sans fuseau horaire spécifié)",
            "**France** : acceptable mais pas prioritaire — à scorer normalement sur le contenu technique sans bonus géographique",
            "**Hors Europe** : pénaliser légèrement, sauf si le contenu technique est exceptionnel",
        ],
    },
    "domaines": {
        "liste": [
            "Développement logiciel (backend, fullstack)",
            "Data Engineering",
            "Intelligence Artificielle / Machine Learning",
            "Informatique générale",
        ],
        "ouverture": "Ouvert à d'autres domaines informatique si le projet est intéressant",
    },
    "scoring": {
        "seuil": 6,
        "bonus": [
            "Stack Python ou JS/TS",
            "Domaine IA/ML ou Data",
            "Pays-Bas",
            "Entreprise internationale",
        ],
        "malus": [
            "Exige expérience professionnelle > 1 an",
            "Uniquement présentiel hors Europe",
            "Stage < 22 semaines",
        ],
        "titre_bonus": ["Junior", "Intern"],
        "penalite_diplome": True,
    },
    "prefilter": {
        "enabled": True,
        "desc_chars": 400,
        "keywords": {
            "langages": [
                "python", "java", "javascript", "typescript", "golang", "rust", "scala",
                "kotlin", "swift", "php", "ruby", "c++", "c#", "matlab",
            ],
            "frameworks_outils": [
                "django", "flask", "fastapi", "spring", "react", "vue", "angular",
                "node", "tensorflow", "pytorch", "scikit", "docker", "kubernetes",
                "git", "sql", "nosql", "mongodb", "postgresql", "aws", "azure", "gcp",
                "linux", "api",
            ],
            "roles": [
                "software", "developer", "engineer", "backend", "frontend", "fullstack",
                "devops", "data", "data science", "machine learning", "deep learning", "nlp",
                "computer vision", "embedded", "firmware", "robotics", "cybersecurity",
                "cloud", "sre", "mlops", "analyst", "tech", "it", "ict", "digital",
                "coding", "programming",
            ],
            "termes_fr_nl_de": [
                "développeur", "ingénieur", "informatique", "logiciel", "données",
                "apprentissage automatique", "intelligence artificielle", "programmation",
                "systèmes embarqués", "réseaux", "cybersécurité",
                "digitaal", "coderen", "informatica", "datenexperte",
            ],
            "contrat": ["intern", "internship", "stage", "werkstudent"],
        },
        "exclude_keywords": [],
    },
    "dedup": {
        "title_noise": [
            "stage", "internship", "intern", "h/f", "f/h", "m/f", "f/m",
            "m/w/d", "w/m/d", "cdi", "cdd",
        ],
        "title_noise_extra_regex": r"20\d\d",
        "similarity": 0.9,
    },
    "models": {
        "filter": {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "batch_min_offers": 6,
            "batch_poll_seconds": 30,
        },
        "generate": {
            "model": "claude-sonnet-4-6",
            "max_tokens": 2048,
        },
    },
    "sources": {
        "enabled_groups": [],
    },
    "source_defaults": {
        "hellowork": {
            "pages": 2,
            "delay": 0.3,
            "c": "Stage",
        },
        "jobspy": {
            "sites": ["linkedin", "indeed"],
            "search_term": "software intern",
            "results_wanted": 30,
            "hours_old": 72,
            "country_indeed": "netherlands",
            "fetch_description": True,
        },
        "adzuna": {
            "country": "nl",
            "what": "internship software",
            "results_per_page": 50,
            "pages": 1,
        },
        "wttj": {"pages": 3, "delay": 0.3, "contract_types": [], "max_offers": 0},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Fusion récursive : `override` a priorité, mais ne supprime aucune clé de `base`."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_search(config_dir: str = PATHS.config_dir) -> dict:
    """Charge `search.yaml` fusionné au-dessus de DEFAULTS. Jamais d'exception si absent."""
    path = os.path.join(config_dir, "search.yaml")
    if not os.path.exists(path):
        return dict(DEFAULTS)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULTS, data)


@functools.lru_cache(maxsize=8)
def get_search(config_dir: str = PATHS.config_dir) -> "Search":
    return Search(load_search(config_dir))


def _joined(values) -> str:
    if isinstance(values, str):
        return values
    return ", ".join(values or [])


class Search:
    """Vue typée sur les valeurs fusionnées de `search.yaml`."""

    def __init__(self, data: dict):
        self.data = data

    # -- accès bruts --------------------------------------------------
    def get(self, *path, default=None):
        node = self.data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # -- raccourcis usuels ---------------------------------------------
    @property
    def threshold(self) -> int:
        return int(self.get("scoring", "seuil", default=6))

    @property
    def prefilter_enabled(self) -> bool:
        return bool(self.get("prefilter", "enabled", default=True))

    @property
    def prefilter_desc_chars(self) -> int:
        return int(self.get("prefilter", "desc_chars", default=400))

    @property
    def prefilter_keywords(self) -> tuple:
        """Mots-clés du pré-filtre, aplatis, normalisés (lower/strip) et dédupliqués."""
        groups = self.get("prefilter", "keywords", default={}) or {}
        flat: list[str] = []
        if isinstance(groups, dict):
            for kws in groups.values():
                flat.extend(kws or [])
        else:
            flat.extend(groups or [])
        normalized = [kw.lower().strip() for kw in flat if kw and kw.strip()]
        return tuple(dict.fromkeys(normalized))

    @property
    def prefilter_exclude_keywords(self) -> tuple:
        excl = self.get("prefilter", "exclude_keywords", default=[]) or []
        normalized = [kw.lower().strip() for kw in excl if kw and kw.strip()]
        return tuple(dict.fromkeys(normalized))

    @property
    def dedup_title_noise(self) -> tuple:
        noise = self.get("dedup", "title_noise", default=[]) or []
        return tuple(dict.fromkeys(n.lower().strip() for n in noise if n and n.strip()))

    @property
    def dedup_extra_regex(self) -> str:
        return self.get("dedup", "title_noise_extra_regex", default=r"20\d\d")

    @property
    def dedup_similarity(self) -> float:
        return float(self.get("dedup", "similarity", default=0.9))

    @property
    def filter_model(self) -> str:
        return self.get("models", "filter", "model", default="claude-haiku-4-5-20251001")

    @property
    def filter_max_tokens(self) -> int:
        return int(self.get("models", "filter", "max_tokens", default=512))

    @property
    def batch_min_offers(self) -> int:
        return int(self.get("models", "filter", "batch_min_offers", default=6))

    @property
    def batch_poll_seconds(self) -> int:
        return int(self.get("models", "filter", "batch_poll_seconds", default=30))

    @property
    def generate_model(self) -> str:
        return self.get("models", "generate", "model", default="claude-sonnet-4-6")

    @property
    def generate_max_tokens(self) -> int:
        return int(self.get("models", "generate", "max_tokens", default=2048))

    @property
    def enabled_groups(self) -> list[str]:
        groups = self.get("sources", "enabled_groups", default=[]) or []
        return list(groups)

    def source_defaults(self, kind: str) -> dict:
        return dict(self.get("source_defaults", kind, default={}) or {})

    # -- placeholders des prompts ---------------------------------------
    def placeholders(self) -> dict:
        sujet = self.get("sujet", default={}) or {}
        duree = self.get("duree", default={}) or {}
        dispo = self.get("disponibilite", default={}) or {}
        geo = self.get("geo", default={}) or {}
        domaines = self.get("domaines", default={}) or {}
        scoring = self.get("scoring", default={}) or {}

        duree_texte = duree.get("texte", "") if duree.get("applicable", True) else ""
        geo_details = "\n".join(f"- {d}" for d in (geo.get("details") or []))

        return {
            "sujet": sujet.get("label", ""),
            "type_contrat": sujet.get("type_contrat", ""),
            "type_contrat_negatif": sujet.get("type_contrat_negatif", ""),
            "expertise": sujet.get("expertise_recruteur", ""),
            "duree": duree_texte,
            "duree_min_semaines": duree.get("min_semaines", ""),
            "date_dispo": dispo.get("texte_fr", ""),
            "date_dispo_en": dispo.get("texte_en", ""),
            "date_dispo_iso": dispo.get("debut_iso", ""),
            "pays_prioritaires": _joined(geo.get("priorite")),
            "pays_secondaires": _joined(geo.get("secondaire")),
            "pays_dernier_recours": _joined(geo.get("dernier_recours")),
            "geo_notes": geo.get("notes", ""),
            "geo_details": geo_details,
            "domaines": _joined(domaines.get("liste")),
            "domaines_ouverture": domaines.get("ouverture", ""),
            "seuil": scoring.get("seuil", ""),
            "bonus": _joined(scoring.get("bonus")),
            "malus": _joined(scoring.get("malus")),
            "titre_bonus": _joined(scoring.get("titre_bonus")),
            "criteres": self.render_criteria_text(),
        }

    def render_criteria_text(self) -> str:
        sujet = self.get("sujet", default={}) or {}
        duree = self.get("duree", default={}) or {}
        dispo = self.get("disponibilite", default={}) or {}
        label = sujet.get("type_contrat", "Stage")
        label = label[:1].upper() + label[1:] if label else label
        duree_txt = f", {duree.get('texte', '')}" if duree.get("applicable", True) else ""
        return f"{label} recherché{duree_txt}, disponible à partir du {dispo.get('texte_fr', '')}"


# ---------------------------------------------------------------------------
# Templates : remplacement de placeholders {clé}
# ---------------------------------------------------------------------------

_warned_placeholders: set = set()


def fill_template(template: str, values: dict, warn: bool = True) -> str:
    """Remplace {clé} sans interpréter les accolades littérales (exemples JSON).

    Un placeholder non fourni reste littéral (jamais d'exception) — un
    avertissement est émis une seule fois par run (mémorisé dans un `set`
    module-level) pour ne pas polluer la sortie sur des centaines d'offres.
    """
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    if warn:
        for placeholder in unfilled_placeholders(template):
            if placeholder not in _warned_placeholders:
                _warned_placeholders.add(placeholder)
                print(
                    f"Avertissement : placeholder {{{placeholder}}} non renseigné, laissé tel quel dans le prompt.",
                    file=sys.stderr,
                )
    return template


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def unfilled_placeholders(text: str) -> list[str]:
    """Liste les placeholders `{xxx}` restants (hors accolades JSON doublées `{{`/`}}`)."""
    # Une accolade JSON doublée type `{{` ne matche pas `_PLACEHOLDER_RE` (nécessite
    # un identifiant Python valide entre crochets), donc rien à filtrer de plus ici.
    return sorted(set(_PLACEHOLDER_RE.findall(text)))


# ---------------------------------------------------------------------------
# Compatibilité : ré-exports / alias pour les modules existants
# ---------------------------------------------------------------------------

def load_profile_text(config_dir: str, search: "Search | None" = None) -> str:
    """Réexport tardif pour éviter un import circulaire (défini dans jobsearch.filter)."""
    from jobsearch.filter import load_profile_text as _impl

    return _impl(config_dir, search=search)


def load_sources(config_dir: str) -> list[dict]:
    from jobsearch.scraper import load_sources as _impl

    return _impl(config_dir)


def load_cv_data(cv_dir: str) -> dict:
    from jobsearch.cv_pdf import load_cv_data as _impl

    return _impl(cv_dir)


def is_tech_offer(offer: dict, search: "Search | None" = None) -> bool:
    from jobsearch.filter import is_tech_offer as _impl

    return _impl(offer, search=search)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Affiche les chemins et valeurs effectives de configuration")
    parser.add_argument("--check-prompts", action="store_true", help="Rend les 3 prompts et échoue si un placeholder reste inconnu")
    args = parser.parse_args()

    if args.check_prompts:
        from jobsearch.filter import build_system_prompt, load_profile_text as _load_profile_text

        search = get_search(PATHS.config_dir)
        unknown_total: list[str] = []
        for name in ("filter.txt", "cover_letter.txt", "cv_adapt.txt"):
            path = PATHS.prompt(name)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                template = f.read()
            profile_text = _load_profile_text(PATHS.config_dir, search=search) if name == "filter.txt" else ""
            rendered = fill_template(template, {**search.placeholders(), "profile": profile_text}, warn=False)
            remaining = unfilled_placeholders(rendered)
            # Les placeholders spécifiques à l'offre (title, company, ...) sont attendus non résolus ici.
            known_offer_placeholders = {
                "title", "company", "location", "url", "description",
                "cv_summary", "points_forts", "cv_data",
            }
            unexpected = [p for p in remaining if p not in known_offer_placeholders]
            if unexpected:
                print(f"{name} : placeholders inconnus restants : {unexpected}")
                unknown_total.extend(unexpected)
        if unknown_total:
            sys.exit(1)
        print("OK : aucun placeholder inconnu dans les 3 prompts.")
        sys.exit(0)

    print(f"PACKAGE_ROOT   : {PACKAGE_ROOT}")
    print(f"JOBSEARCH_HOME : {JOBSEARCH_HOME}")
    print(f"PATHS          : {PATHS}")
    search = get_search(PATHS.config_dir)
    print(f"threshold      : {search.threshold}")
    print(f"filter_model   : {search.filter_model}")
    print(f"generate_model : {search.generate_model}")
    print(f"prefilter kws  : {len(search.prefilter_keywords)} mot(s)-clé(s)")
    print(f"enabled_groups : {search.enabled_groups}")
