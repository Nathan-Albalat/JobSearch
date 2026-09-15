"""Couche de sérialisation JSON-able, partagée entre le futur `--json` du CLI,
l'API HTTP (`jobsearch/server.py`) et le futur serveur MCP (phase 2).

Stdlib uniquement — sur le modèle de `jobsearch/duplicates.py`/`jobsearch/flow.py` :
pas de dépendance FastAPI ici, ce module doit rester importable même sans l'extra
`[server]`. Aucune fonction n'écrit sur disque ni ne lit la configuration
(`search.yaml`) — uniquement les données (`applications.json`, `offers/*.json`,
`output/candidatures/`).
"""
import os

from jobsearch.tracker import STATUSES, _STATUS_LABELS, ensure_history
from jobsearch.duplicates import load_offer

# Longueur de troncature de la description en forme compacte (`offer_dict(full=False)`)
# — assez pour un aperçu, pas pour alourdir une liste de 50 offres.
_DESCRIPTION_PREVIEW_CHARS = 400


def offer_id_from_folder(folder: str) -> str:
    """Extrait l'offer_id d'un nom de dossier de candidature `{slug}_{offer_id}`.

    Le motif `os.path.basename(f).rsplit("_", 1)[-1]` était dupliqué à 4 endroits de
    `jobsearch/tui.py` (l. 1085, 1143, 1149, 1798) — factorisé ici. `rsplit(..., 1)`
    coupe sur le DERNIER underscore : un slug contenant lui-même un underscore
    (ex. "société-x_recrute_11c0ea0a") reste géré correctement, seul l'id final compte.
    """
    return os.path.basename(folder).rsplit("_", 1)[-1]


def application_dict(app: dict, data_dir: str) -> dict:
    """Enrichit un enregistrement de `load_applications()` avec son historique complet.

    Passe TOUJOURS par `tracker.ensure_history` (jamais de réimplémentation) : c'est là
    que vit la reconstruction des anciennes dates ponctuelles en événements `inferred`.
    """
    history = ensure_history(app)
    status = app.get("status", "generated")
    return {
        "offer_id": app.get("offer_id", ""),
        "company": app.get("company", ""),
        "title": app.get("title", ""),
        "url": app.get("url", ""),
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status),
        "notes": app.get("notes", ""),
        "generated_at": app.get("generated_at"),
        "sent_at": app.get("sent_at"),
        "responded_at": app.get("responded_at"),
        "history": history,
    }


def offer_dict(offer: dict, *, full: bool = False) -> dict:
    """Forme compacte (listes) ou complète (détail d'une offre) d'un JSON d'offre.

    Compacte par défaut — c'est la forme utilisée par `GET /api/offers`, où la
    pagination limite déjà le nombre d'entrées mais pas la taille de chacune :
    pas de description ici, sans quoi une page de 50 offres redevient lourde pour
    un consommateur LLM (contrainte nº1 du plan).
    """
    filter_result = offer.get("filter_result") or {}
    base = {
        "id": offer.get("id", ""),
        "title": offer.get("title", ""),
        "company": offer.get("company", ""),
        "location": offer.get("location", ""),
        "country": offer.get("country", ""),
        "score": filter_result.get("score"),
        "url": offer.get("url", ""),
        "date_published": offer.get("date_published"),
        "date_scraped": offer.get("date_scraped"),
        "source": offer.get("source", ""),
    }
    if not full:
        return base

    description = offer.get("description", "") or ""
    base.update(
        {
            "description": description[:2000],
            "filter_result": filter_result,
            "duplicate_of": offer.get("duplicate_of"),
            "invalid": offer.get("invalid"),
            "gen_notes": offer.get("gen_notes", ""),
        }
    )
    return base


def dossier_dict(offer_id: str, data_dir: str, output_dir: str) -> dict:
    """Le dossier complet d'une candidature : candidature + offre + artefacts produits.

    Ressource clé du besoin « écris-moi un mail concernant la candidature Y » : sans
    elle, un agent doit recoller trois sources (applications.json, offers/*.json,
    output/candidatures/) avant de pouvoir répondre. Ne lève jamais — un `offer_id`
    sans candidature ni offre retourne des champs à None/vides plutôt qu'une erreur,
    laissé à l'appelant (route HTTP) de décider du code de statut.
    """
    # Import paresseux pour éviter un cycle (tracker importe déjà ce module ? non,
    # mais reste cohérent avec le principe "aucune dépendance lourde au niveau module").
    from jobsearch.tracker import load_applications

    app = None
    for a in load_applications(data_dir):
        if a.get("offer_id") == offer_id:
            app = a
            break

    offer = load_offer(offer_id, data_dir)

    folder = None
    candidatures_dir = os.path.join(output_dir, "candidatures")
    if os.path.isdir(candidatures_dir):
        for name in os.listdir(candidatures_dir):
            path = os.path.join(candidatures_dir, name)
            if os.path.isdir(path) and offer_id_from_folder(path) == offer_id:
                folder = path
                break

    letter_md = ""
    has_letter_pdf = False
    has_cv_pdf = False
    if folder:
        letter_path = os.path.join(folder, "lettre.md")
        if os.path.exists(letter_path):
            with open(letter_path, encoding="utf-8") as f:
                letter_md = f.read()
        has_letter_pdf = os.path.exists(os.path.join(folder, "lettre.pdf"))
        has_cv_pdf = os.path.exists(os.path.join(folder, "cv_adapte.pdf"))

    return {
        "offer_id": offer_id,
        "application": application_dict(app, data_dir) if app else None,
        "offer": offer_dict(offer, full=True) if offer else None,
        "folder": folder,
        "letter_markdown": letter_md,
        "has_letter_pdf": has_letter_pdf,
        "has_cv_pdf": has_cv_pdf,
    }
