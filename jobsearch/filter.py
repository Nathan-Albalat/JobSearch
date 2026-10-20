import argparse
import functools
import json
import os
import re
import sys
from datetime import date, datetime

import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from jobsearch.config import PATHS, Search, fill_template, get_search
from jobsearch.models import extract_text


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
            "config/search.yaml. Regarde jobsearch/resources/examples/profile.example.yaml "
            "(dans le paquet installé) pour le nouveau format attendu."
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


def _filter_params(search: Search, model_id: str | None = None) -> dict:
    """Modèle, budget et réflexion du filtre — partagés par les modes séquentiel et batch."""
    params = {
        "model": model_id or search.model_for("filter"),
        "max_tokens": search.filter_max_tokens,
    }
    if not search.filter_thinking:
        params["thinking"] = {"type": "disabled"}
    return params


def call_claude(client, system_text: str, offer_text: str, search: Search) -> tuple[str, object]:
    response = client.messages.create(
        **_filter_params(search),
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": offer_text}],
    )
    # extract_text : `content[0]` n'est pas du texte avec un modèle qui réfléchit.
    return extract_text(response, "filter"), response.usage


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


_PENDING_NAME = "pending_batch.json"


def _pending_path(data_dir: str | None = None) -> str:
    return os.path.join(data_dir or PATHS.data_dir, _PENDING_NAME)


def _load_pending(data_dir: str | None = None) -> dict | None:
    """Batch en attente, ou None (absent ou illisible)."""
    try:
        with open(_pending_path(data_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("id") or not isinstance(data.get("offers"), dict):
        return None
    return data


def _save_pending(data: dict, data_dir: str | None = None) -> None:
    """Écriture atomique (.tmp + os.replace)."""
    path = _pending_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _clear_pending(data_dir: str | None = None) -> None:
    try:
        os.remove(_pending_path(data_dir))
    except FileNotFoundError:
        pass


def _wait_batch(client, batch_id: str, total: int, search: Search, progress=None, resumed: bool = False):
    """Attend la fin du batch ; appelle progress(done, total, batch_id, resumed) à chaque interrogation."""
    import time

    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return batch
        counts = batch.request_counts
        done = counts.succeeded + counts.errored
        if progress is not None:
            progress(done, total, batch_id, resumed)
        print(f"  ... en cours : {done}/{total} traitée(s)", end="\r", flush=True)
        time.sleep(search.batch_poll_seconds)


def _apply_batch_results(client, batch_id: str, files_by_id: dict[str, str], threshold: int) -> tuple[int, int]:
    """Applique les résultats d'un batch terminé. files_by_id : offer_id -> chemin du fichier.

    Ignore une offre dont le fichier a disparu ou qui a déjà un filter_result (idempotent).
    """
    total_filtered = 0
    total_pertinent = 0
    total_errored = 0

    for result in client.messages.batches.results(batch_id):
        filepath = files_by_id.get(result.custom_id)
        if filepath is None or not os.path.isfile(filepath):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                offer = json.load(f)
        except (OSError, ValueError):
            continue
        if "filter_result" in offer:
            continue
        if result.result.type != "succeeded":
            total_errored += 1
            print(f"  Erreur batch sur {result.custom_id} ({result.result.type}) — sera retentée au prochain run.", file=sys.stderr)
            continue
        msg = result.result.message
        if msg.stop_reason == "max_tokens":
            total_errored += 1
            print(f"  Réponse tronquée pour {result.custom_id} (max_tokens atteint) — "
                  "augmente models.filter.max_tokens dans search.yaml.", file=sys.stderr)
            continue
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


def filter_batch(
    client,
    to_score: list[tuple[str, dict]],
    system_text: str,
    template: str,
    threshold: int,
    search: Search,
    progress=None,
    data_dir: str | None = None,
) -> tuple[int, int]:
    """Score les offres via l'API Message Batches (-50% de coût, asynchrone)."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    filter_model_id = search.model_for("filter")  # résolu une fois pour tout le batch
    requests_list = [
        Request(
            custom_id=offer["id"],
            params=MessageCreateParamsNonStreaming(
                **_filter_params(search, filter_model_id),
                system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": build_offer_prompt(template, offer)}],
            ),
        )
        for _, offer in to_score
    ]

    batch = client.messages.batches.create(requests=requests_list)
    # Persisté juste après la création : si le terminal se ferme, le batch (facturé) sera repris.
    _save_pending({
        "id": batch.id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": filter_model_id,
        "offers": {offer["id"]: os.path.basename(fp) for fp, offer in to_score},
    }, data_dir)
    print(f"  Batch {batch.id} enregistré ({len(requests_list)} requêtes) — tu peux fermer : "
          "il sera repris au prochain scrape, sans être refacturé.")

    _wait_batch(client, batch.id, len(requests_list), search, progress)

    files_by_id = {offer["id"]: filepath for filepath, offer in to_score}
    result = _apply_batch_results(client, batch.id, files_by_id, threshold)
    _clear_pending(data_dir)
    return result


_BATCH_RESULTS_TTL_DAYS = 29  # résultats conservés 29 jours côté Anthropic


def _resume_pending_batch(client, search: Search, threshold: int, offers_dir: str, progress=None,
                          data_dir: str | None = None):
    """Reprend un batch interrompu. Retourne (filtered, pertinent, exclude_ids, keep_pending)."""
    import anthropic

    pending = _load_pending(data_dir)
    if pending is None:
        return 0, 0, set(), False
    batch_id = pending["id"]

    try:
        age = datetime.now() - datetime.fromisoformat(str(pending.get("created_at")))
        too_old = age.days >= _BATCH_RESULTS_TTL_DAYS
    except (ValueError, TypeError):
        too_old = False
    if too_old:
        print(f"  Batch {batch_id} trop ancien, résultats expirés côté Anthropic : les offres seront renvoyées.",
              file=sys.stderr)
        _clear_pending(data_dir)
        return 0, 0, set(), False

    files_by_id = {oid: os.path.join(offers_dir, fn) for oid, fn in pending["offers"].items()}
    print(f"  Batch précédent {batch_id} trouvé ({len(files_by_id)} offres) — reprise, sans nouvelle facturation.")
    try:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            _wait_batch(client, batch_id, len(files_by_id), search, progress, resumed=True)
        filtered, pertinent = _apply_batch_results(client, batch_id, files_by_id, threshold)
    except anthropic.NotFoundError:
        print(f"  Batch {batch_id} introuvable côté API : abandonné, les offres seront renvoyées.", file=sys.stderr)
        _clear_pending(data_dir)
        return 0, 0, set(), False
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        # Clé invalide / droits : fichier conservé, l'erreur remonte (pas de boucle silencieuse).
        print(f"  Erreur d'authentification en reprenant le batch {batch_id} : {e}. "
              "Batch conservé ; corrige la clé API puis relance.", file=sys.stderr)
        raise
    except anthropic.APIStatusError as e:
        if e.status_code >= 500 or isinstance(e, anthropic.RateLimitError):
            print(f"  Avertissement : reprise du batch {batch_id} impossible ({e}) — "
                  "réessai au prochain lancement.", file=sys.stderr)
            return 0, 0, set(files_by_id), True
        print(f"  Erreur permanente ({e.status_code}) en reprenant le batch {batch_id} : {e}. "
              "Batch abandonné, les offres seront renvoyées.", file=sys.stderr)
        _clear_pending(data_dir)
        return 0, 0, set(), False
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
        print(f"  Avertissement : reprise du batch {batch_id} impossible ({e}) — "
              "réessai au prochain lancement.", file=sys.stderr)
        return 0, 0, set(files_by_id), True
    except Exception as e:  # inconnu : traité comme transitoire (comportement prudent, pas de double facturation)
        print(f"  Avertissement : reprise du batch {batch_id} impossible ({e}) — "
              "réessai au prochain lancement.", file=sys.stderr)
        return 0, 0, set(files_by_id), True
    _clear_pending(data_dir)
    return filtered, pertinent, set(), False


def filter_all(
    config_dir: str = PATHS.config_dir,
    data_dir: str = PATHS.data_dir,
    threshold: int | None = None,
    use_batch: bool = True,
    progress=None,
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
        return 0, 0
    resumed_filtered, resumed_pertinent, excluded_ids, keep_pending = _resume_pending_batch(
        client, search, threshold, offers_dir, progress, data_dir
    )
    excluded_files = set()
    if excluded_ids:
        pend = _load_pending(data_dir) or {}
        excluded_files = set((pend.get("offers") or {}).values())
    all_files = sorted(f for f in os.listdir(offers_dir) if f.endswith(".json"))
    pending = [
        f for f in all_files
        if f not in excluded_files
        and "filter_result" not in json.load(open(os.path.join(offers_dir, f), encoding="utf-8"))
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
        if keep_pending:
            # Un batch précédent reste à reprendre : ne pas écraser pending_batch.json.
            print("  Nouveau batch différé : le batch précédent n'a pas pu être repris (réessai au prochain lancement).",
                  file=sys.stderr)
            return resumed_filtered, resumed_pertinent
        total_filtered, total_pertinent = filter_batch(
            client, to_score, system_text, template, threshold, search, progress, data_dir
        )
        return total_filtered + resumed_filtered, total_pertinent + resumed_pertinent

    total_filtered += resumed_filtered
    total_pertinent += resumed_pertinent

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
