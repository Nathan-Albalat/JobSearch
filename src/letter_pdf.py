"""Rendu PDF de la lettre de motivation (template config/templates/cover_letter.html)."""
import re
from datetime import date
from pathlib import Path

from src.cv_pdf import write_cv_pdf

_SALUTATION_RE = re.compile(r"^(dear|to whom|madame|monsieur|madame,|hi |hello )", re.IGNORECASE)
_CLOSING_RE = re.compile(
    r"^(sincerely|best regards|kind regards|regards|warm regards|respectfully|cordialement|"
    r"bien cordialement|yours (sincerely|faithfully))\b[,.]?$",
    re.IGNORECASE,
)

# Signature manuscrite ajoutée au-dessus du nom (si le fichier existe)
DEFAULT_SIGNATURE_PATH = Path(__file__).resolve().parent.parent / "files" / "letter" / "signature.png"

DEFAULT_SALUTATION = "Dear Hiring Team,"
DEFAULT_CLOSING = "Sincerely"


def split_letter(markdown: str, full_name: str = "") -> tuple[str, str, str]:
    """Sépare une lettre complète en (salutation, corps, mot de clôture).

    Les lettres générées par l'API contiennent déjà "Dear …," en tête et
    "Sincerely,\nNathan" en pied ; le template ajoutant les siens, on les
    extrait pour éviter les doublons. Fallbacks si absents.
    """
    # Retirer les commentaires HTML d'en-tête (métadonnées de lettre.md)
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL).strip()
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    salutation = DEFAULT_SALUTATION
    closing = DEFAULT_CLOSING

    # La lettre API peut commencer par un en-tête (nom, contact, "---") :
    # on cherche la salutation dans les premiers paragraphes et on jette ce qui précède.
    for i, p in enumerate(paragraphs[:5]):
        first_line = p.splitlines()[0].strip()
        if _SALUTATION_RE.match(first_line) and len(first_line) < 80:
            rest = "\n".join(p.splitlines()[1:]).strip()
            salutation = first_line
            paragraphs = ([rest] if rest else []) + paragraphs[i + 1:]
            break

    # Le closing peut être un paragraphe seul ("Sincerely,\nNathan") ou les
    # dernières lignes du dernier paragraphe.
    if paragraphs:
        last_lines = paragraphs[-1].splitlines()
        for i, line in enumerate(last_lines):
            if _CLOSING_RE.match(line.strip()):
                closing = line.strip().rstrip(",.")
                remaining = "\n".join(last_lines[:i]).strip()
                if remaining:
                    paragraphs[-1] = remaining
                else:
                    paragraphs.pop()
                break

    # Retirer un éventuel nom isolé en fin de lettre (déjà dans le template)
    if paragraphs and full_name and paragraphs[-1].strip().lower() == full_name.strip().lower():
        paragraphs.pop()

    return salutation, "\n\n".join(paragraphs), closing


def markdown_body_to_html(text: str) -> str:
    """Chaque bloc séparé par une ligne vide devient un <p> (gras/italique basiques)."""
    def inline(p: str) -> str:
        p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
        p = re.sub(r"\*(.+?)\*", r"<em>\1</em>", p)
        return p

    paragraphs = re.split(r"\n{2,}", text.strip())
    return "\n".join(f"<p>{inline(p.strip())}</p>" for p in paragraphs if p.strip())


def render_letter_html(
    template_path: str,
    cv_data: dict,
    body_markdown: str,
    company_name: str = "",
    company_location: str = "",
    company_dept: str = "",
    position_title: str = "",
    signature_path=None,
) -> str:
    personal = cv_data.get("personal", {})
    full_name = personal.get("full_name", "")
    accent_on = {"ACCENT_COLOR": "#2C5F4A", "ACCENT_LIGHT": "#E8EFE9"}
    accent_off = {"ACCENT_COLOR": "#5F5E5A", "ACCENT_LIGHT": "#F1EFE8"}

    salutation, body, closing = split_letter(body_markdown, full_name)

    # Signature manuscrite (chemin absolu via file:// pour être indépendant du base_url)
    sig = Path(signature_path) if signature_path else DEFAULT_SIGNATURE_PATH
    signature_img = f'<img class="cl-signature" src="{sig.as_uri()}" alt="">' if sig.exists() else ""

    email = personal.get("email", "")
    github = personal.get("github", "")
    if email and github:
        contact = f"{email} · {github}"
    else:
        contact = email or github

    placeholders = {
        **(accent_on if personal.get("accent_enabled", True) else accent_off),
        "FULL_NAME": full_name,
        "SENDER_LOCATION": personal.get("location", ""),
        "EMAIL": contact,
        "GITHUB_LINE": "",  # contact déjà consolidé dans EMAIL
        "COMPANY_NAME": company_name or "—",
        "COMPANY_LOCATION": company_location,
        "COMPANY_DEPT_LINE": f"<br>{company_dept}" if company_dept else "",
        "POSITION_TITLE": position_title,
        "DATE": date.today().strftime("%B %d, %Y"),
        "SALUTATION": salutation,
        "BODY": markdown_body_to_html(body),
        "CLOSING_WORD": closing,
        "SIGNATURE_IMG": signature_img,
    }

    html = Path(template_path).read_text(encoding="utf-8")
    for key, value in placeholders.items():
        html = html.replace(f"{{{{{key}}}}}", str(value))
    return html


def write_letter_pdf(html_content: str, output_path: str) -> None:
    write_cv_pdf(html_content, output_path)
