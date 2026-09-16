"""API HTTP + vue web pour consulter/suivre les candidatures à distance (`jobsearch --serve`).

Dépend de l'extra optionnel `jobsearch[server]` (FastAPI + uvicorn) — même motif que
`jobsearch[jobspy]` : l'installation de base reste légère, cet import n'a lieu que dans
`cli.py` au moment de `--serve` (import paresseux, comme `--tui`/`--setup`).

RÈGLE STRUCTURANTE : ce module n'importe JAMAIS `jobsearch/tui.py` (Textual, démarrage
lourd) ni `jobsearch/generator.py` (dépendance Anthropic, appels API payants). Les seules
sources de données sont `tracker`, `duplicates`, `report`, `flow`, `serialize`, `config`.

Sécurité (voir aussi `jobsearch/cli.py` pour le câblage de `--host`/--allow-write) :
- écoute sur 127.0.0.1 par défaut ;
- jeton partagé `JOBSEARCH_API_TOKEN` (config/.env) — en-tête `X-API-Token` sur `/api/*`,
  cookie `jobsearch_token` sur les pages (posé via `?token=...` sur la première visite) ;
- refuse de démarrer si l'écoute n'est pas sur la boucle locale et qu'aucun jeton n'est
  défini (`build_app`/`run_server`) ;
- écriture (`POST /api/applications/{id}/status`) uniquement si `--allow-write`, absent
  par défaut — 403 sinon, indépendamment du jeton.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobsearch.config import PATHS, _package_resource_dir
from jobsearch.duplicates import load_offer
from jobsearch.flow import render_sankey_svg
from jobsearch.report import load_filtered_offers
from jobsearch.serialize import application_dict, dossier_dict, offer_dict
from jobsearch.tracker import (
    STATUS_COLORS,
    STATUSES,
    compute_stats,
    load_applications,
    mark_status,
    set_note,
)

# Pagination : contrainte n°1 pour un consommateur LLM (~5430 offres, 44 Mo sur disque
# à l'instance de référence) — jamais de route qui dumpe tout.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

COOKIE_NAME = "jobsearch_token"

_env = Environment(
    loader=FileSystemLoader(_package_resource_dir("resources", "web")),
    autoescape=select_autoescape(["html"]),
)
_env.filters["statuscolor"] = lambda s: STATUS_COLORS.get(s) or "#333333"


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


# ------------------------------------------------------------------ #
# Tri/filtre côté serveur — pas de JS côté client (contrainte de la vue web, voir
# CLAUDE.md) : ce sont ces mêmes fonctions qu'utilisent l'API JSON et les pages HTML,
# pour rester cohérentes entre les deux.
# ------------------------------------------------------------------ #

# Candidatures : clé -> fonction d'extraction pour le tri.
_APP_SORT_KEYS = {
    "generated_at": lambda a: a.get("generated_at") or "",
    "company": lambda a: (a.get("company") or "").casefold(),
    # Ordre du PIPELINE (generated → sent → … → cancelled), pas alphabétique :
    # trier par chaîne donnerait « generated, rejected, sent », qui ne dit rien
    # de l'avancement. `STATUSES` porte déjà l'ordre métier.
    "status": lambda a: STATUSES.index(a["status"]) if a.get("status") in STATUSES else len(STATUSES),
}

# Offres : clé -> fonction d'extraction.
_OFFER_SORT_KEYS = {
    "score": lambda o: o.get("filter_result", {}).get("score") if o.get("filter_result", {}).get("score") is not None else -1,
    "date_scraped": lambda o: o.get("date_scraped") or "",
    "company": lambda o: (o.get("company") or "").casefold(),
}


def _sort_items(items: list[dict], keyfuncs: dict, sort: str | None, dir_: str | None, default: str) -> list[dict]:
    """Tri générique : `sort` doit être une clé connue de `keyfuncs`, repli sur `default`
    si absent/inconnu (jamais d'erreur 422 pour un paramètre de tri invalide — un lien mal
    formé ne doit pas casser la page)."""
    key = keyfuncs.get(sort or default, keyfuncs[default])
    reverse = (dir_ or "desc") != "asc"
    return sorted(items, key=key, reverse=reverse)


def _scrape_sessions(offers: list[dict]) -> list[str]:
    """Dates `date_scraped` distinctes des offres passées, triées décroissant.

    Même calcul que `JobSearchApp._scrape_sessions` (`jobsearch/tui.py`) — inspiré de
    cette méthode sans importer `tui.py` (Textual, démarrage lourd, RÈGLE STRUCTURANTE
    du module en tête de fichier)."""
    dates = set()
    for o in offers:
        d = o.get("date_scraped", "")
        if d and len(d) == 10 and d[4] == "-" and d[7] == "-":
            dates.add(d)
    return sorted(dates, reverse=True)


def _get_token() -> str:
    """Lit `JOBSEARCH_API_TOKEN` depuis `config/.env`, comme les autres modules (voir
    `filter.py`/`generator.py`/`notifier.py`/`scraper.py` : `load_dotenv(PATHS.env_path,
    override=True)`)."""
    load_dotenv(PATHS.env_path, override=True)
    return os.environ.get("JOBSEARCH_API_TOKEN", "").strip()


def build_app(*, host: str = "127.0.0.1", allow_write: bool = False) -> FastAPI:
    """Construit l'application FastAPI. Lève `SystemExit` si l'écoute n'est pas sur la
    boucle locale et qu'aucun jeton n'est défini — un échec explicite au démarrage vaut
    mieux qu'un suivi de candidatures ouvert sur le réseau sans protection."""
    token = _get_token()
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if not is_loopback and not token:
        raise SystemExit(
            f"Refus de démarrer : écoute demandée sur {host!r} (pas la boucle locale) "
            "sans JOBSEARCH_API_TOKEN défini dans config/.env. Définis un jeton, ou "
            "relance sur 127.0.0.1."
        )

    app = FastAPI(title="JobSearch", docs_url=None, redoc_url=None)
    app.state.allow_write = allow_write
    app.state.token = token

    # ------------------------------------------------------------------ #
    # Dépendances d'authentification
    # ------------------------------------------------------------------ #
    def _check_api_token(x_api_token: str = Header(default="")) -> None:
        """En-tête `X-API-Token` requis sur `/api/*` dès qu'un jeton est configuré.
        Sans jeton configuré (usage boucle locale sans risque réseau), aucune vérif."""
        if app.state.token and x_api_token != app.state.token:
            raise HTTPException(status_code=401, detail="Jeton manquant ou invalide (en-tête X-API-Token).")

    def _check_page_cookie(request: Request, token_qs: str | None = Query(default=None, alias="token")) -> str | None:
        """Cookie posé via `?token=...` en query string sur la première visite. Retourne
        le jeton à poser en cookie sur la réponse (None si rien à poser)."""
        if not app.state.token:
            return None
        cookie_val = request.cookies.get(COOKIE_NAME, "")
        if cookie_val == app.state.token:
            return None
        if token_qs == app.state.token:
            return app.state.token  # à poser sur la réponse
        raise HTTPException(status_code=401, detail="Jeton manquant ou invalide (?token=... requis).")

    def _require_write() -> None:
        if not app.state.allow_write:
            raise HTTPException(status_code=403, detail="Écriture désactivée — relancer avec --allow-write.")

    # ------------------------------------------------------------------ #
    # Santé — sans authentification (sonde)
    # ------------------------------------------------------------------ #
    @app.get("/health")
    def health():
        return {"status": "ok"}

    # ------------------------------------------------------------------ #
    # API JSON
    # ------------------------------------------------------------------ #
    @app.get("/api/applications")
    def api_applications(
        x_api_token: str = Header(default=""),
        status: str | None = None,
        company: str | None = None,
        q: str | None = None,
        since: str | None = None,
        sort: str | None = None,
        dir: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ):
        _check_api_token(x_api_token)
        apps = load_applications(PATHS.data_dir)
        if status:
            apps = [a for a in apps if a.get("status") == status]
        if company:
            needle = company.casefold()
            apps = [a for a in apps if needle in (a.get("company") or "").casefold()]
        if q:
            # `q` couvre AUSSI les notes, délibérément : échéances, dates
            # d'entretien et noms de recruteurs n'ont pas de champ dédié et
            # vivent en texte libre. Sans ça, une question dont le critère est
            # dans la note (« quelles candidatures ont une deadline ? »)
            # obligerait l'agent à rapatrier toutes les candidatures pour les
            # lire — exactement ce que le filtrage côté serveur doit éviter.
            needle = q.casefold()
            apps = [
                a for a in apps
                if needle in (a.get("company") or "").casefold()
                or needle in (a.get("title") or "").casefold()
                or needle in (a.get("notes") or "").casefold()
            ]
        if since:
            apps = [a for a in apps if (a.get("generated_at") or "") >= since]
        apps = _sort_items(apps, _APP_SORT_KEYS, sort, dir, default="generated_at")
        apps = apps[: _clamp_limit(limit)]
        return {"count": len(apps), "applications": [application_dict(a, PATHS.data_dir) for a in apps]}

    @app.get("/api/applications/{offer_id}")
    def api_application_detail(offer_id: str, x_api_token: str = Header(default="")):
        _check_api_token(x_api_token)
        dossier = dossier_dict(offer_id, PATHS.data_dir, PATHS.output_dir)
        if dossier["application"] is None and dossier["offer"] is None:
            raise HTTPException(status_code=404, detail=f"Aucune candidature ni offre pour {offer_id!r}.")
        return dossier

    @app.post("/api/applications/{offer_id}/status")
    def api_mark_status(offer_id: str, payload: dict, x_api_token: str = Header(default="")):
        _check_api_token(x_api_token)
        _require_write()
        status = payload.get("status", "")
        note = payload.get("note", "")
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail=f"Statut invalide. Valides : {', '.join(STATUSES)}")
        try:
            # Passe par tracker.mark_status : ensure_history() y est appelée AVANT toute
            # mutation (piège documenté dans CLAUDE.md) — jamais de réimplémentation ici.
            app_rec = mark_status(offer_id, status, note=note, data_dir=PATHS.data_dir)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return application_dict(app_rec, PATHS.data_dir)

    @app.get("/api/offers")
    def api_offers(
        x_api_token: str = Header(default=""),
        q: str | None = None,
        min_score: int | None = None,
        since: str | None = None,
        session: str | None = None,
        sort: str | None = None,
        dir: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ):
        _check_api_token(x_api_token)
        offers = load_filtered_offers(PATHS.data_dir)
        offers = [o for o in offers if not o.get("duplicate_of") and not o.get("invalid")]
        if min_score is not None:
            offers = [o for o in offers if o.get("filter_result", {}).get("score", 0) >= min_score]
        if since:
            offers = [o for o in offers if (o.get("date_scraped") or "") >= since]
        if session:
            offers = [o for o in offers if o.get("date_scraped") == session]
        if q:
            needle = q.casefold()
            offers = [
                o for o in offers
                if needle in (o.get("title") or "").casefold() or needle in (o.get("company") or "").casefold()
            ]
        offers = _sort_items(offers, _OFFER_SORT_KEYS, sort, dir, default="score")
        offers = offers[: _clamp_limit(limit)]
        return {"count": len(offers), "offers": [offer_dict(o) for o in offers]}

    @app.get("/api/offers/{offer_id}")
    def api_offer_detail(offer_id: str, x_api_token: str = Header(default="")):
        _check_api_token(x_api_token)
        offer = load_offer(offer_id, PATHS.data_dir)
        if offer is None:
            raise HTTPException(status_code=404, detail=f"Offre introuvable : {offer_id!r}.")
        return offer_dict(offer, full=True)

    @app.get("/api/stats")
    def api_stats(x_api_token: str = Header(default="")):
        _check_api_token(x_api_token)
        return compute_stats(PATHS.data_dir)

    # ------------------------------------------------------------------ #
    # Pages web (iPhone) — Jinja2, CSS inline, une colonne, sans JS
    # ------------------------------------------------------------------ #
    def _render(template_name: str, **ctx) -> str:
        # Alerte « mise à jour disponible » dans le pied de page — même fonction que la
        # TUI (`status_notice`, sans la temporisation 15 min de la CLI : la page web
        # reste ouverte, pas relancée à chaque requête). Toute exception déjà avalée.
        from jobsearch.updates import status_notice

        template = _env.get_template(template_name)
        return template.render(allow_write=app.state.allow_write, update_notice=status_notice(), **ctx)

    def _page_response(
        html: str, token_to_set: str | None, request: Request | None = None
    ) -> HTMLResponse | RedirectResponse:
        """Pose le cookie de session, et RETIRE le jeton de l'URL au passage.

        Sans cette redirection, le `?token=...` de la première visite reste dans
        l'historique du navigateur et dans le titre des onglets partagés — un
        secret durable dans un endroit qu'on ne pense jamais à nettoyer. On pose
        donc le cookie sur une 303 vers le même chemin sans query string : la
        visite suivante passe par le cookie seul.
        """
        if token_to_set and request is not None:
            resp = RedirectResponse(url=request.url.path, status_code=303)
            resp.set_cookie(COOKIE_NAME, token_to_set, httponly=True, samesite="lax")
            return resp
        resp = HTMLResponse(html)
        if token_to_set:
            resp.set_cookie(COOKIE_NAME, token_to_set, httponly=True, samesite="lax")
        return resp

    @app.get("/", response_class=HTMLResponse)
    def page_index(
        request: Request,
        token: str | None = Query(default=None),
        sort: str = "generated_at",
        dir: str = "desc",
    ):
        token_to_set = _check_page_cookie(request, token)
        apps = _sort_items(load_applications(PATHS.data_dir), _APP_SORT_KEYS, sort, dir, default="generated_at")
        apps = [application_dict(a, PATHS.data_dir) for a in apps]
        html = _render("index.html", applications=apps, active="apps", sort=sort, dir=dir)
        return _page_response(html, token_to_set, request)

    @app.get("/a/{offer_id}", response_class=HTMLResponse)
    def page_dossier(offer_id: str, request: Request, token: str | None = Query(default=None)):
        token_to_set = _check_page_cookie(request, token)
        dossier = dossier_dict(offer_id, PATHS.data_dir, PATHS.output_dir)
        if dossier["application"] is None and dossier["offer"] is None:
            raise HTTPException(status_code=404, detail=f"Aucune candidature ni offre pour {offer_id!r}.")
        html = _render("dossier.html", d=dossier, statuses=STATUSES, active="apps")
        return _page_response(html, token_to_set, request)

    @app.get("/offers", response_class=HTMLResponse)
    def page_offers(
        request: Request,
        token: str | None = Query(default=None),
        sort: str = "score",
        dir: str = "desc",
        session: str | None = None,
    ):
        token_to_set = _check_page_cookie(request, token)
        all_offers = load_filtered_offers(PATHS.data_dir)
        all_offers = [o for o in all_offers if not o.get("duplicate_of") and not o.get("invalid")]
        sessions = _scrape_sessions(all_offers)
        offers = all_offers
        if session:
            offers = [o for o in offers if o.get("date_scraped") == session]
        offers = _sort_items(offers, _OFFER_SORT_KEYS, sort, dir, default="score")
        offers = [offer_dict(o) for o in offers[:100]]
        html = _render(
            "offers.html",
            offers=offers,
            active="offers",
            sort=sort,
            dir=dir,
            session=session or "",
            sessions=sessions,
        )
        return _page_response(html, token_to_set, request)

    @app.post("/a/{offer_id}/status")
    async def page_mark_status(offer_id: str, request: Request):
        """Formulaire HTML (boutons de `dossier.html`) — même garde-fous que la route JSON :
        cookie valide requis, et `--allow-write` sinon 403 — avant toute écriture. Passe
        par `tracker.mark_status` comme la route JSON, jamais de réimplémentation."""
        _check_page_cookie(request, request.query_params.get("token"))
        _require_write()
        form = await request.form()
        status = form.get("status", "")
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail=f"Statut invalide. Valides : {', '.join(STATUSES)}")
        try:
            mark_status(offer_id, status, data_dir=PATHS.data_dir)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return RedirectResponse(url=f"/a/{offer_id}", status_code=303)

    @app.post("/a/{offer_id}/note")
    async def page_set_note(offer_id: str, request: Request):
        """Édition de la note libre depuis le téléphone — mêmes garde-fous que le
        changement de statut (cookie valide, puis `--allow-write`) avant écriture.

        C'est le pendant indispensable de l'affichage : une échéance ou une date
        d'entretien se repère en lisant un mail sur son téléphone, pas devant un
        terminal. Sans cette route, il faudrait attendre de rentrer pour la noter.
        """
        _check_page_cookie(request, request.query_params.get("token"))
        _require_write()
        form = await request.form()
        try:
            set_note(offer_id, str(form.get("note", "")))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return RedirectResponse(url=f"/a/{offer_id}", status_code=303)

    @app.get("/stats", response_class=HTMLResponse)
    def page_stats(request: Request, token: str | None = Query(default=None)):
        token_to_set = _check_page_cookie(request, token)
        stats = compute_stats(PATHS.data_dir)
        svg = render_sankey_svg(load_applications(PATHS.data_dir))
        html = _render("stats.html", stats=stats, sankey_svg=svg, active="stats")
        return _page_response(html, token_to_set, request)

    return app


def run_server(
    host: str = "127.0.0.1", port: int = 8420, allow_write: bool = False, log_level: str = "info"
) -> None:
    """Point d'entrée de `jobsearch --serve` (`jobsearch/cli.py`).

    `log_level` par défaut à "info" (pas "warning") : uvicorn n'émet SA LIGNE DE
    DÉMARRAGE et son journal d'accès qu'au niveau "info" — avec "warning", le fichier
    de log d'un service launchd restait vide en permanence, invisible quand le service
    tombait silencieusement. Gardé paramétrable pour un usage interactif plus silencieux
    si besoin (`--log-level warning`).
    """
    import uvicorn

    app = build_app(host=host, allow_write=allow_write)
    if not app.state.token:
        print("⚠️  Aucun JOBSEARCH_API_TOKEN défini (config/.env) — API accessible sans jeton.")
        if host in ("127.0.0.1", "localhost", "::1"):
            print("    Sans risque en boucle locale, mais recommandé avant toute exposition Tailscale.")
    if allow_write:
        print("⚠️  Écriture activée (--allow-write) — POST /api/applications/{id}/status est actif.")
    print(f"→ JobSearch sert sur http://{host}:{port} (Ctrl+C pour arrêter)")
    uvicorn.run(app, host=host, port=port, log_level=log_level)
