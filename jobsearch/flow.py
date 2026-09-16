"""Agrégation et rendu du flux de statuts des candidatures (timeline groupée / Sankey).

Stdlib uniquement — pas de nouvelle dépendance pour une vue secondaire. Le SVG est
construit par concaténation de chaînes (pas de bibliothèque de rendu tierce).
"""
import os
import unicodedata
from collections import Counter
from html import escape

from jobsearch.config import PATHS
from jobsearch.tracker import STATUS_COLORS, STATUSES, _STATUS_LABELS, ensure_history

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
_UNIT_SCALE = 16  # pixels par occurrence
_NODE_GAP_Y = 8
_FONT_SIZE = 13
_LABEL_OFFSET = 6  # distance entre le bord du nœud et le début du texte
_COLUMN_PADDING = 30  # marge de sécurité entre la fin d'un libellé et la colonne suivante


def _text_width(text: str, font_size: int = _FONT_SIZE) -> float:
    """Largeur approximative (px) d'un texte en SVG, sans moteur de rendu disponible.

    Les caractères "larges" au sens Unicode (émoji, CJK — `east_asian_width` in {W, F})
    occupent une avance proche d'un carré ; le reste (latin, ponctuation) suit la règle
    empirique ~0.55em pour une police sans-serif. Volontairement une surestimation légère
    (mieux vaut un déficit de compacité qu'un débordement de colonne).
    """
    width = 0.0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            width += font_size * 1.05
        else:
            width += font_size * 0.55
    return width


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

    # Libellé complet de chaque statut (avec effectif) — calculé une fois, réutilisé pour
    # la largeur des colonnes et pour le rendu du texte.
    labels: dict[str, str] = {
        status: f"{_STATUS_LABELS.get(status, status)} ({total_by_status[status]})"
        for col_statuses in columns.values()
        for status in col_statuses
    }

    # Largeur de colonne : la position x d'une colonne dépend de la largeur du plus grand
    # libellé de la colonne précédente (pas d'une constante fixe), pour garantir qu'aucun
    # texte ne peut jamais déborder sur la colonne suivante quel que soit l'effectif ou la
    # longueur du libellé.
    max_label_w: dict[int, float] = {
        col: max((_text_width(labels[s]) for s in statuses), default=0.0)
        for col, statuses in columns.items()
    }
    col_x: dict[int, float] = {0: float(_MARGIN)}
    for col in range(1, max_rank + 1):
        col_x[col] = (
            col_x[col - 1]
            + _NODE_W
            + _LABEL_OFFSET
            + max_label_w.get(col - 1, 0.0)
            + _COLUMN_PADDING
        )

    nodes: dict[str, dict] = {}
    for col in range(max_rank + 1):
        statuses_in_col = columns.get(col, [])
        y = _MARGIN
        x = col_x[col]
        for status in statuses_in_col:
            h = max(4, total_by_status[status] * _UNIT_SCALE)
            nodes[status] = {"x": x, "y": y, "w": _NODE_W, "h": h}
            y += h + _NODE_GAP_Y

    canvas_h = _MARGIN * 2 + max(
        (n["y"] + n["h"] - _MARGIN for n in nodes.values()), default=100
    )
    canvas_w = (
        col_x[max_rank] + _NODE_W + _LABEL_OFFSET + max_label_w.get(max_rank, 0.0) + _MARGIN
    )

    parts = [
        f'<svg width="{canvas_w:.0f}" height="{canvas_h:.0f}" '
        f'viewBox="0 0 {canvas_w:.0f} {canvas_h:.0f}">',
        f'<rect x="0" y="0" width="{canvas_w:.0f}" height="{canvas_h:.0f}" fill="#1e1e1e"/>',
    ]

    # Rubans. L'ordre d'empilement à CHAQUE extrémité suit la position verticale du nœud à
    # l'AUTRE extrémité (les rubans entrants d'un nœud sont ordonnés par le y de leur nœud
    # source, et les rubans sortants par le y de leur nœud cible) : deux rubans qui ne se
    # croisent pas côté source ne se croisent pas non plus côté cible. Sans ce tri par
    # position (l'ancien code triait par rang puis effectif décroissant, indépendant de la
    # géométrie réelle), deux rubans pouvaient se croiser en X — cas mesuré : `rejected`
    # reçoit à la fois de `sent` (gros nœud, rang 1) et de `interview` (petit nœud, rang 3,
    # positionné plus haut sur son axe) ; sans ce correctif, le ruban de `sent` s'empilait
    # en premier (donc en haut) alors qu'`interview` est visuellement au-dessus.
    outgoing: dict[str, list[tuple[str, str, int]]] = {}
    incoming: dict[str, list[tuple[str, str, int]]] = {}
    for (source, target), count in transitions.items():
        if source not in nodes or target not in nodes:
            continue  # nœud sans occurrence positive (ne devrait pas arriver, robustesse)
        outgoing.setdefault(source, []).append((source, target, count))
        incoming.setdefault(target, []).append((source, target, count))

    source_offset: dict[tuple[str, str], float] = {}
    for source, edges in outgoing.items():
        # Tri par le CENTRE du nœud, pas par son bord supérieur : deux nœuds de
        # colonnes différentes commencent tous deux à `_MARGIN` et leur `y` est
        # donc à égalité, ce qui rendait l'ordre arbitraire. Cas réel mesuré :
        # `refusée` recevait de `envoyée` (grand nœud, centre bas) et d'`entretien`
        # (petit nœud, centre haut), tous deux en y=40 — le ruban d'`entretien`
        # plongeait alors à travers celui d'`envoyée`.
        edges.sort(key=lambda e: nodes[e[1]]["y"] + nodes[e[1]]["h"] / 2)
        offset = 0.0
        for source_s, target_s, count in edges:
            source_offset[(source_s, target_s)] = offset
            offset += count * _UNIT_SCALE

    target_offset: dict[tuple[str, str], float] = {}
    for target, edges in incoming.items():
        edges.sort(key=lambda e: nodes[e[0]]["y"] + nodes[e[0]]["h"] / 2)
        offset = 0.0
        for source_s, target_s, count in edges:
            target_offset[(source_s, target_s)] = offset
            offset += count * _UNIT_SCALE

    # Ordre de dessin des rubans lui-même sans incidence visuelle (formes disjointes par
    # construction), mais trié par rang source pour un rendu stable/reproductible.
    ordered_transitions = sorted(
        transitions.items(),
        key=lambda kv: (STATUS_RANK.get(kv[0][0], 0), -kv[1]),
    )
    for (source, target), count in ordered_transitions:
        if source not in nodes or target not in nodes:
            continue
        ribbon_h = count * _UNIT_SCALE
        sy0 = nodes[source]["y"] + source_offset[(source, target)]
        sy1 = sy0 + ribbon_h
        ty0 = nodes[target]["y"] + target_offset[(source, target)]
        ty1 = ty0 + ribbon_h

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

    # Nœuds + libellés. Dessinés après les rubans (donc par-dessus), avec un fond sombre
    # semi-opaque derrière chaque texte : une transition longue (ex. rang 0→4) traverse en
    # une seule courbe les colonnes intermédiaires, et sans ce fond le libellé en clair
    # devenait illisible posé sur un ruban semi-transparent.
    for status, n in nodes.items():
        color = STATUS_COLORS.get(status) or "#555555"
        parts.append(
            f'<rect x="{n["x"]:.1f}" y="{n["y"]:.1f}" width="{n["w"]}" height="{n["h"]:.1f}" '
            f'fill="{color}" stroke="#111" stroke-width="1"/>'
        )
        label_text = labels[status]
        label = escape(label_text)
        text_x = n["x"] + n["w"] + _LABEL_OFFSET
        text_y = n["y"] + n["h"] / 2
        label_w = _text_width(label_text)
        parts.append(
            f'<rect x="{text_x - 3:.1f}" y="{text_y - 9:.1f}" width="{label_w + 6:.1f}" '
            f'height="18" fill="#1e1e1e" fill-opacity="0.82" rx="3"/>'
        )
        parts.append(
            f'<text x="{text_x:.1f}" y="{text_y:.1f}" font-size="{_FONT_SIZE}" fill="#eeeeee" '
            f'dominant-baseline="middle">{label}</text>'
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
