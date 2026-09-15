"""Serveur MCP (Model Context Protocol) pour consulter/suivre les candidatures depuis un
client MCP (`jobsearch --mcp`) — extension du même socle que `jobsearch/server.py`
(API HTTP), mais adressé aux clients MCP (Claude Code, app Claude) plutôt qu'à un
navigateur/agent HTTP générique.

Dépend de l'extra optionnel `jobsearch[mcp]` (paquet `mcp`) — même motif que
`jobsearch[server]`/`jobsearch[jobspy]` : import paresseux dans `cli.py`, jamais au
niveau module ailleurs dans le paquet.

RÈGLE STRUCTURANTE (identique à `server.py`) : ce module n'importe JAMAIS
`jobsearch/tui.py` (Textual, démarrage lourd) ni `jobsearch/generator.py` (dépendance
Anthropic, appels API payants). Les seules sources de données sont `tracker`,
`duplicates`, `report`, `serialize`, `config`.

EXACTEMENT 4 OUTILS, volontairement — chaque outil déclaré coûte du contexte dans
*toutes* les conversations d'un client MCP, y compris celles sans rapport avec la
recherche d'emploi : `list_applications`, `get_application`, `search_offers`,
`mark_application_status`. Ne pas en ajouter un cinquième sans repenser ce compromis.

Pagination : mêmes bornes que l'API HTTP (`DEFAULT_LIMIT`/`MAX_LIMIT` de
`jobsearch/server.py`, dupliquées ici plutôt qu'importées pour ne pas tirer FastAPI dans
les dépendances de l'extra `[mcp]`) — aucun outil ne doit pouvoir renvoyer les ~5430
offres d'un coup à un modèle.

Sécurité réseau (transport streamable-http uniquement — le transport stdio n'a pas
cette problématique, le processus est lancé localement par le client) : même garde-fou
que `jobsearch/server.py::build_app` — refus de démarrer sur une adresse non-loopback
sans `JOBSEARCH_API_TOKEN` défini. Le SDK MCP 2.2.0 n'offre pas de mécanisme
d'authentification par jeton simple pour ce transport (voir `run_mcp_streamable_http` :
seul un `TokenVerifier`/`AuthSettings` OAuth complet est prévu, hors de portée d'un
jeton partagé comme celui de `--serve`) — documenté ici et dans le README : ce mode ne
doit être exposé que derrière un tunnel de confiance (Tailscale), jamais nu sur
Internet.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from jobsearch.config import PATHS
from jobsearch.duplicates import load_offer
from jobsearch.report import load_filtered_offers
from jobsearch.serialize import application_dict, dossier_dict, offer_dict
from jobsearch.tracker import STATUSES, load_applications, mark_status

# Mêmes bornes que jobsearch/server.py (DEFAULT_LIMIT/MAX_LIMIT) — dupliquées
# volontairement pour ne pas importer server.py (donc FastAPI) depuis l'extra [mcp].
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def _get_token() -> str:
    """Lit `JOBSEARCH_API_TOKEN` depuis `config/.env`, comme `jobsearch/server.py`."""
    load_dotenv(PATHS.env_path, override=True)
    return os.environ.get("JOBSEARCH_API_TOKEN", "").strip()


def build_server(*, allow_write: bool = False):
    """Construit le `MCPServer` avec ses 4 outils. Fonction pure (pas d'I/O réseau) :
    testable sans démarrer de transport, sur le modèle de `server.build_app`."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer(
        name="jobsearch",
        title="JobSearch — suivi de candidatures",
        description=(
            "Accès en lecture (et, si autorisé, en écriture de statut) au suivi des "
            "candidatures de recherche d'emploi/stage de l'utilisateur : entreprises, "
            "offres, lettres de motivation envoyées, statuts, notes libres (échéances, "
            "dates d'entretien, noms de recruteurs)."
        ),
        instructions=(
            "Pour répondre à une question sur une candidature, une entreprise, une "
            "relance, un entretien ou une échéance : commence TOUJOURS par restreindre "
            "côté serveur avec list_applications (filtre q/status/company/since), puis "
            "lis le petit sous-ensemble obtenu avec get_application si besoin de détail "
            "(lettre envoyée, offre complète). Ne jamais appeler list_applications sans "
            "filtre dans l'espoir de tout lire ensuite : la liste est plafonnée et "
            "volumineuse. Les échéances, dates d'entretien et noms de recruteurs ne sont "
            "PAS dans des champs dédiés : ils vivent dans le champ de notes en texte "
            "libre de chaque candidature, et list_applications/get_application y "
            "cherchent aussi via le paramètre q."
        ),
        version="1.2.0",
    )

    @server.tool(
        name="list_applications",
        title="Lister les candidatures de recherche d'emploi",
        description=(
            "Recherche et filtre les candidatures envoyées (recherche de stage/emploi) : "
            "entreprise, poste, statut (generated/sent/answered/interview/rejected/"
            "accepted/cancelled), date. Retourne pour chacune l'entreprise, le titre, le "
            "statut, les dates clés et la NOTE LIBRE — c'est dans cette note que vivent "
            "les échéances de réponse, les dates d'entretien et les noms de recruteurs, "
            "aucun champ dédié n'existe pour eux. Le paramètre q cherche dans "
            "entreprise + titre + CETTE NOTE : pour « quelle est la deadline chez X ? » "
            "ou « quand était l'entretien Farmless ? », utilise q=\"<entreprise ou mot-clé>\" "
            "et lis les notes retournées plutôt que de conclure que l'info n'existe pas. "
            "Toujours filtrer avant de lire — jamais appeler sans filtre pour tout "
            "rapatrier, la liste est plafonnée."
        ),
    )
    def list_applications(
        q: str | None = None,
        status: str | None = None,
        company: str | None = None,
        since: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        apps = load_applications(PATHS.data_dir)
        if status:
            apps = [a for a in apps if a.get("status") == status]
        if company:
            needle = company.casefold()
            apps = [a for a in apps if needle in (a.get("company") or "").casefold()]
        if q:
            # `q` couvre aussi les notes — mêmes motifs que jobsearch/server.py::api_applications :
            # échéances/entretiens/recruteurs vivent en texte libre, pas dans un champ dédié.
            needle = q.casefold()
            apps = [
                a for a in apps
                if needle in (a.get("company") or "").casefold()
                or needle in (a.get("title") or "").casefold()
                or needle in (a.get("notes") or "").casefold()
            ]
        if since:
            apps = [a for a in apps if (a.get("generated_at") or "") >= since]
        apps = sorted(apps, key=lambda a: a.get("generated_at") or "", reverse=True)
        apps = apps[: _clamp_limit(limit)]
        return {"count": len(apps), "applications": [application_dict(a, PATHS.data_dir) for a in apps]}

    @server.tool(
        name="get_application",
        title="Détail d'une candidature (dossier complet)",
        description=(
            "Retourne le dossier complet d'UNE candidature identifiée par son offer_id "
            "(obtenu via list_applications ou search_offers) : le statut et l'historique "
            "des changements, la NOTE LIBRE (échéances, dates d'entretien, noms de "
            "recruteurs — pas de champ dédié pour ces infos), l'offre d'origine "
            "(description, entreprise, lieu), et le texte de la lettre de motivation déjà "
            "envoyée. C'est l'outil à utiliser pour « rappelle-moi le sujet de cette "
            "candidature » ou pour rédiger un mail de relance/suivi cohérent avec la "
            "lettre déjà envoyée — toujours après avoir localisé l'offer_id avec "
            "list_applications, jamais en devinant l'id."
        ),
    )
    def get_application(offer_id: str) -> dict:
        return dossier_dict(offer_id, PATHS.data_dir, PATHS.output_dir)

    @server.tool(
        name="search_offers",
        title="Chercher des offres d'emploi/stage collectées",
        description=(
            "Cherche parmi les offres d'emploi/stage collectées par le pipeline de "
            "scraping (PAS encore candidatées) : titre, entreprise, score de pertinence "
            "Claude (0-10). Exclut automatiquement les offres déjà candidatées, les "
            "doublons manuels et les offres marquées invalides (fermées/annulées/mauvais "
            "type de contrat) — cet outil sert à explorer ce qu'il RESTE à considérer, "
            "pas l'historique des candidatures (voir list_applications pour ça). Filtrer "
            "avec q/min_score/since avant de lire : la liste est plafonnée."
        ),
    )
    def search_offers(
        q: str | None = None,
        min_score: int | None = None,
        since: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        offers = load_filtered_offers(PATHS.data_dir)
        offers = [o for o in offers if not o.get("duplicate_of") and not o.get("invalid")]
        if min_score is not None:
            offers = [o for o in offers if o.get("filter_result", {}).get("score", 0) >= min_score]
        if since:
            offers = [o for o in offers if (o.get("date_scraped") or "") >= since]
        if q:
            needle = q.casefold()
            offers = [
                o for o in offers
                if needle in (o.get("title") or "").casefold() or needle in (o.get("company") or "").casefold()
            ]
        offers = sorted(offers, key=lambda o: o.get("filter_result", {}).get("score", 0), reverse=True)
        offers = offers[: _clamp_limit(limit)]
        return {"count": len(offers), "offers": [offer_dict(o) for o in offers]}

    @server.tool(
        name="mark_application_status",
        title="Changer le statut d'une candidature",
        description=(
            "Met à jour le statut de suivi d'une candidature (generated/sent/answered/"
            "interview/rejected/accepted/cancelled) avec une note optionnelle, et "
            "consigne l'événement dans son historique. À utiliser uniquement sur "
            "confirmation explicite de l'utilisateur (« marque-la comme envoyée », "
            "« note que j'ai eu un entretien ») — jamais de façon spéculative. Nécessite "
            "que le serveur ait été démarré avec --allow-write ; sinon cet appel échoue."
        ),
    )
    def mark_application_status(offer_id: str, status: str, note: str = "") -> dict:
        # `ToolError` et non PermissionError/ValueError : le SDK ne transmet le message
        # au client QUE pour ToolError — toute autre exception est enveloppée dans un
        # `UnexpectedToolError: Error executing tool ...` qui avale le motif. L'agent
        # dirait alors « ça a échoué » sans pouvoir expliquer qu'il manque --allow-write.
        if not allow_write:
            raise ToolError(
                "Écriture désactivée sur ce serveur MCP. Relancer `jobsearch --mcp "
                "--allow-write` pour autoriser le changement de statut."
            )
        if status not in STATUSES:
            raise ToolError(f"Statut invalide {status!r}. Valides : {', '.join(STATUSES)}")
        # Passe par tracker.mark_status : ensure_history() y est appelée AVANT toute
        # mutation (piège documenté dans CLAUDE.md) — jamais de réimplémentation ici.
        app_rec = mark_status(offer_id, status, note=note, data_dir=PATHS.data_dir)
        return application_dict(app_rec, PATHS.data_dir)

    return server


def run_mcp_stdio(*, allow_write: bool = False) -> None:
    """Point d'entrée de `jobsearch --mcp` (transport stdio, cas principal : Claude Code
    / app Claude tournant sur la même machine que le pipeline)."""
    server = build_server(allow_write=allow_write)
    server.run(transport="stdio")


def run_mcp_streamable_http(
    *, host: str = "127.0.0.1", port: int = 8421, allow_write: bool = False
) -> None:
    """Point d'entrée de `jobsearch --mcp --mcp-transport streamable-http` (agent
    distant). Même garde-fou réseau que `jobsearch/server.py::build_app` : refus de
    démarrer sur une adresse non-loopback sans `JOBSEARCH_API_TOKEN` défini.

    Limite assumée (voir docstring de module) : le SDK MCP 2.2.0 ne propose pas de
    vérification par jeton partagé simple pour ce transport (seul un `TokenVerifier`
    OAuth complet est prévu) — ce jeton n'est donc PAS branché ici comme il l'est pour
    `--serve`. Le garde-fou se limite à refuser un démarrage non-loopback sans jeton
    configuré, pour rester cohérent avec `--serve`, mais n'authentifie pas réellement
    les requêtes de ce transport : à n'exposer que derrière un tunnel de confiance
    (Tailscale), jamais nu sur Internet.
    """
    token = _get_token()
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if not is_loopback and not token:
        raise SystemExit(
            f"Refus de démarrer : écoute demandée sur {host!r} (pas la boucle locale) "
            "sans JOBSEARCH_API_TOKEN défini dans config/.env. Le transport "
            "streamable-http du SDK MCP 2.2.0 n'authentifie pas les requêtes par jeton "
            "partagé (voir jobsearch/mcp_server.py) — définis un jeton et n'expose ce "
            "mode que derrière un tunnel de confiance (Tailscale), ou relance sur "
            "127.0.0.1."
        )
    if not token:
        print(
            "⚠️  Aucun JOBSEARCH_API_TOKEN défini — le transport streamable-http MCP "
            "n'authentifie de toute façon pas les requêtes (limite du SDK 2.2.0). "
            "N'expose ce mode que derrière Tailscale."
        )
    server = build_server(allow_write=allow_write)
    import asyncio

    asyncio.run(server.run_streamable_http_async(host=host, port=port))
