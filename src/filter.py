import argparse
import json
import os
import re
import sys
from datetime import date

import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv


def load_profile_text(config_dir: str) -> str:
    path = os.path.join(config_dir, "profile.yaml")
    with open(path, "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)

    formation = profile.get("formation", {})
    stage = profile.get("stage", {})
    geo = profile.get("preferences_geo", {})
    domaines = profile.get("domaines_recherches", [])
    competences = profile.get("competences_techniques", {})
    langues = profile.get("langues", [])
    criteres = profile.get("criteres_pertinence", {})

    lines = [
        f"Formation : {formation.get('niveau', '')} — {formation.get('ecole', '')} ({formation.get('specialite', '')})",
        f"Stage recherché : {stage.get('duree_min_semaines', '')} semaines minimum, disponible à partir du {stage.get('disponibilite_debut', '')}",
        f"Géographie : priorité {', '.join(geo.get('priorite', []))} ; ensuite {', '.join(geo.get('secondaire', []))}",
        f"Domaines : {', '.join(domaines.get('liste', [])) if isinstance(domaines, dict) else ', '.join(domaines)}",
        f"Langages maîtrisés : {', '.join(competences.get('langages_maitrise', []))}",
        f"Langages pratiqués : {', '.join(competences.get('langages_pratiques', []))}",
        f"Outils : {', '.join(competences.get('outils', []))}",
        f"Langues : {', '.join(l['langue'] + ' (' + l['niveau'] + ')' for l in langues)}",
        f"Score minimum requis : {criteres.get('score_minimum', 6)}/10",
        f"Bonus si : {', '.join(criteres.get('bonus', []))}",
        f"Pénalité si : {', '.join(criteres.get('malus', []))}",
    ]
    return "\n".join(lines)


def strip_html(html: str, max_chars: int = 2000) -> str:
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text


_TECH_KEYWORDS = {
    # Langages
    "python", "java", "javascript", "typescript", "golang", "rust", "scala",
    "kotlin", "swift", "php", "ruby", "c++", "c#", "matlab",
    # NB : le langage R n'est pas listé — trop court pour être cherché sans
    # bruit ("R&D", initiales…). Ces offres sont captées par "data"/"analyst".
    # Frameworks / outils
    "django", "flask", "fastapi", "spring", "react", "vue", "angular",
    "node", "tensorflow", "pytorch", "scikit", "docker", "kubernetes",
    "git", "sql", "nosql", "mongodb", "postgresql", "aws", "azure", "gcp",
    "linux", "api",
    # Rôles tech
    "software", "developer", "engineer", "backend", "frontend", "fullstack",
    "devops", "data", "data science", "machine learning", "deep learning", "nlp",
    "computer vision", "embedded", "firmware", "robotics", "cybersecurity",
    "cloud", "sre", "mlops", "analyst", "tech", "it", "ict", "digital",
    "coding", "programming",
    # Termes français / néerlandais / allemands
    "développeur", "ingénieur", "informatique", "logiciel", "données",
    "apprentissage automatique", "intelligence artificielle", "programmation",
    "systèmes embarqués", "réseaux", "cybersécurité",
    "digitaal", "coderen", "informatica", "datenexperte",
    # Termes génériques stage tech
    "intern", "internship", "stage", "werkstudent",
}

# Recherche par mot entier : un simple `kw in text` provoquait des faux positifs
# massifs ("git" dans "digital", "rust" dans "trust", "intern" dans "international",
# et surtout l'ancien "r " qui matchait "manager ", "senior "…). Sur l'historique,
# ~43 % des offres envoyées à l'API l'étaient pour rien, sans qu'aucune offre
# pertinente (score >= 6) ne dépende de ces correspondances partielles.
_TECH_KEYWORDS_RE = re.compile(
    "|".join(
        r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])"
        for kw in sorted(_TECH_KEYWORDS)
    )
)


def is_tech_offer(offer: dict) -> bool:
    """Returns True if the offer appears tech-related based on title + start of description."""
    title = offer.get("title", "").lower()
    desc_start = strip_html(offer.get("description", ""), max_chars=400).lower()
    text = title + " " + desc_start
    return bool(_TECH_KEYWORDS_RE.search(text))


def _fill(template: str, values: dict) -> str:
    """Remplace {clé} sans interpréter les accolades littérales (exemples JSON)."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def build_system_prompt(template: str, profile_text: str) -> str:
    """Returns the static part of the prompt (before ---OFFER---)."""
    static_part = template.split("---OFFER---")[0]
    return _fill(static_part, {"profile": profile_text})


def build_offer_prompt(template: str, offer: dict) -> str:
    """Returns the offer-specific part of the prompt (after ---OFFER---)."""
    parts = template.split("---OFFER---")
    offer_part = parts[1] if len(parts) > 1 else template
    return _fill(offer_part, {
        "title": offer.get("title", ""),
        "company": offer.get("company", ""),
        "location": offer.get("location", ""),
        "url": offer.get("url", ""),
        "description": strip_html(offer.get("description", ""), max_chars=1500),
    })


def call_claude(client, system_text: str, offer_text: str) -> tuple[str, object]:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
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


def score_offer(client, offer: dict, system_text: str, template: str) -> tuple[dict, object]:
    offer_text = build_offer_prompt(template, offer)
    raw, usage = call_claude(client, system_text, offer_text)
    result = parse_filter_response(raw)
    result["filtered_at"] = str(date.today())
    return result, usage


_MODEL = "claude-haiku-4-5-20251001"
_BATCH_MIN_OFFERS = 6  # en dessous, le mode séquentiel suffit


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
) -> tuple[int, int]:
    """Score les offres via l'API Message Batches (-50% de coût, asynchrone)."""
    import time

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests_list = [
        Request(
            custom_id=offer["id"],
            params=MessageCreateParamsNonStreaming(
                model=_MODEL,
                max_tokens=512,
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
        time.sleep(30)

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
    config_dir: str = "config",
    data_dir: str = "data",
    threshold: int = 6,
    use_batch: bool = True,
) -> tuple[int, int]:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Erreur : ANTHROPIC_API_KEY manquant dans .env", file=sys.stderr)
        sys.exit(1)

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)

    template_path = os.path.join(config_dir, "prompts", "filter.txt")
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    profile_text = load_profile_text(config_dir)
    system_text = build_system_prompt(template, profile_text)
    offers_dir = os.path.join(data_dir, "offers")

    total_filtered = 0
    total_pertinent = 0
    total_prefiltered = 0
    total_cache_creation = 0
    total_cache_read = 0

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
        if not is_tech_offer(offer):
            offer["filter_result"] = _prefilter_result()
            _save_offer_file(filepath, offer)
            total_prefiltered += 1
        else:
            to_score.append((filepath, offer))

    print(f"  → {total_prefiltered} offre(s) écartée(s) par pré-filtre, {len(to_score)} à scorer via API.")

    # Mode batch (-50% de coût) dès qu'il y a assez d'offres
    if use_batch and len(to_score) >= _BATCH_MIN_OFFERS:
        total_filtered, total_pertinent = filter_batch(
            client, to_score, system_text, template, threshold
        )
        return total_filtered, total_pertinent

    # Mode séquentiel (petits volumes ou --no-batch)
    for idx, (filepath, offer) in enumerate(to_score, 1):
        total_pending = len(to_score)
        print(f"  [{idx}/{total_pending}] ⏳ {offer.get('title', '?')[:55]}", end="\r", flush=True)

        try:
            result, usage = score_offer(client, offer, system_text, template)
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
    parser.add_argument("--threshold", type=int, default=6, help="Score minimum (défaut: 6)")
    parser.add_argument("--no-batch", action="store_true", help="Force le mode séquentiel (sans Message Batches)")
    args = parser.parse_args()

    print(f"Filtrage des offres (seuil : {args.threshold}/10)...")
    filtered, pertinent = filter_all(threshold=args.threshold, use_batch=not args.no_batch)
    print(f"\n{filtered} offre(s) filtrée(s), {pertinent} pertinente(s) (score >= {args.threshold}).")
