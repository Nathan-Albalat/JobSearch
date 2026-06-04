import argparse
import sys

from src.scraper import scrape_all
from src.filter import filter_all
from src.report import generate_report, load_filtered_offers
from src.generator import generate
from src.notifier import send_notification
from src.manual import from_file, from_url


def cmd_scrape(threshold: int) -> None:
    print("=" * 50)
    print("ÉTAPE 1/3 — Collecte des offres")
    print("=" * 50)
    new_offers = scrape_all()
    print(f"→ {len(new_offers)} nouvelle(s) offre(s) collectée(s).\n")

    print("=" * 50)
    print("ÉTAPE 2/3 — Filtrage par pertinence (Claude API)")
    print("=" * 50)
    filtered, pertinent = filter_all(threshold=threshold)
    print(f"→ {filtered} offre(s) analysée(s), {pertinent} pertinente(s) (score ≥ {threshold}).\n")

    print("=" * 50)
    print("ÉTAPE 3/3 — Génération du rapport")
    print("=" * 50)
    report_path = generate_report(threshold=threshold)
    print(f"→ Rapport généré : {report_path}\n")

    all_filtered = load_filtered_offers("data")
    pertinent_offers = [
        o for o in all_filtered if o["filter_result"].get("score", 0) >= threshold
    ]
    sent = send_notification(pertinent_offers, threshold, report_path)
    if sent:
        print("→ Notification email envoyée.\n")

    print("Ouvre le rapport, coche les offres qui t'intéressent, puis lance :")
    print("  python main.py --generate <offer_id>")


def cmd_generate(offer_id: str) -> None:
    print("=" * 50)
    print(f"Génération de candidature pour l'offre : {offer_id}")
    print("=" * 50)
    folder = generate(offer_id)
    print(f"\nCandidature prête dans : {folder}")
    print(f"  - {folder}/lettre.md")
    print(f"  - {folder}/cv_adapte.md")


def cmd_from_file(args) -> None:
    print("=" * 50)
    print("Candidature manuelle — depuis fichier HTML")
    print("=" * 50)
    folder = from_file(
        args.from_file,
        url_source=args.url_source,
        title_override=args.title or "",
        company_override=args.company or "",
    )
    print(f"\nCandidature prête dans : {folder}")
    print(f"  - {folder}/lettre.md")
    print(f"  - {folder}/cv_adapte.md")


def cmd_from_url(args) -> None:
    print("=" * 50)
    print("Candidature manuelle — depuis URL")
    print("=" * 50)
    folder = from_url(
        args.from_url,
        title_override=args.title or "",
        company_override=args.company or "",
    )
    print(f"\nCandidature prête dans : {folder}")
    print(f"  - {folder}/lettre.md")
    print(f"  - {folder}/cv_adapte.md")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline automatisé de recherche de stage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python main.py --scrape
  python main.py --scrape --threshold 7
  python main.py --generate 0151e52f
  python main.py --from-url https://example.com/jobs/stage-dev
  python main.py --from-file ~/Downloads/offre_wttj.html --url-source "https://..." --company "Criteo"
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scrape",
        action="store_true",
        help="Collecte les offres, filtre par pertinence et génère le rapport",
    )
    group.add_argument(
        "--generate",
        metavar="OFFER_ID",
        help="Génère la lettre de motivation et le CV adapté pour l'offre donnée",
    )
    group.add_argument(
        "--from-file",
        metavar="PATH",
        help="Génère une candidature depuis une page HTML sauvegardée (ex: WTTJ)",
    )
    group.add_argument(
        "--from-url",
        metavar="URL",
        help="Télécharge une page d'offre et génère la candidature",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=6,
        help="Score minimum de pertinence (défaut: 6, utilisé avec --scrape)",
    )
    parser.add_argument(
        "--url-source",
        default="",
        metavar="URL",
        help="URL source de l'offre (utilisé avec --from-file pour l'inclure dans la lettre)",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Surcharge le titre extrait (utilisé avec --from-file / --from-url)",
    )
    parser.add_argument(
        "--company",
        default="",
        help="Surcharge l'entreprise extraite (utilisé avec --from-file / --from-url)",
    )

    args = parser.parse_args()

    if args.scrape:
        cmd_scrape(args.threshold)
    elif args.generate:
        cmd_generate(args.generate)
    elif args.from_file:
        cmd_from_file(args)
    elif args.from_url:
        cmd_from_url(args)


if __name__ == "__main__":
    main()
