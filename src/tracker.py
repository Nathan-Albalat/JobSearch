"""Suivi des candidatures — data/applications.json."""
import json
import os
from datetime import date

STATUSES = ["generated", "sent", "answered", "interview", "rejected", "accepted"]

_STATUS_LABELS = {
    "generated": "📝 générée",
    "sent": "📤 envoyée",
    "answered": "📬 réponse reçue",
    "interview": "🎙 entretien",
    "rejected": "❌ refusée",
    "accepted": "✅ acceptée",
}


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, "applications.json")


def load_applications(data_dir: str = "data") -> list[dict]:
    path = _path(data_dir)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_applications(apps: list[dict], data_dir: str = "data") -> None:
    os.makedirs(data_dir, exist_ok=True)
    with open(_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)


def record_generated(offer: dict, data_dir: str = "data") -> None:
    """Enregistre (ou met à jour) une candidature après génération de la lettre/CV."""
    apps = load_applications(data_dir)
    for app in apps:
        if app["offer_id"] == offer.get("id"):
            app["generated_at"] = str(date.today())
            save_applications(apps, data_dir)
            return
    apps.append(
        {
            "offer_id": offer.get("id", ""),
            "company": offer.get("company", ""),
            "title": offer.get("title", ""),
            "url": offer.get("url", ""),
            "generated_at": str(date.today()),
            "sent_at": None,
            "status": "generated",
            "notes": "",
        }
    )
    save_applications(apps, data_dir)


def mark_status(offer_id: str, status: str, note: str = "", data_dir: str = "data") -> dict:
    if status not in STATUSES:
        raise ValueError(f"Statut invalide '{status}'. Valides : {', '.join(STATUSES)}")
    apps = load_applications(data_dir)
    for app in apps:
        if app["offer_id"] == offer_id:
            app["status"] = status
            if status == "sent" and not app.get("sent_at"):
                app["sent_at"] = str(date.today())
            if note:
                app["notes"] = (app.get("notes", "") + " | " + note).strip(" |")
            save_applications(apps, data_dir)
            return app
    raise KeyError(f"Aucune candidature pour l'offre '{offer_id}'. Lance d'abord --generate.")


def render_applications_table(apps: list[dict]) -> str:
    if not apps:
        return "Aucune candidature enregistrée."
    lines = [
        f"{'ID':<10} {'Statut':<18} {'Entreprise':<25} {'Titre':<45} Notes",
        "-" * 110,
    ]
    for app in apps:
        label = _STATUS_LABELS.get(app["status"], app["status"])
        lines.append(
            f"{app['offer_id']:<10} {label:<18} {app['company'][:24]:<25} "
            f"{app['title'][:44]:<45} {app.get('notes', '')}"
        )
    return "\n".join(lines)


def render_tracking_section(data_dir: str = "data") -> list[str]:
    """Section markdown 'Suivi des candidatures' pour le rapport."""
    apps = load_applications(data_dir)
    if not apps:
        return []
    counts: dict[str, int] = {}
    for app in apps:
        counts[app["status"]] = counts.get(app["status"], 0) + 1
    lines = ["## Suivi des candidatures", ""]
    lines.append(" · ".join(f"{_STATUS_LABELS[s]} : {counts[s]}" for s in STATUSES if s in counts))
    waiting = [a for a in apps if a["status"] == "sent"]
    if waiting:
        lines += ["", "**En attente de réponse :**"]
        for app in waiting:
            lines.append(f"- {app['title']} — {app['company']} (envoyée le {app.get('sent_at', '?')}, id `{app['offer_id']}`)")
    lines.append("")
    return lines
