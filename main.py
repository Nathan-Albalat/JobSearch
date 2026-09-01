import argparse
import os
import sys

from src.config import PACKAGE_ROOT, PROJECT_ROOT, PATHS


def _check_setup() -> None:
    """Détecte les fichiers de config manquants (dans `JOBSEARCH_HOME`) et propose de lancer setup.sh."""
    required = {
        PATHS.env_path: ".env.example",
        os.path.join(PATHS.config_dir, "profile.yaml"): "config/profile.example.yaml",
        os.path.join(PATHS.config_dir, "search.yaml"): "config/search.example.yaml",
        os.path.join(PATHS.cv_dir, "cv_data.yaml"): "cv_master/cv_data.example.yaml",
        os.path.join(PATHS.cv_dir, "cv_master.md"): "cv_master/cv_master.example.md",
    }
    missing = [f for f in required if not os.path.exists(f)]
    if not missing:
        return

    # Garde-fou : si le dépôt contient encore des données (ancien emplacement,
    # avant migration), ne JAMAIS laisser croire à une installation vierge —
    # proposer --migrate plutôt qu'un simple setup.sh qui ne créerait que des
    # fichiers vides à côté de milliers d'offres déjà collectées.
    legacy_offers_dir = os.path.join(PROJECT_ROOT, "data", "offers")
    legacy_offers = (
        [f for f in os.listdir(legacy_offers_dir) if f.endswith(".json")]
        if os.path.isdir(legacy_offers_dir)
        else []
    )
    if legacy_offers:
        from src.config import JOBSEARCH_HOME

        print(f"⚠️  {JOBSEARCH_HOME} vide ou absent — configuration manquante, MAIS le dépôt")
        print(f"    contient encore {len(legacy_offers)} offre(s) dans {legacy_offers_dir}.")
        print("    Ce sont probablement des données d'une installation antérieure à la séparation")
        print("    code/données : elles n'ont pas disparu, elles n'ont simplement pas encore été")
        print("    copiées vers leur nouvel emplacement.")
        print()
        print("    Lance d'abord :  python main.py --migrate         (aperçu, n'écrit rien)")
        print("    Puis :           python main.py --migrate --apply (copie réelle)")
        sys.exit(1)

    print("⚠️  Première utilisation détectée — fichiers de configuration manquants :")
    for f in missing:
        print(f"   • {f}  (exemple : {required[f]})")
    print()
    answer = input("Lancer setup.sh pour les créer automatiquement ? [O/n] ").strip().lower()
    if answer in ("", "o", "oui", "y", "yes"):
        os.system(f"bash {os.path.join(PACKAGE_ROOT, 'setup.sh')}")
        print()
        print("✓ Fichiers créés. Édite-les avant de relancer.")
        sys.exit(0)
    else:
        print("Abandon. Lance 'bash setup.sh' manuellement ou crée les fichiers à la main.")
        sys.exit(1)


from src.scraper import scrape_all
from src.filter import filter_all
from src.report import generate_report, load_filtered_offers
from src.generator import generate
from src.notifier import send_notification
from src.manual import from_file, from_url
from src.tracker import load_applications, mark_status, render_applications_table, STATUSES
from src.duplicates import mark_duplicate, clear_duplicate


def cmd_scrape(
    threshold: int | None,
    use_batch: bool = True,
    include_groups: set[str] | None = None,
) -> None:
    from src.config import get_search

    if threshold is None:
        threshold = get_search().threshold
    print("=" * 50)
    print("ÉTAPE 1/3 — Collecte des offres")
    print("=" * 50)
    new_offers = scrape_all(include_groups=include_groups)
    print(f"→ {len(new_offers)} nouvelle(s) offre(s) collectée(s).\n")

    print("=" * 50)
    print("ÉTAPE 2/3 — Filtrage par pertinence (Claude API)")
    print("=" * 50)
    filtered, pertinent = filter_all(threshold=threshold, use_batch=use_batch)
    print(f"→ {filtered} offre(s) analysée(s), {pertinent} pertinente(s) (score ≥ {threshold}).\n")

    print("=" * 50)
    print("ÉTAPE 3/3 — Génération du rapport")
    print("=" * 50)
    report_path = generate_report(threshold=threshold)
    print(f"→ Rapport généré : {report_path}\n")

    all_filtered = load_filtered_offers(PATHS.data_dir)
    pertinent_offers = [
        o for o in all_filtered if o["filter_result"].get("score", 0) >= threshold
    ]
    sent = send_notification(pertinent_offers, threshold, report_path)
    if sent:
        print("→ Notification email envoyée.\n")

    print("Ouvre le rapport, coche les offres qui t'intéressent, puis lance :")
    print("  python main.py --generate <offer_id>")


def cmd_generate(offer_id: str, letter_only: bool = False) -> None:
    print("=" * 50)
    mode = " (lettre seule)" if letter_only else ""
    print(f"Génération de candidature pour l'offre : {offer_id}{mode}")
    print("=" * 50)
    folder = generate(offer_id, letter_only=letter_only)
    print(f"\nCandidature prête dans : {folder}")
    print(f"  - {folder}/lettre.md")
    print(f"  - {folder}/lettre.pdf")
    if not letter_only:
        print(f"  - {folder}/cv_adapte.html")
        print(f"  - {folder}/cv_adapte.pdf")


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


def cmd_letter_pdf(args) -> None:
    import json
    import os

    from src.cv_pdf import load_cv_data
    from src.letter_pdf import render_letter_pdf

    path = args.letter_pdf
    if not os.path.exists(path):
        sys.exit(f"Erreur : fichier introuvable : {path}")

    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        body = data.get("body_markdown", "")
        company = data.get("company_name", "")
        location = data.get("company_location", "")
        dept = data.get("company_dept", "")
        title = data.get("position_title", "")
    else:
        with open(path, encoding="utf-8") as f:
            body = f.read()
        company = args.company
        location = args.location
        dept = args.dept
        title = args.title

    cv_data = load_cv_data(PATHS.cv_dir)
    output = args.output or os.path.splitext(path)[0] + ".pdf"
    render_letter_pdf(
        PATHS.template("cover_letter.html"),
        cv_data,
        body,
        output_path=output,
        company_name=company,
        company_location=location,
        company_dept=dept,
        position_title=title,
    )
    print(f"Lettre PDF générée : {output}")


def main() -> None:
    if "--migrate" not in sys.argv:
        _check_setup()

    parser = argparse.ArgumentParser(
        description="Pipeline automatisé de recherche de stage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python main.py --scrape
  python main.py --scrape --threshold 7
  python main.py --scrape --with fr   # inclut aussi les sources FR-only (HelloWork, WTTJ FR)
  python main.py --generate 0151e52f
  python main.py --from-url https://example.com/jobs/stage-dev
  python main.py --from-file ~/Downloads/offre_wttj.html --url-source "https://..." --company "Criteo"
  python main.py --mark-duplicate dcd4359a c52e7b97   # marque dcd4359a comme doublon de c52e7b97
  python main.py --unmark-duplicate dcd4359a          # annule le marquage
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
    group.add_argument(
        "--letter-pdf",
        metavar="PATH",
        help="Rend une lettre en PDF : fichier .md/.txt (corps de lettre, avec --company/--title/--location/--dept) ou .json (format files/letter/)",
    )
    group.add_argument(
        "--tui",
        action="store_true",
        help="Ouvre l'interface interactive (revue des offres, génération, suivi)",
    )
    group.add_argument(
        "--applications",
        action="store_true",
        help="Affiche le tableau de suivi des candidatures",
    )
    group.add_argument(
        "--mark",
        nargs=2,
        metavar=("OFFER_ID", "STATUS"),
        help=f"Met à jour le statut d'une candidature ({', '.join(STATUSES)})",
    )
    group.add_argument(
        "--mark-duplicate",
        nargs=2,
        metavar=("OFFER_ID", "TARGET_ID"),
        help="Marque OFFER_ID comme doublon de TARGET_ID (offre déjà candidatée)",
    )
    group.add_argument(
        "--unmark-duplicate",
        metavar="OFFER_ID",
        help="Annule le marquage doublon de OFFER_ID",
    )
    group.add_argument(
        "--migrate",
        action="store_true",
        help="Copie la config perso et les données du dépôt vers JOBSEARCH_HOME (~/.jobsearch/ par défaut). "
             "Mode à blanc par défaut — ajouter --apply pour copier réellement. Ne supprime ni ne déplace jamais rien.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Avec --migrate : copie réellement (au lieu du mode à blanc)",
    )
    parser.add_argument(
        "--location",
        default="",
        help="Lieu de l'entreprise (utilisé avec --letter-pdf)",
    )
    parser.add_argument(
        "--dept",
        default="",
        help="Département / équipe de l'entreprise (utilisé avec --letter-pdf)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Chemin du PDF de sortie (utilisé avec --letter-pdf)",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Note libre ajoutée avec --mark",
    )
    parser.add_argument(
        "--no-batch",
        action="store_true",
        help="Force le filtrage séquentiel (sans l'API Message Batches)",
    )
    parser.add_argument(
        "--letter-only",
        action="store_true",
        help="Avec --generate : produit uniquement la lettre (pas de CV adapté — économie d'un appel API)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        help="Score minimum de pertinence (défaut: config/search.yaml, utilisé avec --scrape)",
    )
    parser.add_argument(
        "--with",
        dest="with_groups",
        action="append",
        default=[],
        metavar="GROUP",
        help="Active un groupe de sources optionnelles pour ce scrape (ex: --with fr pour HelloWork). Répétable.",
    )
    parser.add_argument(
        "--without",
        dest="without_groups",
        action="append",
        default=[],
        metavar="GROUP",
        help="Désactive un groupe de sources pour ce scrape, même s'il est activé par défaut. Répétable.",
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

    if args.letter_pdf:
        cmd_letter_pdf(args)
    elif args.tui:
        from src.tui import run_tui  # import paresseux : textual requis seulement ici
        run_tui()
    elif args.scrape:
        from src.config import get_search

        groups = (set(get_search().enabled_groups) | set(args.with_groups)) - set(args.without_groups)
        cmd_scrape(args.threshold, use_batch=not args.no_batch, include_groups=groups)
    elif args.generate:
        cmd_generate(args.generate, letter_only=args.letter_only)
    elif args.from_file:
        cmd_from_file(args)
    elif args.from_url:
        cmd_from_url(args)
    elif args.applications:
        print(render_applications_table(load_applications()))
    elif args.mark:
        offer_id, status = args.mark
        try:
            app = mark_status(offer_id, status, note=args.note)
        except (KeyError, ValueError) as e:
            sys.exit(f"Erreur : {e}")
        print(f"Candidature {offer_id} ({app['company']}) → statut : {status}")
    elif args.mark_duplicate:
        offer_id, target_id = args.mark_duplicate
        try:
            mark_duplicate(offer_id, target_id)
        except ValueError as e:
            sys.exit(f"Erreur : {e}")
        print(f"Offre {offer_id} marquée comme doublon de {target_id}.")
    elif args.unmark_duplicate:
        offer_id = args.unmark_duplicate
        try:
            clear_duplicate(offer_id)
        except ValueError as e:
            sys.exit(f"Erreur : {e}")
        print(f"Marquage doublon retiré pour {offer_id}.")
    elif args.migrate:
        from src.migrate import run_migrate

        run_migrate(apply=args.apply)


if __name__ == "__main__":
    main()
