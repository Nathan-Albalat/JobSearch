"""Suivi des candidatures — data/applications.json."""
import json
import os
from collections import Counter
from datetime import date

STATUSES = ["generated", "sent", "answered", "interview", "rejected", "accepted", "cancelled"]
# Statuts indiquant qu'une réponse de l'entreprise a été reçue
RESPONSE_STATUSES = {"answered", "interview", "rejected", "accepted"}

_STATUS_LABELS = {
    "generated": "📝 générée",
    "sent": "📤 envoyée",
    "answered": "📬 réponse reçue",
    "interview": "🎙 entretien",
    "rejected": "❌ refusée",
    "accepted": "✅ acceptée",
    "cancelled": "🚫 annulée",
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
            if status in RESPONSE_STATUSES and not app.get("responded_at"):
                app["responded_at"] = str(date.today())
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


# Codes pays → noms lisibles, pour regrouper les localisations par pays.
_COUNTRY_NAMES = {
    "NL": "Netherlands", "FR": "France", "DE": "Germany", "BE": "Belgium",
    "ES": "Spain", "IT": "Italy", "GB": "United Kingdom", "UK": "United Kingdom",
    "IE": "Ireland", "CH": "Switzerland", "LU": "Luxembourg", "PT": "Portugal",
    "SE": "Sweden", "DK": "Denmark", "NO": "Norway", "FI": "Finland",
    "AT": "Austria", "PL": "Poland",
}


def _application_location(app: dict, data_dir: str) -> str:
    """Localisation d'une candidature (regroupée par pays), via jointure avec l'offre JSON.

    1) code pays connu (`country`) → nom lisible ; 2) sinon dernier segment d'une
    `location` propre (ex. « Netherlands ») ; 3) sinon « Inconnu ». Les blobs
    JSON-LD bruts et les candidatures manuelles sans offre → repli propre.
    """
    offer_path = os.path.join(data_dir, "offers", f"{app.get('offer_id', '')}.json")
    if not os.path.exists(offer_path):
        return "Inconnu"
    try:
        with open(offer_path, encoding="utf-8") as f:
            offer = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "Inconnu"

    country = (offer.get("country") or "").strip()
    if country.upper() in _COUNTRY_NAMES:
        return _COUNTRY_NAMES[country.upper()]

    location = (offer.get("location") or "").strip()
    # Ignore les blobs JSON-LD bruts (ex. "[{'@type': 'Place', ...}]").
    if location and "@type" not in location and not location.lstrip().startswith(("[{", "{")):
        return location.split(",")[-1].strip() or "Inconnu"
    if country:
        return country
    return "Inconnu"


def compute_stats(data_dir: str = "data") -> dict:
    """Statistiques des candidatures pour le panneau de stats de la TUI."""
    apps = load_applications(data_dir)
    by_status = Counter(a.get("status", "generated") for a in apps)
    active = [a for a in apps if a.get("status") != "cancelled"]

    accepted = by_status.get("accepted", 0)
    rejected = by_status.get("rejected", 0)
    generated = by_status.get("generated", 0)
    cancelled = by_status.get("cancelled", 0)
    coarse = {
        "Acceptée": accepted,
        "Refusée": rejected,
        "Annulée": cancelled,
        "En attente": len(apps) - accepted - rejected - cancelled - generated,
    }

    by_location = Counter(_application_location(a, data_dir) for a in apps)

    by_month: dict[str, int] = {}
    for a in apps:
        g = a.get("generated_at")
        if g:
            month = str(g)[:7]  # YYYY-MM
            by_month[month] = by_month.get(month, 0) + 1
    by_month = dict(sorted(by_month.items()))

    # --- Métriques de retour de candidature (les annulées sont exclues) ---
    sent = sum(1 for a in active if a.get("status") != "generated")
    responded = sum(1 for a in active if a.get("status") in RESPONSE_STATUSES)
    decided = accepted + rejected

    delays = []
    for a in active:
        s, r = a.get("sent_at"), a.get("responded_at")
        if s and r:
            try:
                delays.append((date.fromisoformat(r[:10]) - date.fromisoformat(s[:10])).days)
            except ValueError:
                pass

    rates = {
        "sent": sent,
        "responded": responded,
        "taux_reponse": (responded / sent) if sent else None,
        "taux_acceptation": (accepted / decided) if decided else None,
        "delai_moyen_jours": (sum(delays) / len(delays)) if delays else None,
        "n_delai": len(delays),
    }

    return {
        "total": len(apps),
        "by_status": dict(by_status),
        "coarse": coarse,
        "by_location": dict(by_location.most_common()),
        "by_month": by_month,
        "rates": rates,
    }


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
