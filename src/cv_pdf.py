import os
from pathlib import Path

import yaml


def load_cv_data(cv_dir: str) -> dict:
    path = os.path.join(cv_dir, "cv_data.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cv_data_to_text(cv_data: dict) -> str:
    """Returns a flat text summary of the CV for use in the cover letter prompt."""
    lines = []
    personal = cv_data.get("personal", {})
    lines.append(f"Name: {personal.get('full_name', '')}")
    lines.append(f"GitHub: {personal.get('github', '')}")

    edu = cv_data.get("education", {})
    lines.append(f"\nEducation: {edu.get('school', '')} ({edu.get('dates', '')})")
    lines.append(f"  {edu.get('detail', '')}")

    avail = cv_data.get("availability", {})
    lines.append(f"\nAvailability: from {avail.get('start', '')}, {avail.get('duration', '')}")
    lines.append(f"Mobility: {avail.get('mobility', '')}")

    lines.append("\nExperiences:")
    for exp in cv_data.get("experiences", []):
        lines.append(f"  - {exp['title']} ({exp['date']}): {exp['subtitle']}")
        for b in exp.get("bullets", []):
            lines.append(f"      • {b}")
        if exp.get("stack"):
            lines.append(f"      Stack: {', '.join(exp['stack'])}")

    lines.append("\nProjects:")
    for proj in cv_data.get("projects", []):
        lines.append(f"  - {proj['title']} ({proj['date']})")
        for b in proj.get("bullets", []):
            lines.append(f"      • {b}")
        if proj.get("stack"):
            lines.append(f"      Stack: {', '.join(proj['stack'])}")

    skills = cv_data.get("skills", {})
    lines.append(f"\nSkills — Proficient: {skills.get('proficient', '')}")
    lines.append(f"Skills — Familiar: {skills.get('familiar', '')}")
    lines.append(f"Skills — Tools: {skills.get('tools', '')}")
    lines.append(f"Skills — Domains: {skills.get('domains', '')}")

    langs = cv_data.get("languages", [])
    lines.append("\nLanguages: " + ", ".join(f"{l['name']} ({l['level']})" for l in langs))

    return "\n".join(lines)


def _build_entry(title: str, date: str, subtitle: str,
                 bullets: list, stack: list = None) -> str:
    bullets_html = "\n".join(f"<li>{b}</li>" for b in bullets)
    stack_html = ""
    if stack:
        tags = "".join(f'<span class="stack-tag">{s}</span>' for s in stack)
        stack_html = f'<div>{tags}</div>'
    return f"""
        <div class="entry">
          <div class="entry-header">
            <span class="entry-title">{title}</span>
            <span class="entry-date">{date}</span>
          </div>
          <p class="entry-sub">{subtitle}</p>
          <div class="entry-desc">
            <ul>
              {bullets_html}
            </ul>
            {stack_html}
          </div>
        </div>"""


def render_cv_html(template_path: str, static_data: dict, adapted: dict) -> str:
    template = Path(template_path).read_text(encoding="utf-8")

    personal = static_data.get("personal", {})
    edu = static_data.get("education", {})
    avail = static_data.get("availability", {})
    langs = static_data.get("languages", [])
    accent_on = {"ACCENT_COLOR": "#2C5F4A", "ACCENT_LIGHT": "#E8EFE9"}
    accent_off = {"ACCENT_COLOR": "#5F5E5A", "ACCENT_LIGHT": "#F1EFE8"}
    accent = accent_on if personal.get("accent_enabled", True) else accent_off

    exp_html = "\n".join(
        _build_entry(e["title"], e["date"], e["subtitle"], e.get("bullets", []), e.get("stack"))
        for e in adapted.get("experiences", [])
    )
    proj_html = "\n".join(
        _build_entry(p["title"], p["date"], p["subtitle"], p.get("bullets", []), p.get("stack"))
        for p in adapted.get("projects", [])
    )

    placeholders = {
        **accent,
        "FULL_NAME": personal.get("full_name", ""),
        "HEADLINE": adapted.get("headline", "Software Engineering Student — Final Year"),
        "LOCATION": personal.get("location", ""),
        "EMAIL": personal.get("email", ""),
        "GITHUB": personal.get("github", ""),
        "EXPERIENCE_ENTRIES": exp_html,
        "PROJECT_ENTRIES": proj_html,
        "EDU_SCHOOL": edu.get("school", ""),
        "EDU_DATES": edu.get("dates", ""),
        "EDU_DETAIL": edu.get("detail", ""),
        "START_DATE": avail.get("start", ""),
        "DURATION": avail.get("duration", ""),
        "MOBILITY": avail.get("mobility", ""),
        "SKILLS_PROFICIENT": adapted.get("skills_proficient", ""),
        "SKILLS_FAMILIAR": adapted.get("skills_familiar", ""),
        "SKILLS_TOOLS": adapted.get("skills_tools", ""),
        "SKILLS_DOMAINS": adapted.get("skills_domains", ""),
        "LANG1_NAME": langs[0]["name"] if len(langs) > 0 else "",
        "LANG1_LEVEL": langs[0]["level"] if len(langs) > 0 else "",
        "LANG2_NAME": langs[1]["name"] if len(langs) > 1 else "",
        "LANG2_LEVEL": langs[1]["level"] if len(langs) > 1 else "",
        "OTHER_INFO": static_data.get("other", ""),
    }

    html = template
    for key, value in placeholders.items():
        html = html.replace(f"{{{{{key}}}}}", str(value))
    return html


def write_cv_pdf(html_content: str, output_path: str, base_url: str = "config/templates/") -> None:
    try:
        from weasyprint import HTML
    except ImportError:
        raise ImportError(
            "WeasyPrint est requis pour générer le PDF. "
            "Installe-le avec : pip install weasyprint\n"
            "(Sur macOS, brew install pango peut aussi être nécessaire)"
        )
    # base_url permet de résoudre les chemins relatifs (polices locales fonts/)
    HTML(string=html_content, base_url=base_url).write_pdf(output_path)
