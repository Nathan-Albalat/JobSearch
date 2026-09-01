import argparse
import functools
import json
import os
import re
import sys
from datetime import date

import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from jobsearch.config import PATHS, Search, fill_template, get_search


# Ancien format de `competences_techniques` (clés fixes, contexte informatique) —
# conservé pour rétrocompatibilité : les profils existants (dont celui réellement
# utilisé par l'auteur) reposent encore dessus. `render_competences_lines` bascule
# vers ce format seulement si le nouveau (`competences`, mapping libre de
# catégories) est absent.
_LEGACY_COMPETENCES_LABELS = [
    ("langages_maitrise", "Langages maîtrisés"),
    ("langages_pratiques", "Langages pratiqués"),
    ("frameworks", "Frameworks"),
    ("outils", "Outils"),
    ("domaines_ml", "Domaines ML pratiqués"),
]


def render_competences_lines(profile: dict) -> list[str]:
    """Une ligne "Catégorie : item1, item2" par catégorie de compétences.

    Nouveau format (généraliste, choisi librement par l'utilisateur à
    l'installation) : `competences: {Catégorie: [items]}`. Ancien format
    (clés fixes, hérité du sujet historique "stage tech") : toujours lu si
    `competences` (nouveau) est absent — ne jamais lever d'exception sur
    l'un ou l'autre.
    """
    competences = profile.get("competences")
    if isinstance(competences, dict) and competences:
        return [
            f"{categorie} : {', '.join(items or [])}"
            for categorie, items in competences.items()
        ]
    legacy = profile.get("competences_techniques", {}) or {}
    return [
        f"{label} : {', '.join(legacy.get(key, []) or [])}"
        for key, label in _LEGACY_COMPETENCES_LABELS
    ]


def render_profile_text(profile: dict, search: Search) -> str:
    """Corps commun à `load_profile_text` (lecture disque) et à l'aperçu du
    prompt de l'assistant `--setup` (`jobsearch/wizard.py`, profil encore en
    mémoire, pas encore écrit sur disque) — pas de lecture de fichier ici."""
    formation = profile.get("formation", {})
    langues = profile.get("langues", [])

    lines = [
        f"Formation : {formation.get('niveau', '')} — {formation.get('ecole', '')} "
        f"({formation.get('specialite', '')}), diplôme prévu {formation.get('diplome_prevu', '')}",
        search.render_criteria_text(),
        f"Géographie : priorité {', '.join(search.get('geo', 'priorite', default=[]))} ; "
        f"ensuite {', '.join(search.get('geo', 'secondaire', default=[]))}",
        f"Domaines : {', '.join(search.get('domaines', 'liste', default=[]))} "
        f"— {search.get('domaines', 'ouverture', default='')}",
        *render_competences_lines(profile),
        f"Langues : {', '.join(l['langue'] + ' (' + l.get('niveau', '') + ')' for l in langues)}",
        f"Score minimum requis : {search.threshold}/10",
        f"Bonus si : {', '.join(search.get('scoring', 'bonus', default=[]))}",
        f"Pénalité si : {', '.join(search.get('scoring', 'malus', default=[]))}",
    ]
    return "\n".join(lines)


def load_profile_text(config_dir: str, search: Search | None = None) -> str:
    path = os.path.join(config_dir, "profile.yaml")
    with open(path, "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)

    if "stage" in profile or "criteres_pertinence" in profile:
        raise ValueError(
            "config/profile.yaml contient encore une clé 'stage:' ou 'criteres_pertinence:' — "
            "ce format est obsolète. Ces clés dépendent du sujet de recherche et ont migré vers "
            "config/search.yaml. Regarde config/profile.example.yaml pour le nouveau format attendu."
        )

    if search is None:
        search = get_search(config_dir)

    return render_profile_text(profile, search)


def strip_html(html: str, max_chars: int = 2000) -> str:
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text


# Recherche par mot entier : un simple `kw in text` provoquait des faux positifs
# massifs ("git" dans "digital", "rust" dans "trust", "intern" dans "international",
# et surtout l'ancien "r " qui matchait "manager ", "senior "…). Sur l'historique,
# ~43 % des offres envoyées à l'API l'étaient pour rien, sans qu'aucune offre
# pertinente (score >= 6) ne dépende de ces correspondances partielles.
#
# `sorted()` + `re.escape` sont conservés à l'identique : c'est ce qui garantit
# une regex identique à l'ancienne version pour une liste de mots-clés
# identique — condition de la preuve de non-régression.
@functools.lru_cache(maxsize=8)
def _keywords_re(keywords: tuple) -> "re.Pattern | None":
    if not keywords:
        return None
    return re.compile(
        "|".join(
            r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])"
            for kw in sorted(keywords)
        )
    )


def is_tech_offer(offer: dict, search: Search | None = None) -> bool:
    """Returns True if the offer appears tech-related based on title + start of description."""
    if search is None:
        search = get_search()

    if not search.prefilter_enabled:
        return True

    desc_chars = search.prefilter_desc_chars
    title = offer.get("title", "").lower()
    desc_start = strip_html(offer.get("description", ""), max_chars=desc_chars).lower()
    text = title + " " + desc_start

    exclude_re = _keywords_re(search.prefilter_exclude_keywords)
    if exclude_re is not None and exclude_re.search(text):
        return False

    keywords_re = _keywords_re(search.prefilter_keywords)
    if keywords_re is None:
        # Pas de mots-clés configurés : équivalent à "pas de pré-filtre", pas à
        # "tout écarter" — une liste vide par faute de frappe YAML ne doit pas
        # faire scorer 0 offre en silence.
        return True
    return bool(keywords_re.search(text))


def build_system_prompt(template: str, profile_text: str, search: Search | None = None) -> str:
    """Returns the static part of the prompt (before ---OFFER---)."""
    if search is None:
        search = get_search()
    static_part = template.split("---OFFER---")[0]
    return fill_template(static_part, {**search.placeholders(), "profile": profile_text})


def build_offer_prompt(template: str, offer: dict) -> str:
    """Returns the offer-specific part of the prompt (after ---OFFER---)."""
    parts = template.split("---OFFER---")
    offer_part = parts[1] if len(parts) > 1 else template
    return fill_template(offer_part, {
        "title": offer.get("title", ""),
        "company": offer.get("company", ""),
        "location": offer.get("location", ""),
        "url": offer.get("url", ""),
        "description": strip_html(offer.get("description", ""), max_chars=1500),
    }, warn=False)


def call_claude(client, system_text: str, offer_text: str, search: Search) -> tuple[str, object]:
    response = client.messages.create(
        model=search.filter_model,
        max_tokens=search.filter_max_tokens,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": offer_text}],
    )
    return response.content[0].text, response.usage


def parse_filter_response(text: str) -> dict:
    text = text.strip()
    # Retirer d'éventuels blocs ```json ... ```
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def score_offer(client, offer: dict, system_text: str, template: str, search: Search) -> tuple[dict, object]:
    offer_text = build_offer_prompt(template, offer)
    raw, usage = call_claude(client, system_text, offer_text, search)
    result = parse_filter_response(raw)
    result["filtered_at"] = str(date.today())
    return result, usage


def _save_offer_file(filepath: str, offer: dict) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(offer, f, ensure_ascii=False, indent=2)


def _prefilter_result() -> dict:
    return {
        "score": 0,
        "pertinent": False,
        "raison": "Hors domaine (pré-filtre mots-clés, pas d'appel API)",
        "points_forts": [],
        "points_faibles": [],
        "filtered_at": str(date.today()),
        "prefiltered": True,
    }


def filter_batch(
    client,
    to_score: list[tuple[str, dict]],
    system_text: str,
    template: str,
    threshold: int,
    search: Search,
) -> tuple[int, int]:
    """Score les offres via l'API Message Batches (-50% de coût, asynchrone)."""
    import time

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests_list = [
        Request(
            custom_id=offer["id"],
            params=MessageCreateParamsNonStreaming(
                model=search.filter_model,
                max_tokens=search.filter_max_tokens,
                system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": build_offer_prompt(template, offer)}],
            ),
        )
        for _, offer in to_score
    ]

    batch = client.messages.batches.create(requests=requests_list)
    print(f"  Batch créé : {batch.id} ({len(requests_list)} requêtes). Attente des résultats...")

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        counts = batch.request_counts
        print(
            f"  ... en cours : {counts.succeeded + counts.errored}/{len(requests_list)} traitée(s)",
            end="\r",
            flush=True,
        )
        time.sleep(search.batch_poll_seconds)

    files_by_id = {offer["id"]: (filepath, offer) for filepath, offer in to_score}
    total_filtered = 0
    total_pertinent = 0
    total_errored = 0

    for result in client.messages.batches.results(batch.id):
        entry = files_by_id.get(result.custom_id)
        if entry is None:
            continue
        filepath, offer = entry
        if result.result.type != "succeeded":
            total_errored += 1
            print(f"  Erreur batch sur {result.custom_id} ({result.result.type}) — sera retentée au prochain run.", file=sys.stderr)
            continue
        msg = result.result.message
        raw = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            parsed = parse_filter_response(raw)
        except (json.JSONDecodeError, IndexError) as e:
            total_errored += 1
            print(f"  Réponse illisible pour {result.custom_id} : {e}", file=sys.stderr)
            continue
        parsed["filtered_at"] = str(date.today())
        offer["filter_result"] = parsed
        _save_offer_file(filepath, offer)
        total_filtered += 1
        if parsed.get("score", 0) >= threshold:
            total_pertinent += 1
            print(f"  [{parsed['score']}/10] {offer.get('title', '?')} — {offer.get('company', '?')}")

    print(f"\n  → Batch terminé : {total_filtered} réussie(s), {total_errored} en erreur.")
    return total_filtered, total_pertinent


def filter_all(
    config_dir: str = PATHS.config_dir,
    data_dir: str = PATHS.data_dir,
    threshold: int | None = None,
    use_batch: bool = True,
) -> tuple[int, int]:
    load_dotenv(PATHS.env_path, override=True)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Erreur : ANTHROPIC_API_KEY manquant dans .env", file=sys.stderr)
        sys.exit(1)

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)

    search = get_search(config_dir)
    if threshold is None:
        threshold = search.threshold

    template_path = PATHS.prompt("filter.txt")
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    profile_text = load_profile_text(config_dir, search=search)
    system_text = build_system_prompt(template, profile_text, search=search)
    offers_dir = os.path.join(data_dir, "offers")

    total_filtered = 0
    total_pertinent = 0
    total_prefiltered = 0
    total_cache_creation = 0
    total_cache_read = 0

    if not os.path.isdir(offers_dir):
        print("Aucune offre à filtrer (lance d'abord un scrape).")
        return
    all_files = sorted(f for f in os.listdir(offers_dir) if f.endswith(".json"))
    pending = [
        f for f in all_files
        if "filter_result" not in json.load(open(os.path.join(offers_dir, f), encoding="utf-8"))
    ]
    total_pending = len(pending)
    print(f"  {total_pending} offre(s) à analyser ({len(all_files) - total_pending} déjà filtrée(s)).")

    # Pré-filtre mots-clés : écarte les offres hors domaine sans appel API
    to_score: list[tuple[str, dict]] = []
    for filename in pending:
        filepath = os.path.join(offers_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            offer = json.load(f)
        if search.prefilter_enabled and not is_tech_offer(offer, search=search):
            offer["filter_result"] = _prefilter_result()
            _save_offer_file(filepath, offer)
            total_prefiltered += 1
        else:
            to_score.append((filepath, offer))

    print(f"  → {total_prefiltered} offre(s) écartée(s) par pré-filtre, {len(to_score)} à scorer via API.")

    # Mode batch (-50% de coût) dès qu'il y a assez d'offres
    if use_batch and len(to_score) >= search.batch_min_offers:
        total_filtered, total_pertinent = filter_batch(
            client, to_score, system_text, template, threshold, search
        )
        return total_filtered, total_pertinent

    # Mode séquentiel (petits volumes ou --no-batch)
    for idx, (filepath, offer) in enumerate(to_score, 1):
        total_pending = len(to_score)
        print(f"  [{idx}/{total_pending}] ⏳ {offer.get('title', '?')[:55]}", end="\r", flush=True)

        try:
            result, usage = score_offer(client, offer, system_text, template, search)
        except Exception as e:
            err_str = str(e).lower()
            if "credit" in err_str or "balance" in err_str or "billing" in err_str:
                print(
                    f"\nErreur : crédit Anthropic insuffisant. Recharge sur console.anthropic.com.",
                    file=sys.stderr,
                )
                print(
                    f"→ {total_filtered} offre(s) filtrée(s) avant interruption.",
                    file=sys.stderr,
                )
                break
            print(f"  Erreur sur {offer.get('id', '?')} : {e}", file=sys.stderr)
            continue

        total_cache_creation += getattr(usage, "cache_creation_input_tokens", 0) or 0
        total_cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0

        offer["filter_result"] = result
        _save_offer_file(filepath, offer)

        total_filtered += 1
        if result.get("score", 0) >= threshold:
            total_pertinent += 1
            print(
                f"\n  [{result['score']}/10] {offer.get('title', '?')} — {offer.get('company', '?')}"
            )

    if total_cache_creation or total_cache_read:
        cache_hit_rate = (total_cache_read / (total_cache_creation + total_cache_read) * 100) if (total_cache_creation + total_cache_read) else 0
        print(f"  → Cache : {total_cache_creation} tokens écrits, {total_cache_read} tokens lus ({cache_hit_rate:.0f}% de hits).")
    return total_filtered, total_pertinent


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Filtre les offres via Claude API")
    parser.add_argument("--threshold", type=int, default=None, help="Score minimum (défaut: config/search.yaml)")
    parser.add_argument("--no-batch", action="store_true", help="Force le mode séquentiel (sans Message Batches)")
    args = parser.parse_args()

    threshold = args.threshold if args.threshold is not None else get_search().threshold
    print(f"Filtrage des offres (seuil : {threshold}/10)...")
    filtered, pertinent = filter_all(threshold=threshold, use_batch=not args.no_batch)
    print(f"\n{filtered} offre(s) filtrée(s), {pertinent} pertinente(s) (score >= {threshold}).")
