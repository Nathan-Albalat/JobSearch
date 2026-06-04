import argparse
import json
import os
import re
import sys
from datetime import date

from dotenv import load_dotenv

from src.filter import load_profile_text, strip_html


def load_offer(offer_id: str, data_dir: str) -> dict:
    path = os.path.join(data_dir, "offers", f"{offer_id}.json")
    if not os.path.exists(path):
        print(f"Erreur : offre '{offer_id}' introuvable dans {data_dir}/offers/", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_cv_master(cv_dir: str) -> str:
    path = os.path.join(cv_dir, "cv_master.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def call_sonnet(client, prompt: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
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


def generate_cover_letter(
    client,
    offer: dict,
    profile_text: str,
    cv_text: str,
    template: str,
) -> str:
    fr = offer.get("filter_result", {})
    points_forts = ", ".join(fr.get("points_forts", [])) or "Voir profil"

    cv_summary = "\n".join(cv_text.split("\n")[:60])

    prompt = template.format(
        profile=profile_text,
        cv_summary=cv_summary,
        title=offer.get("title", ""),
        company=offer.get("company", "") or "l'entreprise",
        location=offer.get("location", ""),
        description=strip_html(offer.get("description", ""), max_chars=2500),
        points_forts=points_forts,
    )
    return call_sonnet(client, prompt)


def generate_cv_adapt(
    client,
    offer: dict,
    cv_text: str,
    template: str,
) -> str:
    fr = offer.get("filter_result", {})
    points_forts = ", ".join(fr.get("points_forts", [])) or "Voir profil"

    prompt = template.format(
        cv_master=cv_text,
        title=offer.get("title", ""),
        company=offer.get("company", "") or "l'entreprise",
        description=strip_html(offer.get("description", ""), max_chars=2500),
        points_forts=points_forts,
    )
    return call_sonnet(client, prompt)


def generate(
    offer_id: str,
    config_dir: str = "config",
    data_dir: str = "data",
    cv_dir: str = "cv_master",
    output_dir: str = "output",
) -> str:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Erreur : ANTHROPIC_API_KEY manquant dans .env", file=sys.stderr)
        sys.exit(1)

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    offer = load_offer(offer_id, data_dir)
    if not offer.get("filter_result"):
        print(
            f"Avertissement : l'offre {offer_id} n'a pas encore été filtrée. Génération quand même...",
            file=sys.stderr,
        )

    profile_text = load_profile_text(config_dir)
    cv_text = load_cv_master(cv_dir)

    with open(os.path.join(config_dir, "prompts", "cover_letter.txt"), "r", encoding="utf-8") as f:
        cover_template = f.read()
    with open(os.path.join(config_dir, "prompts", "cv_adapt.txt"), "r", encoding="utf-8") as f:
        cv_template = f.read()

    folder = make_output_dir(offer, output_dir)

    print(f"Génération de la lettre de motivation...")
    lettre = generate_cover_letter(client, offer, profile_text, cv_text, cover_template)
    lettre_path = os.path.join(folder, "lettre.md")
    with open(lettre_path, "w", encoding="utf-8") as f:
        f.write(f"<!-- Offre : {offer.get('title', '')} — {offer.get('company', '')} -->\n")
        f.write(f"<!-- URL : {offer.get('url', '')} -->\n")
        f.write(f"<!-- Généré le : {date.today()} -->\n\n")
        f.write(lettre)

    print(f"Génération du CV adapté...")
    cv_adapte = generate_cv_adapt(client, offer, cv_text, cv_template)
    cv_path = os.path.join(folder, "cv_adapte.md")
    with open(cv_path, "w", encoding="utf-8") as f:
        f.write(cv_adapte)

    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Génère lettre + CV adapté pour une offre")
    parser.add_argument("offer_id", help="ID de l'offre (ex: 0151e52f)")
    parser.add_argument("--config", default="config")
    parser.add_argument("--data", default="data")
    parser.add_argument("--cv", default="cv_master")
    parser.add_argument("--output", default="output")
    args = parser.parse_args()

    folder = generate(args.offer_id, args.config, args.data, args.cv, args.output)
    print(f"\nCandidature générée dans : {folder}")
    print(f"  - {folder}/lettre.md")
    print(f"  - {folder}/cv_adapte.md")
