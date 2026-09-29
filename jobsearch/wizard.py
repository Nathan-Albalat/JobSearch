"""Assistant d'installation interactif (`jobsearch --setup`).

App Textual autonome (distincte de `jobsearch.tui.StageTUI`) qui accompagne
un tiers dans les étapes de première configuration : présentation, clés API
(Anthropic + optionnelles), sujet de recherche, pré-filtre, profil, aperçu
du prompt de scoring (non sautable, paramétré avec le sujet ET le profil —
d'où le profil collecté avant), récap.

Motif « aperçu puis confirmation » repris de `jobsearch/migrate.py` : rien
n'est écrit sur le disque sans que le contenu ait été montré, et un fichier
déjà présent n'est jamais écrasé sans confirmation explicite. Navigation :
`échap` revient à l'étape précédente (les réponses déjà saisies sont
conservées) — sauf sur la première étape, où `échap` quitte l'assistant.
Toutes les écritures sont différées jusqu'à la confirmation du récapitulatif
final : revenir en arrière et re-valider ne redemande donc jamais de
confirmation d'écrasement sur un fichier que l'assistant vient lui-même
d'écrire.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime

import yaml
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, Static, TextArea

from jobsearch.config import DEFAULTS, PATHS, Search, _deep_merge, fill_template

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


class _Back:
    """Sentinelle : « revenir à l'étape précédente », distincte des données
    d'un écran et de l'annulation complète de l'assistant."""

    def __repr__(self) -> str:  # pragma: no cover - confort de debug
        return "BACK"


BACK = _Back()


# ---------------------------------------------------------------------------
# Aide à la génération de contenu (fonctions pures, testables sans TUI)
# ---------------------------------------------------------------------------


def parse_csv(value: str, default: list[str] | None = None) -> list[str]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items if items else list(default or [])


def _yaml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


_MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
_MOIS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _ordinal_en(day: int) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def parse_disponibilite(value: str) -> dict:
    """Dérive `debut_iso`/`texte_fr`/`texte_en` depuis une saisie libre de
    date de disponibilité (JJ/MM/AAAA ou AAAA-MM-JJ). Vide si non renseignée ;
    conservée telle quelle en texte (ISO vide) si le format n'est pas reconnu,
    plutôt que de rejeter la saisie."""
    value = (value or "").strip()
    if not value:
        return {"debut_iso": "", "texte_fr": "", "texte_en": ""}
    dt = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        return {"debut_iso": "", "texte_fr": value, "texte_en": value}
    return {
        "debut_iso": dt.strftime("%Y-%m-%d"),
        "texte_fr": f"{dt.day} {_MOIS_FR[dt.month - 1]} {dt.year}",
        "texte_en": f"{_ordinal_en(dt.day)} {_MOIS_EN[dt.month - 1]} {dt.year}",
    }


def render_geo_notes(pays: list[str], region_ville: str) -> str:
    """Décrit la géographie de l'utilisateur (injecté dans le prompt via
    {geo_notes}) — dérivé des pays choisis, jamais des notes de l'exemple
    livré (qui peuvent porter le contexte d'un tiers)."""
    region_ville = (region_ville or "").strip()
    if pays:
        base = f"Priorité géographique : {', '.join(pays)}."
    else:
        base = "Aucune priorité géographique particulière renseignée."
    if region_ville:
        base = f"{base} Priorité locale : {region_ville}."
    return base


def render_geo_details(pays: list[str]) -> list[str]:
    """Bloc réellement injecté dans le barème du prompt ({geo_details}) —
    une entrée générique par pays choisi, jamais celles de l'exemple livré."""
    if not pays:
        return [
            "**Zone non précisée** : à définir avant le premier scrape "
            "(config/search.yaml → geo.details)."
        ]
    details = [f"**{p}** : zone prioritaire pour cette recherche." for p in pays]
    details.append(
        "**Reste** : à scorer normalement sur le contenu technique, sans bonus géographique."
    )
    return details


# Opposé du type de contrat choisi, injecté dans le prompt via
# {type_contrat_negatif} — TOUJOURS dérivé du contrat choisi, jamais laissé
# retomber sur la valeur de l'exemple livré (« une offre d'emploi », qui
# n'a de sens que pour un stage) : un CDI décrit avec ce défaut produirait un
# prompt contradictoire avec lui-même (« évaluer une offre de CDI, et non une
# offre d'emploi »).
_TYPE_CONTRAT_NEGATIF = {
    "stage": "une offre d'emploi",
    "alternance": "un stage ou un CDI",
    "cdi": "un stage ou une alternance",
    "cdd": "un stage ou un CDI",
    "freelance": "un poste salarié",
}


def derive_type_contrat_negatif(type_contrat: str) -> str:
    """Dérive `sujet.type_contrat_negatif` du type de contrat choisi — repli
    neutre (jamais une devinette hasardeuse) si le type saisi est libre et non
    reconnu."""
    key = (type_contrat or "").strip().lower()
    for name, negatif in _TYPE_CONTRAT_NEGATIF.items():
        if name in key:
            return negatif
    return f"un contrat autre que {type_contrat}" if type_contrat else "un autre type de contrat"


# Mention de poste valorisée dans le barème ({titre_bonus}) — pertinente pour
# un contrat d'insertion (stage/alternance), pas pour un CDI où « Junior »/
# « Intern » n'ont pas de sens dans un titre de poste recherché. Sans repli
# vide explicite, l'ancien défaut ("Junior, Intern") restait injecté tel quel
# pour n'importe quel contrat.
_TITRE_BONUS = {
    "stage": ["Junior", "Intern"],
    "alternance": ["Junior", "Alternant", "Apprenti"],
    "cdi": [],
    "cdd": [],
    "freelance": [],
}


def derive_titre_bonus(type_contrat: str) -> list[str]:
    key = (type_contrat or "").strip().lower()
    for name, bonus in _TITRE_BONUS.items():
        if name in key:
            return bonus
    return []


def render_sujet_label(values: dict) -> str:
    """Dérive `sujet.label` ({sujet} dans les prompts lettre/CV) du type de
    contrat ET du premier domaine visé — jamais l'exemple livré ("stage en
    informatique et ingénierie logicielle"), qui contredirait un `type_contrat`
    différent (ex: CDI) dans le même prompt."""
    contrat = (values.get("type_contrat") or "").strip() or "poste"
    domaines = values.get("domaines") or []
    return f"{contrat} en {domaines[0]}" if domaines else contrat


def render_domaines_ouverture(values: dict) -> str:
    """Formulation neutre d'ouverture à d'autres domaines ({domaines_ouverture})
    — mentionne le premier domaine choisi quand il est connu plutôt que de
    reprendre tel quel le texte générique de l'exemple livré."""
    domaines = values.get("domaines") or []
    if domaines:
        return f"Ouvert à d'autres domaines proches de {domaines[0]} si le projet est intéressant"
    return "Ouvert à d'autres domaines proches si le projet est intéressant"


def render_scoring_bonus(values: dict) -> list[str]:
    """Dérivé des domaines et du pays prioritaire choisis — jamais ceux de
    l'auteur d'origine (ex: « Pays-Bas »)."""
    bonus = [f"Domaine {d}" for d in values.get("domaines", [])[:2]]
    pays = values.get("pays") or []
    if pays:
        bonus.append(f"Localisation : {pays[0]}")
    return bonus


def load_prefilter_presets() -> dict:
    """Jeux de mots-clés du pré-filtre par secteur, livrés avec le paquet
    (`jobsearch/resources/prefilter_presets.yaml`) — jamais ceux de
    l'utilisateur (`~/.jobsearch/`, non lu ici)."""
    with open(PATHS.prefilter_presets_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# Mots-clés du groupe `contrat` du pré-filtre, dérivés du type de contrat
# choisi à l'étape « sujet » — indépendant du secteur (preset ou saisie
# libre) choisi à l'étape « pré-filtre ». Recherche par sous-chaîne du type de
# contrat saisi (ex: "stage" matche "stage" et "stage de fin d'études").
_CONTRAT_KEYWORDS = {
    "stage": ["stage", "internship", "intern", "werkstudent"],
    "alternance": ["alternance", "apprentissage", "apprenticeship"],
    "cdi": ["cdi", "permanent", "full-time", "full time"],
    "cdd": ["cdd", "temporary", "fixed-term", "fixed term"],
    "freelance": ["freelance", "contractor", "independent"],
}


def derive_contrat_keywords(type_contrat: str) -> list[str]:
    """Dérive le groupe `contrat` du pré-filtre depuis le type de contrat
    choisi — c'est le levier n°1 du pivot vers un autre type de contrat
    (voir search.example.yaml)."""
    key = (type_contrat or "").strip().lower()
    for name, kws in _CONTRAT_KEYWORDS.items():
        if name in key:
            return kws
    return [key] if key else []


# ---------------------------------------------------------------------------
# Génération de sources.yaml — 4e occurrence de la famille de bugs « valeur
# d'auteur héritée » (voir CLAUDE.md, section « Garde-fou anti-régression ») :
# `config/sources.yaml` était jusqu'ici versionné tel quel, avec les requêtes
# personnelles de l'auteur (stage informatique aux Pays-Bas), jamais touché par
# `jobsearch --setup`. Ce qui suit dérive `sources.yaml` du secteur choisi à
# l'étape « pré-filtre » et du type de contrat/pays choisis à l'étape « sujet »
# — sans nouvelle question posée à l'utilisateur.
# ---------------------------------------------------------------------------


def load_source_presets() -> dict:
    """Catalogue de sources par secteur (`jobsearch/resources/source_presets.yaml`)
    — requêtes de recherche, flux à URL fixe et table géographique. Contenu du
    paquet, jamais lu depuis l'utilisateur (`~/.jobsearch/`) — même patron que
    `load_prefilter_presets`."""
    with open(PATHS.source_presets_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _country_name_to_code() -> dict[str, str]:
    """Table nom-de-pays (anglais, canonique) -> code ISO, reconstruite depuis
    `jobsearch.tracker._COUNTRY_NAMES` plutôt que dupliquée ici — un seul
    endroit à mettre à jour si un pays est ajouté. "UK" est exclu : c'est un
    alias de code vers le même nom que "GB" dans `_COUNTRY_NAMES`, et seul
    "GB" existe comme clé dans `source_presets.yaml` → `geo`."""
    from jobsearch.tracker import _COUNTRY_NAMES

    return {name.lower(): code for code, name in _COUNTRY_NAMES.items() if code != "UK"}


def resolve_country_code(pays_libre: str) -> str | None:
    """Résout un nom de pays saisi librement à l'étape « sujet » (ex: "Pays-Bas",
    "France", "Netherlands") en code ISO 2 lettres, via les tables déjà
    maintenues dans `jobsearch.tracker` (alias FR/EN/langue locale -> nom
    canonique anglais -> code) — pas de troisième table de noms de pays.
    `None` si non reconnu : la géographie n'est alors jamais devinée, la
    source correspondante est simplement omise (voir `render_sources_yaml`)."""
    from jobsearch.tracker import _COUNTRY_ALIASES

    key = (pays_libre or "").strip().lower()
    if not key:
        return None
    canonical = _COUNTRY_ALIASES.get(key, key)
    return _country_name_to_code().get(canonical.lower())


# Terme anglais du type de contrat, pour les sources internationales
# (jobspy/Adzuna) — distinct de `_CONTRAT_KEYWORDS` (groupe de mots-clés du
# pré-filtre) et de `_TYPE_CONTRAT_NEGATIF` (formulation négative du prompt).
_CONTRAT_EN = {
    "stage": "internship",
    "alternance": "apprenticeship",
    "cdi": "permanent",
    "cdd": "temporary",
    "freelance": "freelance",
}


def derive_contrat_en(type_contrat: str) -> str:
    key = (type_contrat or "").strip().lower()
    for name, en in _CONTRAT_EN.items():
        if name in key:
            return en
    return type_contrat or ""


# Libellé français capitalisé du type de contrat, attendu par le paramètre `c`
# de HelloWork (ex: "Stage", "CDI").
_CONTRAT_FR_LABEL = {
    "stage": "Stage",
    "alternance": "Alternance",
    "cdi": "CDI",
    "cdd": "CDD",
    "freelance": "Freelance",
}


def derive_contrat_fr_label(type_contrat: str) -> str:
    key = (type_contrat or "").strip().lower()
    for name, label in _CONTRAT_FR_LABEL.items():
        if name in key:
            return label
    return (type_contrat or "").strip().capitalize()


# `contract_types` attendu par l'API WTTJ — seuls "internship"/"apprenticeship"
# ont été vérifiés en usage réel (voir CLAUDE.md, section WTTJ). Repli sur une
# liste vide (aucun filtre côté client, jamais une valeur inventée) pour les
# types de contrat non couverts ci-dessous.
_WTTJ_CONTRACT_TYPES = {
    "stage": ["internship", "apprenticeship"],
    "alternance": ["apprenticeship", "internship"],
    "cdi": ["full_time"],
    "cdd": ["fixed_term"],
    "freelance": ["freelance"],
}


def derive_wttj_contract_types(type_contrat: str) -> list[str]:
    key = (type_contrat or "").strip().lower()
    for name, types in _WTTJ_CONTRACT_TYPES.items():
        if name in key:
            return types
    return []


# `job_types` attendu par l'API Arbeitnow — seul "internship" a été vérifié en
# usage réel (config d'origine de ce dépôt). Repli sur une liste vide (aucun
# filtre) pour les types de contrat non couverts, jamais une valeur inventée.
_ARBEITNOW_JOB_TYPES = {
    "stage": ["internship"],
    "alternance": ["internship"],
    "cdi": ["full-time"],
    "cdd": ["contract"],
    "freelance": ["contract"],
}


def derive_arbeitnow_job_types(type_contrat: str) -> list[str]:
    key = (type_contrat or "").strip().lower()
    for name, types in _ARBEITNOW_JOB_TYPES.items():
        if name in key:
            return types
    return []


def _dump_sources_block(sources: list[dict]) -> str:
    """Sérialise une liste de sources, indentée sous la clé `sources:` — passe
    toujours par `yaml.safe_dump` (syntaxe garantie valide), les en-têtes de
    section restant, eux, des commentaires ajoutés à la main autour de ce bloc
    (voir `render_sources_yaml`) : pas de commentaire par entrée individuelle,
    contrairement à `search.example.yaml` qui, lui, préexistait déjà commenté."""
    if not sources:
        return ""
    text = yaml.safe_dump(sources, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return "\n".join(("  " + line if line else line) for line in text.rstrip("\n").split("\n")) + "\n"


def _source_context(values: dict, prefilter_values: dict | None = None) -> dict:
    """Dérive, en un seul endroit, tout ce qui dépend du secteur (preset ou
    saisie libre choisi à l'étape « pré-filtre ») et de la géographie/contrat
    (étape « sujet ») : requêtes métier, géographie résolue, flux à URL fixe.
    Factorise ce que `render_sources_yaml` ET `render_source_defaults`
    (`source_defaults.jobspy`/`source_defaults.adzuna` de `search.yaml`) ont
    besoin de dériver de façon identique — sans cette factorisation, les deux
    auraient fini par diverger silencieusement (c'est exactement ce type de
    duplication qui a produit la 5e occurrence du bug « valeur d'auteur
    héritée », voir CLAUDE.md)."""
    prefilter_values = prefilter_values or PREFILTER_DEFAULTS
    presets = load_source_presets()
    preset_name = (prefilter_values or {}).get("preset", "")
    preset = presets.get(preset_name) if preset_name not in ("", "libre") else None

    if preset:
        queries_en = list(preset.get("queries_en") or [])
        queries_fr = list(preset.get("queries_fr") or [])
        feeds = [dict(f) for f in (preset.get("feeds") or [])]
        secteur_label = (load_prefilter_presets().get(preset_name) or {}).get("label", preset_name)
    else:
        # Saisie libre (ou aucun secteur choisi à l'étape pré-filtre) : pas de
        # requêtes métier dédiées, seul le secteur nommé par l'utilisateur (s'il
        # y en a un) sert de terme de recherche. Jamais de repli sur les
        # requêtes informatiques par défaut : même biais que documenté pour le
        # pré-filtre (voir `render_prefilter_keywords`).
        custom_label = ((prefilter_values or {}).get("custom_label") or "").strip()
        queries_en = [custom_label] if custom_label else []
        queries_fr = [custom_label] if custom_label else []
        # Arbeitnow (généraliste) reste un repli raisonnable même sans secteur
        # identifié — c'est la seule source à URL fixe commune à tous les
        # presets, jamais une source informatique.
        arbeitnow_generic = next(
            (dict(f) for f in (presets.get("sante", {}).get("feeds") or []) if f.get("type") == "api_json"),
            None,
        )
        feeds = [arbeitnow_generic] if arbeitnow_generic else []
        secteur_label = custom_label or "(secteur non renseigné)"

    type_contrat = values.get("type_contrat") or "stage"
    contrat_en = derive_contrat_en(type_contrat)
    contrat_fr_label = derive_contrat_fr_label(type_contrat)
    pays = values.get("pays") or []

    geo_table = presets.get("geo") or {}
    primary_code = resolve_country_code(pays[0]) if pays else None
    primary_geo = geo_table.get(primary_code) if primary_code else None
    france_code = next((c for c in (resolve_country_code(p) for p in pays) if c == "FR"), None)
    france_is_primary = primary_code == "FR"

    # Arbeitnow : job_types régénéré depuis le type de contrat choisi, jamais
    # laissé au repli du preset (potentiellement calibré pour un autre contrat).
    for f in feeds:
        if f.get("type") == "api_json" and "job_types" in (f.get("params") or {}):
            f["params"]["job_types"] = derive_arbeitnow_job_types(type_contrat)

    return {
        "queries_en": queries_en,
        "queries_fr": queries_fr,
        "feeds": feeds,
        "secteur_label": secteur_label,
        "type_contrat": type_contrat,
        "contrat_en": contrat_en,
        "contrat_fr_label": contrat_fr_label,
        "pays": pays,
        "geo_table": geo_table,
        "primary_code": primary_code,
        "primary_geo": primary_geo,
        "france_code": france_code,
        "france_is_primary": france_is_primary,
    }


def render_sources_yaml(values: dict, prefilter_values: dict | None = None) -> str:
    """Génère `sources.yaml` depuis les réponses déjà collectées par
    l'assistant — AUCUNE nouvelle question posée : le secteur vient de l'étape
    « pré-filtre » (`prefilter_values["preset"]`), le type de contrat et les
    pays prioritaires de l'étape « sujet » (`values`). Contrairement à
    `render_search_yaml`, ce fichier est entièrement neuf (pas de
    `.example.yaml` à préserver ligne à ligne) : assemblé par blocs
    `yaml.safe_dump` (syntaxe garantie valide), séparés par des commentaires de
    section écrits à la main.

    Sans géographie reconnue (`resolve_country_code` -> None) ou sans requête
    de secteur, les sources concernées sont simplement omises plutôt que
    générées avec une valeur inventée — consignées dans l'en-tête du fichier
    ("Non générées automatiquement")."""
    ctx = _source_context(values, prefilter_values)
    queries_en = ctx["queries_en"]
    queries_fr = ctx["queries_fr"]
    feeds = ctx["feeds"]
    secteur_label = ctx["secteur_label"]
    type_contrat = ctx["type_contrat"]
    contrat_en = ctx["contrat_en"]
    contrat_fr_label = ctx["contrat_fr_label"]
    pays = ctx["pays"]
    geo_table = ctx["geo_table"]
    primary_code = ctx["primary_code"]
    primary_geo = ctx["primary_geo"]
    france_code = ctx["france_code"]
    france_is_primary = ctx["france_is_primary"]

    skipped: list[str] = []

    # --- jobspy (LinkedIn / Indeed) ---
    jobspy_sources: list[dict] = []
    if primary_geo and queries_en:
        search_term = f"{queries_en[0]} {contrat_en}".strip()
        jobspy_sources.append({
            "name": f"JobSpy - {search_term.capitalize()} {primary_geo['label']}",
            "type": "jobspy",
            "country": primary_code,
            "params": {
                "sites": ["linkedin", "indeed"],
                "search_term": search_term,
                "location": primary_geo["label"],
                "country_indeed": primary_geo["country_indeed"],
                "hours_old": 72,
                "results_wanted": 50,
            },
        })
        if len(pays) > 1:
            second_query = queries_en[1] if len(queries_en) > 1 else queries_en[0]
            broad_term = f"{second_query} {contrat_en}".strip()
            jobspy_sources.append({
                "name": f"JobSpy - {broad_term.capitalize()} ({', '.join(pays)})",
                "type": "jobspy",
                "country": "INTL",
                "params": {
                    "sites": ["linkedin"],
                    "search_term": broad_term,
                    "location": ", ".join(pays),
                    "hours_old": 72,
                    "results_wanted": 40,
                },
            })
    elif pays:
        skipped.append(
            f"JobSpy (LinkedIn/Indeed) — pays prioritaire {pays[0]!r} non reconnu dans la "
            "table géographique, source ignorée."
        )

    # --- Adzuna ---
    adzuna_sources: list[dict] = []
    if primary_geo and queries_en:
        if primary_geo.get("adzuna_supported"):
            what = f"{queries_en[0]} {contrat_en}".strip()
            adzuna_sources.append({
                "name": f"Adzuna {primary_code} - {what.capitalize()}",
                "type": "adzuna",
                "country": primary_code,
                "params": {"country": primary_code.lower(), "what": what, "pages": 2},
            })
        else:
            skipped.append(f"Adzuna — {primary_geo['label']} non couvert par l'API Adzuna, source ignorée.")

    # --- WTTJ (pays prioritaire) ---
    wttj_sources: list[dict] = []
    if primary_geo and queries_fr:
        contract_types = derive_wttj_contract_types(type_contrat)
        for q in queries_fr[:2]:
            entry = {
                "name": f"WTTJ - {q.capitalize()} {primary_geo['label']}",
                "type": "wttj",
                "country": primary_code,
                "params": {
                    "job_title": q,
                    "locations": {
                        "country": primary_geo["label"],
                        "country_code": primary_code,
                        "label": primary_geo["label"],
                        "latitude": primary_geo["latitude"],
                        "longitude": primary_geo["longitude"],
                    },
                    "pages": 3,
                },
            }
            if contract_types:
                entry["params"]["contract_types"] = list(contract_types)
            wttj_sources.append(entry)

    # --- WTTJ France + HelloWork (groupe optionnel `fr`) — seulement si la
    # France fait partie des pays prioritaires (n'importe quelle position),
    # sans quoi elles n'ont pas de sens (voir CLAUDE.md). WTTJ FR n'est générée
    # séparément que si la France N'EST PAS déjà le pays prioritaire (sinon
    # déjà couverte, en actif, par le bloc WTTJ ci-dessus).
    wttj_fr_sources: list[dict] = []
    hellowork_sources: list[dict] = []
    if france_code and not france_is_primary and queries_fr:
        fr_geo = geo_table.get("FR")
        contract_types = derive_wttj_contract_types(type_contrat)
        for q in queries_fr[:2]:
            entry = {
                "name": f"WTTJ - {q.capitalize()} FR",
                "type": "wttj",
                "group": "fr",
                "optional": True,
                "country": "FR",
                "params": {
                    "job_title": q,
                    "locations": {
                        "country": fr_geo["label"],
                        "country_code": "FR",
                        "label": fr_geo["label"],
                        "latitude": fr_geo["latitude"],
                        "longitude": fr_geo["longitude"],
                    },
                    "pages": 3,
                },
            }
            if contract_types:
                entry["params"]["contract_types"] = list(contract_types)
            wttj_fr_sources.append(entry)
    if france_code and queries_fr:
        # HelloWork est FR-only par nature : toujours en groupe optionnel `fr`,
        # que la France soit le pays prioritaire ou non (même choix que la
        # config d'origine de ce dépôt).
        for q in queries_fr[:2]:
            hellowork_sources.append({
                "name": f"HelloWork - {q.capitalize()} ({contrat_fr_label})",
                "type": "hellowork",
                "group": "fr",
                "optional": True,
                "country": "FR",
                "params": {"k": q, "c": contrat_fr_label, "pages": 2},
            })

    header_lines = [
        "# sources.yaml — généré par `jobsearch --setup`.",
        f"# Secteur : {secteur_label}",
        f"# Type de contrat : {type_contrat}",
        f"# Pays prioritaires : {', '.join(pays) if pays else '(non renseigné)'}",
        "#",
        "# `enabled: false` désactive une source individuellement, sans la supprimer",
        "# (onglet Réglages de la TUI, touche 6, ou édition directe de ce fichier).",
        "# `group`/`optional: true` : source désactivée par défaut, incluse avec",
        "# `jobsearch --scrape --with <groupe>` (ex: --with fr).",
    ]
    if skipped:
        header_lines.append("#")
        header_lines.append("# Non générées automatiquement (à ajouter soi-même si besoin) :")
        header_lines.extend(f"#   - {s}" for s in skipped)

    groups = [
        ("  # --- Flux à URL fixe (RSS / API JSON) ---", feeds),
        ("\n  # --- LinkedIn / Indeed (jobspy) ---", jobspy_sources),
        ("\n  # --- Adzuna (requiert ADZUNA_APP_ID / ADZUNA_APP_KEY dans .env) ---", adzuna_sources),
        ("\n  # --- Welcome to the Jungle (API publique v3, sans auth) ---", wttj_sources),
        ("\n  # --- WTTJ France (groupe optionnel 'fr') ---", wttj_fr_sources),
        ("\n  # --- HelloWork (France, groupe optionnel 'fr') ---", hellowork_sources),
    ]
    non_empty = [(comment, srcs) for comment, srcs in groups if srcs]

    if not non_empty:
        body = "sources: []\n"
    else:
        body = "sources:\n" + "".join(f"{comment}\n{_dump_sources_block(srcs)}" for comment, srcs in non_empty)

    return "\n".join(header_lines) + "\n\n" + body


# ---------------------------------------------------------------------------
# Génération de `source_defaults` (search.yaml) — 5e occurrence de la famille
# de bugs « valeur d'auteur héritée » (voir CLAUDE.md, section « Garde-fou
# anti-régression ») : `source_defaults.jobspy`/`source_defaults.adzuna` était
# jusqu'ici recopié tel quel depuis `search.example.yaml` par `render_search_yaml`
# — la plus grave des trois fuites relevées, puisque ce bloc est réellement lu
# au SCRAPE (`get_search().source_defaults(...)`, voir `jobsearch/scraper.py` et
# `jobsearch/scraper_jobspy.py`), pas seulement au scoring : une source qui omet
# un paramètre optionnel (`sources.yaml`) retombait silencieusement sur
# "software intern" / "netherlands". Réutilise `_source_context` (même
# dérivation, même secteur/géographie que `sources.yaml`) plutôt que de
# dupliquer cette logique une troisième fois.
# ---------------------------------------------------------------------------


def render_source_defaults(values: dict, prefilter_values: dict | None = None) -> dict:
    """Régénère entièrement `source_defaults.jobspy`/`source_defaults.adzuna`
    depuis les réponses de l'assistant — jamais recopié de
    `search.example.yaml`. Sans géographie reconnue ou sans requête de secteur
    dérivable (`_source_context`), les clés concernées (`search_term`,
    `country_indeed`, `country`, `what`) sont laissées vides plutôt
    qu'héritées de l'exemple livré — même principe que `geo.secondaire`/
    `geo.dernier_recours`."""
    ctx = _source_context(values, prefilter_values)
    queries_en = ctx["queries_en"]
    contrat_en = ctx["contrat_en"]
    primary_code = ctx["primary_code"]
    primary_geo = ctx["primary_geo"]

    search_term = f"{queries_en[0]} {contrat_en}".strip() if queries_en else ""

    jobspy = {
        "sites": ["linkedin", "indeed"],
        "search_term": search_term,
        "results_wanted": 30,
        "hours_old": 72,
        "country_indeed": primary_geo["country_indeed"] if primary_geo else "",
        "fetch_description": True,
    }
    adzuna = {
        "country": (
            primary_code.lower()
            if primary_code and primary_geo and primary_geo.get("adzuna_supported")
            else ""
        ),
        "what": search_term,
        "results_per_page": 50,
        "pages": 1,
    }
    return {"jobspy": jobspy, "adzuna": adzuna}


def _set_source_defaults(text: str, source_defaults: dict) -> str:
    """Remplace intégralement le bloc `source_defaults:` (deux sous-sections
    `jobspy`/`adzuna`) par le contenu dérivé de l'entrée utilisateur — même
    motif que `_set_prefilter_keywords` : un remplacement en bloc via
    `yaml.safe_dump` (syntaxe garantie valide), plutôt qu'une substitution
    scalaire par scalaire qui laisserait passer une clé oubliée."""
    lines = text.split("\n")
    span = _section_span(lines, "source_defaults")
    if span is None:
        return text
    start, end = span
    dumped = yaml.safe_dump(source_defaults, allow_unicode=True, sort_keys=False, default_flow_style=False)
    new_lines = ["  " + line if line else line for line in dumped.rstrip("\n").split("\n")]
    lines[start + 1:end] = new_lines
    return "\n".join(lines)


# Aucun secteur pré-sélectionné : JobSearch est généraliste, et pré-cocher
# « informatique » réintroduirait en silence le biais que les presets existent
# pour supprimer — un juriste repartirait avec des mots-clés de développeur et
# obtiendrait zéro offre sans comprendre pourquoi. Le secteur est donc un choix
# explicite de l'utilisateur.
PREFILTER_DEFAULTS = {
    "preset": "",
    "custom_label": "",
    "custom_keywords_raw": "",
}


def render_prefilter_keywords(prefilter_values: dict, search_values: dict) -> dict[str, list[str]]:
    """Groupes de mots-clés du pré-filtre — soit le preset sectoriel choisi,
    soit une saisie libre, complétés du groupe `contrat` dérivé du type de
    contrat. Jamais les 83 mots-clés informatiques comme défaut universel :
    le preset informatique n'est retenu que si explicitement choisi."""
    # Pas de repli sur "informatique" : ce serait réintroduire en silence le biais
    # que ce module existe pour supprimer. Sans preset explicite, on ne pose aucun
    # mot-clé sectoriel — seul le groupe `contrat` (dérivé plus bas) est écrit.
    preset_name = (prefilter_values or {}).get("preset", "")
    if preset_name in ("", "libre"):
        groups: dict[str, list[str]] = {
            "mots_cles": parse_csv((prefilter_values or {}).get("custom_keywords_raw", ""))
        }
        if not groups["mots_cles"]:
            groups = {}
    else:
        presets = load_prefilter_presets()
        preset = presets.get(preset_name) or presets.get("informatique") or {}
        groups = {k: list(v) for k, v in (preset.get("keywords") or {}).items()}
    groups["contrat"] = derive_contrat_keywords(search_values.get("type_contrat", ""))
    return groups


def render_expertise_recruteur(prefilter_values: dict, search_values: dict) -> str:
    """Dérivé du secteur choisi à l'étape « pré-filtre » (preset ou saisie
    libre) et du type de contrat — jamais « informatique » par défaut."""
    # « les {type_contrat} » attend un pluriel : `search.yaml` stocke le singulier
    # (« stage », « CDI »). On accorde, sauf pour les sigles déjà invariables.
    type_contrat = search_values.get("type_contrat") or "stage"
    if type_contrat and type_contrat.islower() and not type_contrat.endswith("s"):
        type_contrat = f"{type_contrat}s"
    preset_name = (prefilter_values or {}).get("preset", "")
    if preset_name in ("", "libre"):
        secteur = ((prefilter_values or {}).get("custom_label") or "").strip()
        # Sans secteur nommé, on reste neutre plutôt que d'écrire « en ce secteur ».
        return f"spécialisé dans les {type_contrat} en {secteur}" if secteur else f"spécialisé dans les {type_contrat}"
    presets = load_prefilter_presets()
    preset = presets.get(preset_name) or presets.get("informatique") or {}
    template = preset.get("expertise_recruteur", "spécialisé dans les {type_contrat}")
    return template.format(type_contrat=type_contrat)


def render_scoring_malus(values: dict) -> list[str]:
    """Dérivé du type de contrat (et, si applicable, de la durée minimale)
    choisis à l'étape « sujet »."""
    type_contrat = values.get("type_contrat") or "le contrat recherché"
    malus = [f"Ne correspond pas au type de contrat recherché ({type_contrat})"]
    if values.get("duree_applicable"):
        malus.append(f"Durée inférieure à {values.get('duree_semaines')} semaines")
    return malus


def _section_span(lines: list[str], section: str) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{section}:"):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j] and not lines[j][0].isspace():
            end = j
            break
    return start, end


def _get_scalar(text: str, section: str, key: str) -> str | None:
    """Lit la valeur scalaire courante de `section.key` (déquotée si besoin),
    utile pour composer une nouvelle valeur à partir de l'existante (ex:
    `geo.notes`) sans écraser le texte déjà présent dans le `.example.yaml`."""
    lines = text.split("\n")
    span = _section_span(lines, section)
    if span is None:
        return None
    start, end = span
    pat = re.compile(rf"^\s*{re.escape(key)}:\s*(?P<val>[^#]*?)\s*(?:#.*)?$")
    for i in range(start + 1, end):
        m = pat.match(lines[i])
        if m:
            val = m.group("val").strip()
            if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                val = val[1:-1]
            return val
    return None


def _set_scalar(text: str, section: str, key: str, new_value: str) -> str:
    lines = text.split("\n")
    span = _section_span(lines, section)
    if span is None:
        return text
    start, end = span
    # Le commentaire de fin de ligne est conservé : il documente le RÉGLAGE, pas la
    # valeur d'exemple (ex. « applicable: true  # false pour un CDI : neutralise les
    # règles de durée » reste utile une fois la valeur choisie). Seules exceptions
    # écartées plus bas : les commentaires qui ne sont qu'une liste d'exemples.
    pat = re.compile(rf"^(\s*){re.escape(key)}:\s*(?P<val>[^#]*?)\s*(?P<comment>#.*)?$")
    for i in range(start + 1, end):
        m = pat.match(lines[i])
        if m:
            comment = m.group("comment") or ""
            # « # ex: … » n'énumère que des valeurs possibles : devenu inutile une fois
            # le choix fait, et trompeur s'il suggère autre chose que la valeur retenue.
            if re.match(r"#\s*ex\s*:", comment, re.I):
                comment = ""
            line = f"{m.group(1)}{key}: {new_value}"
            lines[i] = f"{line}   {comment}" if comment else line
            break
    return "\n".join(lines)


def _set_list(text: str, section: str, key: str, items: list[str]) -> str:
    lines = text.split("\n")
    span = _section_span(lines, section)
    if span is None:
        return text
    start, end = span
    # Deux formes possibles pour la ligne de la clé : `key:` ouvrant un bloc
    # indenté, ou `key: []` (mapping/liste vide en ligne — forme des
    # `.example.yaml` génériques, ex: `domaines.liste: []`).
    key_pat = re.compile(rf"^(\s*){re.escape(key)}:\s*(?:\[\]\s*)?(?:#.*)?$")
    for i in range(start + 1, end):
        m = key_pat.match(lines[i])
        if not m:
            continue
        indent = m.group(1)
        item_indent = indent + "  "
        j = i + 1
        while j < end and lines[j].startswith(item_indent + "-"):
            j += 1
        if items:
            lines[i] = f"{indent}{key}:"
            new_items = [f"{item_indent}- {_yaml_str(it)}" for it in items]
            lines[i + 1:j] = new_items
        else:
            lines[i] = f"{indent}{key}: []"
            del lines[i + 1:j]
        break
    return "\n".join(lines)


def _set_prefilter_keywords(text: str, groups: dict[str, list[str]]) -> str:
    """Remplace intégralement le bloc `prefilter.keywords` (un niveau de nom
    de groupe de plus que `_set_list`, et éventuellement écrit comme un
    mapping vide `{}` dans le `.example.yaml` générique) par les groupes
    choisis à l'étape « pré-filtre » — jamais les mots-clés informatiques de
    l'exemple livré avec le paquet."""
    lines = text.split("\n")
    span = _section_span(lines, "prefilter")
    if span is None:
        return text
    start, end = span
    # Deux formes possibles pour la ligne `keywords` : un mapping vide en
    # ligne (`keywords: {}`, forme du `.example.yaml` générique) ou une clé
    # ouvrant un bloc indenté (`keywords:`, forme déjà pivotée par un run
    # précédent de l'assistant).
    key_pat = re.compile(r"^(\s*)keywords:\s*(?:\{\}\s*)?(?:#.*)?$")
    for i in range(start + 1, end):
        m = key_pat.match(lines[i])
        if not m:
            continue
        indent = m.group(1)
        sub_indent = indent + "  "
        item_indent = sub_indent + "  "
        j = i + 1
        while j < end and lines[j].startswith(sub_indent):
            j += 1
        new_block: list[str] = []
        for group_name, items in groups.items():
            if not items:
                continue
            new_block.append(f"{sub_indent}{group_name}:")
            new_block.extend(f"{item_indent}- {_yaml_str(it)}" for it in items)
        if new_block:
            lines[i] = f"{indent}keywords:"
        else:
            lines[i] = f"{indent}keywords: {{}}"
        lines[i + 1:j] = new_block
        break
    return "\n".join(lines)


def render_search_yaml(values: dict, prefilter_values: dict | None = None) -> str:
    """Génère `search.yaml` en substituant les valeurs de l'assistant dans le
    `.example.yaml` du paquet — préserve les ~200 lignes de commentaires
    d'origine (jamais de `yaml.safe_dump`, qui les détruirait).

    Aucun repli en dur : un `.example` manquant est une anomalie d'installation
    (fichier absent du paquet publié), pas un cas nominal à faire disparaître
    en silence — c'est exactement ce silence qui produisait auparavant la
    boucle infinie de `_check_setup` sur une installation pipx."""
    example_path = PATHS.example("search.example.yaml")
    if not os.path.exists(example_path):
        raise FileNotFoundError(
            f"Fichier d'exemple manquant : {example_path} — installation du paquet incomplète "
            "(jobsearch/resources/examples/ aurait dû être embarqué, voir pyproject.toml)."
        )
    with open(example_path, encoding="utf-8") as f:
        text = f.read()

    text = _set_scalar(text, "sujet", "type_contrat", _yaml_str(values["type_contrat"]))
    text = _set_scalar(text, "sujet", "label", _yaml_str(render_sujet_label(values)))
    text = _set_scalar(
        text, "sujet", "type_contrat_negatif",
        _yaml_str(derive_type_contrat_negatif(values["type_contrat"])),
    )
    prefilter_values = prefilter_values or PREFILTER_DEFAULTS
    text = _set_scalar(
        text, "sujet", "expertise_recruteur",
        _yaml_str(render_expertise_recruteur(prefilter_values, values)),
    )
    text = _set_scalar(text, "duree", "applicable", "true" if values["duree_applicable"] else "false")
    if values["duree_applicable"]:
        text = _set_scalar(text, "duree", "min_semaines", str(values["duree_semaines"]))
        text = _set_scalar(
            text, "duree", "texte", _yaml_str(f"{values['duree_semaines']} semaines minimum")
        )
    else:
        # Pas de durée minimale (ex: CDI) : neutralise aussi le texte, sinon
        # `duree.texte` continue de décrire une contrainte qui ne s'applique plus.
        text = _set_scalar(text, "duree", "texte", _yaml_str(""))

    dispo = parse_disponibilite(values.get("dispo_raw", ""))
    text = _set_scalar(text, "disponibilite", "debut_iso", _yaml_str(dispo["debut_iso"]))
    text = _set_scalar(text, "disponibilite", "texte_fr", _yaml_str(dispo["texte_fr"]))
    text = _set_scalar(text, "disponibilite", "texte_en", _yaml_str(dispo["texte_en"]))

    text = _set_list(text, "geo", "priorite", values["pays"])
    region_ville = (values.get("region_ville") or "").strip()
    # `notes` et `details` sont tous deux réellement injectés dans le barème du prompt
    # ({geo_notes}/{geo_details}) : on les régénère entièrement depuis les pays choisis,
    # on ne les fait jamais hériter de l'exemple livré (qui peut porter le contexte d'un tiers).
    text = _set_scalar(text, "geo", "notes", _yaml_str(render_geo_notes(values["pays"], region_ville)))
    text = _set_list(text, "geo", "details", render_geo_details(values["pays"]))
    # `geo.secondaire`/`geo.dernier_recours` ne sont pas demandés par cet assistant
    # (seule `geo.priorite` l'est) : laissés vides plutôt qu'hérités de l'exemple
    # livré ("Europe (hors pays frontaliers)"/"Hors Europe", qui présuppose une
    # recherche centrée sur l'Europe). Un vide explicite neutralise proprement les
    # bonus/malus associés dans le prompt ; à affiner depuis l'onglet Réglages.
    text = _set_list(text, "geo", "secondaire", [])
    text = _set_list(text, "geo", "dernier_recours", [])

    text = _set_list(text, "domaines", "liste", values["domaines"])
    text = _set_scalar(text, "domaines", "ouverture", _yaml_str(render_domaines_ouverture(values)))
    text = _set_scalar(text, "scoring", "seuil", str(values["seuil"]))
    text = _set_list(text, "scoring", "bonus", render_scoring_bonus(values))
    text = _set_list(text, "scoring", "malus", render_scoring_malus(values))
    text = _set_list(text, "scoring", "titre_bonus", derive_titre_bonus(values["type_contrat"]))
    text = _set_prefilter_keywords(text, render_prefilter_keywords(prefilter_values, values))
    # `source_defaults` est réellement consommé au scrape (get_search().source_defaults(...)
    # dans jobsearch/scraper.py et jobsearch/scraper_jobspy.py) : jamais laissé hériter du
    # sujet d'origine de l'exemple (« software intern » / « netherlands »), voir la section
    # dédiée plus haut dans ce fichier.
    text = _set_source_defaults(text, render_source_defaults(values, prefilter_values))
    return text


# ---------------------------------------------------------------------------
# Garde-fou anti-régression — troisième bug de la même famille (cf. CLAUDE.md,
# section « Vérification anti-régression du sujet de recherche ») : une valeur
# de `search.yaml` dérivée de l'entrée utilisateur qui, par erreur (clé oubliée
# dans `render_search_yaml`, condition mal branchée…), retombe silencieusement
# sur la valeur de `config/search.example.yaml` livrée avec le paquet — celle
# du sujet d'origine (stage tech, Pays-Bas). Exposé sans appel API via
# `python -m jobsearch.config --check-wizard-defaults`.
# ---------------------------------------------------------------------------

# Clés connues pour dépendre de l'entrée utilisateur (sujet/domaines/contrat) —
# à étendre à chaque nouvelle clé dérivée ajoutée à `render_search_yaml`. Chemins
# à profondeur variable (2 niveaux pour la plupart des sections, 3 pour
# `source_defaults.<source>.<clé>` — ajouté en 5e occurrence de ce bug, voir
# CLAUDE.md) : `_lookup_path` ci-dessous marche pour les deux.
RISK_KEYS: list[tuple[str, ...]] = [
    ("sujet", "label"),
    ("sujet", "type_contrat_negatif"),
    ("scoring", "titre_bonus"),
    ("geo", "secondaire"),
    ("geo", "dernier_recours"),
    ("domaines", "ouverture"),
    ("source_defaults", "jobspy", "search_term"),
    ("source_defaults", "jobspy", "country_indeed"),
    ("source_defaults", "adzuna", "country"),
    ("source_defaults", "adzuna", "what"),
]


def _lookup_path(doc: dict, path: tuple[str, ...]):
    """Lit `doc[path[0]][path[1]]...` en tolérant les niveaux absents —
    factorise la lecture des clés de `RISK_KEYS`, à profondeur variable
    (2 niveaux pour la plupart des sections, 3 pour `source_defaults`)."""
    node = doc
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node

def _guard_profiles() -> list[dict]:
    """Deux profils volontairement très contrastés (le sujet d'origine du
    paquet, et celui de l'audit ayant motivé ce garde-fou : CDI / France /
    droit) — n'importe quelle clé de RISK_KEYS encore égale à la valeur de
    l'exemple livré pour l'un des deux profils, ou identique entre les deux
    profils malgré ce contraste, trahit une valeur non dérivée de l'entrée
    utilisateur. Fonction (plutôt qu'une constante de module) : `SEARCH_DEFAULTS`/
    `PREFILTER_DEFAULTS` sont définis plus bas dans ce fichier."""
    return [
        {
            "search_values": {
                **SEARCH_DEFAULTS,
                "type_contrat": "stage",
                "pays": ["Pays-Bas"],
                "domaines": ["Développement logiciel"],
            },
            "prefilter_values": {**PREFILTER_DEFAULTS, "preset": "informatique"},
        },
        {
            "search_values": {
                **SEARCH_DEFAULTS,
                "type_contrat": "CDI",
                "pays": ["France"],
                "region_ville": "Lyon",
                "domaines": ["Droit des affaires"],
            },
            "prefilter_values": {**PREFILTER_DEFAULTS, "preset": "droit"},
        },
    ]


def _load_example_search_yaml() -> dict:
    # Pas de garde silencieuse ici non plus : un .example absent doit planter
    # bruyamment (voir render_search_yaml ci-dessus pour le même raisonnement).
    example_path = PATHS.example("search.example.yaml")
    if not os.path.exists(example_path):
        raise FileNotFoundError(
            f"Fichier d'exemple manquant : {example_path} — installation du paquet incomplète."
        )
    with open(example_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# `geo.secondaire`/`geo.dernier_recours` sont volontairement TOUJOURS vides,
# quel que soit le profil (voir `render_search_yaml` : cet assistant ne
# demande pas ces deux champs, laissés à affiner depuis l'onglet Réglages) —
# exclus du contrôle croisé « doit varier d'un profil à l'autre » ci-dessous,
# qui les signalerait sinon en faux positif permanent.
_ALWAYS_EMPTY_BY_DESIGN = {("geo", "secondaire"), ("geo", "dernier_recours")}


def check_wizard_defaults() -> list[str]:
    """Rend `search.yaml` pour deux profils simulés très contrastés (le sujet
    d'origine du paquet, puis CDI / France / droit — le scénario ayant motivé
    ce garde-fou) et vérifie deux invariants sur `RISK_KEYS` :

    1. pour le profil le plus éloigné du sujet d'origine (CDI/France/droit),
       aucune clé ne doit conserver la valeur littérale de
       `config/search.example.yaml` — un « stage » calibré tech/Pays-Bas n'a
       aucune raison de produire la même chose qu'un CDI en droit ;
    2. les deux profils, volontairement très contrastés, doivent produire des
       valeurs différentes sur les clés censées dépendre de l'entrée
       utilisateur (à l'exception documentée de `_ALWAYS_EMPTY_BY_DESIGN`).

    Couvre aussi `sources.yaml` (4e occurrence de la même famille de bug — voir
    CLAUDE.md) : échoue si les requêtes générées sont identiques entre les deux
    profils, ou si le profil CDI/France/droit contient encore un littéral de
    l'auteur (`_SOURCES_AUTHOR_LITERALS`, ex. « software intern », « Netherlands »).

    Fonction pure, sans Textual ni appel API — testable et réutilisable (voir
    `python -m jobsearch.config --check-wizard-defaults`). Retourne la liste
    des anomalies détectées (vide = rien à signaler)."""
    example = _load_example_search_yaml()
    profiles = _guard_profiles()
    rendered = [
        yaml.safe_load(render_search_yaml(p["search_values"], p["prefilter_values"])) or {}
        for p in profiles
    ]
    anomalies: list[str] = []

    # (1) Le profil CDI/France/droit (index 1) ne doit littéralement rien devoir
    # au sujet d'origine (stage tech, Pays-Bas) livré dans search.example.yaml.
    contrat_eloigne = profiles[1]["search_values"]["type_contrat"]
    for path in RISK_KEYS:
        label = ".".join(path)
        value = _lookup_path(rendered[1], path)
        example_value = _lookup_path(example, path)
        if value == example_value:
            anomalies.append(
                f"{label} = {value!r} — identique à search.example.yaml "
                f"pour le profil {contrat_eloigne!r} (valeur potentiellement héritée "
                "sans dérivation depuis l'entrée utilisateur)."
            )

    # (2) Contrôle croisé entre les deux profils contrastés.
    for path in RISK_KEYS:
        if path in _ALWAYS_EMPTY_BY_DESIGN:
            continue
        label = ".".join(path)
        v0 = _lookup_path(rendered[0], path)
        v1 = _lookup_path(rendered[1], path)
        if v0 == v1:
            anomalies.append(
                f"{label} identique entre les deux profils simulés contrastés "
                f"(stage/Pays-Bas vs CDI/France/droit) : {v0!r} — suspect d'une valeur "
                "non dérivée de l'entrée utilisateur."
            )

    # (3) sources.yaml — même contrôle croisé, plus une recherche de littéraux
    # de l'auteur d'origine (config/sources.yaml, avant sa neutralisation en
    # sources.example.yaml) sur le profil le plus éloigné.
    rendered_sources_text = [
        render_sources_yaml(p["search_values"], p["prefilter_values"]) for p in profiles
    ]
    contrat_eloigne_sources = profiles[1]["search_values"]["type_contrat"]
    text_eloigne_lower = rendered_sources_text[1].lower()
    for literal in _SOURCES_AUTHOR_LITERALS:
        if literal in text_eloigne_lower:
            anomalies.append(
                f"sources.yaml contient le littéral de l'auteur {literal!r} pour le profil "
                f"{contrat_eloigne_sources!r} (CDI/France/droit) — requête ou géographie "
                "potentiellement héritée sans dérivation depuis l'entrée utilisateur."
            )

    def _flatten_queries(text: str) -> str:
        doc = yaml.safe_load(text) or {}
        return " ".join(
            str((s.get("params") or {}).get(k, ""))
            for s in (doc.get("sources") or [])
            for k in ("search_term", "what", "job_title", "k")
        )

    q0 = _flatten_queries(rendered_sources_text[0])
    q1 = _flatten_queries(rendered_sources_text[1])
    if q0 and q0 == q1:
        anomalies.append(
            "sources.yaml : les requêtes de recherche générées (search_term/what/job_title/k) "
            "sont identiques entre les deux profils simulés contrastés (stage/informatique/"
            "Pays-Bas vs CDI/droit/France) — suspect d'une requête non dérivée du secteur/sujet "
            "choisi."
        )

    # (4) Fichiers d'exemple du CV (cv_data.example.yaml / cv_master.example.md) —
    # ne dépendent d'aucune entrée utilisateur (`--setup` ne collecte pas de CV,
    # voir CLAUDE.md), donc pas de rendu à comparer entre profils ici : simple
    # recherche de littéraux biographiques de l'auteur d'origine, livrés tels
    # quels avec le paquet à tout nouvel utilisateur.
    anomalies.extend(_check_cv_examples())

    return anomalies


# Littéraux biographiques de l'auteur d'origine (stage informatique, Pays-Bas,
# Polytech, disponible février 2027, 22 semaines minimum) qui ne doivent jamais
# subsister dans les fichiers d'exemple du CV livrés avec le paquet
# (`jobsearch/resources/examples/cv_data.example.yaml`,
# `.../cv_master.example.md`) — ces fichiers doivent rester un CV générique,
# aussi bien utilisable par un juriste que par un développeur (voir CLAUDE.md,
# 5e occurrence de la famille de bug « valeur d'auteur héritée »). Comparaison
# insensible à la casse.
_CV_AUTHOR_LITERALS = [
    "pays-bas",
    "netherlands",
    "polytech",
    "info5",
    "février 2027",
    "22 semaines",
]


def _check_cv_examples() -> list[str]:
    """Recherche les littéraux de `_CV_AUTHOR_LITERALS` dans les deux fichiers
    d'exemple du CV — ce sont eux qui alimentent `{cv_summary}` du prompt de
    lettre de motivation (`jobsearch/generator.py`) pour tout nouvel
    utilisateur n'ayant pas encore personnalisé son CV. Fonction pure, sans
    dépendance à un profil simulé (contrairement au reste de
    `check_wizard_defaults`) : ces fichiers ne varient pas selon les réponses
    de l'assistant."""
    anomalies: list[str] = []
    paths = {
        "cv_data.example.yaml": PATHS.example("cv_data.example.yaml"),
        "cv_master.example.md": PATHS.example("cv_master.example.md"),
    }
    for filename, path in paths.items():
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            content_lower = f.read().lower()
        for literal in _CV_AUTHOR_LITERALS:
            if literal in content_lower:
                anomalies.append(
                    f"{filename} contient le littéral biographique de l'auteur {literal!r} "
                    "— ce fichier doit rester un CV générique (voir CLAUDE.md)."
                )
    return anomalies


# Littéraux de l'auteur d'origine (ex-config/sources.yaml, calibrée sur un
# stage informatique aux Pays-Bas, avant sa neutralisation en
# config/sources.example.yaml) — ne doivent jamais réapparaître dans un
# sources.yaml généré pour un profil différent. Comparaison insensible à la
# casse (voir check_wizard_defaults, point 3).
_SOURCES_AUTHOR_LITERALS = [
    "software intern",
    "netherlands",
    "développeur informatique",
    "stage développeur",
]


def render_filter_prompt(values: dict) -> str:
    """Part du défaut générique livré avec le paquet et le paramètre avec les
    réponses de l'étape « sujet » — non sautable (décision utilisateur)."""
    base_path = os.path.join(PATHS.package_prompts_dir, "filter.txt")
    with open(base_path, encoding="utf-8") as f:
        base = f.read()

    domaines_txt = ", ".join(values["domaines"]) if values["domaines"] else "(non renseigné)"
    contrat_txt = values["type_contrat"] or "(non renseigné)"
    region_ville = (values.get("region_ville") or "").strip()
    region_line = (
        f"Région/Ville prioritaire (renseignée à l'installation) : {region_ville}\n"
        if region_ville
        else ""
    )
    extra = (
        "## Repères issus de la configuration initiale\n"
        f"Type de contrat recherché (renseigné à l'installation) : {contrat_txt}\n"
        f"Domaines prioritaires (renseignés à l'installation) : {domaines_txt}\n"
        f"{region_line}"
        "Ces valeurs recoupent celles de config/search.yaml (placeholders {type_contrat}/{domaines}\n"
        "utilisés ci-dessus) — ce bloc sert de repère lisible, à affiner librement depuis\n"
        "l'onglet Réglages (touche 6) une fois quelques offres scorées.\n\n"
    )
    marker = "## Considérations géographiques détaillées"
    if marker in base:
        base = base.replace(marker, extra + marker, 1)
    else:
        base = base + "\n" + extra
    return base


def parse_competences_text(text: str) -> dict[str, list[str]]:
    """Parse le contenu du `TextArea` « Compétences » de `ProfileScreen` : une
    catégorie par ligne, format libre `Catégorie: item1, item2` — l'utilisateur
    choisit lui-même le nom de ses catégories (voir `render_profile_yaml`)."""
    result: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        categorie, items_raw = line.split(":", 1)
        categorie = categorie.strip()
        if categorie:
            result[categorie] = parse_csv(items_raw)
    return result


def render_competences_text(competences: dict[str, list[str]]) -> str:
    """Inverse de `parse_competences_text` — pré-remplit le `TextArea`."""
    return "\n".join(f"{categorie}: {', '.join(items)}" for categorie, items in competences.items())


def render_profile_yaml(values: dict) -> str:
    langues = []
    for item in values["langues"]:
        if ":" in item:
            langue, niveau = item.split(":", 1)
        else:
            langue, niveau = item, ""
        langues.append({"langue": langue.strip(), "niveau": niveau.strip()})

    data = {
        "formation": {
            "ecole": values["ecole"],
            "niveau": values["niveau"],
            "specialite": values["specialite"],
            "diplome_prevu": values["diplome_prevu"],
        },
        # Mapping libre de catégories choisies par l'utilisateur (ex: "Langages",
        # "Domaines du droit", "Logiciels") — jamais les clés fixes de l'ancien
        # format (`langages_maitrise`/`frameworks`/...), qui codaient en dur un
        # vocabulaire informatique inadapté à un outil généraliste. La lecture,
        # elle, reste rétrocompatible avec l'ancien format : voir
        # `jobsearch.filter.render_competences_lines`.
        "competences": values["competences"],
        "langues": langues,
    }
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _build_preview_search(search_values: dict, prefilter_values: dict | None) -> Search:
    """`Search` construite en mémoire depuis les réponses de l'assistant, sans
    passer par le disque — utilisée uniquement pour l'aperçu paramétré du
    prompt (`PromptPreviewScreen`), jamais pour le fichier réellement écrit
    (qui doit, lui, conserver ses placeholders `{xxx}`, résolus au scoring)."""
    text = render_search_yaml(search_values, prefilter_values)
    data = yaml.safe_load(text) or {}
    return Search(_deep_merge(DEFAULTS, data))


def render_filter_prompt_preview(
    search_values: dict, prefilter_values: dict | None, profile_values: dict | None
) -> str:
    """Aperçu du prompt de scoring, placeholders résolus — jamais écrit tel
    quel sur le disque (voir `render_filter_prompt`, qui garde les
    placeholders littéraux, résolus plus tard par `fill_template` au moment
    du scoring depuis `search.yaml`)."""
    base = render_filter_prompt(search_values)
    search = _build_preview_search(search_values, prefilter_values)
    profile = {
        "formation": {
            "ecole": (profile_values or {}).get("ecole", ""),
            "niveau": (profile_values or {}).get("niveau", ""),
            "specialite": (profile_values or {}).get("specialite", ""),
            "diplome_prevu": (profile_values or {}).get("diplome_prevu", ""),
        },
        "competences": (profile_values or {}).get("competences", {}),
        "langues": [
            {"langue": item.split(":", 1)[0].strip(), "niveau": item.split(":", 1)[1].strip()}
            if ":" in item
            else {"langue": item.strip(), "niveau": ""}
            for item in (profile_values or {}).get("langues", [])
        ],
    }
    from jobsearch.filter import render_profile_text

    profile_text = render_profile_text(profile, search)
    return fill_template(base, {**search.placeholders(), "profile": profile_text}, warn=False)


def check_api_key(key: str) -> tuple[bool, str]:
    """Appel minimal (métadonnées, non facturé) pour valider une clé API."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        client.models.list(limit=1)
        return True, "Clé API valide."
    except Exception as e:  # pragma: no cover - dépend du réseau/de la clé
        return False, f"Clé invalide ou erreur réseau : {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------

WELCOME_TEXT = """\
[b]Bienvenue dans l'assistant d'installation de JobSearch.[/b]

JobSearch automatise la recherche d'un contrat (stage, alternance, CDI…) :
collecte des offres (RSS, APIs, LinkedIn/Indeed…), filtrage par pertinence
par un petit modèle Claude (scoring 0-10), rapport, puis lettre de motivation
et CV adapté par un modèle plus capable pour les offres retenues (modèles
modifiables dans l'onglet Réglages de la TUI).

La porte d'entrée du quotidien est l'interface interactive :
  [b]jobsearch --tui[/b]

Coût API : le filtrage (petit modèle) est peu coûteux, quelques centimes pour des
dizaines d'offres. La génération de candidature (modèle plus capable) coûte plus cher à
l'appel — à réserver aux offres qui t'intéressent vraiment.

Cet assistant configure, dans l'ordre : ta clé API, ton sujet de recherche,
le prompt de scoring (obligatoire — sans lui, le scoring reste calibré sur
un exemple générique), puis ton profil. Rien n'est écrit sur le disque avant
le récapitulatif final ; \\[échap] revient à l'étape précédente à tout moment.

[dim]entrée : continuer   ·   échap : annuler[/dim]
"""


class WelcomeScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("enter", "go", "Continuer"),
        Binding("escape", "cancel", "Annuler"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="wizard-box"):
            yield Static(WELCOME_TEXT)

    def action_go(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class OverwriteConfirmScreen(ModalScreen[bool]):
    """Confirmation oui/non générique (validation clé API, écrasement de fichier)."""

    BINDINGS = [
        Binding("enter", "yes", "Oui", show=False),
        Binding("y", "yes", "Oui", show=False),
        Binding("n", "no", "Non", show=False),
        Binding("escape", "no", "Non"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-box"):
            yield Label(self._message)
            yield Label("\\[entrée / y] Oui   \\[n / échap] Non")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class InfoScreen(ModalScreen[None]):
    """Affiche un message (résultat de validation de clé…) — entrée pour continuer."""

    BINDINGS = [Binding("enter", "go", "Continuer"), Binding("escape", "go", "Continuer")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-box"):
            yield Label(self._message)
            yield Label("[dim]entrée pour continuer[/dim]")

    def action_go(self) -> None:
        self.dismiss(None)


API_KEYS_DEFAULTS = {
    "key": "",
    "adzuna_id": "",
    "adzuna_key": "",
    "notify_email": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_user": "",
    "smtp_password": "",
}


class ApiKeyScreen(ModalScreen[dict | _Back]):
    """Étape 2 — Clés API. Toutes sautables (champ vide = non renseignée).

    Couvre la clé Anthropic (requise pour filtre/génération) et les clés
    optionnelles déjà présentes dans `.env.example` (Adzuna, notifications
    SMTP) — auparavant seule la clé Anthropic était proposée ici, ce qui
    laissait les autres sources/`.env` non couvertes par l'assistant."""

    BINDINGS = [Binding("escape", "back", "Retour")]

    def __init__(self, initial: dict | None = None) -> None:
        super().__init__()
        self._initial = {**API_KEYS_DEFAULTS, **(initial or {})}

    def compose(self) -> ComposeResult:
        v = self._initial
        with VerticalScroll(id="wizard-box"):
            yield Label(
                "Clés API — toutes optionnelles à l'exception de la première (recommandée).\n"
                "Laisse un champ vide pour passer cette étape sur cette clé.\n"
                "entrée dans n'importe quel champ : valider   ·   échap : retour à l'étape précédente"
            )
            yield Label(
                "\n[b]Anthropic[/b] (console.anthropic.com/settings/keys) — requise pour le "
                "filtrage des offres et la génération de candidature :"
            )
            yield Input(value=v["key"], password=True, placeholder="sk-ant-...", id="key-input")
            yield Label(
                "\n[b]Adzuna[/b] (optionnel — source d'offres supplémentaire) — clés gratuites "
                "sur developer.adzuna.com :"
            )
            yield Input(value=v["adzuna_id"], placeholder="ADZUNA_APP_ID", id="adzuna-id-input")
            yield Input(value=v["adzuna_key"], placeholder="ADZUNA_APP_KEY", id="adzuna-key-input")
            yield Label(
                "\n[b]Notifications email[/b] (optionnel — email envoyé après chaque scrape).\n"
                "Pour Gmail : active la validation en 2 étapes, puis crée un « mot de passe "
                "d'application » sur myaccount.google.com/apppasswords (pas ton mot de passe habituel) :"
            )
            yield Input(value=v["notify_email"], placeholder="NOTIFY_EMAIL (destinataire)", id="notify-email-input")
            yield Input(value=v["smtp_host"], placeholder="SMTP_HOST", id="smtp-host-input")
            yield Input(value=v["smtp_user"], placeholder="SMTP_USER (adresse d'envoi)", id="smtp-user-input")
            yield Input(
                value=v["smtp_password"], password=True,
                placeholder="SMTP_PASSWORD (mot de passe d'application)", id="smtp-password-input",
            )

    def on_mount(self) -> None:
        self.query_one("#key-input", Input).focus()

    def _collect(self) -> dict:
        return {
            "key": self.query_one("#key-input", Input).value.strip(),
            "adzuna_id": self.query_one("#adzuna-id-input", Input).value.strip(),
            "adzuna_key": self.query_one("#adzuna-key-input", Input).value.strip(),
            "notify_email": self.query_one("#notify-email-input", Input).value.strip(),
            "smtp_host": self.query_one("#smtp-host-input", Input).value.strip(),
            "smtp_user": self.query_one("#smtp-user-input", Input).value.strip(),
            "smtp_password": self.query_one("#smtp-password-input", Input).value.strip(),
        }

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._collect())

    def action_back(self) -> None:
        self.dismiss(BACK)


def render_env_file(keys: dict) -> str:
    """Génère `.env` à partir de `.env.example` (commentaires conservés) —
    écrit même quand toutes les clés sont sautées, pour que `.env` existe
    toujours après un `--setup` complet (sinon `_check_setup`, dans
    `cli.py`, continue de le réclamer).

    Aucun repli en dur : un `.env.example` manquant est une anomalie
    d'installation (voir `render_search_yaml`)."""
    example_path = PATHS.example(".env.example")
    if not os.path.exists(example_path):
        raise FileNotFoundError(
            f"Fichier d'exemple manquant : {example_path} — installation du paquet incomplète."
        )
    with open(example_path, encoding="utf-8") as f:
        text = f.read()
    replacements = {
        "ANTHROPIC_API_KEY=your_key_here": f"ANTHROPIC_API_KEY={keys.get('key', '')}",
        "NOTIFY_EMAIL=": f"NOTIFY_EMAIL={keys.get('notify_email', '')}",
        "SMTP_HOST=smtp.gmail.com": f"SMTP_HOST={keys.get('smtp_host') or 'smtp.gmail.com'}",
        "SMTP_USER=your_gmail@gmail.com": f"SMTP_USER={keys.get('smtp_user', '')}",
        "SMTP_PASSWORD=": f"SMTP_PASSWORD={keys.get('smtp_password', '')}",
        "ADZUNA_APP_ID=": f"ADZUNA_APP_ID={keys.get('adzuna_id', '')}",
        "ADZUNA_APP_KEY=": f"ADZUNA_APP_KEY={keys.get('adzuna_key', '')}",
    }
    for old, new in replacements.items():
        text = text.replace(old, new, 1)
    return text


SEARCH_DEFAULTS = {
    "type_contrat": "stage",
    "duree_applicable": True,
    "duree_semaines": 22,
    "pays": ["Pays-Bas"],
    "domaines": [],
    "seuil": 6,
    "region_ville": "",
    "dispo_raw": "",
}


class SearchScreen(ModalScreen[dict | _Back]):
    """Étape 3 — Sujet de recherche."""

    BINDINGS = [Binding("escape", "back", "Retour")]

    def __init__(self, initial: dict | None = None) -> None:
        super().__init__()
        self._initial = {**SEARCH_DEFAULTS, **(initial or {})}

    def compose(self) -> ComposeResult:
        v = self._initial
        with VerticalScroll(id="wizard-box"):
            yield Label(
                "Sujet de recherche — entrée dans n'importe quel champ valide le formulaire.\n"
                "échap : retour à l'étape précédente (les réponses saisies sont conservées)."
            )
            yield Label("Type de contrat (ex: stage, CDI, alternance) :")
            yield Input(value=v["type_contrat"], id="s-contrat")
            yield Label("Durée minimale applicable ? (o/n) :")
            yield Input(value="o" if v["duree_applicable"] else "n", id="s-duree-app")
            yield Label("Durée minimale, en semaines :")
            yield Input(value=str(v["duree_semaines"]), id="s-duree-sem")
            yield Label("Pays prioritaires (séparés par des virgules) :")
            yield Input(value=", ".join(v["pays"]), id="s-pays")
            yield Label("Région / Ville prioritaire (optionnel — laisser vide si sans importance) :")
            yield Input(value=v["region_ville"], id="s-region")
            yield Label("Date de disponibilité (JJ/MM/AAAA, optionnel) :")
            yield Input(value=v["dispo_raw"], placeholder="JJ/MM/AAAA", id="s-dispo")
            yield Label("Domaines visés (séparés par des virgules) :")
            yield Input(value=", ".join(v["domaines"]), id="s-domaines")
            yield Label("Seuil de score de pertinence (0-10) :")
            yield Input(value=str(v["seuil"]), id="s-seuil")
            yield Label("", id="s-error")

    def on_mount(self) -> None:
        self.query_one("#s-contrat", Input).focus()

    def _collect(self) -> dict | None:
        error = self.query_one("#s-error", Label)
        try:
            duree_sem = int(self.query_one("#s-duree-sem", Input).value.strip() or "22")
            seuil = int(self.query_one("#s-seuil", Input).value.strip() or "6")
        except ValueError:
            error.update("[red]Durée et seuil doivent être des nombres entiers.[/red]")
            return None
        contrat = self.query_one("#s-contrat", Input).value.strip() or "stage"
        duree_app = self.query_one("#s-duree-app", Input).value.strip().lower() not in ("n", "non", "no")
        pays = parse_csv(self.query_one("#s-pays", Input).value, ["Pays-Bas"])
        region_ville = self.query_one("#s-region", Input).value.strip()
        dispo_raw = self.query_one("#s-dispo", Input).value.strip()
        domaines = parse_csv(self.query_one("#s-domaines", Input).value, [])
        return {
            "type_contrat": contrat,
            "duree_applicable": duree_app,
            "duree_semaines": duree_sem,
            "pays": pays,
            "region_ville": region_ville,
            "dispo_raw": dispo_raw,
            "domaines": domaines,
            "seuil": seuil,
        }

    def on_input_submitted(self, event: Input.Submitted) -> None:
        values = self._collect()
        if values is not None:
            self.dismiss(values)

    def action_back(self) -> None:
        self.dismiss(BACK)


class PrefilterScreen(ModalScreen[dict | _Back]):
    """Étape 3bis — Pré-filtre (mots-clés gratuits appliqués avant l'appel API).

    Choix d'un jeu de mots-clés pré-rempli par secteur (informatique compris,
    conservé comme un preset parmi d'autres — jamais le défaut universel), ou
    saisie libre. Le groupe `contrat` est dérivé du type de contrat choisi à
    l'étape précédente, indépendamment de ce choix.
    """

    BINDINGS = [Binding("escape", "back", "Retour")]

    def __init__(self, initial: dict | None = None) -> None:
        super().__init__()
        self._initial = {**PREFILTER_DEFAULTS, **(initial or {})}
        self._presets = load_prefilter_presets()
        self._order = list(self._presets.keys())

    def compose(self) -> ComposeResult:
        v = self._initial
        lines = "\n".join(
            f"  {i + 1}. {name} — {self._presets[name].get('label', name)}"
            for i, name in enumerate(self._order)
        )
        with VerticalScroll(id="wizard-box"):
            yield Label(
                "Pré-filtre — mots-clés appliqués gratuitement avant l'appel à l'API,\n"
                "pour écarter les offres hors sujet sans les faire scorer.\n"
                "échap : retour à l'étape précédente.\n\n"
                "Secteurs disponibles :\n"
                f"{lines}\n"
                "  libre — saisir ses propres mots-clés"
            )
            yield Label("\nNuméro, nom du secteur, ou 'libre' :")
            yield Input(value=v["preset"], id="pf-preset")
            yield Label(
                "Si 'libre' : mots-clés séparés par des virgules "
                "(ex: infirmier, hôpital, soins, patient) :"
            )
            yield Input(value=v.get("custom_keywords_raw", ""), id="pf-keywords")
            yield Label(
                "Si 'libre' : secteur en quelques mots, pour le prompt "
                "(ex: 'la vente', 'la santé') :"
            )
            yield Input(value=v.get("custom_label", ""), id="pf-label")
            yield Label("", id="pf-error")

    def on_mount(self) -> None:
        self.query_one("#pf-preset", Input).focus()

    def _resolve_preset(self, raw: str) -> str | None:
        raw = raw.strip().lower()
        if not raw:
            return None
        if raw in ("libre", "custom"):
            return "libre"
        if raw in self._presets:
            return raw
        try:
            idx = int(raw) - 1
        except ValueError:
            idx = -1
        if 0 <= idx < len(self._order):
            return self._order[idx]
        for name in self._order:
            if self._presets[name].get("label", "").strip().lower() == raw:
                return name
        return None

    def _collect(self) -> dict | None:
        error = self.query_one("#pf-error", Label)
        raw = self.query_one("#pf-preset", Input).value
        preset = self._resolve_preset(raw)
        if preset is None:
            error.update(
                "[red]Choix invalide — entre un numéro, un nom de secteur ou 'libre'.[/red]"
            )
            return None
        custom_keywords_raw = self.query_one("#pf-keywords", Input).value.strip()
        custom_label = self.query_one("#pf-label", Input).value.strip()
        if preset == "libre" and not custom_keywords_raw:
            error.update("[red]Saisie libre choisie : au moins un mot-clé est requis.[/red]")
            return None
        return {
            "preset": preset,
            "custom_keywords_raw": custom_keywords_raw,
            "custom_label": custom_label,
        }

    def on_input_submitted(self, event: Input.Submitted) -> None:
        values = self._collect()
        if values is not None:
            self.dismiss(values)

    def action_back(self) -> None:
        self.dismiss(BACK)


class PromptPreviewScreen(ModalScreen[bool | _Back]):
    """Étape 5 — Prompt de scoring. Non sautable : impossible de continuer sans
    le valider, mais on peut toujours remonter à l'étape « sujet »."""

    BINDINGS = [
        Binding("enter", "go", "Continuer"),
        Binding("escape", "back", "Retour"),
    ]

    def __init__(self, prompt_text: str) -> None:
        super().__init__()
        self._prompt_text = prompt_text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="wizard-box-wide"):
            yield Label(
                "[b]Étape obligatoire[/b] — aperçu du prompt de scoring, paramétré avec tes réponses.\n"
                "Point de départ, affinable ensuite depuis l'onglet Réglages (touche 6) de la TUI.\n"
                "Les valeurs entre accolades ({xxx}) sont substituées ci-dessous pour l'aperçu\n"
                "uniquement — le fichier réellement écrit, lui, conserve ces placeholders : ils sont\n"
                "résolus automatiquement à chaque scoring, depuis config/search.yaml.\n"
                "(Exception : {title}/{company}/{location}/{url}/{description} en bas de l'aperçu —\n"
                "propres à chaque offre, jamais connus à l'avance, résolus eux aussi au scoring.)\n"
                "entrée : continuer   ·   échap : retour à l'étape profil"
            )
            yield Static("[b]" + "─" * 78 + "[/b]\n[b]Aperçu du prompt (paramétré)[/b]\n" + "─" * 78)
            yield Static(self._prompt_text)

    def action_go(self) -> None:
        self.dismiss(True)

    def action_back(self) -> None:
        self.dismiss(BACK)


PROFILE_DEFAULTS = {
    "ecole": "Mon École",
    "niveau": "Ingénieur 5ème année",
    "specialite": "Informatique",
    "diplome_prevu": "2027",
    # Mapping libre de catégories : JobSearch est généraliste, ce ne sont pas
    # forcément des compétences "informatique" (voir render_profile_yaml) —
    # ces deux catégories ne sont qu'un exemple de départ, librement renommable/
    # remplaçable dans le TextArea de l'écran.
    "competences": {"Langages": ["Python", "Java", "C"], "Outils": ["Git", "Docker", "Linux"]},
    "langues": ["Français:Natif", "Anglais:Courant (B2/C1)"],
}


class ProfileScreen(ModalScreen[dict | _Back]):
    """Étape 4 — Profil du candidat.

    Les compétences sont saisies en paires libres Catégorie/Détail (ex:
    "Langages: Python, C++" ou "Domaines du droit: Droit des affaires, Droit
    social") plutôt qu'en vocabulaire informatique fixe (langages/frameworks/
    outils) — l'utilisateur nomme lui-même ses catégories. Voir
    `jobsearch.filter.render_competences_lines` pour la rétrocompatibilité
    avec l'ancien format à clés fixes."""

    BINDINGS = [
        Binding("escape", "back", "Retour"),
        Binding("ctrl+s", "submit", "Valider", priority=True),
    ]

    def __init__(self, initial: dict | None = None) -> None:
        super().__init__()
        self._initial = {**PROFILE_DEFAULTS, **(initial or {})}

    def compose(self) -> ComposeResult:
        v = self._initial
        with VerticalScroll(id="wizard-box"):
            yield Label(
                "Profil du candidat.\n"
                "ctrl+s : valider tout le formulaire   ·   échap : retour à l'étape précédente"
            )
            yield Label("École :")
            yield Input(value=v["ecole"], id="p-ecole")
            yield Label("Niveau (ex: Ingénieur 5ème année) :")
            yield Input(value=v["niveau"], id="p-niveau")
            yield Label("Spécialité :")
            yield Input(value=v["specialite"], id="p-specialite")
            yield Label("Diplôme prévu (année) :")
            yield Input(value=v["diplome_prevu"], id="p-diplome")
            yield Label(
                "Compétences — une catégorie par ligne, format \"Catégorie: item1, item2\".\n"
                "Choisis toi-même le nom de tes catégories, ex :\n"
                "  Langages: Python, C++\n"
                "  Frameworks: React, Django\n"
                "ou, dans un autre domaine :\n"
                "  Domaines du droit: Droit des affaires, Droit social\n"
                "  Logiciels: Lexis, Dalloz"
            )
            yield TextArea(render_competences_text(v["competences"]), id="p-competences")
            yield Label("Langues (format \"Langue:Niveau\", séparées par des virgules) :")
            yield Input(value=", ".join(v["langues"]), id="p-langues")

    def on_mount(self) -> None:
        self.query_one("#p-ecole", Input).focus()

    def _collect(self) -> dict:
        return {
            "ecole": self.query_one("#p-ecole", Input).value.strip(),
            "niveau": self.query_one("#p-niveau", Input).value.strip(),
            "specialite": self.query_one("#p-specialite", Input).value.strip(),
            "diplome_prevu": self.query_one("#p-diplome", Input).value.strip(),
            "competences": parse_competences_text(self.query_one("#p-competences", TextArea).text),
            "langues": parse_csv(self.query_one("#p-langues", Input).value),
        }

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._collect())

    def action_submit(self) -> None:
        self.dismiss(self._collect())

    def action_back(self) -> None:
        self.dismiss(BACK)


class ConfirmWriteScreen(ModalScreen[bool | _Back]):
    """Étape 6 — Récapitulatif PRÉ-écriture : rien n'est encore écrit sur le
    disque à ce stade. Confirmer déclenche la phase d'écriture (avec, le cas
    échéant, une confirmation d'écrasement par fichier déjà présent)."""

    BINDINGS = [
        Binding("enter", "go", "Écrire"),
        Binding("escape", "back", "Retour"),
    ]

    def __init__(self, plan: list[tuple[str, str]]) -> None:
        super().__init__()
        self._plan = plan

    def compose(self) -> ComposeResult:
        lines = ["[b]Récapitulatif[/b]\n", "Sur confirmation, les fichiers suivants seront écrits"]
        lines.append("(une confirmation d'écrasement sera demandée pour ceux déjà présents) :\n")
        for label, path in self._plan:
            marker = " [dim](existe déjà)[/dim]" if os.path.exists(path) else ""
            lines.append(f"  · {label} — {path}{marker}")
        lines.append(
            "\nÀ personnaliser plus tard, si besoin (onglet Réglages, touche 6) :"
            "\n  - cv_master/cv_data.yaml et cv_master.md — écrits ci-dessus avec un contenu "
            "générique (exemple livré avec le paquet), à remplacer par ton propre CV"
            "\n  - config/templates/*.html (charte graphique CV/lettre)"
            "\n  - clés API laissées vides ci-dessus (éditables directement dans .env)"
        )
        lines.append("\n[dim]entrée : écrire ces fichiers   ·   échap : retour à l'étape aperçu du prompt[/dim]")
        with VerticalScroll(id="wizard-box"):
            yield Static("\n".join(lines))

    def action_go(self) -> None:
        self.dismiss(True)

    def action_back(self) -> None:
        self.dismiss(BACK)


class SummaryScreen(ModalScreen[None]):
    """Écran final, affiché après l'écriture effective des fichiers."""

    BINDINGS = [Binding("enter", "go", "Terminer"), Binding("escape", "go", "Terminer")]

    def __init__(self, written: list[tuple[str, str]], skipped: list[str]) -> None:
        super().__init__()
        self._written = written
        self._skipped = skipped

    def compose(self) -> ComposeResult:
        lines = ["[b]Terminé[/b]\n", "Configuré :"]
        for label, path in self._written:
            lines.append(f"  ✓ {label} — {path}")
        if self._skipped:
            lines.append("\nNon écrit (fichier existant conservé) :")
            for label in self._skipped:
                lines.append(f"  · {label}")
        lines.append(
            "\n[b]Fortement recommandé[/b] : personnalise davantage le prompt de scoring et le "
            "pré-filtre avant de scorer beaucoup d'offres. Le barème livré est générique — un jeu "
            "d'exemples réels calibrés sur ta propre recherche (remplacer les exemples A-D de "
            "config/prompts/filter.txt par 8-14 offres vraies) et un pré-filtre de mots-clés affiné "
            "améliorent nettement la précision du classement. À faire depuis l'onglet Réglages "
            "(touche 6) de la TUI."
        )
        lines.append("\nProchaine commande :\n  [b]jobsearch --scrape[/b]\n(ou jobsearch --tui puis 'S')")
        lines.append("\n[dim]entrée pour terminer[/dim]")
        with VerticalScroll(id="wizard-box"):
            yield Static("\n".join(lines))

    def action_go(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class SetupWizard(App):
    TITLE = "Installation de JobSearch"

    CSS = """
    WelcomeScreen, OverwriteConfirmScreen, InfoScreen, ApiKeyScreen, SearchScreen,
    PrefilterScreen, PromptPreviewScreen, ProfileScreen, ConfirmWriteScreen, SummaryScreen {
        align: center middle;
    }
    #wizard-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 90;
        max-width: 95%;
        height: auto;
        max-height: 90%;
    }
    #wizard-box-wide {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 100;
        max-width: 98%;
        height: auto;
        max-height: 92%;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._written: list[tuple[str, str]] = []
        self._skipped: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("")
        yield Footer()

    def on_mount(self) -> None:
        self.run_wizard()

    async def _write_with_confirm(self, path: str, content: str, label: str) -> bool:
        if os.path.exists(path):
            ok = await self.push_screen_wait(
                OverwriteConfirmScreen(
                    f"{label} existe déjà :\n{path}\n\nÉcraser avec la nouvelle configuration ?"
                )
            )
            if not ok:
                self._skipped.append(label)
                return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # L'arborescence de données est créée en même temps, comme le fait
        # `setup.sh` : sans `data/offers/`, la TUI plantait au lancement juste
        # après un `--setup` (aucun scrape encore effectué). Un dossier vide est
        # un état parfaitement normal — c'est son ABSENCE qui cassait.
        os.makedirs(PATHS.offers_dir, exist_ok=True)
        os.makedirs(PATHS.output_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self._written.append((label, path))
        return True

    def _build_write_items(self, answers: dict) -> list[tuple[str, str, str]]:
        """(label, path, contenu) — dérivé des réponses collectées. Recalculé
        au moment de l'écriture (étape 6), jamais avant."""
        items: list[tuple[str, str, str]] = []
        # `.env` est toujours écrit, même si toutes les clés ont été sautées :
        # `_check_setup` (jobsearch/cli.py) le réclame ensuite, et un fichier
        # commenté sans clé renseignée reste un état cohérent (à compléter plus
        # tard), là où une absence totale relance le message « première
        # utilisation détectée » en boucle après un --setup pourtant complet.
        api_keys = answers.get("api_keys", API_KEYS_DEFAULTS)
        items.append(("Clés API (.env)", PATHS.env_path, render_env_file(api_keys)))
        search_values = answers["search_values"]
        prefilter_values = answers.get("prefilter_values", PREFILTER_DEFAULTS)
        items.append((
            "Sujet de recherche (search.yaml)",
            os.path.join(PATHS.config_dir, "search.yaml"),
            render_search_yaml(search_values, prefilter_values),
        ))
        items.append((
            "Sources à scraper (sources.yaml)",
            os.path.join(PATHS.config_dir, "sources.yaml"),
            render_sources_yaml(search_values, prefilter_values),
        ))
        items.append((
            "Prompt de scoring (filter.txt)",
            os.path.join(PATHS.user_prompts_dir, "filter.txt"),
            render_filter_prompt(search_values),
        ))
        items.append((
            "Profil (profile.yaml)",
            os.path.join(PATHS.config_dir, "profile.yaml"),
            render_profile_yaml(answers["profile_values"]),
        ))
        # CV master : servent uniquement à `--generate` (candidature), jamais au scrape
        # ni à la revue des offres (`--tui`) — mais `_check_setup` les réclame quand
        # même avant de laisser lancer QUOI que ce soit. Plutôt que retirer cette
        # exigence (et risquer un `--generate` qui échoue plus tard sans repère clair),
        # on les initialise ici depuis les `.example` du paquet : un CV générique,
        # explicitement signalé comme tel (voir ConfirmWriteScreen/SummaryScreen),
        # à personnaliser ensuite via `cv_master/cv_data.yaml` (onglet Réglages, touche 6).
        # Aucune garde silencieuse : un `.example` absent ici, c'est exactement le bug
        # qui faisait annoncer une réussite à l'assistant tout en n'écrivant jamais ces
        # deux fichiers — d'où la boucle infinie de `_check_setup` sur une installation
        # pipx (les `.example` n'étaient pas embarqués dans le paquet).
        cv_example_data = PATHS.example("cv_data.example.yaml")
        cv_example_md = PATHS.example("cv_master.example.md")
        if not os.path.exists(cv_example_data):
            raise FileNotFoundError(
                f"Fichier d'exemple manquant : {cv_example_data} — installation du paquet incomplète."
            )
        if not os.path.exists(cv_example_md):
            raise FileNotFoundError(
                f"Fichier d'exemple manquant : {cv_example_md} — installation du paquet incomplète."
            )
        with open(cv_example_data, encoding="utf-8") as f:
            items.append((
                "CV master, générique à personnaliser (cv_data.yaml)",
                os.path.join(PATHS.cv_dir, "cv_data.yaml"),
                f.read(),
            ))
        with open(cv_example_md, encoding="utf-8") as f:
            items.append((
                "Résumé CV, générique à personnaliser (cv_master.md)",
                os.path.join(PATHS.cv_dir, "cv_master.md"),
                f.read(),
            ))
        return items

    @work
    async def run_wizard(self) -> None:
        answers: dict = {}
        step = 0
        while True:
            if step == 0:
                proceed = await self.push_screen_wait(WelcomeScreen())
                if not proceed:
                    self.exit()
                    return
                step = 1

            elif step == 1:
                result = await self.push_screen_wait(ApiKeyScreen(initial=answers.get("api_keys")))
                if result is BACK:
                    step = 0
                    continue
                answers["api_keys"] = result
                if answers["api_keys"]["key"]:
                    do_validate = await self.push_screen_wait(
                        OverwriteConfirmScreen(
                            "Valider la clé Anthropic via un appel API minimal (métadonnées, non facturé) ?"
                        )
                    )
                    if do_validate:
                        ok, msg = await asyncio.to_thread(check_api_key, answers["api_keys"]["key"])
                        await self.push_screen_wait(InfoScreen(msg))
                step = 2

            elif step == 2:
                result = await self.push_screen_wait(SearchScreen(initial=answers.get("search_values")))
                if result is BACK:
                    step = 1
                    continue
                answers["search_values"] = result
                step = 3

            elif step == 3:
                result = await self.push_screen_wait(
                    PrefilterScreen(initial=answers.get("prefilter_values"))
                )
                if result is BACK:
                    step = 2
                    continue
                answers["prefilter_values"] = result
                step = 4

            elif step == 4:
                # Le profil est collecté AVANT l'aperçu du prompt (étape 5) : l'aperçu
                # substitue {profile} avec ces réponses, donc il doit les avoir en main.
                result = await self.push_screen_wait(ProfileScreen(initial=answers.get("profile_values")))
                if result is BACK:
                    step = 3
                    continue
                answers["profile_values"] = result
                step = 5

            elif step == 5:
                prompt_text = render_filter_prompt_preview(
                    answers["search_values"], answers.get("prefilter_values"), answers["profile_values"]
                )
                confirmed = await self.push_screen_wait(PromptPreviewScreen(prompt_text))
                if confirmed is BACK:
                    step = 4
                    continue
                step = 6

            elif step == 6:
                plan = [(label, path) for label, path, _content in self._build_write_items(answers)]
                confirmed = await self.push_screen_wait(ConfirmWriteScreen(plan))
                if confirmed is BACK:
                    step = 5
                    continue
                step = 7

            elif step == 7:
                for label, path, content in self._build_write_items(answers):
                    await self._write_with_confirm(path, content, label)
                await self.push_screen_wait(SummaryScreen(self._written, self._skipped))
                self.exit()
                return


def run_setup() -> None:
    SetupWizard().run()


if __name__ == "__main__":
    run_setup()
