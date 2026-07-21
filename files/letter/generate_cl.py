"""
Générateur de lettre de motivation.

Usage basique (données en dur, pour tester) :
    python generate_cl.py

Usage pipeline (données passées en argument) :
    python generate_cl.py --input offer_data.json

Ce script :
  1. Charge le template HTML (template_en_cl.html)
  2. Remplit les placeholders {{XXX}} avec les données
  3. Convertit le corps Markdown en HTML si nécessaire
  4. Génère un PDF via WeasyPrint

Structure d'entrée attendue depuis la pipeline (fichier JSON) :
{
  "company_name": "ING Bank",
  "company_location": "Amsterdam, Netherlands",
  "company_dept": "Non-Financial Risk · NFR NL",
  "position_title": "Graduate Intern — NFR NL",
  "body_markdown": "Dear Hiring Team,\n\nParagraphe 1...\n\nSincerely,"
}

Le champ body_markdown peut aussi être du HTML brut si tu préfères
que le LLM génère directement du HTML (plus simple à injecter).
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path

from weasyprint import HTML

BASE_DIR = Path(__file__).parent
TEMPLATE_PATH = BASE_DIR / "template_en_cl.html"


# ---------------------------------------------------------------------------
# Accent — même switch que generate_cv.py
# ---------------------------------------------------------------------------
ACCENT_ENABLED = True
ACCENT_ON  = {"ACCENT_COLOR": "#2C5F4A", "ACCENT_LIGHT": "#E8EFE9"}
ACCENT_OFF = {"ACCENT_COLOR": "#5F5E5A", "ACCENT_LIGHT": "#F1EFE8"}


# ---------------------------------------------------------------------------
# Données statiques (toujours les mêmes d'une lettre à l'autre)
# ---------------------------------------------------------------------------
STATIC_DATA = {
    "FULL_NAME":        "Nathan Albalat",
    "SENDER_LOCATION":  "Grenoble, France",
    "EMAIL":            "",                     # à compléter
    "GITHUB_LINE":      "github.com/Nathan-Albalat",
    "SALUTATION":       "Dear Hiring Team,",
    "CLOSING_WORD":     "Sincerely",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def markdown_body_to_html(text: str) -> str:
    """
    Conversion minimaliste Markdown → HTML pour le corps de la lettre.
    Chaque bloc de texte séparé par une ligne vide devient un <p>.
    Ne gère pas les listes ou titres — inutile pour une lettre.

    Si tu préfères que le LLM génère du HTML directement, cette fonction
    n'est pas appelée (voir --html-body dans parse_args).
    """
    paragraphs = re.split(r"\n{2,}", text.strip())
    return "\n".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


def render_html(template_path: Path, data: dict) -> str:
    """Charge le template et remplace chaque {{CLE}} par sa valeur."""
    html = template_path.read_text(encoding="utf-8")
    for key, value in data.items():
        html = html.replace(f"{{{{{key}}}}}", str(value))
    return html


def generate_pdf(html_content: str, output_path: Path) -> None:
    """Convertit une chaîne HTML en fichier PDF via WeasyPrint."""
    HTML(string=html_content).write_pdf(output_path)


def slugify(text: str) -> str:
    """Transforme un titre de poste en nom de fichier propre."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Génère une lettre de motivation PDF.")
    parser.add_argument("--input", type=Path, default=None,
                        help="Fichier JSON avec les données de l'offre.")
    parser.add_argument("--html-body", action="store_true",
                        help="Le champ body_markdown contient déjà du HTML brut.")
    args = parser.parse_args()

    # --- Chargement des données dynamiques ---
    if args.input:
        offer = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        # Données de test (exemple ING)
        offer = {
            "company_name":     "ING Bank",
            "company_location": "Amsterdam, Netherlands",
            "company_dept":     "Non-Financial Risk · NFR NL",
            "position_title":   "Graduate Intern — NFR NL",
            "body_markdown": (
                "ING's move toward Co-Pilot agent-based solutions within its "
                "Non-Financial Risk function is exactly the kind of real-world "
                "AI application I find most compelling — not AI as a research "
                "exercise, but as a practical tool reshaping how large "
                "organizations manage complex, regulated processes. The Graduate "
                "Intern role in your NFR NL team caught my attention precisely "
                "because it sits at that intersection.\n\n"

                "ING's scale and international culture make it a rare environment "
                "where technical work carries genuine organizational impact. "
                "Contributing to risk identification and monitoring systems used "
                "across a major European bank — and doing so from Amsterdam — is "
                "an opportunity I'm actively looking for as I complete my final "
                "year of engineering studies.\n\n"

                "On the technical side, I bring directly relevant experience. My "
                "machine learning coursework has given me hands-on exposure to "
                "reinforcement learning methods (Q-Learning, Multi-Armed Bandits), "
                "and I've been actively working with agentic development tools — "
                "Codex, Claude Code, GitHub Copilot — as part of my day-to-day "
                "engineering workflow. I understand not just how to prompt these "
                "systems, but how to structure them as reliable components in a "
                "larger pipeline. Beyond theory, my internship at TOUTENVERT gave "
                "me practical experience translating messy business problems into "
                "working software: I built data transfer tools, web applications, "
                "and infrastructure visualization tools from scratch, under real "
                "operational constraints. My earlier work at CNRS Institut Néel "
                "also gave me early exposure to applying computational thinking "
                "in a rigorous, domain-specific context.\n\n"

                "I am available from March 15th 2027 for a minimum of 22 weeks, "
                "and relocating to Amsterdam fits naturally into my plans.\n\n"

                "I would genuinely enjoy the chance to discuss how my background "
                "could contribute to your team's work. Thank you for considering "
                "my application."
            ),
        }

    # --- Conversion du corps ---
    if args.html_body:
        body_html = offer["body_markdown"]   # déjà du HTML
    else:
        body_html = markdown_body_to_html(offer["body_markdown"])

    # --- Construction du dictionnaire final ---
    cl_data = {
        **STATIC_DATA,
        **(ACCENT_ON if ACCENT_ENABLED else ACCENT_OFF),
        "COMPANY_NAME":     offer["company_name"],
        "COMPANY_LOCATION": offer["company_location"],
        "COMPANY_DEPT":     offer["company_dept"],
        "POSITION_TITLE":   offer["position_title"],
        "DATE":             date.today().strftime("%B %d, %Y"),
        "BODY":             body_html,
    }

    # --- Rendu HTML + PDF ---
    html_content = render_html(TEMPLATE_PATH, cl_data)

    slug = slugify(offer["position_title"])
    output_path = BASE_DIR / f"cl_nathan_albalat_{slug}.pdf"
    debug_path  = BASE_DIR / f"cl_nathan_albalat_{slug}.html"

    debug_path.write_text(html_content, encoding="utf-8")
    generate_pdf(html_content, output_path)

    print(f"PDF généré  : {output_path}")
    print(f"HTML debug  : {debug_path}")


if __name__ == "__main__":
    main()