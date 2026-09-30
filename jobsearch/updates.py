"""Alerte « mise à jour disponible » — stdlib uniquement (`urllib.request`, `json`, `os`,
`time`), pour rester importable sans les extras `[server]`/`[mcp]`.

Source de vérité de la version installée : `jobsearch.__version__`. **Ne JAMAIS utiliser
`importlib.metadata.version("jobsearch")`** : en installation éditable, les métadonnées
de distribution sont figées au moment de l'installation et ne suivent pas les changements
de version du code (mesuré : le paquet déclarait 1.2.0 pendant que `importlib.metadata`
répondait encore 0.1.0) — une vérification de mise à jour fondée là-dessus annoncerait
une mise à jour en permanence.

Deux temporisations distinctes, qui ne servent PAS la même chose :
- `checked_at` (`data/update_check.json`) : ne refait l'appel réseau GitHub qu'une fois
  toutes les 24 h. Évite de ralentir CHAQUE lancement d'une commande par une requête
  réseau, et évite de se faire rate-limiter par l'API GitHub anonyme.
- `notified_at` (même fichier) : ne réaffiche le message en CLI qu'une fois toutes les
  15 minutes, même si le cache réseau (24 h) indique toujours qu'une mise à jour est
  disponible — sinon CHAQUE commande CLI republierait le message en boucle.

Comportement impératif — ne jamais nuire au fonctionnement normal de l'outil :
- Délai réseau court (`_TIMEOUT`), TOUTES les exceptions avalées (hors ligne, API
  indisponible, quota dépassé, JSON inattendu…) → aucune alerte, aucune trace d'erreur.
- L'appel réseau ne bloque jamais le démarrage d'une commande au-delà du cache 24 h.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from jobsearch import __version__
from jobsearch.config import PATHS

_RELEASES_URL = "https://api.github.com/repos/Nathan-Albalat/JobSearch/releases/latest"
_TIMEOUT = 3  # secondes — délai réseau court, ne doit jamais bloquer un démarrage
_CHECK_INTERVAL = 24 * 3600  # 24h — fréquence de l'appel réseau
_NOTIFY_INTERVAL = 15 * 60  # 15min — fréquence de la notification CLI

_CACHE_PATH = os.path.join(PATHS.data_dir, "update_check.json")


def _parse_version(tag: str) -> tuple[int, ...]:
    """Normalise un tag GitHub (`v1.3`, `1.10.2`…) en tuple d'entiers comparables —
    jamais une comparaison de chaînes (`"1.10" > "1.9"` est faux en lexicographique)."""
    tag = tag.strip().lstrip("vV")
    parts = []
    for chunk in tag.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _load_cache() -> dict:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _CACHE_PATH)
    except Exception:
        # Écrire le cache n'est qu'une optimisation (évite de refaire l'appel réseau) —
        # un échec d'écriture disque ne doit jamais faire échouer la commande en cours.
        pass


def _fetch_latest_tag() -> str | None:
    """Un seul appel réseau, anonyme, vers l'API GitHub — toute exception avalée."""
    try:
        req = urllib.request.Request(
            _RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "jobsearch-update-check"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        tag = payload.get("tag_name")
        return tag if isinstance(tag, str) and tag else None
    except Exception:
        return None


def check_for_update(*, force: bool = False) -> str | None:
    """Retourne la dernière version disponible (`tag_name` brut, ex. `"v1.3"`) si elle
    est plus récente que `jobsearch.__version__`, sinon `None`.

    N'effectue l'appel réseau qu'une fois toutes les 24h (cache `data/update_check.json`),
    sauf `force=True` (tests). Toute exception réseau/JSON est avalée : retourne `None`
    plutôt que de propager — cette fonction ne doit jamais faire échouer l'appelant.
    """
    try:
        cache = _load_cache()
        now = time.time()
        checked_at = cache.get("checked_at", 0)

        if force or (now - checked_at) >= _CHECK_INTERVAL:
            tag = _fetch_latest_tag()
            cache["checked_at"] = now
            if tag is not None:
                cache["latest_tag"] = tag
            _save_cache(cache)

        latest_tag = cache.get("latest_tag")
        if not latest_tag:
            return None
        if _parse_version(latest_tag) > _parse_version(__version__):
            return latest_tag
        return None
    except Exception:
        # Garde-fou ultime : quelle que soit la cause (cache corrompu, disque plein…),
        # cette fonction ne doit jamais lever — l'alerte de mise à jour ne doit jamais
        # nuire au fonctionnement normal de l'outil.
        return None


def update_message(latest_tag: str) -> str:
    version = latest_tag.strip().lstrip("vV")
    return f"Mise à jour disponible : v{version} (actuelle : {__version__}) — `jobsearch --upgrade`"


_REPO_SPEC = "jobsearch @ git+https://github.com/Nathan-Albalat/JobSearch"


def _install_mode() -> str:
    """`dev` (dépôt cloné, `pip install -e .`), `pipx`, ou `pip` (tout le reste)."""
    from jobsearch.config import PROJECT_ROOT

    if os.path.isdir(os.path.join(PROJECT_ROOT, ".git")):
        return "dev"
    if "pipx" in os.path.normpath(sys.prefix).split(os.sep):
        return "pipx"
    return "pip"


def run_upgrade() -> int:
    """`jobsearch --upgrade` : met à jour l'outil selon son mode d'installation.

    Seul le mode pipx lance la mise à jour lui-même. Les autres affichent la commande :
    - dev : un `git pull` sur un dépôt de travail peut entrer en conflit avec des
      modifications locales, c'est au développeur de le lancer ;
    - pip : l'environnement peut être partagé avec d'autres paquets ;
    - pipx sous Windows : `jobsearch.exe` est verrouillé tant qu'il tourne, pipx ne
      peut pas le remplacer depuis le processus même qu'il met à jour.
    La config et les données (`JOBSEARCH_HOME`) ne sont jamais touchées.
    """
    latest = check_for_update(force=True)
    if latest:
        print(f"Nouvelle version disponible : {latest} (actuelle : {__version__}).")
    else:
        print(f"Version actuelle : {__version__} — aucune release plus récente trouvée "
              "(ou vérification impossible hors ligne).")

    mode = _install_mode()
    if mode == "dev":
        from jobsearch.config import PROJECT_ROOT

        print("Installation de développement (dépôt cloné) : mets à jour à la main —\n"
              f"  git -C \"{PROJECT_ROOT}\" pull\n"
              "  pip install -e .   (dans le venv du dépôt, seulement si les dépendances ont changé)")
        return 0
    if mode == "pip":
        print("Installation pip : lance —\n"
              f"  \"{sys.executable}\" -m pip install --upgrade \"{_REPO_SPEC}\"")
        return 0

    # pipx
    if sys.platform.startswith("win"):
        print("Sous Windows, jobsearch ne peut pas se remplacer lui-même pendant qu'il tourne.\n"
              "Ferme la TUI et lance dans un nouveau terminal :\n  pipx upgrade jobsearch")
        return 0
    if not latest:
        print("Rien à mettre à jour.")
        return 0
    pipx = shutil.which("pipx")
    if not pipx:
        print("pipx introuvable dans le PATH. Lance toi-même : pipx upgrade jobsearch")
        return 1
    print("Lancement de : pipx upgrade jobsearch\n")
    code = subprocess.run([pipx, "upgrade", "jobsearch"], check=False).returncode
    if code != 0:
        print(f"\npipx a échoué (code {code}) — la version installée est inchangée.")
        return code
    print("\nMise à jour terminée. Ta config et tes données (~/.jobsearch/) sont inchangées.")
    try:
        from jobsearch import service

        if os.path.exists(service.plist_path()):
            print("Le service web tourne encore sur l'ancienne version : "
                  "`jobsearch --service restart` pour le relancer.")
    except Exception:
        pass
    return 0


def cli_notice() -> str | None:
    """Message à afficher en CLI, limité à une fois toutes les 15 minutes
    (`notified_at`, distinct du cache réseau 24h de `check_for_update`).

    Toute exception avalée — ne doit jamais empêcher une commande CLI de se terminer.
    """
    try:
        latest_tag = check_for_update()
        if not latest_tag:
            return None
        cache = _load_cache()
        now = time.time()
        notified_at = cache.get("notified_at", 0)
        if (now - notified_at) < _NOTIFY_INTERVAL:
            return None
        cache["notified_at"] = now
        _save_cache(cache)
        return update_message(latest_tag)
    except Exception:
        return None


def status_notice() -> str | None:
    """Message à afficher dans la TUI et le pied de page web — pas de temporisation
    15 min ici (contrairement à `cli_notice`) : ce sont des affichages persistants
    dans une interface déjà ouverte, pas une sortie répétée à chaque commande."""
    try:
        latest_tag = check_for_update()
        return update_message(latest_tag) if latest_tag else None
    except Exception:
        return None
