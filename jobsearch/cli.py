import argparse
import os
import sys

from jobsearch.config import PACKAGE_ROOT, PROJECT_ROOT, PATHS


def _check_setup() -> None:
    """Détecte les fichiers de config manquants (dans `JOBSEARCH_HOME`) et propose de lancer setup.sh."""
    required = {
        PATHS.env_path: "jobsearch/resources/examples/.env.example",
        os.path.join(PATHS.config_dir, "profile.yaml"): "jobsearch/resources/examples/profile.example.yaml",
        os.path.join(PATHS.config_dir, "search.yaml"): "jobsearch/resources/examples/search.example.yaml",
        os.path.join(PATHS.cv_dir, "cv_data.yaml"): "jobsearch/resources/examples/cv_data.example.yaml",
        os.path.join(PATHS.cv_dir, "cv_master.md"): "jobsearch/resources/examples/cv_master.example.md",
    }
    missing = [f for f in required if not os.path.exists(f)]
    if not missing:
        return

    # Garde-fou : si le dépôt contient encore des données (ancien emplacement,
    # avant migration), ne JAMAIS laisser croire à une installation vierge —
    # proposer --migrate plutôt qu'un simple setup.sh qui ne créerait que des
    # fichiers vides à côté de milliers d'offres déjà collectées.
    legacy_offers_dir = os.path.join(PROJECT_ROOT, "data", "offers")
    from jobsearch.config import JOBSEARCH_HOME_EXPLICIT

    legacy_offers = (
        [f for f in os.listdir(legacy_offers_dir) if f.endswith(".json")]
        if os.path.isdir(legacy_offers_dir) and not JOBSEARCH_HOME_EXPLICIT
        else []
    )
    if legacy_offers:
        from jobsearch.config import JOBSEARCH_HOME

        print(f"⚠️  {JOBSEARCH_HOME} vide ou absent — configuration manquante, MAIS le dépôt")
        print(f"    contient encore {len(legacy_offers)} offre(s) dans {legacy_offers_dir}.")
        print("    Ce sont probablement des données d'une installation antérieure à la séparation")
        print("    code/données : elles n'ont pas disparu, elles n'ont simplement pas encore été")
        print("    copiées vers leur nouvel emplacement.")
        print()
        print("    Lance d'abord :  jobsearch --migrate         (aperçu, n'écrit rien)")
        print("    Puis :           jobsearch --migrate --apply (copie réelle)")
        sys.exit(1)

    print("⚠️  Première utilisation détectée — fichiers de configuration manquants :")
    for f in missing:
        print(f"   • {f}  (exemple : {required[f]})")
    print()
    # L'assistant est proposé en premier : il pose les questions et écrit une config
    # cohérente, là où `setup.sh` se contente de copier les `.example` en laissant
    # l'utilisateur les éditer à la main. `setup.sh` reste disponible pour qui
    # préfère partir des fichiers bruts (ou n'a pas de terminal interactif).
    # `input()` lève EOFError dès que l'entrée n'est pas un terminal (pipe, cron,
    # CI) : sans cette garde, l'utilisateur reçoit une trace Python au lieu d'un
    # message. L'assistant étant une app Textual, il lui faut de toute façon un
    # vrai terminal — on se contente donc d'afficher la marche à suivre.
    if sys.stdin.isatty():
        try:
            answer = input("Lancer l'assistant de configuration (jobsearch --setup) ? [O/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
            print()
        if answer in ("", "o", "oui", "y", "yes"):
            from jobsearch.wizard import run_setup

            run_setup()
            sys.exit(0)
        print()
        print("Abandon. Pour configurer JobSearch :")
    else:
        print("Pour configurer JobSearch (dans un terminal interactif) :")
    print("   • jobsearch --setup            assistant guidé (recommandé)")
    # `setup.sh` n'est PAS embarqué dans le paquet : en installation pipx,
    # PACKAGE_ROOT vaut site-packages/ et le script n'y existe pas. Ne le
    # proposer que s'il est réellement là (dépôt cloné, `pip install -e .`).
    setup_sh = os.path.join(PACKAGE_ROOT, "setup.sh")
    if os.path.isfile(setup_sh):
        print(f"   • bash {setup_sh}")
        print("     copie les fichiers d'exemple, à éditer ensuite à la main")
    sys.exit(1)


from jobsearch.scraper import scrape_all
from jobsearch.filter import filter_all
from jobsearch.report import generate_report, load_filtered_offers
from jobsearch.generator import generate
from jobsearch.notifier import send_notification
from jobsearch.manual import from_file, from_url
from jobsearch.tracker import load_applications, mark_status, render_applications_table, STATUSES, record_generated
from jobsearch.duplicates import (
    mark_duplicate,
    clear_duplicate,
    load_offer,
    mark_invalid,
    clear_invalid,
    INVALID_REASONS,
)


def _missing_extra(extra: str, paquets: str) -> None:
    """Message actionnable quand un extra optionnel manque, plutôt qu'un
    `ModuleNotFoundError` brut — même intention que `fetch_jobspy`
    (`jobsearch/scraper_jobspy.py`) pour python-jobspy. Le cas nominal est une
    installation pipx : `pipx install jobsearch` n'embarque aucun extra, et
    l'utilisateur n'a aucune raison de deviner `pipx inject`."""
    sys.exit(
        f"Extra « {extra} » non installé — cette commande en a besoin.\n"
        f"  pipx       : pipx inject jobsearch {paquets}\n"
        f"  venv local : pip install -e '.[{extra}]'"
    )


def cmd_scrape(
    threshold: int | None,
    use_batch: bool = True,
    include_groups: set[str] | None = None,
) -> None:
    from jobsearch.config import get_search

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
    print("  jobsearch --generate <offer_id>")


def cmd_generate(offer_id: str, letter_only: bool = False, notes: str = "") -> None:
    print("=" * 50)
    mode = " (lettre seule)" if letter_only else ""
    print(f"Génération de candidature pour l'offre : {offer_id}{mode}")
    print("=" * 50)
    folder = generate(offer_id, letter_only=letter_only, notes=notes)
    print(f"\nCandidature prête dans : {folder}")
    print(f"  - {folder}/lettre.md")
    print(f"  - {folder}/lettre.pdf")
    if not letter_only:
        print(f"  - {folder}/cv_adapte.html")
        print(f"  - {folder}/cv_adapte.pdf")


def cmd_record(offer_id: str) -> None:
    """Enregistre une offre dans le suivi sans générer ni lettre ni CV (pendant du `M` de la TUI)."""
    offer = load_offer(offer_id)
    if offer is None:
        sys.exit(f"Erreur : offre '{offer_id}' introuvable dans {PATHS.data_dir}/offers/")
    record_generated(offer, PATHS.data_dir)
    print(f"Offre {offer_id} ({offer.get('company', '?')}) enregistrée dans le suivi — aucun fichier produit.")


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

    from jobsearch.cv_pdf import load_cv_data
    from jobsearch.letter_pdf import render_letter_pdf

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
    # `--help`/`-h` est exempté au même titre que --migrate/--setup : _check_setup
    # s'exécute AVANT parse_args, donc sans cette exemption le tout premier réflexe
    # d'un nouvel utilisateur (lire l'aide) tombait sur la question de configuration
    # au lieu de l'aide. Sans argument, en revanche, _check_setup reste pertinent :
    # c'est précisément le moment d'indiquer quoi faire.
    _EXEMPT = {"--migrate", "--setup", "--help", "-h"}
    if not _EXEMPT.intersection(sys.argv[1:]):
        _check_setup()

    parser = argparse.ArgumentParser(
        description="Pipeline automatisé de recherche de stage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  jobsearch --setup    # première installation : assistant interactif
  jobsearch --scrape
  jobsearch --scrape --threshold 7
  jobsearch --scrape --with fr   # inclut aussi les sources FR-only (HelloWork, WTTJ FR)
  jobsearch --generate 0151e52f
  jobsearch --generate 0151e52f --notes "Insiste sur le projet X"
  jobsearch --record 0151e52f    # enregistre dans le suivi, sans lettre ni CV
  jobsearch --from-url https://example.com/jobs/stage-dev
  jobsearch --from-file ~/Downloads/offre_wttj.html --url-source "https://..." --company "Criteo"
  jobsearch --mark-duplicate dcd4359a c52e7b97   # marque dcd4359a comme doublon de c52e7b97
  jobsearch --unmark-duplicate dcd4359a          # annule le marquage
  jobsearch --mark-invalid dcd4359a --reason closed   # écarte une offre fermée/hors-sujet
  jobsearch --unmark-invalid dcd4359a                 # annule le marquage
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
        "--record",
        metavar="OFFER_ID",
        help="Enregistre l'offre dans le suivi (data/applications.json) sans générer ni lettre ni CV — "
             "aucun appel API, pour les offres qui n'en demandent pas (formulaire en ligne, CV générique)",
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
        "--setup",
        action="store_true",
        help="Assistant d'installation interactif (clé API, sujet de recherche, prompt de scoring, profil)",
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
        "--serve",
        action="store_true",
        help="Démarre l'API HTTP + la vue web (consultation depuis l'iPhone, agents distants). "
             "Écoute sur 127.0.0.1 par défaut — voir --host/--port/--allow-write. "
             "Nécessite l'extra jobsearch[server] (FastAPI + uvicorn).",
    )
    group.add_argument(
        "--mcp",
        action="store_true",
        help="Démarre le serveur MCP (4 outils : list_applications, get_application, search_offers, "
             "mark_application_status). Transport stdio par défaut (Claude Code, app Claude locale) — "
             "voir --mcp-transport/--host/--port/--allow-write. Nécessite l'extra jobsearch[mcp].",
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
        "--mark-invalid",
        metavar="OFFER_ID",
        help=f"Marque OFFER_ID comme invalide (offre morte/hors-sujet — motif via --reason, "
             f"{', '.join(INVALID_REASONS)})",
    )
    group.add_argument(
        "--unmark-invalid",
        metavar="OFFER_ID",
        help="Annule le marquage invalide de OFFER_ID",
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
        "--reason",
        default="other",
        choices=list(INVALID_REASONS),
        help=f"Avec --mark-invalid : motif ({', '.join(INVALID_REASONS)})",
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
        "--notes",
        default="",
        help="Avec --generate : consigne ponctuelle injectée dans les prompts lettre et CV "
             "(prime sur gen_notes de l'offre, posé via la touche N de la TUI)",
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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Avec --applications : sortie JSON (même sérialisation que l'API HTTP) au lieu du tableau texte",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Avec --serve : adresse d'écoute (défaut : 127.0.0.1, boucle locale uniquement)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8420,
        help="Avec --serve : port d'écoute (défaut : 8420)",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Avec --serve/--mcp : active l'écriture (changement de statut à distance). "
             "Absent par défaut — lecture seule sans ce drapeau.",
    )
    parser.add_argument(
        "--mcp-transport",
        default="stdio",
        choices=["stdio", "streamable-http"],
        help="Avec --mcp : transport à utiliser (défaut : stdio, pour un client MCP local). "
             "streamable-http nécessite --host/--port et suit le même garde-fou réseau que --serve.",
    )

    args = parser.parse_args()

    if args.letter_pdf:
        cmd_letter_pdf(args)
    elif args.setup:
        from jobsearch.wizard import run_setup  # import paresseux : textual requis seulement ici

        run_setup()
    elif args.tui:
        from jobsearch.tui import run_tui  # import paresseux : textual requis seulement ici
        run_tui()
    elif args.scrape:
        from jobsearch.config import get_search

        groups = (set(get_search().enabled_groups) | set(args.with_groups)) - set(args.without_groups)
        cmd_scrape(args.threshold, use_batch=not args.no_batch, include_groups=groups)
    elif args.generate:
        cmd_generate(args.generate, letter_only=args.letter_only, notes=args.notes)
    elif args.record:
        cmd_record(args.record)
    elif args.from_file:
        cmd_from_file(args)
    elif args.from_url:
        cmd_from_url(args)
    elif args.applications:
        if args.json:
            import json as _json

            from jobsearch.serialize import application_dict

            apps = load_applications()
            print(_json.dumps([application_dict(a, PATHS.data_dir) for a in apps], ensure_ascii=False, indent=2))
        else:
            print(render_applications_table(load_applications()))
    elif args.serve:
        try:
            # Import paresseux (fastapi/uvicorn requis seulement ici) ET appel dans le
            # même try : l'un ou l'autre peut lever selon où vit l'import du SDK.
            from jobsearch.server import run_server

            run_server(host=args.host, port=args.port, allow_write=args.allow_write)
        except ImportError:
            _missing_extra("server", "fastapi uvicorn python-multipart")
    elif args.mcp:
        # Import paresseux : `mcp` requis seulement ici (extra jobsearch[mcp]).
        # `mcp_server` importe le SDK à l'intérieur de `build_server`, pas au niveau
        # module : l'ImportError ne surgit donc qu'à l'APPEL, pas à l'import. Le
        # try doit englober les deux, sinon le message actionnable est contourné.
        from jobsearch.mcp_server import run_mcp_stdio, run_mcp_streamable_http

        try:
            if args.mcp_transport == "stdio":
                run_mcp_stdio(allow_write=args.allow_write)
            else:
                run_mcp_streamable_http(host=args.host, port=args.port, allow_write=args.allow_write)
        except ImportError:
            _missing_extra("mcp", "mcp")
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
    elif args.mark_invalid:
        offer_id = args.mark_invalid
        try:
            mark_invalid(offer_id, reason=args.reason)
        except ValueError as e:
            sys.exit(f"Erreur : {e}")
        print(f"Offre {offer_id} marquée invalide ({INVALID_REASONS[args.reason]}).")
    elif args.unmark_invalid:
        offer_id = args.unmark_invalid
        try:
            clear_invalid(offer_id)
        except ValueError as e:
            sys.exit(f"Erreur : {e}")
        print(f"Marquage invalide retiré pour {offer_id}.")
    elif args.migrate:
        from jobsearch.migrate import run_migrate

        run_migrate(apply=args.apply)


if __name__ == "__main__":
    main()
