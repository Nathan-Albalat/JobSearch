"""Rendu PDF de la lettre de motivation (template config/templates/cover_letter.html)."""
import re
from datetime import date
from pathlib import Path

from src.cv_pdf import write_cv_pdf, _ABS_TEMPLATES_DIR

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


_HRULE_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")  # --- *** ___
_HEADER_ARTIFACT_RE = re.compile(
    r"^\s*(objet|subject)\s*:|"          # ligne "Objet :" / "Subject:"
    r"^\s*\[[^\]]*\]\s*$",               # placeholder isolé "[date]"
    re.IGNORECASE,
)

# Mention parasite ajoutée par certains outils de traduction (DeepL, etc.)
_TRANSLATION_ARTIFACT_RE = re.compile(
    r"^\s*(translated|traduit)\s+with\s+deepl\.com",
    re.IGNORECASE,
)


def _is_trailing_artifact(para: str) -> bool:
    """Un paragraphe de fin de lettre qui n'est qu'une mention de traduction parasite."""
    lines = [l for l in para.splitlines() if l.strip()]
    return bool(lines) and all(_TRANSLATION_ARTIFACT_RE.match(_demark(l)) for l in lines)


def _demark(line: str) -> str:
    """Retire les marqueurs Markdown d'emphase/titre en tête et autour d'une ligne."""
    line = line.strip()
    line = re.sub(r"^#{1,6}\s*", "", line)          # titres
    line = re.sub(r"^(\*\*|__|\*|_)(.*?)(\*\*|__|\*|_)$", r"\2", line).strip()
    return line


def _is_header_line(line: str, full_name: str) -> bool:
    """Une ligne isolée issue d'un en-tête parasite (nom, contact/email, date/lieu, objet, ---)."""
    if _HRULE_RE.match(line.strip()):
        return True
    s = _demark(line)
    if not s:
        return True
    if _HEADER_ARTIFACT_RE.match(s):
        return True
    # ligne de contact : email, ou séparateur nom | email | github
    if "@" in s and ("|" in s or "·" in s or "." in s.split()[-1]) and len(s) < 120:
        return True
    # nom isolé de l'expéditeur
    if full_name and s.lower() == full_name.strip().lower():
        return True
    # ligne "Ville, le [date]" / date en tête
    if len(s) < 60 and re.search(r"\[[^\]]*\]|\ble\b.*\d|\d{1,2}[/-]\d", s.lower()):
        return True
    return False


def _is_header_artifact(para: str, full_name: str) -> bool:
    """Un paragraphe est un en-tête parasite si toutes ses lignes non vides le sont."""
    lines = [l for l in para.splitlines() if l.strip()]
    return bool(lines) and all(_is_header_line(l, full_name) for l in lines) or not lines


def split_letter(markdown: str, full_name: str = "") -> tuple[str, str, str]:
    """Sépare une lettre complète en (salutation, corps, mot de clôture).

    Les lettres générées par l'API doivent contenir "Dear …," en tête et
    "Sincerely," en pied ; le template ajoutant son propre en-tête/nom/date, on
    extrait et on nettoie tout en-tête ou signature parasite pour éviter les
    doublons (et les emails/placeholders hallucinés par le modèle).
    """
    # Retirer les commentaires HTML d'en-tête (métadonnées de lettre.md)
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL).strip()
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    salutation = DEFAULT_SALUTATION
    closing = DEFAULT_CLOSING

    # 1) Salutation : la chercher dans les premiers paragraphes ; jeter ce qui précède.
    for i, p in enumerate(paragraphs[:6]):
        first_line = _demark(p.splitlines()[0])
        if _SALUTATION_RE.match(first_line) and len(first_line) < 80:
            rest = "\n".join(p.splitlines()[1:]).strip()
            salutation = first_line.rstrip()
            paragraphs = ([rest] if rest else []) + paragraphs[i + 1:]
            break
    else:
        # Pas de salutation détectée : retirer un éventuel bloc d'en-tête en tête.
        while paragraphs and _is_header_artifact(paragraphs[0], full_name):
            paragraphs.pop(0)

    # 1bis) Retirer les artefacts de traduction en fin de lettre (ex. "Translated with
    # DeepL.com (free version)") avant de chercher le mot de clôture, sinon celui-ci
    # peut se retrouver décalé au-delà des deux derniers paragraphes.
    while paragraphs and _is_trailing_artifact(paragraphs[-1]):
        paragraphs.pop()

    # 2) Closing : sur les deux derniers paragraphes (le mot de clôture peut être séparé du nom).
    for idx in (-1, -2):
        if len(paragraphs) < abs(idx):
            continue
        lines = paragraphs[idx].splitlines()
        for i, line in enumerate(lines):
            if _CLOSING_RE.match(_demark(line)):
                closing = _demark(line).rstrip(",.")
                remaining = "\n".join(lines[:i]).strip()
                paragraphs[idx] = remaining
                # supprimer les paragraphes désormais vides / signatures après le closing
                paragraphs = [p for p in paragraphs if _demark(p).strip()]
                break
        else:
            continue
        break

    # 3) Nettoyage final : retirer règles horizontales, placeholders et nom isolé résiduels.
    paragraphs = [p for p in paragraphs if not _is_header_artifact(p, full_name)]

    return salutation, "\n\n".join(paragraphs), closing


def markdown_body_to_html(text: str) -> str:
    """Chaque bloc séparé par une ligne vide devient un <p> (gras/italique basiques)."""
    def inline(p: str) -> str:
        p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
        p = re.sub(r"\*(.+?)\*", r"<em>\1</em>", p)
        p = re.sub(r"\[date\]", "", p, flags=re.IGNORECASE)
        return p

    paragraphs = [
        p.strip() for p in re.split(r"\n{2,}", text.strip())
        if p.strip() and not _HRULE_RE.match(p.strip())
    ]
    return "\n".join(f"<p>{inline(p)}</p>" for p in paragraphs if p.strip())


def render_letter_html(
    template_path: str,
    cv_data: dict,
    body_markdown: str,
    company_name: str = "",
    company_location: str = "",
    company_dept: str = "",
    position_title: str = "",
    signature_path=None,
    signature_layout: str = "stacked",
    include_signature: bool = True,
) -> str:
    personal = cv_data.get("personal", {})
    full_name = personal.get("full_name", "")
    # Garde-fou : certaines offres ont une location = blob JSON-LD brut ; ne pas l'afficher.
    loc = company_location or ""
    if "@type" in loc or loc.lstrip().startswith("[{") or loc.lstrip().startswith("{"):
        company_location = ""
    accent_on = {"ACCENT_COLOR": "#2C5F4A", "ACCENT_LIGHT": "#E8EFE9"}
    accent_off = {"ACCENT_COLOR": "#5F5E5A", "ACCENT_LIGHT": "#F1EFE8"}

    salutation, body, closing = split_letter(body_markdown, full_name)

    # Signature manuscrite (chemin absolu via file:// pour être indépendant du base_url)
    sig = Path(signature_path) if signature_path else DEFAULT_SIGNATURE_PATH
    signature_img = (
        f'<img class="cl-signature" src="{sig.as_uri()}" alt="">'
        if include_signature and sig.exists()
        else ""
    )

    contact = " · ".join(v for v in (personal.get("email", ""), personal.get("github", "")) if v)
    portfolio = personal.get("portfolio", "")
    if portfolio:
        contact = f"{contact}<br>{portfolio}" if contact else portfolio

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
        "SIGNOFF_LAYOUT_CLASS": "signoff-inline" if signature_layout == "inline" else "",
    }

    html = Path(template_path).read_text(encoding="utf-8")
    for key, value in placeholders.items():
        html = html.replace(f"{{{{{key}}}}}", str(value))
    return html


def write_letter_pdf(html_content: str, output_path: str) -> None:
    write_cv_pdf(html_content, output_path)


def render_letter_pdf(
    template_path: str,
    cv_data: dict,
    body_markdown: str,
    output_path: str,
    base_url: str = None,
    **render_kwargs,
) -> None:
    if base_url is None:
        base_url = _ABS_TEMPLATES_DIR
    """Rend la lettre en PDF en repliant automatiquement le layout de signature
    (empilé → en ligne → sans signature) pour ne jamais déborder sur une 2e page."""
    try:
        from weasyprint import HTML
    except ImportError:
        raise ImportError(
            "WeasyPrint est requis pour générer le PDF. "
            "Installe-le avec : pip install weasyprint\n"
            "(Sur macOS, brew install pango peut aussi être nécessaire)"
        )

    for layout, include_sig in (("stacked", True), ("inline", True), ("stacked", False)):
        html = render_letter_html(
            template_path, cv_data, body_markdown,
            signature_layout=layout, include_signature=include_sig,
            **render_kwargs,
        )
        doc = HTML(string=html, base_url=base_url).render()
        if len(doc.pages) <= 1 or not include_sig:
            doc.write_pdf(output_path)
            return
