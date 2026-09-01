"""Agrégation et rendu du flux de statuts des candidatures (timeline groupée / Sankey).

Stdlib uniquement — pas de nouvelle dépendance pour une vue secondaire. Le SVG est
construit par concaténation de chaînes (pas de bibliothèque de rendu tierce).
"""
import os
from collections import Counter
from html import escape

from src.config import PATHS
from src.tracker import STATUS_COLORS, STATUSES, _STATUS_LABELS, ensure_history

# Rang (colonne) de chaque statut dans le diagramme de flux. accepted/rejected/cancelled
# partagent le rang terminal 4 : ce sont les trois issues finales d'une candidature.
STATUS_RANK = {
    "generated": 0,
    "sent": 1,
    "answered": 2,
    "interview": 3,
    "accepted": 4,
    "rejected": 4,
    "cancelled": 4,
}

DEFAULT_FLOW_PATH = os.path.join(PATHS.output_dir, "stats", "flux.html")


def build_transitions(apps: list[dict]) -> dict[tuple[str, str], int]:
    """Compte les transitions (statut n -> statut n+1) sur tous les historiques."""
    counts: dict[tuple[str, str], int] = {}
    for app in apps:
        history = ensure_history(app)
        for i in range(len(history) - 1):
            key = (history[i]["status"], history[i + 1]["status"])
            counts[key] = counts.get(key, 0) + 1
    return counts


def flow_tree(apps: list[dict]) -> dict:
    """Même information que `build_transitions`, en arbre — pour le rendu texte de la TUI.

    Retourne `{"generated": {"total": int, "children": {statut: {...}}}}`. Une candidature
    sans historique exploitable n'apparaît nulle part (racine unique `generated`, point
    d'entrée de toute candidature).
    """
    transitions = build_transitions(apps)
    children_of: dict[str, list[tuple[str, int]]] = {}
    for (source, target), count in transitions.items():
        children_of.setdefault(source, []).append((target, count))

    root_total = 0
    for app in apps:
        history = ensure_history(app)
        if history and history[0]["status"] == "generated":
            root_total += 1

    def build(status: str, total: int, visited: frozenset) -> dict:
        node = {"total": total, "children": {}}
        if status in visited:
            # Transition en arrière (ex. correction de saisie) : évite une récursion infinie.
            return node
        visited = visited | {status}
        for target, count in sorted(children_of.get(status, []), key=lambda x: -x[1]):
            node["children"][target] = build(target, count, visited)
        return node

    return {"generated": build("generated", root_total, frozenset())}


def by_month_status(apps: list[dict]) -> dict[str, Counter]:
    """Par mois (`YYYY-MM`), le nombre de transitions atteignant chaque statut.

    Compte les événements de l'historique (pas l'état courant des candidatures) : le passé
    ne se réécrit pas quand une candidature change de statut plus tard.
    """
    result: dict[str, Counter] = {}
    for app in apps:
        for event in ensure_history(app):
            month = event["at"][:7]
            result.setdefault(month, Counter())[event["status"]] += 1
    return dict(sorted(result.items()))


# --- Rendu SVG ---

_MARGIN = 40
_NODE_W = 18
_COLUMN_GAP_X = 210
_UNIT_SCALE = 16  # pixels par occurrence
_NODE_GAP_Y = 8


def render_sankey_svg(apps: list[dict]) -> str:
    """SVG (diagramme de Sankey) du flux de statuts, construit par concaténation de chaînes."""
    total_by_status: Counter = Counter()
    for app in apps:
        for event in ensure_history(app):
            total_by_status[event["status"]] += 1
    transitions = build_transitions(apps)

    if not total_by_status:
        # Pas de `xmlns` : le SVG est enrobé dans un document HTML5 (voir `write_flow_html`),
        # qui reconnaît nativement l'élément inline sans namespace explicite — évite toute
        # occurrence de "http://" dans une sortie censée être 100% autonome.
        return (
            '<svg width="400" height="80">'
            '<text x="20" y="40" font-size="14" fill="#eee">Aucune candidature enregistrée.</text>'
            "</svg>"
        )

    max_rank = max(STATUS_RANK.get(s, 0) for s in total_by_status)

    columns: dict[int, list[str]] = {}
    for status in STATUSES:
        if total_by_status.get(status, 0) <= 0:
            continue
        columns.setdefault(STATUS_RANK.get(status, 0), []).append(status)

    nodes: dict[str, dict] = {}
    for col in range(max_rank + 1):
        statuses_in_col = columns.get(col, [])
        y = _MARGIN
        x = _MARGIN + col * _COLUMN_GAP_X
        for status in statuses_in_col:
            h = max(4, total_by_status[status] * _UNIT_SCALE)
            nodes[status] = {"x": x, "y": y, "w": _NODE_W, "h": h}
            y += h + _NODE_GAP_Y

    canvas_h = _MARGIN * 2 + max(
        (n["y"] + n["h"] - _MARGIN for n in nodes.values()), default=100
    )
    canvas_w = _MARGIN * 2 + _NODE_W + max_rank * _COLUMN_GAP_X + 180  # + espace labels

    parts = [
        f'<svg width="{canvas_w:.0f}" height="{canvas_h:.0f}" '
        f'viewBox="0 0 {canvas_w:.0f} {canvas_h:.0f}">',
        f'<rect x="0" y="0" width="{canvas_w:.0f}" height="{canvas_h:.0f}" fill="#1e1e1e"/>',
    ]

    # Rubans (ordonnés par rang source puis effectif décroissant, pour un rendu stable).
    source_offset: dict[str, float] = {s: 0.0 for s in nodes}
    target_offset: dict[str, float] = {s: 0.0 for s in nodes}
    ordered_transitions = sorted(
        transitions.items(),
        key=lambda kv: (STATUS_RANK.get(kv[0][0], 0), -kv[1]),
    )
    for (source, target), count in ordered_transitions:
        if source not in nodes or target not in nodes:
            continue  # nœud sans occurrence positive (ne devrait pas arriver, robustesse)
        ribbon_h = count * _UNIT_SCALE
        sy0 = nodes[source]["y"] + source_offset[source]
        sy1 = sy0 + ribbon_h
        ty0 = nodes[target]["y"] + target_offset[target]
        ty1 = ty0 + ribbon_h
        source_offset[source] += ribbon_h
        target_offset[target] += ribbon_h

        x0 = nodes[source]["x"] + nodes[source]["w"]
        x1 = nodes[target]["x"]
        xm = (x0 + x1) / 2
        # Une transition « en arrière » (rang cible <= rang source) donne x1 <= x0 : la
        # courbe part vers la gauche mais le chemin reste géométriquement valide.
        color = STATUS_COLORS.get(target) or STATUS_COLORS.get(source) or "#888888"
        path = (
            f"M{x0:.1f},{sy0:.1f} "
            f"C{xm:.1f},{sy0:.1f} {xm:.1f},{ty0:.1f} {x1:.1f},{ty0:.1f} "
            f"L{x1:.1f},{ty1:.1f} "
            f"C{xm:.1f},{ty1:.1f} {xm:.1f},{sy1:.1f} {x0:.1f},{sy1:.1f} Z"
        )
        parts.append(f'<path d="{path}" fill="{color}" fill-opacity="0.55" stroke="none"/>')

    # Nœuds + libellés.
    for status, n in nodes.items():
        color = STATUS_COLORS.get(status) or "#555555"
        parts.append(
            f'<rect x="{n["x"]:.1f}" y="{n["y"]:.1f}" width="{n["w"]}" height="{n["h"]:.1f}" '
            f'fill="{color}" stroke="#111" stroke-width="1"/>'
        )
        label = escape(_STATUS_LABELS.get(status, status))
        text_x = n["x"] + n["w"] + 6
        text_y = n["y"] + n["h"] / 2
        parts.append(
            f'<text x="{text_x:.1f}" y="{text_y:.1f}" font-size="13" fill="#eeeeee" '
            f'dominant-baseline="middle">{label} ({total_by_status[status]})</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def write_flow_html(path: str = DEFAULT_FLOW_PATH, apps: list[dict] | None = None) -> str:
    """Enrobe le SVG du flux dans un HTML autonome (CSS inline, aucun asset externe).

    Ajoute sous le diagramme un tableau des transitions. Crée le dossier parent. Retourne
    le chemin écrit.
    """
    apps = apps or []
    svg = render_sankey_svg(apps)
    transitions = build_transitions(apps)
    rows = "\n".join(
        f"<tr><td>{escape(_STATUS_LABELS.get(s, s))}</td>"
        f"<td>{escape(_STATUS_LABELS.get(t, t))}</td><td>{count}</td></tr>"
        for (s, t), count in sorted(transitions.items(), key=lambda kv: -kv[1])
    )
    if not rows:
        rows = '<tr><td colspan="3">Aucune transition enregistrée.</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Flux des candidatures</title>
<style>
  body {{ background: #1e1e1e; color: #eee; font-family: sans-serif; padding: 24px; }}
  h1 {{ font-size: 18px; }}
  table {{ border-collapse: collapse; margin-top: 16px; }}
  td, th {{ padding: 4px 12px; border: 1px solid #444; text-align: left; }}
</style>
</head>
<body>
<h1>Flux des candidatures — diagramme de Sankey</h1>
{svg}
<h2>Transitions</h2>
<table>
<tr><th>De</th><th>Vers</th><th>Nombre</th></tr>
{rows}
</table>
</body>
</html>
"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
