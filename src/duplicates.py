"""Marquage manuel des offres en doublon (`duplicate_of`).

Stdlib uniquement (json, os, re) — sur le modèle de `src/flow.py`. Ne dépend
volontairement ni de `src/scraper.py` (import lourd : feedparser/requests/yaml,
alourdirait le démarrage TUI/CLI) ni de `src/tracker.py` (qui ne connaît que les
candidatures, pas les offres).

Cas d'usage : la dédup fuzzy du scraper (préventive, à l'ingestion) ne peut pas
détecter tous les doublons inter-sources. Exemple réel : l'offre Farmless
"Software Engineering Intern (Full-Stack)" (LinkedIn, candidature envoyée) est
ressortie via WTTJ sous le titre "Fullstack Software Engineering Intern" —
similarité mesurée 0.392 contre un seuil de 0.9. Pire : Farmless publie aussi un
"Software Engineering Intern (Rust)", poste réellement différent, dont le
recouvrement de mots avec l'offre WTTJ est EXACTEMENT le même (0.667) que le
vrai doublon. Aucun seuil ne peut départager ces deux cas : il faut comprendre
que « Fullstack » ≠ « Rust », donc marquage manuel assisté (`suggest_targets`
propose, l'utilisateur confirme dans la TUI).
"""
import json
import os
import re

from src.config import PATHS

APPLICATIONS_FILENAME = "applications.json"


def _offer_path(offer_id: str, data_dir: str = PATHS.data_dir) -> str:
    return os.path.join(data_dir, "offers", f"{offer_id}.json")


def load_offer(offer_id: str, data_dir: str = PATHS.data_dir) -> dict | None:
    """Lecture tolérante d'une offre — retourne None si absente/illisible.

    Contrairement à `generator.py` (l. 18-24), qui fait `sys.exit` sur une offre
    introuvable : inutilisable en TUI, où l'on veut afficher un message et continuer.
    """
    path = _offer_path(offer_id, data_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_applications_raw(data_dir: str = PATHS.data_dir) -> list[dict]:
    """Lecture minimale de applications.json, sans dépendre de src.tracker."""
    path = os.path.join(data_dir, APPLICATIONS_FILENAME)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _normalize_company(name: str) -> str:
    """casefold + ponctuation retirée + espaces normalisés.

    Ne réutilise volontairement pas `_normalize_for_dedup` de `src/scraper.py` :
    celle-ci retire les mots parasites de TITRES (stage, intern, h/f, 2026…) et
    mangerait un nom d'entreprise contenant l'un de ces termes ; elle appelle
    aussi `get_search()`, donc la config. Une normalisation d'entreprise dédiée
    est plus correcte, pas seulement plus commode.
    """
    name = (name or "").casefold()
    name = re.sub(r"[^\w\s]", " ", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _save_offer_file(offer_id: str, offer: dict, data_dir: str = PATHS.data_dir) -> None:
    with open(_offer_path(offer_id, data_dir), "w", encoding="utf-8") as f:
        json.dump(offer, f, ensure_ascii=False, indent=2)


def mark_duplicate(offer_id: str, target_id: str, data_dir: str = PATHS.data_dir) -> dict:
    """Marque `offer_id` comme doublon de `target_id` (écrit `duplicate_of`).

    Relit toujours l'offre complète depuis le disque avant mutation (jamais un
    dict TUI en mémoire, potentiellement partiel) — même cycle que
    `src/filter.py` (l. 159-161, 241-242, 302, 341-342) pour ne jamais perdre
    `filter_result`/`description`.

    Règles de validation (chacune lève ValueError avec un message clair) :
    - l'offre et la cible doivent exister
    - la cible doit avoir une candidature dans applications.json
    - l'offre ne doit pas avoir de candidature elle-même
    - offer_id != target_id
    - la cible ne doit pas être elle-même marquée doublon (interdit chaînes/cycles)
    """
    if offer_id == target_id:
        raise ValueError(f"Une offre ne peut pas être son propre doublon ({offer_id!r}).")

    offer = load_offer(offer_id, data_dir)
    if offer is None:
        raise ValueError(f"Offre introuvable : {offer_id!r}.")

    target = load_offer(target_id, data_dir)
    if target is None:
        raise ValueError(f"Offre cible introuvable : {target_id!r}.")

    apps = _load_applications_raw(data_dir)
    apps_by_offer = {a.get("offer_id"): a for a in apps}

    if target_id not in apps_by_offer:
        raise ValueError(
            f"La cible {target_id!r} n'a pas de candidature dans applications.json "
            "— le marquage doublon n'a de sens que vers une offre candidatée."
        )

    if offer_id in apps_by_offer:
        raise ValueError(
            f"L'offre {offer_id!r} a déjà une candidature — elle ne peut pas être "
            "marquée comme doublon d'une autre offre."
        )

    if target.get("duplicate_of"):
        raise ValueError(
            f"La cible {target_id!r} est elle-même marquée doublon de "
            f"{target['duplicate_of']!r} — chaîne interdite."
        )

    offer["duplicate_of"] = target_id
    _save_offer_file(offer_id, offer, data_dir)
    return offer


def clear_duplicate(offer_id: str, data_dir: str = PATHS.data_dir) -> dict:
    """Retire le marquage doublon d'une offre (annulation). Idempotent."""
    offer = load_offer(offer_id, data_dir)
    if offer is None:
        raise ValueError(f"Offre introuvable : {offer_id!r}.")
    offer.pop("duplicate_of", None)
    _save_offer_file(offer_id, offer, data_dir)
    return offer


def suggest_targets(offer: dict, apps: list[dict]) -> list[dict]:
    """Candidatures dont l'entreprise correspond à celle de `offer` (normalisée).

    Ne trie pas par titre — la comparaison de chaînes ne peut pas départager
    « Fullstack » de « Rust » (cf. docstring du module) ; seule l'entreprise est
    un signal fiable, le reste est laissé à l'humain.
    """
    company_norm = _normalize_company(offer.get("company", ""))
    if not company_norm:
        return []
    return [a for a in apps if _normalize_company(a.get("company", "")) == company_norm]
