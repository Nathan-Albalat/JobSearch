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


def undo_last_status(offer_id: str, data_dir: str = PATHS.data_dir) -> tuple[dict, dict]:
    """Retire le dernier événement du parcours (erreur de saisie) et revient au statut précédent.

    Retourne `(app, removed_event)`. Refuse de retirer l'événement initial (`generated`) :
    une candidature a toujours au moins ce point d'entrée. Changer de statut ne suffisait
    pas à corriger une erreur : l'événement fautif restait dans `history`, donc dans la
    timeline, l'arbre de parcours et le Sankey.

    `sent_at`/`responded_at` sont remis en cohérence avec l'historique restant : ils
    alimentent le taux de réponse et le délai moyen de `compute_stats`, et une date posée
    par l'événement retiré fausserait ces métriques.
    """
    apps = load_applications(data_dir)
    for app in apps:
        if app["offer_id"] != offer_id:
            continue
        history = ensure_history(app)  # avant toute mutation, comme mark_status
        if len(history) <= 1:
            raise ValueError("Rien à annuler : le parcours ne contient que l'étape initiale.")
        removed = history.pop()
        app["history"] = history
        app["status"] = history[-1]["status"]
        remaining = {e["status"] for e in history}
        if "sent" not in remaining and not remaining & RESPONSE_STATUSES:
            app.pop("sent_at", None)
        if not remaining & RESPONSE_STATUSES:
            app.pop("responded_at", None)
        save_applications(apps, data_dir)
        return app, removed
    raise KeyError(f"Aucune candidature pour l'offre '{offer_id}'.")


def set_note(offer_id: str, note: str, data_dir: str = PATHS.data_dir) -> dict:
    """Remplace la note libre d'une candidature (ne concatène pas).

    Distinct de `mark_status(..., note=...)`, qui AJOUTE au fil des changements
    de statut : ici on édite, donc on remplace — c'est ce que fait déjà la touche
    `n` de la TUI, dont le code inline vivait en double. Pas d'`ensure_history`
    ici : une note ne touche pas au parcours des statuts.

    Ce champ est délibérément du texte libre : échéances, dates d'entretien et
    noms de recruteurs n'ont pas de champ dédié et n'en auront pas — on ne peut
    pas anticiper la liste, et un modèle extrait très bien « entretien le 07/08 »
    d'une phrase. Voir le paramètre `q` de l'API, qui cherche aussi là-dedans.
    """
    apps = load_applications(data_dir)
    for app in apps:
        if app["offer_id"] == offer_id:
            app["notes"] = note.strip()
            save_applications(apps, data_dir)
            return app
    raise KeyError(f"Aucune candidature pour l'offre '{offer_id}'.")


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


_ADDRESS_LOCALITY_RE = re.compile(r"""addressLocality['"]?\s*:\s*['"]([^'"]+)['"]""")
UNKNOWN_CITY = "Non précisée"


def _city_from_offer(location: str) -> str | None:
    """Ville d'une offre, ou `None` quand `location` n'en contient pas de façon fiable.

    Blob JSON-LD → `addressLocality` ; sinon premier segment d'une `location` à ≥ 2 segments
    séparés par des virgules, sauf s'il s'agit d'un nom/code de pays (« Nederland » seul,
    « Pays-Bas (Zoetermeer HQ) ou autre… » → `None`).
    """
    if not location:
        return None
    if "@type" in location:
        match = _ADDRESS_LOCALITY_RE.search(location)
        return match.group(1).strip() if match else None
    if location.lstrip().startswith(("[{", "{")):
        return None
    parts = [p.strip() for p in location.split(",")]
    if len(parts) < 2 or not parts[0]:
        return None
    first = parts[0]
    if _COUNTRY_ALIASES_RE.fullmatch(first) or first.upper() in _COUNTRY_NAMES:
        return None
    return first


def _application_place(app: dict, data_dir: str) -> tuple[str, str | None]:
    """`(pays, ville)` d'une candidature, via jointure avec l'offre JSON.

    Pays : 1) code pays connu (`country`) → nom lisible ; 2) sinon alias de nom de pays
    trouvé dans `location` (anglais/français/local, en limites de mots) → nom
    canonique ; 3) sinon dernier segment d'une `location` propre (ex. « Netherlands »)
    ; 4) sinon « Inconnu ». Les blobs JSON-LD bruts et les candidatures manuelles
    sans offre → repli propre. Ville : voir `_city_from_offer` (`None` si indéterminable).
    """
    offer_path = os.path.join(data_dir, "offers", f"{app.get('offer_id', '')}.json")
    if not os.path.exists(offer_path):
        return "Inconnu", None
    try:
        with open(offer_path, encoding="utf-8") as f:
            offer = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "Inconnu", None

    country = (offer.get("country") or "").strip()
    location = (offer.get("location") or "").strip()
    city = _city_from_offer(location)
    if country.upper() in _COUNTRY_NAMES:
        return _COUNTRY_NAMES[country.upper()], city

    # Ignore les blobs JSON-LD bruts (ex. "[{'@type': 'Place', ...}]").
    if location and "@type" not in location and not location.lstrip().startswith(("[{", "{")):
        match = _COUNTRY_ALIASES_RE.search(location)
        if match:
            return _COUNTRY_ALIASES[match.group(1).lower()], city
        return (location.split(",")[-1].strip() or "Inconnu"), city
    if country:
        return country, city
    return "Inconnu", city


def _application_location(app: dict, data_dir: str) -> str:
    """Pays d'une candidature (wrapper de `_application_place`)."""
    return _application_place(app, data_dir)[0]


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

    # Une seule passe : chaque _application_place ouvre un JSON d'offre.
    by_location: Counter = Counter()
    place_cities: dict[str, Counter] = {}
    by_month: dict[str, int] = {}
    by_day: dict[str, int] = {}
    by_day_country: dict[str, dict[str, int]] = {}
    for a in apps:
        country, city = _application_place(a, data_dir)
        by_location[country] += 1
        place_cities.setdefault(country, Counter())[city or UNKNOWN_CITY] += 1
        g = a.get("generated_at")
        if g:
            by_month[str(g)[:7]] = by_month.get(str(g)[:7], 0) + 1  # YYYY-MM
            day = str(g)[:10]
            by_day[day] = by_day.get(day, 0) + 1
            per_country = by_day_country.setdefault(day, {})
            per_country[country] = per_country.get(country, 0) + 1
    by_month = dict(sorted(by_month.items()))
    by_day = dict(sorted(by_day.items()))
    by_day_country = dict(sorted(by_day_country.items()))
    by_place = {
        country: {"total": by_location[country], "cities": dict(place_cities[country].most_common())}
        for country, _ in by_location.most_common()
    }

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
        "by_day": by_day,
        "by_place": by_place,
        "by_day_country": by_day_country,
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
