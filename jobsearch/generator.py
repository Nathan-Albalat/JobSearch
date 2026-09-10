import argparse
import json
import os
import re
import sys
from datetime import date

from dotenv import load_dotenv

from jobsearch.filter import load_profile_text, strip_html
from jobsearch.cv_pdf import load_cv_data, cv_data_to_text, render_cv_html, write_cv_pdf
from jobsearch.cv_sync import sync_cv_master
from jobsearch.letter_pdf import render_letter_pdf
from jobsearch.tracker import record_generated
from jobsearch.config import PATHS, Search, fill_template, get_search  # noqa: F401 (fill_template ré-exporté)


def load_offer(offer_id: str, data_dir: str) -> dict:
    path = os.path.join(data_dir, "offers", f"{offer_id}.json")
    if not os.path.exists(path):
        print(f"Erreur : offre '{offer_id}' introuvable dans {data_dir}/offers/", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def call_sonnet(client, prompt: str, search: Search | None = None) -> str:
    if search is None:
        search = get_search()
    response = client.messages.create(
        model=search.generate_model,
        max_tokens=search.generate_max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:40] or "offre"


def make_output_dir(offer: dict, output_dir: str) -> str:
    company = offer.get("company", "") or ""
    slug = _slugify(company) if company else "offre"
    offer_id = offer.get("id", "unknown")
    folder_name = f"{slug}_{offer_id}"
    folder_path = os.path.join(output_dir, "candidatures", folder_name)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def render_notes_block(notes: str, template: str, template_name: str) -> str:
    """Construit le bloc de consigne ponctuelle injecté via `{notes_block}`.

    Le titre de section fait partie de la VALEUR, pas du template : une ligne
    du template ne contenant que `{notes_block}` est retirée en bloc par
    `fill_template` quand la valeur résolue est vide (même mécanique que
    `{duree_bullet_8_10}`) — si le titre était littéral dans le template, une
    section "## Additional instructions" vide survivrait à chaque offre sans note.
    """
    notes = (notes or "").strip()
    if not notes:
        return ""
    if "{notes_block}" not in template:
        # Prompt personnalisé antérieur à cette fonctionnalité : la note serait
        # sinon perdue en silence, sans aucun signe pour l'utilisateur.
        print(
            f"Avertissement : note ignorée — {template_name} ne contient pas {{notes_block}}.",
            file=sys.stderr,
        )
        return ""
    # Le "\n" final restitue la ligne vide qui séparait le bloc de la section
    # suivante : le template n'en a plus, sinon un `{notes_block}` vide laisserait
    # deux sauts de ligne consécutifs une fois la ligne retirée par fill_template.
    return (
        "## Additional instructions from the candidate\n"
        "Follow these as a priority, without breaking the output format below.\n"
        f"{notes}\n"
    )


def generate_cover_letter(
    client,
    offer: dict,
    profile_text: str,
    cv_summary: str,
    template: str,
    search: Search | None = None,
    notes_block: str = "",
) -> str:
    if search is None:
        search = get_search()
    fr = offer.get("filter_result", {})
    points_forts = ", ".join(fr.get("points_forts", [])) or "Voir profil"

    prompt = fill_template(template, {
        **search.placeholders(),
        "profile": profile_text,
        "cv_summary": cv_summary,
        "title": offer.get("title", ""),
        "company": offer.get("company", "") or "l'entreprise",
        "location": offer.get("location", ""),
        "description": strip_html(offer.get("description", ""), max_chars=2500),
        "points_forts": points_forts,
        "notes_block": notes_block,
    })
    return call_sonnet(client, prompt, search=search)


def generate_cv_adapt(
    client,
    offer: dict,
    cv_data: dict,
    template: str,
    search: Search | None = None,
    notes_block: str = "",
) -> dict:
    """Asks Claude to return an adapted JSON structure for the CV template."""
    import yaml as _yaml

    if search is None:
        search = get_search()
    fr = offer.get("filter_result", {})
    points_forts = ", ".join(fr.get("points_forts", [])) or "See profile"

    prompt = fill_template(template, {
        **search.placeholders(),
        "cv_data": _yaml.dump(cv_data, allow_unicode=True, sort_keys=False),
        "title": offer.get("title", ""),
        "company": offer.get("company", "") or "the company",
        "description": strip_html(offer.get("description", ""), max_chars=2500),
        "points_forts": points_forts,
        "notes_block": notes_block,
    })
    raw = call_sonnet(client, prompt, search=search)

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def generate(
    offer_id: str,
    config_dir: str = PATHS.config_dir,
    data_dir: str = PATHS.data_dir,
    cv_dir: str = PATHS.cv_dir,
    output_dir: str = PATHS.output_dir,
    letter_only: bool = False,
    notes: str = "",
) -> str:
    load_dotenv(PATHS.env_path, override=True)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Erreur : ANTHROPIC_API_KEY manquant dans .env", file=sys.stderr)
        sys.exit(1)

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    search = get_search(config_dir)  # une seule résolution par run, passée vers le bas

    offer = load_offer(offer_id, data_dir)
    if not offer.get("filter_result"):
        print(
            f"Avertissement : l'offre {offer_id} n'a pas encore été filtrée. Génération quand même...",
            file=sys.stderr,
        )

    # L'argument explicite (CLI --notes) l'emporte sur la note stockée dans le JSON de
    # l'offre (touche N de l'onglet Génération) — permet de surcharger ponctuellement.
    notes = notes.strip() or offer.get("gen_notes", "")

    profile_text = load_profile_text(config_dir, search=search)
    sync_cv_master(cv_dir)  # cv_master.md toujours aligné sur cv_data.yaml
    cv_data = load_cv_data(cv_dir)
    cv_summary = cv_data_to_text(cv_data)

    with open(PATHS.prompt("cover_letter.txt"), "r", encoding="utf-8") as f:
        cover_template = f.read()

    folder = make_output_dir(offer, output_dir)

    print(f"Génération de la lettre de motivation...")
    cover_notes_block = render_notes_block(notes, cover_template, "cover_letter.txt")
    lettre = generate_cover_letter(
        client, offer, profile_text, cv_summary, cover_template, search=search, notes_block=cover_notes_block,
    )
    lettre_path = os.path.join(folder, "lettre.md")
    with open(lettre_path, "w", encoding="utf-8") as f:
        f.write(f"<!-- Offre : {offer.get('title', '')} — {offer.get('company', '')} -->\n")
        f.write(f"<!-- URL : {offer.get('url', '')} -->\n")
        f.write(f"<!-- Généré le : {date.today()} -->\n")
        if notes:
            f.write(f"<!-- Consigne : {notes} -->\n")
        f.write("\n")
        f.write(lettre)

    lettre_pdf_path = os.path.join(folder, "lettre.pdf")
    try:
        render_letter_pdf(
            PATHS.template("cover_letter.html"),
            cv_data,
            lettre,
            output_path=lettre_pdf_path,
            company_name=offer.get("company", ""),
            company_location=offer.get("location", ""),
            position_title=offer.get("title", ""),
        )
        print(f"Lettre PDF générée : {lettre_pdf_path}")
    except ImportError as e:
        print(f"Avertissement : {e}", file=sys.stderr)

    if letter_only:
        print("Mode lettre seule : CV adapté non généré (économie d'un appel API).")
    else:
        print(f"Génération du CV adapté...")
        with open(PATHS.prompt("cv_adapt.txt"), "r", encoding="utf-8") as f:
            cv_template = f.read()
        template_path = PATHS.template("cv_en.html")
        cv_notes_block = render_notes_block(notes, cv_template, "cv_adapt.txt")
        adapted = generate_cv_adapt(client, offer, cv_data, cv_template, search=search, notes_block=cv_notes_block)

        html_content = render_cv_html(template_path, cv_data, adapted)

        html_path = os.path.join(folder, "cv_adapte.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        pdf_path = os.path.join(folder, "cv_adapte.pdf")
        try:
            write_cv_pdf(html_content, pdf_path)
            print(f"PDF généré : {pdf_path}")
        except ImportError as e:
            print(f"Avertissement : {e}", file=sys.stderr)
            print(f"HTML de prévisualisation disponible : {html_path}", file=sys.stderr)

    record_generated(offer, data_dir)
    print(f"Candidature enregistrée dans le suivi (data/applications.json).")

    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Génère lettre + CV adapté pour une offre")
    parser.add_argument("offer_id", help="ID de l'offre (ex: 0151e52f)")
    parser.add_argument("--config", default=PATHS.config_dir)
    parser.add_argument("--data", default=PATHS.data_dir)
    parser.add_argument("--cv", default=PATHS.cv_dir)
    parser.add_argument("--output", default=PATHS.output_dir)
    parser.add_argument("--letter-only", action="store_true",
                        help="Génère uniquement la lettre (pas de CV adapté — économie d'un appel API)")
    parser.add_argument("--notes", default="",
                        help="Consigne ponctuelle injectée dans les prompts lettre et CV (prime sur gen_notes de l'offre)")
    args = parser.parse_args()

    folder = generate(args.offer_id, args.config, args.data, args.cv, args.output, args.letter_only, args.notes)
    print(f"\nCandidature générée dans : {folder}")
    print(f"  - {folder}/lettre.md")
    print(f"  - {folder}/lettre.pdf")
    if not args.letter_only:
        print(f"  - {folder}/cv_adapte.html")
        print(f"  - {folder}/cv_adapte.pdf")
