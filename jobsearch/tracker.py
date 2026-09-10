"""Suivi des candidatures — data/applications.json."""
import json
import os
import re
from collections import Counter
from datetime import date, datetime

from jobsearch.config import PATHS

STATUSES = ["generated", "sent", "answered", "interview", "rejected", "accepted", "cancelled"]
# Statuts indiquant qu'une réponse de l'entreprise a été reçue
RESPONSE_STATUSES = {"answered", "interview", "rejected", "accepted"}

_STATUS_LABELS = {
    "generated": "📝 générée",
    "sent": "📤 envoyée",
    "answered": "📬 réponse reçue",
    # 🎤 et non 🎙 : ce dernier est de largeur East_Asian_Width « Neutral » (1 cellule),
    # seul de son espèce parmi ces libellés — son glyphe débordait sur l'espace suivant
    # et désalignait la ligne « entretien » dans les rendus en colonnes.
    "interview": "🎤 entretien",
    "rejected": "❌ refusée",
    "accepted": "✅ acceptée",
    "cancelled": "🚫 annulée",
}

# Couleurs de surlignage par statut, source de vérité unique pour la TUI (onglet Suivi)
# et pour le rendu SVG du flux (src/flow.py). `generated` : pas de surlignage (None).
STATUS_COLORS = {
    "generated": None,          # pas de surlignage
    "sent": "#584b00",          # jaune
    "answered": "#6b4310",      # ambre
    "interview": "#46326b",     # violet
    "accepted": "#1e4620",      # vert
    "rejected": "#870000",      # rouge
    "cancelled": "#2b2b2b",     # gris
}


def _to_iso(value: str) -> str:
    """Normalise une date/datetime stockée en chaîne ISO datetime.

    Les dates ponctuelles existantes (`generated_at`, `sent_at`, `responded_at`) sont
    stockées en date seule (`str(date.today())`, 10 caractères) : on complète à minuit.
    """
    value = str(value)
    if len(value) == 10:
        return value + "T00:00:00"
    return value


def ensure_history(app: dict) -> list[dict]:
    """Historique de `app`, reconstruit depuis les dates existantes s'il est absent.

    Ne modifie pas `app` : retourne une nouvelle liste. Les événements reconstruits
    portent `"inferred": True` (heure inconnue → T00:00:00).
    """
    existing = app.get("history")
    if existing:
        return list(existing)

    history: list[dict] = []
    generated_at = app.get("generated_at")
    if generated_at:
        history.append({"status": "generated", "at": _to_iso(generated_at), "inferred": True})

    sent_at = app.get("sent_at")
    if sent_at:
        history.append({"status": "sent", "at": _to_iso(sent_at), "inferred": True})

    status = app.get("status")
    responded_at = app.get("responded_at")
    if responded_at and status in RESPONSE_STATUSES:
        history.append({"status": status, "at": _to_iso(responded_at), "inferred": True})

    if status == "cancelled":
        ref = app.get("sent_at") or app.get("generated_at")
        if ref:
            history.append({"status": "cancelled", "at": _to_iso(ref), "inferred": True})

    return history


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, "applications.json")


def load_applications(data_dir: str = PATHS.data_dir) -> list[dict]:
    path = _path(data_dir)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_applications(apps: list[dict], data_dir: str = PATHS.data_dir) -> None:
    os.makedirs(data_dir, exist_ok=True)
    with open(_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)


def record_generated(offer: dict, data_dir: str = PATHS.data_dir) -> None:
    """Enregistre (ou met à jour) une candidature après génération de la lettre/CV."""
    apps = load_applications(data_dir)
    for app in apps:
        if app["offer_id"] == offer.get("id"):
            # Reconstruction avant mutation (voir mark_status) : sinon l'événement
            # `generated` déduit porterait la date du jour au lieu de la date d'origine.
            history = ensure_history(app)
            app["generated_at"] = str(date.today())
            if not history or history[-1]["status"] != "generated":
                history = history + [
                    {"status": "generated", "at": datetime.now().isoformat(timespec="seconds")}
                ]
            app["history"] = history
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
            "history": [{"status": "generated", "at": datetime.now().isoformat(timespec="seconds")}],
        }
    )
    save_applications(apps, data_dir)


def mark_status(offer_id: str, status: str, note: str = "", data_dir: str = PATHS.data_dir) -> dict:
    if status not in STATUSES:
        raise ValueError(f"Statut invalide '{status}'. Valides : {', '.join(STATUSES)}")
    apps = load_applications(data_dir)
    for app in apps:
        if app["offer_id"] == offer_id:
            # L'historique doit être reconstruit AVANT toute mutation : `ensure_history`
            # déduit l'événement de réponse depuis `status` + `responded_at`. Reconstruire
            # après la mise à jour réécrivait le passé avec le nouveau statut — un passage
            # interview → rejected effaçait purement et simplement l'étape « entretien ».
            history = ensure_history(app)
            app["status"] = status
            if status == "sent" and not app.get("sent_at"):
                app["sent_at"] = str(date.today())
            if status in RESPONSE_STATUSES and not app.get("responded_at"):
                app["responded_at"] = str(date.today())
            if note:
                app["notes"] = (app.get("notes", "") + " | " + note).strip(" |")
            if not history or history[-1]["status"] != status:
                history = history + [
                    {"status": status, "at": datetime.now().isoformat(timespec="seconds")}
                ]
            app["history"] = history
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

# Alias de noms de pays (anglais / français / langue locale) -> nom canonique
# (les mêmes que ceux de _COUNTRY_NAMES). Sert de repli quand une offre ajoutée
# manuellement n'a pas de code `country` : sans ça, "Delft, Pays-Bas" et
# "Pays-Bas (Zoetermeer HQ) ou autre bureau européen" retombaient chacun sur
# leur propre libellé, éclatant les Pays-Bas en 3 entrées au lieu d'une.
_COUNTRY_ALIASES = {
    "netherlands": "Netherlands", "nederland": "Netherlands",
    "pays-bas": "Netherlands", "pays bas": "Netherlands", "holland": "Netherlands",
    "france": "France",
    "germany": "Germany", "allemagne": "Germany", "deutschland": "Germany",
    "belgium": "Belgium", "belgique": "Belgium", "belgië": "Belgium", "belgie": "Belgium",
    "spain": "Spain", "espagne": "Spain", "españa": "Spain", "espana": "Spain",
    "italy": "Italy", "italie": "Italy", "italia": "Italy",
    "united kingdom": "United Kingdom", "royaume-uni": "United Kingdom",
    "royaume uni": "United Kingdom", "great britain": "United Kingdom",
    "ireland": "Ireland", "irlande": "Ireland", "éire": "Ireland", "eire": "Ireland",
    "switzerland": "Switzerland", "suisse": "Switzerland", "schweiz": "Switzerland",
    "svizzera": "Switzerland",
    "luxembourg": "Luxembourg", "luxemburg": "Luxembourg",
    "portugal": "Portugal",
    "sweden": "Sweden", "suède": "Sweden", "suede": "Sweden", "sverige": "Sweden",
    "denmark": "Denmark", "danemark": "Denmark", "danmark": "Denmark",
    "norway": "Norway", "norvège": "Norway", "norvege": "Norway", "norge": "Norway",
    "finland": "Finland", "finlande": "Finland", "suomi": "Finland",
    "austria": "Austria", "autriche": "Austria", "österreich": "Austria",
    "osterreich": "Austria",
    "poland": "Poland", "pologne": "Poland", "polska": "Poland",
}

# Une seule regex, compilée une fois, alternative triée par longueur décroissante
# (sinon "pays" pourrait matcher avant "pays-bas") et en limites de mots — même
# mécanique que `_TECH_KEYWORDS_RE` dans jobsearch/filter.py, pour la même raison :
# un `alias in text` brut ferait matcher "Irelande" via "ireland" ou "Francfort"
# via un alias plus court, comme "git" matchait "diGITal" côté pré-filtre.
_COUNTRY_ALIASES_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_COUNTRY_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _application_location(app: dict, data_dir: str) -> str:
    """Localisation d'une candidature (regroupée par pays), via jointure avec l'offre JSON.

    1) code pays connu (`country`) → nom lisible ; 2) sinon alias de nom de pays
    trouvé dans `location` (anglais/français/local, en limites de mots) → nom
    canonique ; 3) sinon dernier segment d'une `location` propre (ex. « Netherlands »)
    ; 4) sinon « Inconnu ». Les blobs JSON-LD bruts et les candidatures manuelles
    sans offre → repli propre.
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
        match = _COUNTRY_ALIASES_RE.search(location)
        if match:
            return _COUNTRY_ALIASES[match.group(1).lower()]
        return location.split(",")[-1].strip() or "Inconnu"
    if country:
        return country
    return "Inconnu"


def compute_stats(data_dir: str = PATHS.data_dir) -> dict:
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


def render_tracking_section(data_dir: str = PATHS.data_dir) -> list[str]:
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
