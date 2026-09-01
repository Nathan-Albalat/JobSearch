"""Génère cv_master.md depuis cv_data.yaml (source de vérité unique)."""
import os

from src.config import PATHS
from src.cv_pdf import load_cv_data


def generate_cv_master_md(cv_data: dict) -> str:
    personal = cv_data.get("personal", {})
    education = cv_data.get("education", [])
    avail = cv_data.get("availability", {})
    skills = cv_data.get("skills", {})

    lines = [
        f"# CV — {personal.get('full_name', '')}",
        "",
        "<!-- FICHIER GÉNÉRÉ AUTOMATIQUEMENT depuis cv_data.yaml — ne pas éditer à la main. -->",
        "<!-- Modifie cv_data.yaml puis lance : python -m src.cv_sync -->",
        "",
        "## Résumé",
        cv_data.get("summary", ""),
        "",
        "---",
        "",
        "## Formation",
        "",
    ]
    for edu in education:
        lines.append(f"### {edu.get('school', '')}")
        lines.append(f"**{edu.get('detail', '')}** | {edu.get('dates', '')}")
        lines.append("")
    lines += [
        "---",
        "",
        "## Expériences professionnelles",
        "",
    ]

    def _entries(items: list[dict]) -> list[str]:
        out = []
        for item in items:
            out.append(f"### {item.get('title', '')}")
            out.append(f"**{item.get('subtitle', '')}** | {item.get('date', '')}")
            for b in item.get("bullets", []):
                out.append(f"- {b}")
            if item.get("stack"):
                out.append(f"- **Stack** : {', '.join(item['stack'])}")
            out.append("")
        return out

    lines += _entries(cv_data.get("experiences", []))
    lines += ["---", "", "## Projets", ""]
    lines += _entries(cv_data.get("projects", []))
    lines += [
        "---",
        "",
        "## Compétences techniques",
        "",
        f"- **Maîtrisés** : {skills.get('proficient', '')}",
        f"- **Pratiqués** : {skills.get('familiar', '')}",
        f"- **Outils** : {skills.get('tools', '')}",
        f"- **Domaines** : {skills.get('domains', '')}",
        "",
        "---",
        "",
        "## Langues",
        "",
    ]
    for lang in cv_data.get("languages", []):
        lines.append(f"- {lang.get('name', '')} : {lang.get('level', '')}")
    lines += [
        "",
        "---",
        "",
        "## Informations complémentaires",
        "",
        f"- **Disponibilité** : à partir de {avail.get('start', '')}, {avail.get('duration', '')}",
        f"- **Mobilité** : {avail.get('mobility', '')}",
        f"- {cv_data.get('other', '').replace('<br>', ' · ')}",
        "",
    ]
    return "\n".join(lines)


def sync_cv_master(cv_dir: str = PATHS.cv_dir) -> str:
    cv_data = load_cv_data(cv_dir)
    content = generate_cv_master_md(cv_data)
    path = os.path.join(cv_dir, "cv_master.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


if __name__ == "__main__":
    path = sync_cv_master()
    print(f"cv_master.md régénéré : {path}")
