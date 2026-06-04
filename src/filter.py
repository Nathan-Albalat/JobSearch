import argparse
import json
import os
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


def build_prompt(template: str, profile_text: str, offer: dict) -> str:
    return template.format(
        profile=profile_text,
        title=offer.get("title", ""),
        company=offer.get("company", ""),
        location=offer.get("location", ""),
        url=offer.get("url", ""),
        description=strip_html(offer.get("description", "")),
    )


def call_claude(client, prompt: str) -> str:
    from anthropic import Anthropic  # import local pour éviter crash si pas installé

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def parse_filter_response(text: str) -> dict:
    text = text.strip()
    # Retirer d'éventuels blocs ```json ... ```
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def score_offer(client, offer: dict, profile_text: str, template: str) -> dict:
    prompt = build_prompt(template, profile_text, offer)
    raw = call_claude(client, prompt)
    result = parse_filter_response(raw)
    result["filtered_at"] = str(date.today())
    return result


def filter_all(
    config_dir: str = "config",
    data_dir: str = "data",
    threshold: int = 6,
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
    offers_dir = os.path.join(data_dir, "offers")

    total_filtered = 0
    total_pertinent = 0

    for filename in sorted(os.listdir(offers_dir)):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(offers_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            offer = json.load(f)

        if "filter_result" in offer:
            continue  # déjà filtré

        try:
            result = score_offer(client, offer, profile_text, template)
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
            print(f"  Erreur sur {offer.get('id', filename)} : {e}", file=sys.stderr)
            continue

        offer["filter_result"] = result
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(offer, f, ensure_ascii=False, indent=2)

        total_filtered += 1
        if result.get("score", 0) >= threshold:
            total_pertinent += 1
            print(
                f"  [{result['score']}/10] {offer.get('title', '?')} — {offer.get('company', '?')}"
            )

    return total_filtered, total_pertinent


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Filtre les offres via Claude API")
    parser.add_argument("--threshold", type=int, default=6, help="Score minimum (défaut: 6)")
    args = parser.parse_args()

    print(f"Filtrage des offres (seuil : {args.threshold}/10)...")
    filtered, pertinent = filter_all(threshold=args.threshold)
    print(f"\n{filtered} offre(s) filtrée(s), {pertinent} pertinente(s) (score >= {args.threshold}).")
