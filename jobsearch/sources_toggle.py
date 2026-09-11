"""Activation/désactivation individuelle des sources de `config/sources.yaml`.

`config/sources.yaml` est un fichier utilisateur abondamment commenté (en-têtes
de section, pièges documentés inline — voir CLAUDE.md). Comme pour `search.yaml`
dans `jobsearch/wizard.py`, on ne le réécrit JAMAIS via `yaml.safe_dump` (qui
détruirait tous les commentaires) : les fonctions ci-dessous éditent le fichier
ligne à ligne, en ne touchant qu'à la clé `enabled:` d'un bloc de source précis.

Une source est active par défaut (`enabled` absent ou `true`) ; `enabled: false`
la désactive individuellement — indépendamment du mécanisme de groupe existant
(`optional`/`group`, réservé aux sources FR-only activées via `--with fr`), voir
le test ajouté dans `scrape_all` (`jobsearch/scraper.py`). Les deux mécanismes
coexistent : une source peut être à la fois dans un groupe non activé ET
désactivée individuellement, les deux sont testés séparément.

Choix pour la réactivation (`enabled: true`) : on RETIRE la ligne `enabled:`
plutôt que de la mettre à `true` — le défaut « actif » redevient implicite,
comme pour toute source qui n'a jamais été désactivée. Évite d'accumuler des
lignes `enabled: true` inertes au fil des allers-retours dans la TUI.
"""
from __future__ import annotations

import re

import yaml

from jobsearch.config import PATHS

SOURCES_FILENAME = "sources.yaml"

# Repère le début d'un bloc de source : `- name: ...` (avec indentation capturée,
# pour aligner les nouvelles clés insérées sur le reste du bloc). Les trois formes
# YAML sont acceptées — guillemets doubles (celle du fichier livré), apostrophes,
# et scalaire nu : `sources.yaml` est un fichier édité à la main, et n'accepter que
# la forme livrée ferait échouer la bascule sur « Source introuvable » dès qu'un
# utilisateur retire les guillemets.
_NAME_LINE_RE = re.compile(
    r'^(?P<indent>\s*)- name:\s*'
    r'(?:"(?P<dq>(?:[^"\\]|\\.)*)"|\'(?P<sq>(?:[^\']|\'\')*)\'|(?P<bare>[^#\n]*?))'
    r'\s*(?:#.*)?$'
)


def _block_name(match: "re.Match[str]") -> str:
    """Nom de la source capturé par `_NAME_LINE_RE`, quelle que soit sa forme YAML."""
    if match.group("dq") is not None:
        return match.group("dq")
    if match.group("sq") is not None:
        return match.group("sq").replace("\'\'", "\'")
    return (match.group("bare") or "").strip()

# Clé `enabled:` déjà présente dans un bloc (valeur bool YAML simple).
_ENABLED_LINE_RE = re.compile(r'^(?P<indent>\s*)enabled:\s*(?P<val>true|false)\s*(?P<comment>#.*)?$')


def _sources_path(config_dir: str) -> str:
    import os

    return os.path.join(config_dir, SOURCES_FILENAME)


def load_sources_state(config_dir: str = PATHS.config_dir) -> list[dict]:
    """État courant des sources pour affichage (TUI) : name/type/country/group/
    optional/enabled (défaut True si absent). Lecture via `yaml.safe_load`
    (les commentaires ne comptent pas pour une simple lecture d'état) — seule
    l'écriture (`set_source_enabled`) doit préserver le fichier ligne à ligne."""
    path = _sources_path(config_dir)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return []
    result = []
    for source in data.get("sources", []) or []:
        result.append(
            {
                "name": source.get("name", ""),
                "type": source.get("type", "rss"),
                "country": source.get("country", ""),
                "group": source.get("group", ""),
                "optional": bool(source.get("optional", False)),
                "enabled": source.get("enabled", True) is not False,
            }
        )
    return result


def _find_block(lines: list[str], name: str) -> tuple[int, int, str] | None:
    """Retourne (index de la ligne `- name:`, index de fin exclusif du bloc,
    indentation de la ligne `- name:`) pour la source `name`, ou None si
    absente. La fin du bloc est repérée par la prochaine ligne `- name:`
    (chaque entrée de `sources.yaml` commence toujours par ce champ) ou la
    fin du fichier."""
    start = None
    indent = ""
    for i, line in enumerate(lines):
        m = _NAME_LINE_RE.match(line)
        if m and _block_name(m) == name:
            start = i
            indent = m.group("indent")
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _NAME_LINE_RE.match(lines[j]):
            end = j
            break
    return start, end, indent


def set_source_enabled(name: str, enabled: bool, config_dir: str = PATHS.config_dir) -> None:
    """Bascule une source individuelle dans `sources.yaml`, en préservant tous
    les commentaires et l'ordre des entrées (édition ligne à ligne, jamais
    `yaml.safe_dump` — voir docstring du module)."""
    path = _sources_path(config_dir)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")

    block = _find_block(lines, name)
    if block is None:
        raise ValueError(f"Source introuvable dans {path} : {name!r}")
    start, end, indent = block

    # Clé `enabled:` déjà présente dans le bloc ?
    enabled_idx = None
    for j in range(start + 1, end):
        if _ENABLED_LINE_RE.match(lines[j]):
            enabled_idx = j
            break

    if enabled:
        # Réactivation : on retire la ligne plutôt que d'écrire `enabled: true`
        # (voir docstring du module — le défaut « actif » redevient implicite).
        if enabled_idx is not None:
            del lines[enabled_idx]
    else:
        key_line = f"{indent}  enabled: false"
        if enabled_idx is not None:
            lines[enabled_idx] = key_line
        else:
            # Insérée juste après `- name:`, avant les autres clés du bloc.
            lines.insert(start + 1, key_line)

    new_text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
