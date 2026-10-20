"""Persistance de `jobsearch --serve` via un agent launchd utilisateur (`jobsearch --service`).

Stdlib uniquement (`plistlib`, `subprocess`, `os`, `shutil`) — aucune dépendance nouvelle,
importable sans l'extra `[server]` (on doit pouvoir faire `jobsearch --service status`
même si fastapi/uvicorn ne sont pas installés : ce module ne fait qu'écrire un plist et
piloter launchd, il ne démarre jamais lui-même le serveur en process Python).

launchd est propre à macOS : sur toute autre plateforme, les actions expliquent
clairement l'absence de support (suggestion : une unit systemd --user) plutôt que de
planter sur un `FileNotFoundError: launchctl`.

RÈGLE STRUCTURANTE (comme server.py) : ce module n'importe jamais tui.py/generator.py —
il n'a même pas besoin de l'extra [server], il se contente de fabriquer la commande
`jobsearch --serve ...` que launchd invoquera lui-même en sous-processus.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys

LABEL = "com.jobsearch.server"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _not_macos_message() -> str:
    return (
        "jobsearch --service n'est disponible que sur macOS (launchd). "
        f"Plateforme détectée : {sys.platform!r}.\n"
        "Sur Linux, l'équivalent est une unit systemd --user : crée "
        "~/.config/systemd/user/jobsearch.service avec ExecStart=jobsearch --serve ..., "
        "puis `systemctl --user enable --now jobsearch`."
    )


def launch_agents_dir() -> str:
    return os.path.expanduser("~/Library/LaunchAgents")


def plist_path() -> str:
    return os.path.join(launch_agents_dir(), f"{LABEL}.plist")


def _uid() -> int:
    # `os.getuid()` n'existe que sur POSIX — suffisant ici, ce module n'est
    # jamais appelé pour ses actions réelles hors macOS (voir _is_macos()).
    return os.getuid()


def build_plist(
    *,
    host: str = "127.0.0.1",
    port: int = 8420,
    allow_write: bool = False,
    jobsearch_bin: str | None = None,
) -> dict:
    """Construit le contenu du plist (dict prêt pour `plistlib.dump`).

    `jobsearch_bin` : chemin absolu de l'exécutable `jobsearch`, résolu via
    `shutil.which` si non fourni. Résolu UNE FOIS à l'installation (pas à
    chaque démarrage du service) — c'est le comportement attendu d'un agent
    launchd : le plist fige la commande à lancer.
    """
    from jobsearch.config import JOBSEARCH_HOME, JOBSEARCH_HOME_EXPLICIT, PATHS

    exe = jobsearch_bin or shutil.which("jobsearch")
    if not exe:
        raise FileNotFoundError(
            "Exécutable `jobsearch` introuvable dans le PATH (shutil.which a échoué) — "
            "installe le paquet (pipx install / pip install -e .) avant `--service install`."
        )
    exe = os.path.abspath(exe)

    args = [exe, "--serve", "--host", host, "--port", str(port)]
    if allow_write:
        args.append("--allow-write")

    # Le journal vit sous JOBSEARCH_HOME/data/server.log — c'est le point qui
    # manquait avant cette fonctionnalité : l'utilisateur n'avait aucune trace
    # quand le service tombait (processus lancé à la main, terminal fermé).
    os.makedirs(PATHS.data_dir, exist_ok=True)
    log_path = os.path.join(PATHS.data_dir, "server.log")

    env: dict = {}
    # Capturé à l'installation seulement si JOBSEARCH_HOME diffère du défaut
    # (~/.jobsearch) : sinon on fige inutilement une variable d'environnement
    # que le process héritera de toute façon par défaut.
    if JOBSEARCH_HOME_EXPLICIT:
        env["JOBSEARCH_HOME"] = JOBSEARCH_HOME

    plist: dict = {
        "Label": LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
        "WorkingDirectory": os.path.expanduser("~"),
    }
    if env:
        plist["EnvironmentVariables"] = env
    return plist


def write_plist(plist: dict, dest: str | None = None) -> str:
    """Écrit le plist sur disque (répertoire créé si besoin). Retourne le chemin écrit."""
    path = dest or plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist, f)
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _launchctl_bootstrap(path: str) -> tuple[bool, str]:
    """Charge l'agent — forme moderne `bootstrap`, repli sur `load` si elle échoue
    (ex : macOS ancien, ou agent déjà bootstrapped autrement)."""
    res = _run(["launchctl", "bootstrap", f"gui/{_uid()}", path])
    if res.returncode == 0:
        return True, "chargé (launchctl bootstrap)"
    res2 = _run(["launchctl", "load", "-w", path])
    # `load` renvoie 0 même quand il n'a rien chargé : on vérifie.
    if res2.returncode == 0 and _is_loaded():
        return True, "chargé (launchctl load, repli)"
    detail = (res.stderr or res.stdout or "").strip() or (res2.stderr or res2.stdout or "").strip()
    return False, f"échec du chargement launchd : {detail or 'erreur inconnue'}"


def _launchctl_bootout() -> tuple[bool, str]:
    """Décharge l'agent — forme moderne `bootout`, repli sur `unload`."""
    res = _run(["launchctl", "bootout", f"gui/{_uid()}/{LABEL}"])
    if res.returncode == 0:
        return True, "déchargé (launchctl bootout)"
    res2 = _run(["launchctl", "unload", "-w", plist_path()])
    if res2.returncode == 0:
        return True, "déchargé (launchctl unload, repli)"
    detail = (res.stderr or res.stdout or "").strip() or (res2.stderr or res2.stdout or "").strip()
    # Un agent non chargé n'est pas une erreur bloquante pour `uninstall` : on le
    # signale mais on continue (le plist doit quand même être supprimé).
    return False, f"rien à décharger ou échec : {detail or 'erreur inconnue'}"


def install(*, host: str = "127.0.0.1", port: int = 8420, allow_write: bool = False) -> str:
    """Écrit le plist et charge l'agent. Retourne un message de résultat."""
    if not _is_macos():
        return _not_macos_message()
    plist = build_plist(host=host, port=port, allow_write=allow_write)
    path = write_plist(plist)
    ok, detail = _launchctl_bootstrap(path)
    lines = [f"Plist écrit : {path}", detail]
    if not ok:
        lines.append("→ Le plist est écrit mais l'agent n'a pas pu être chargé. "
                      "Réessaie avec `jobsearch --service start`.")
    else:
        lines.append(f"→ Service actif sur http://{host}:{port}"
                      + (" (écriture autorisée)" if allow_write else " (lecture seule)"))
    return "\n".join(lines)


def uninstall() -> str:
    if not _is_macos():
        return _not_macos_message()
    path = plist_path()
    if not os.path.exists(path):
        return f"Rien à désinstaller : {path} n'existe pas."
    ok, detail = _launchctl_bootout()
    os.remove(path)
    return f"{detail}\nPlist supprimé : {path}"


def start() -> str:
    if not _is_macos():
        return _not_macos_message()
    path = plist_path()
    if not os.path.exists(path):
        return f"Aucun plist installé ({path}) — lance d'abord `jobsearch --service install`."
    ok, detail = _launchctl_bootstrap(path)
    return detail


def stop() -> str:
    if not _is_macos():
        return _not_macos_message()
    ok, detail = _launchctl_bootout()
    return detail


def restart() -> str:
    if not _is_macos():
        return _not_macos_message()
    stop_msg = stop()
    # `bootout` est asynchrone : un `bootstrap` immédiat échoue tant que l'agent n'est pas déchargé.
    import time
    for _ in range(50):
        if not _is_loaded():
            break
        time.sleep(0.1)
    start_msg = start()
    return f"{stop_msg}\n{start_msg}"


def _is_loaded() -> bool:
    res = _run(["launchctl", "print", f"gui/{_uid()}/{LABEL}"])
    return res.returncode == 0


def _pid_if_running() -> int | None:
    res = _run(["launchctl", "list", LABEL])
    if res.returncode != 0:
        return None
    # `launchctl list <label>` imprime un plist-ish texte avec une clé "PID" = N ;
    # au repos ("PID" absent ou = 0/introuvable), on considère le service arrêté.
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith('"PID"'):
            try:
                return int(line.split("=")[1].strip().rstrip(";").strip())
            except (IndexError, ValueError):
                return None
    return None


def status() -> str:
    """Diagnostic lisible : plist présent, agent chargé, process vivant, adresse d'écoute —
    c'est la commande que l'utilisateur lance quand « ça ne marche plus »."""
    if not _is_macos():
        return _not_macos_message()

    path = plist_path()
    lines = []
    if not os.path.exists(path):
        lines.append(f"Plist : absent ({path})")
        lines.append("→ `jobsearch --service install` pour installer le service.")
        return "\n".join(lines)
    lines.append(f"Plist : présent ({path})")

    # Adresse d'écoute lue directement dans le plist installé (pas dans les
    # arguments d'appel de cette commande : c'est la config RÉELLEMENT active).
    host, port, allow_write = "?", "?", False
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        prog_args = data.get("ProgramArguments", [])
        if "--host" in prog_args:
            host = prog_args[prog_args.index("--host") + 1]
        if "--port" in prog_args:
            port = prog_args[prog_args.index("--port") + 1]
        allow_write = "--allow-write" in prog_args
    except (OSError, plistlib.InvalidFileException, ValueError, IndexError):
        pass

    loaded = _is_loaded()
    lines.append(f"Agent launchd : {'chargé' if loaded else 'non chargé'}")

    pid = _pid_if_running() if loaded else None
    if pid:
        lines.append(f"Processus : actif (PID {pid})")
    else:
        lines.append("Processus : arrêté")

    lines.append(f"Adresse configurée : http://{host}:{port}" + (" (écriture autorisée)" if allow_write else " (lecture seule)"))
    return "\n".join(lines)


def logs(lines: int = 50) -> str:
    if not _is_macos():
        return _not_macos_message()
    from jobsearch.config import PATHS

    log_path = os.path.join(PATHS.data_dir, "server.log")
    if not os.path.exists(log_path):
        return f"Aucun journal pour l'instant : {log_path} n'existe pas encore."
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.readlines()
    tail = content[-lines:]
    return f"— {log_path} (dernières {len(tail)} ligne(s)) —\n" + "".join(tail)
