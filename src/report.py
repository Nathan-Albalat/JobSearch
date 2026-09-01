import argparse
import json
import os
import sys
from datetime import date

from src.config import PATHS, get_search
from src.tracker import render_tracking_section


def load_filtered_offers(data_dir: str) -> list[dict]:
    offers_dir = os.path.join(data_dir, "offers")
    offers = []
    for filename in os.listdir(offers_dir):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(offers_dir, filename), "r", encoding="utf-8") as f:
            offer = json.load(f)
        if "filter_result" in offer:
            offers.append(offer)
    return offers


def render_report(offers: list[dict], threshold: int | None = None, data_dir: str = PATHS.data_dir) -> str:
    if threshold is None:
        threshold = get_search().threshold
    offers_dir = os.path.join(data_dir, "offers")
    total_scraped = sum(
        1
        for f in os.listdir(offers_dir)
        if f.endswith(".json")
    ) if os.path.isdir(offers_dir) else len(offers)

    pertinent = [
        o for o in offers
        if o["filter_result"].get("score", 0) >= threshold and not o.get("duplicate_of")
    ]
    non_pertinent = [o for o in offers if o["filter_result"].get("score", 0) < threshold]
    duplicates = [
        o for o in offers
        if o["filter_result"].get("score", 0) >= threshold and o.get("duplicate_of")
    ]

    pertinent.sort(key=lambda o: o["filter_result"].get("score", 0), reverse=True)

    today = str(date.today())
    lines = [
        f"# Rapport de candidatures — {today}",
        "",
        f"**Offres collectées :** {total_scraped}  ",
        f"**Analysées par Claude :** {len(offers)}  ",
        f"**Pertinentes (score ≥ {threshold}) :** {len(pertinent)}",
        "",
        "---",
        "",
    ]

    if pertinent:
        lines.append(f"## Offres pertinentes ({len(pertinent)})")
        lines.append("")
        for offer in pertinent:
            fr = offer["filter_result"]
            score = fr.get("score", "?")
            title = offer.get("title", "Sans titre")
            company = offer.get("company", "") or "Entreprise inconnue"
            location = offer.get("location", "")
            source = offer.get("source", "")
            url = offer.get("url", "")
            raison = fr.get("raison", "")
            forts = fr.get("points_forts", [])
            faibles = fr.get("points_faibles", [])

            lines.append(f"### [{score}/10] {title} — {company}")
            lines.append(f"- [ ] **À candidater**")
            if location:
                lines.append(f"- 📍 {location} · {source}")
            else:
                lines.append(f"- 📍 {source}")
            if url:
                lines.append(f"- 🔗 [Voir l'offre]({url})")
            if raison:
                lines.append(f"- 💬 *{raison}*")
            if forts:
                lines.append(f"- ✅ Points forts : {', '.join(forts)}")
            if faibles:
                lines.append(f"- ⚠️ Points faibles : {', '.join(faibles)}")
            lines.append("")
            lines.append("---")
            lines.append("")
    else:
        lines.append("## Offres pertinentes (0)")
        lines.append("")
        lines.append("> Aucune offre n'a atteint le seuil de pertinence.")
        lines.append("")
        lines.append("---")
        lines.append("")

    if non_pertinent:
        lines.append(f"## Offres non pertinentes ({len(non_pertinent)})")
        lines.append("")
        lines.append(f"> {len(non_pertinent)} offre(s) écartée(s) (score < {threshold}) — non listées en détail.")
        lines.append("")

    if duplicates:
        lines.append(f"## Doublons marqués ({len(duplicates)})")
        lines.append("")
        lines.append(
            f"> {len(duplicates)} offre(s) pertinente(s) mais marquée(s) comme doublon "
            "d'une candidature existante (`duplicate_of`) — exclues de la liste à candidater."
        )
        lines.append("")

    tracking = render_tracking_section(data_dir)
    if tracking:
        lines.append("---")
        lines.append("")
        lines.extend(tracking)

    return "\n".join(lines)


def generate_report(
    data_dir: str = PATHS.data_dir,
    threshold: int | None = None,
    config_dir: str = PATHS.config_dir,
) -> str:
    if threshold is None:
        threshold = get_search(config_dir).threshold

    offers = load_filtered_offers(data_dir)
    if not offers:
        print(
            "Aucune offre filtrée trouvée. Lance d'abord : python -m src.filter",
            file=sys.stderr,
        )
        sys.exit(1)

    content = render_report(offers, threshold, data_dir=data_dir)
    filename = f"rapport_{date.today()}.md"
    output_path = os.path.join(data_dir, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Génère le rapport de validation des offres")
    parser.add_argument("--threshold", type=int, default=None, help="Score minimum (défaut: config/search.yaml)")
    args = parser.parse_args()

    path = generate_report(threshold=args.threshold)
    print(f"Rapport généré : {path}")
