"""Résolution des modèles Claude (`auto:<famille>` ou identifiant épinglé), cache de la liste
des modèles, avis de bascule, extraction de texte robuste et écriture dans `search.yaml`.

Sans Textual ni import Anthropic au niveau module (importable depuis `config.py`, `server.py`,
`mcp_server.py`). Même règle que `updates.py` : **ne jamais nuire** — `resolve_model` ne lève
jamais, toute erreur réseau/disque est avalée.

Format d'un réglage (`models.<tâche>.model` dans `search.yaml`) :
- `auto:haiku` / `auto:sonnet` / `auto:opus` : le plus récent de la famille (cache 24 h) ;
- un identifiant exact (`claude-sonnet-5-5`) : modèle épinglé ;
- `local:<modèle>` : RÉSERVÉ à la phase « modèle local » (Ollama), refusé pour l'instant.
"""
from __future__ import annotations

import os
import re
import time

TASKS = ("filter", "letter", "cv")
TASK_LABELS = {"filter": "filtre", "letter": "lettre", "cv": "CV"}
FAMILIES = ("haiku", "sonnet", "opus")

_FALLBACK = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}

_REFRESH_INTERVAL = 24 * 3600  # liste des modèles rafraîchie au plus une fois par jour
_RETRY_INTERVAL = 3600         # après un échec réseau, nouvel essai au bout d'une heure
_TIMEOUT = 5                   # secondes


class ModelSpecError(ValueError):
    """Réglage de modèle invalide ou pas encore pris en charge (`local:`)."""


class ModelResponseError(RuntimeError):
    """Réponse inexploitable : refus du modèle ou sortie tronquée."""


# ---------------------------------------------------------------------------
# Cache (data/models_cache.json) — même motif que updates.py
# ---------------------------------------------------------------------------

def _cache_path() -> str:
    from jobsearch.config import PATHS

    return os.path.join(PATHS.data_dir, "models_cache.json")


def _load_cache() -> dict:
    import json

    try:
        with open(_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    import json

    try:
        path = _cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass  # le cache n'est qu'une optimisation


def _fetch_models() -> list[dict] | None:
    """Un appel `client.models.list()` (non facturé). None si impossible (clé absente,
    hors ligne, erreur API…)."""
    try:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        import anthropic

        client = anthropic.Anthropic(timeout=_TIMEOUT, max_retries=0)
        out = []
        for m in client.models.list(limit=100):
            created = getattr(m, "created_at", None)
            out.append({
                "id": m.id,
                "display_name": getattr(m, "display_name", None) or m.id,
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
            })
        return out or None
    except Exception:
        return None


def _refresh_if_stale(cache: dict, *, force: bool = False) -> dict:
    now = time.time()
    fresh = (now - cache.get("fetched_at", 0)) < _REFRESH_INTERVAL and cache.get("models")
    recently_tried = (now - cache.get("attempted_at", 0)) < _RETRY_INTERVAL
    if not force and (fresh or recently_tried):
        return cache
    models = _fetch_models()
    cache["attempted_at"] = now
    if models:
        cache["models"] = models
        cache["fetched_at"] = now
    _save_cache(cache)
    return cache


# ---------------------------------------------------------------------------
# Résolution
# ---------------------------------------------------------------------------

_DATED_RE = re.compile(r"^(?P<base>.+)-\d{8}$")


def parse_spec(spec: str) -> tuple[str, str]:
    """Retourne ("auto", famille) ou ("pinned", id). Lève ModelSpecError sinon."""
    spec = (spec or "").strip()
    if not spec:
        raise ModelSpecError("réglage de modèle vide")
    if spec.startswith("local:"):
        raise ModelSpecError(
            f"{spec!r} : les modèles locaux (`local:`) ne sont pas encore pris en charge "
            "(phase ultérieure) — choisir `auto:<famille>` ou un identifiant Claude."
        )
    if spec.startswith("auto:"):
        family = spec[5:].strip().lower()
        if family not in FAMILIES:
            raise ModelSpecError(f"{spec!r} : famille inconnue (attendu : {', '.join('auto:' + f for f in FAMILIES)}).")
        return "auto", family
    return "pinned", spec


def _family_of(model_id: str) -> str | None:
    for fam in FAMILIES:
        if model_id.startswith(f"claude-{fam}-"):
            return fam
    return None


def _latest_in_family(models: list[dict], family: str) -> str | None:
    ids = {m["id"] for m in models if isinstance(m, dict) and m.get("id")}
    cands = []
    for m in models:
        mid = m.get("id") if isinstance(m, dict) else None
        if not mid or not mid.startswith(f"claude-{family}-"):
            continue
        dated = _DATED_RE.match(mid)
        if dated and dated.group("base") in ids:
            continue  # l'alias sans date existe : on le préfère au snapshot daté
        cands.append((m.get("created_at") or "", -len(mid), mid))
    if not cands:
        return None
    return max(cands)[2]


def resolve_model(spec: str, *, refresh: bool = True) -> str:
    """Identifiant concret pour un réglage. Ne lève jamais (sauf `local:`/format invalide,
    remonté explicitement — c'est une erreur de config, pas de réseau)."""
    kind, value = parse_spec(spec)
    if kind == "pinned":
        return value
    try:
        cache = _load_cache()
        if refresh:
            cache = _refresh_if_stale(cache)
        resolved = cache.get("resolved") if isinstance(cache.get("resolved"), dict) else {}
        latest = _latest_in_family(cache.get("models") or [], value)
        if latest is None:
            return resolved.get(spec) or _FALLBACK[value]
        previous = resolved.get(spec)
        if previous != latest:
            resolved[spec] = latest
            cache["resolved"] = resolved
            if previous:
                notices = cache.get("pending_notices")
                notices = notices if isinstance(notices, list) else []
                notices.append({"spec": spec, "from": previous, "to": latest,
                                "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
                cache["pending_notices"] = notices
            _save_cache(cache)
        return latest
    except Exception:
        return _FALLBACK[value]


def list_available(refresh: bool = False) -> list[dict]:
    """Modèles du cache (`id`, `display_name`, `created_at`), triés par famille puis date
    décroissante. `refresh=True` force un appel réseau non facturé."""
    try:
        cache = _refresh_if_stale(_load_cache(), force=refresh)
        models = [m for m in (cache.get("models") or []) if isinstance(m, dict) and m.get("id")]
    except Exception:
        models = []

    def key(m):
        fam = _family_of(m["id"])
        return (FAMILIES.index(fam) if fam else len(FAMILIES), _neg(m.get("created_at") or ""))

    return sorted(models, key=key)


def _neg(s: str) -> tuple:
    return tuple(-ord(c) for c in s)


# ---------------------------------------------------------------------------
# Avis de bascule
# ---------------------------------------------------------------------------

def model_change_notice() -> str | None:
    """Message « Modèle mis à jour : A → B (lettre) », renvoyé UNE fois puis effacé.
    Ne lève jamais."""
    try:
        from jobsearch.config import get_search

        search = get_search()
        for task in TASKS:  # déclenche la résolution (donc détecte une bascule)
            try:
                search.model_for(task)
            except Exception:
                pass
        cache = _load_cache()
        notices = cache.get("pending_notices")
        if not isinstance(notices, list) or not notices:
            return None
        parts = []
        for n in notices:
            tasks = [TASK_LABELS[t] for t in TASKS if getattr(search, f"{t}_model", None) == n.get("spec")]
            suffix = f" ({', '.join(tasks)})" if tasks else ""
            parts.append(f"{n.get('from')} → {n.get('to')}{suffix}")
        cache["pending_notices"] = []
        _save_cache(cache)
        return "Modèle mis à jour : " + " ; ".join(parts)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Extraction du texte d'une réponse
# ---------------------------------------------------------------------------

def extract_text(response, task: str = "") -> str:
    """Concatène les blocs `type == "text"`. Piège : avec un modèle qui réfléchit
    (Sonnet 5.5…), `content[0]` est un bloc de réflexion, pas du texte."""
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        details = getattr(response, "stop_details", None)
        cat = getattr(details, "category", None) if details is not None else None
        raise ModelResponseError(f"Le modèle a refusé de répondre (catégorie : {cat or 'inconnue'}).")
    if stop == "max_tokens":
        where = f"models.{task}.max_tokens" if task else "models.<tâche>.max_tokens"
        raise ModelResponseError(
            f"Réponse tronquée (max_tokens atteint, réflexion incluse) — augmenter `{where}` dans search.yaml."
        )
    text = "".join(b.text for b in (response.content or []) if getattr(b, "type", None) == "text")
    return text


# ---------------------------------------------------------------------------
# Écriture dans search.yaml (ligne à ligne, jamais yaml.safe_dump)
# ---------------------------------------------------------------------------

def _is_content(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#")


def set_task_model(task: str, spec: str, config_dir: str | None = None) -> None:
    """Écrit `models.<task>.model: "<spec>"` dans `search.yaml`, en préservant les
    commentaires. Insère le sous-bloc de la tâche s'il est absent."""
    from jobsearch.config import PATHS

    if task not in TASKS:
        raise ValueError(f"Tâche inconnue : {task!r}")
    parse_spec(spec)  # refuse local: et les formats invalides
    config_dir = config_dir or PATHS.config_dir
    path = os.path.join(config_dir, "search.yaml")
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")

    new_model = f'    model: "{spec}"'
    head = next((i for i, l in enumerate(lines) if re.match(r"^models:\s*(#.*)?$", l)), None)
    if head is None:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", "models:", f"  {task}:", new_model, ""]
    else:
        end = len(lines)
        for j in range(head + 1, len(lines)):
            if _is_content(lines[j]) and not lines[j].startswith((" ", "\t")):
                end = j
                break
        # ne pas avaler les blancs/commentaires de fin de bloc
        while end > head + 1 and not _is_content(lines[end - 1]):
            end -= 1
        t_head = next((i for i in range(head + 1, end) if re.match(rf"^  {task}:\s*(#.*)?$", lines[i])), None)
        if t_head is None:
            lines[end:end] = [f"  {task}:", new_model]
        else:
            t_end = end
            for j in range(t_head + 1, end):
                if _is_content(lines[j]) and len(lines[j]) - len(lines[j].lstrip()) <= 2:
                    t_end = j
                    break
            m_idx = next((i for i in range(t_head + 1, t_end) if re.match(r"^    model:", lines[i])), None)
            if m_idx is None:
                lines.insert(t_head + 1, new_model)
            else:
                comment = re.search(r"\s+#.*$", lines[m_idx])
                lines[m_idx] = new_model + (comment.group(0) if comment else "")

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)
