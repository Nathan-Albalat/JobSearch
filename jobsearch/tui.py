"""Interface TUI (Textual) — revue des offres, génération, suivi des candidatures.

La TUI est une simple vue sur les fichiers du pipeline : elle relit l'état
courant depuis data/ à chaque lancement (et sur `r`), n'écrit que via les
fonctions métier existantes, et ne persiste aucun état propre — à une
exception près : la file de génération de l'onglet 2 est persistée dans
`data/gen_queue.json` (voir `load_queue`/`save_queue`), pour survivre à un
redémarrage entre le scrape et la revue.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unicodedata
import webbrowser
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from rich.style import Style
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual.widgets.data_table import RowDoesNotExist
from textual.widgets.option_list import Option

from jobsearch.duplicates import (
    INVALID_REASONS,
    _normalize_company,
    clear_duplicate,
    clear_invalid,
    get_gen_notes,
    load_offer,
    mark_duplicate,
    mark_invalid,
    set_gen_notes,
    suggest_targets,
)
from jobsearch.filter import strip_html
from jobsearch.flow import DEFAULT_FLOW_PATH, by_month_status, flow_tree, write_flow_html
from jobsearch.report import load_filtered_offers
from jobsearch.serialize import offer_id_from_folder
from jobsearch.tracker import (
    STATUS_COLORS,
    STATUSES,
    _STATUS_LABELS,
    compute_stats,
    ensure_history,
    load_applications,
    mark_status,
    record_generated,
)
from jobsearch.config import (
    MODELS_SETTINGS_KEY, PATHS, SOURCES_SETTINGS_KEY, Search, get_search, load_search, settings_files,
)
from jobsearch.sources_toggle import load_sources_state, set_source_enabled


QUEUE_PATH = os.path.join(PATHS.data_dir, "gen_queue.json")


def load_queue(path: str = QUEUE_PATH) -> list[str]:
    """Charge la file de génération persistée. Tolère absence de fichier / JSON corrompu."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def save_queue(ids, path: str = QUEUE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, indent=2, ensure_ascii=False)


def _cells(values):
    return [str(v) for v in values]


def _visible_width(text: str) -> int:
    """Largeur d'affichage en cellules terminal — un émoji « large » en occupe 2.

    `len()` compte 1 pour tous les émoji alors que la plupart en occupent 2 à l'écran.
    Le mélange était visible dans « Parcours des candidatures » : 🎙 (entretien) est
    de largeur Neutral, donc rendu sur 1 cellule là où 📝/📤/❌ en prennent 2 — la
    ligne entretien se décalait d'un caractère vers la gauche.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """Complète `text` à `width` cellules d'affichage (équivalent de `f'{t:<w}'`, émoji compris)."""
    return text + " " * max(0, width - _visible_width(text))


def _hbar(label, value, total, label_w, bar_w=22, style=None):
    """Une ligne de barre horizontale : label aligné + barre proportionnelle + valeur + %."""
    filled = round(bar_w * value / total) if total else 0
    filled = min(bar_w, filled)
    if value > 0:
        filled = max(1, filled)  # une candidature reste visible
    bar = "█" * filled + "░" * (bar_w - filled)
    pct = round(100 * value / total) if total else 0
    color = f"[{style}]" if style else ""
    close = "[/]" if style else ""
    return f"{_pad(str(label), label_w)} {color}{bar}{close} {value:>2} ({pct:>3}%)"


# Échelles de temps partagées par l'histogramme et le graphe en courbes par pays.
# Calibrées pour tenir dans ~96 colonnes : max seaux, largeur de colonne de l'histogramme,
# repère d'axe tous les N seaux, colonnes par seau dans les courbes, libellé.
_SCALES = {
    "day": {"max": 90, "col_w": 1, "every": 7, "line_cols": 1, "label": "jour"},
    "week": {"max": 32, "col_w": 3, "every": 4, "line_cols": 3, "label": "semaine"},
    "month": {"max": 16, "col_w": 6, "every": 1, "line_cols": 6, "label": "mois"},
}
_SCALE_CYCLE = ("day", "week", "month")


def _bucket_start(d, scale):
    """Clé de seau : le jour, le lundi de la semaine ou le 1er du mois."""
    if scale == "week":
        return d - timedelta(days=d.weekday())
    if scale == "month":
        return d.replace(day=1)
    return d


def _next_bucket(d, scale):
    if scale == "week":
        return d + timedelta(days=7)
    if scale == "month":
        return date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return d + timedelta(days=1)


def _bucket_label(d, scale):
    if scale == "month":
        return _short_month(d.strftime("%Y-%m"))
    return d.strftime("%d/%m")


def _bucket_axis(by_day_groups, scale, today=None, max_buckets=None):
    """Liste des seaux (débuts) de la 1re candidature à aujourd'hui, vides inclus, tronquée aux plus récents."""
    days = [d for d in by_day_groups if d]
    if not days:
        return []
    today = today or date.today()
    end = _bucket_start(max(today, max(days)), scale)
    cur = _bucket_start(min(days), scale)
    out = []
    while cur <= end:
        out.append(cur)
        cur = _next_bucket(cur, scale)
    max_buckets = max_buckets or _SCALES[scale]["max"]
    return out[-max_buckets:]


def _bucket_counts(by_day, scale, today=None, max_buckets=None):
    """Agrège `{jour ISO: n}` par jour/semaine/mois -> [(début de seau, total)], seaux vides inclus."""
    per_day = {}
    for k, v in (by_day or {}).items():
        d = _parse_day(k)
        if d and v:
            per_day[d] = per_day.get(d, 0) + v
    axis = _bucket_axis(per_day, scale, today, max_buckets)
    totals = Counter()
    for d, n in per_day.items():
        totals[_bucket_start(d, scale)] += n
    return [(b, totals.get(b, 0)) for b in axis]


def _axis_labels(width, positions_labels):
    """Ligne d'axe X : place chaque libellé seulement si la place est libre."""
    axis = [" "] * width
    for pos, label in positions_labels:
        if pos < 0 or pos + len(label) > width:
            continue
        if all(c == " " for c in axis[max(0, pos - 1):pos + len(label)]):
            axis[pos:pos + len(label)] = list(label)
    return "".join(axis).rstrip()


def render_time_histogram(buckets, scale="week", height=7):
    """Histogramme vertical ASCII des candidatures par seau de temps (inspiré d'AsciiStogram)."""
    if not buckets:
        return "[dim]Aucune donnée temporelle.[/dim]"
    cfg = _SCALES[scale]
    col_w, every = cfg["col_w"], cfg["every"]
    values = [v for _, v in buckets]
    top = max(max(values), 1)
    bar_w = col_w if col_w == 1 else col_w - 1 if col_w <= 3 else col_w - 2
    y_w = len(str(top))
    lines = []
    for level in range(height, 0, -1):
        threshold = top * level / height
        axis = f"{top:>{y_w}} " if level == height else " " * (y_w + 1)
        cells = "".join(
            ("█" * bar_w).center(col_w) if v > 0 and v >= threshold else " " * col_w
            for v in values
        )
        lines.append(f"{axis}┤{cells}")
    lines.append(f"{0:>{y_w}} └" + "─" * (col_w * len(buckets)))
    marks = []
    for i, (b, _) in enumerate(buckets):
        if i % every == 0:
            label = _bucket_label(b, scale)
            marks.append((i * col_w + max(0, (col_w - len(label)) // 2), label))
    lines.append(" " * (y_w + 2) + _axis_labels(col_w * len(buckets) + 5, marks))
    return "\n".join(lines)


def render_flow_tree(tree: dict) -> str:
    """Rendu arborescent de `src.flow.flow_tree` — barres via `_hbar`."""
    if not tree:
        return "[dim]Aucune donnée.[/dim]"
    root_status, root_node = next(iter(tree.items()))
    total = root_node["total"]
    if not total:
        return "[dim]Aucune donnée.[/dim]"
    lines: list[str] = []

    def walk(status: str, node: dict, depth: int) -> None:
        prefix = "" if depth == 0 else " " * (4 * (depth - 1) + 1) + "└→ "
        label = f"{prefix}{_STATUS_LABELS.get(status, status)}"
        color = STATUS_COLORS.get(status)
        lines.append(_hbar(label, node["total"], total, label_w=40, bar_w=16, style=color))
        for child_status, child_node in node["children"].items():
            walk(child_status, child_node, depth + 1)

    walk(root_status, root_node, 0)
    return "\n".join(lines)


def render_month_status_bars(by_month, bar_w: int = 30) -> str:
    """Barre segmentée par statut, une ligne par mois (`src.flow.by_month_status`)."""
    if not by_month:
        return "[dim]Aucune donnée temporelle.[/dim]"
    max_total = max(sum(counter.values()) for counter in by_month.values())
    lines = []
    for month, counter in by_month.items():
        total = sum(counter.values())
        segments = []
        for status in STATUSES:
            v = counter.get(status, 0)
            if v <= 0:
                continue
            n = max(1, round(bar_w * v / max_total)) if max_total else 0
            color = STATUS_COLORS.get(status) or "#888888"
            segments.append(f"[{color}]{'█' * n}[/]")
        bar = "".join(segments)
        lines.append(f"{_short_month(month):<6} {bar} {total}")
    legend = "   ".join(
        f"[{STATUS_COLORS[s]}]■[/] {_STATUS_LABELS[s]}" for s in STATUSES if STATUS_COLORS.get(s)
    )
    lines += ["", legend]
    return "\n".join(lines)


def _short_month(ym):
    """'2026-07' -> '07/26'."""
    try:
        y, m = ym.split("-")[:2]
        return f"{m}/{y[2:]}"
    except (ValueError, IndexError):
        return ym[:5]


_DAY_LABELS = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")
_MONTH_ABBR = ("Jan", "Fév", "Mar", "Avr", "Mai", "Jui", "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc")
_HEATMAP_ACCENT = "green"
_HEATMAP_GLYPHS = ("▪", "░", "▒", "▓", "█")
_HEATMAP_MAX_WEEKS = 26
_COUNTRY_PALETTE = ("cyan", "magenta", "yellow", "green", "blue")


def _parse_day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _heat_cell(n: int, top: int) -> str:
    """Deux cellules d'affichage pour un jour : niveau 0 (▪ grisé) à 4, seuils relatifs au max."""
    if n <= 0:
        return f"[grey30]{_HEATMAP_GLYPHS[0]}[/] "
    level = min(4, max(1, -(-4 * n // top)))  # ceil(4 * n / top)
    return f"[{_HEATMAP_ACCENT}]{_HEATMAP_GLYPHS[level]}[/] "


def render_activity_heatmap(by_day, today=None) -> str:
    """Grille d'activité façon GitHub : une case = les candidatures créées ce jour-là."""
    days = {d: n for d, n in ((_parse_day(k), v) for k, v in (by_day or {}).items()) if d and n > 0}
    if not days:
        return "[dim]Aucune donnée temporelle.[/dim]"
    today = today or date.today()
    first_monday = min(days) - timedelta(days=min(days).weekday())
    this_monday = today - timedelta(days=today.weekday())
    n_weeks = min(_HEATMAP_MAX_WEEKS, (this_monday - first_monday).days // 7 + 1)
    n_weeks = max(1, n_weeks)
    start = this_monday - timedelta(weeks=n_weeks - 1)
    top = max(days.values())

    label_w = 4
    # En-tête : abréviation du mois au-dessus de la 1re semaine de chaque mois.
    header = [" "] * (2 * n_weeks + 3)
    prev_month = None
    for w in range(n_weeks):
        monday = start + timedelta(weeks=w)
        if monday.month != prev_month:
            label = _MONTH_ABBR[monday.month - 1]
            pos = 2 * w
            if all(c == " " for c in header[max(0, pos - 1):pos + len(label)]):
                header[pos:pos + len(label)] = list(label)
            prev_month = monday.month
    lines = [" " * label_w + "".join(header).rstrip()]
    for row in range(7):
        cells = []
        for w in range(n_weeks):
            d = start + timedelta(weeks=w, days=row)
            cells.append("  " if d > today else _heat_cell(days.get(d, 0), top))
        lines.append(_pad(_DAY_LABELS[row], label_w) + "".join(cells).rstrip())

    legend = " ".join(
        [f"[grey30]{_HEATMAP_GLYPHS[0]}[/]"] + [f"[{_HEATMAP_ACCENT}]{g}[/]" for g in _HEATMAP_GLYPHS[1:]]
    )
    lines += ["", f"{' ' * label_w}[dim]Moins[/dim] {legend} [dim]Plus[/dim]", ""]

    # Résumé (sur tout l'historique, pas seulement la fenêtre affichée)
    ordered = sorted(days)
    longest = run = 0
    prev = None
    for d in ordered:
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        longest = max(longest, run)
        prev = d
    cursor = today if today in days else today - timedelta(days=1)
    current = 0
    while cursor in days:
        current += 1
        cursor -= timedelta(days=1)
    busiest = max(ordered, key=lambda d: (days[d], d))
    weeks_active = {d - timedelta(days=d.weekday()) for d in ordered}
    avg = sum(days.values()) / len(weeks_active)
    lines += [
        f"Jours actifs : [b]{len(days)}[/b]   Série la plus longue : [b]{longest}[/b] j   "
        f"Série en cours : [b]{current}[/b] j",
        f"Jour le plus actif : [b]{busiest.strftime('%d/%m/%Y')}[/b] ({days[busiest]})   "
        f"Moyenne : [b]{avg:.1f}[/b] / semaine active",
    ]
    return "\n".join(lines)


def render_location_tree(by_place, total, max_cities=6) -> str:
    """Arbre pays → villes : une barre par pays, ses villes indentées dessous."""
    if not by_place or not total:
        return "[dim]Aucune donnée.[/dim]"
    unknown = "Non précisée"
    rows = []  # (label, valeur, style)
    for country, info in by_place.items():
        rows.append((country, info["total"], "cyan"))
        cities = info.get("cities", {})
        named = [(c, n) for c, n in cities.items() if c != unknown]
        if len(named) > max_cities:
            keep, rest = named[:max_cities - 1], named[max_cities - 1:]
            named = keep + [(f"Autres ({len(rest)} villes)", sum(n for _, n in rest))]
        if unknown in cities:
            named.append((unknown, cities[unknown]))
        rows += [("  " + c, n, "dim cyan") for c, n in named]
    label_w = min(26, max(_visible_width(r[0]) for r in rows))
    out = []
    for label, value, style in rows:
        while _visible_width(label) > label_w:
            label = label[:-1]
        out.append(_hbar(label, value, total, label_w, style=style))
    return "\n".join(out)


def _chart_scale(max_value, max_height=10):
    """(pas, hauteur, sommet) d'un graphe en courbes : une ligne = `pas` unités entières.

    max <= max_height : une ligne par unité. Sinon le plus petit pas parmi 1, 2, 5, 10, 20, 25,
    50, 100… tel que ceil(max/pas) <= max_height ; sommet = pas × ceil(max/pas).
    """
    m = max(1, int(-(-max_value // 1)))
    if m <= max_height:
        return 1, m, m
    steps = sorted({b * 10 ** k for b in (1, 2, 5) for k in range(0, 7)} | {25 * 10 ** k for k in range(0, 6)})
    for step in steps:
        n = -(-m // step)
        if n <= max_height:
            return step, n, step * n
    return m, 1, m


def render_line_chart(series, height=8, colors=None) -> str:
    """Graphe en courbes ASCII (portage stdlib de l'algorithme `asciichart`).

    `series` : liste de listes de valeurs de même longueur. Axe Y étiqueté à gauche
    (`┼` sur 0 et le max, `┤` ailleurs), segments `─ │ ╭ ╮ ╰ ╯`. La série au plus grand
    cumul est dessinée en dernier pour rester lisible. Chaque ligne a la même largeur.
    """
    series = [list(s) for s in series if s]
    if not series:
        return ""
    n = len(series[0])
    step, height, top = _chart_scale(max(max(s) for s in series), height)
    colors = list(colors or [])
    order = sorted(range(len(series)), key=lambda i: sum(series[i]))
    label_w = len(str(top))
    offset = label_w + 2  # libellé + espace + caractère d'axe
    grid = [[(" ", None)] * (n + offset) for _ in range(height + 1)]

    for y in range(height + 1):
        row = height - y
        label = str(y * step)
        grid[row] = [(c, None) for c in _pad(label.rjust(label_w), label_w) + " "] + [
            ("┼" if y in (0, height) else "┤", None)
        ] + grid[row][offset:]

    for i in order:
        s = series[i]
        color = colors[i] if i < len(colors) else None
        # Demi vers le haut (pas l'arrondi bancaire) ; une valeur > 0 reste au-dessus de la ligne 0.
        ys = [max(1, int(v / step + 0.5)) if v > 0 else 0 for v in s]
        for x in range(n - 1):
            y0, y1 = ys[x], ys[x + 1]
            if y0 == y1:
                grid[height - y0][x + offset] = ("─", color)
            else:
                grid[height - y1][x + offset] = ("╰" if y0 > y1 else "╭", color)
                grid[height - y0][x + offset] = ("╮" if y0 > y1 else "╯", color)
                for y in range(min(y0, y1) + 1, max(y0, y1)):
                    grid[height - y][x + offset] = ("│", color)
        if n == 1:
            grid[height - ys[0]][offset] = ("─", color)

    lines = []
    for row in grid:
        lines.append("".join(f"[{c}]{ch}[/]" if c else ch for ch, c in row))
    return "\n".join(lines)


def render_country_timeline(by_day_country, scale="week", today=None, top=4) -> str:
    """Candidatures par pays et par seau (jour/semaine/mois), une courbe par pays.

    Chaque seau est répété sur `line_cols` colonnes : paliers lisibles façon `asciichart`.
    """
    per_day = {}
    for k, countries in (by_day_country or {}).items():
        d = _parse_day(k)
        if d and countries:
            per_day[d] = countries
    if not per_day:
        return "[dim]Aucune donnée temporelle.[/dim]"
    cfg = _SCALES[scale]
    cols = cfg["line_cols"]
    axis_buckets = _bucket_axis(per_day, scale, today)

    totals: Counter = Counter()
    for countries in per_day.values():
        totals.update(countries)
    ranked = [c for c, _ in totals.most_common()]
    shown = ranked[:top]
    others = ranked[top:]
    groups = [(c, [c]) for c in shown]
    if others:
        groups.append(("Autres", others))

    per_bucket: dict = {}
    for d, countries in per_day.items():
        per_bucket.setdefault(_bucket_start(d, scale), Counter()).update(countries)
    series = []
    for _, members in groups:
        vals = [sum(per_bucket.get(b, {}).get(m, 0) for m in members) for b in axis_buckets]
        series.append([v for v in vals for _ in range(cols)])
    colors = [_COUNTRY_PALETTE[i % len(_COUNTRY_PALETTE)] for i in range(len(groups))]

    chart = render_line_chart(series, height=10, colors=colors)
    label_w = len(str(_chart_scale(max(max(s) for s in series), 10)[2]))
    offset = label_w + 2
    every = cfg["every"] if scale != "month" else 1
    marks = [
        (offset + i * cols, _bucket_label(b, scale))
        for i, b in enumerate(axis_buckets)
        if i % every == 0
    ]
    axis = _axis_labels(offset + cols * len(axis_buckets) + 5, marks)
    grand = sum(totals.values())
    legend = "   ".join(
        f"[{colors[i]}]●[/] {name}  {sum(totals[m] for m in members)} "
        f"({round(100 * sum(totals[m] for m in members) / grand)}%)"
        for i, (name, members) in enumerate(groups)
    )
    unit = {"day": "jour", "week": "semaine", "month": "mois"}[scale]
    title = f"[dim]Candidatures par pays — par {unit}[/dim]"
    return "\n".join([title, chart, axis, "", legend])


class HighlightDataTable(DataTable):
    """DataTable dont les lignes peuvent être surlignées sur toute leur largeur.

    `row_styles` associe une clé de ligne (celle passée à `add_row(key=...)`) à un
    `rich.style.Style` utilisé comme fond de ligne complet — contrairement à un
    style appliqué cellule par cellule, qui ne couvre pas le padding/l'espace résiduel.
    """

    row_styles: dict = {}

    def _get_row_style(self, row_index, base_style):
        if row_index >= 0 and self.row_styles:
            try:
                key = self._row_locations.get_key(row_index)
                style = self.row_styles.get(key)
                if style is not None:
                    return style
            except LookupError:
                pass
        return super()._get_row_style(row_index, base_style)


# Surlignage des candidatures déjà envoyées (onglet Candidatures) — vert foncé, modifiable ici.
# Valeur identique à STATUS_COLORS["accepted"] mais usage distinct (onglet différent) : les deux
# constantes restent séparées pour qu'un futur ajustement de l'une n'affecte pas l'autre.
SENT_HIGHLIGHT_COLOR = "#1e4620"
# Surlignage des offres de la dernière session de scrape (onglet Offres) — bleu foncé, modifiable ici.
NEW_OFFER_HIGHLIGHT_COLOR = "#1b3a5c"
# Surlignage des doublons confirmés (onglet Offres) — gris, teinte de `cancelled`.
DUPLICATE_HIGHLIGHT_COLOR = "#2b2b2b"
# Surlignage des doublons suspectés (onglet Offres) — violet sobre, pas de teinte fluo.
SUSPECTED_HIGHLIGHT_COLOR = "#5c4075"
# Surlignage des offres marquées invalides (onglet Offres) — rouge sourd, distinct du
# rouge vif de "rejected" (#870000, onglet Suivi) : deux onglets différents, deux sens différents.
INVALID_HIGHLIGHT_COLOR = "#5c1a1a"
# Statuts considérés comme « en attente de réponse » (candidature envoyée, décision non tombée).
# Ne pilote plus l'affichage (voir TRACK_STATUS_STYLES ci-dessous) : conservé comme définition
# métier, pendant de RESPONSE_STATUSES dans src/tracker.py.
PENDING_STATUSES = {"sent", "answered", "interview"}

# Table de correspondance statut → style de ligne pour l'onglet Suivi, construite depuis
# src/tracker.STATUS_COLORS (source de vérité unique, partagée avec src/flow.py) — modifiable
# ici pour les couleurs. `generated` est filtré (None) : une candidature pas encore envoyée
# n'a pas de surlignage. Couleur de texte : grey50 pour `cancelled`, white pour les autres.
TRACK_STATUS_STYLES = {
    status: Style(color="grey50" if status == "cancelled" else "white", bgcolor=color)
    for status, color in STATUS_COLORS.items()
    if color is not None
}

CONSOLE_URL = "https://console.anthropic.com/settings/billing"

# Animation du spinner affiché dans la barre de statut pendant un scrape.
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

STATUS_KEYS = {
    "s": "sent",
    "a": "answered",
    "i": "interview",
    "j": "rejected",
    "v": "accepted",
    "z": "cancelled",
}

HELP_TEXT = """\
[b]Raccourcis globaux[/b]
  [b #7aa2f7]1 / 2 / 3 / 4 / 5 / 6[/]  Onglets : Offres · Génération · Candidatures · Suivi · Stats · Réglages
  [b #7aa2f7]r[/]                  Recharger les données depuis le disque
  [b #7aa2f7]S / s[/]              Lancer scrape + filtre + rapport (spinner dans la barre de statut)
                     (`s` = statut « envoyée » dans l'onglet Suivi uniquement)
  [b #7aa2f7]^S[/]                 Idem, en incluant aussi les sources FR-only (groupe 'fr', ex: HelloWork, WTTJ FR)
  [b #7aa2f7]c[/]                  Ouvrir la Claude Console (recharge crédit API)
  [b #7aa2f7]?[/]                  Cette aide
  [b #7aa2f7]q[/]                  Quitter

Règle générale : [b #7aa2f7]o[/] ouvre toujours l'OFFRE (navigateur), [b #7aa2f7]O[/]
ouvre toujours le DOSSIER de candidature — même sens partout où ces touches existent.

[b]Onglet Offres[/b]
  [b #7aa2f7]↑ / ↓[/]   Naviguer
  [b #7aa2f7]espace[/]  Cocher / décocher l'offre pour génération
  [b #7aa2f7]o[/]       Ouvrir l'offre dans le navigateur
  [b #7aa2f7]+ / -[/]   Augmenter / diminuer le seuil de score
  [b #7aa2f7]h[/]       Masquer / afficher les offres déjà candidatées, doublons et invalides
  [b #7aa2f7]d[/]       Cycler la période affichée (tout · session · 7j · 30j)
  [b #7aa2f7]D[/]       Choisir une session de scrape précise
  [b #7aa2f7]g[/]       Passer à l'onglet Génération
  [b #7aa2f7]m[/]       Marquer/démarquer l'offre comme doublon d'une candidature existante
                     (`?` = entreprise déjà candidatée, à vérifier — pas forcément un doublon)
  [b #7aa2f7]X[/]       Marquer/démarquer l'offre comme invalide (offre fermée/annulée/mauvais
                     contrat…) — retire aussi l'offre de la file de génération
  (les offres de la dernière session sont surlignées en bleu)

[b]Onglet Génération[/b]
  [b #7aa2f7]espace[/]  Cocher / décocher une offre (filtre optionnel pour g / L, sinon traite tout)
  [b #7aa2f7]g[/]       Lancer la génération (lettre + CV)
  [b #7aa2f7]L[/]       Générer la lettre seule (sans CV adapté — économie d'un appel API)
  [b #7aa2f7]A[/]       Ajouter une offre manuelle (fichier HTML ou URL) à la liste, puis g / L
  [b #7aa2f7]x[/]       Retirer de la file (lignes cochées, sinon la ligne sous le curseur)
  [b #7aa2f7]N[/]       Éditer la note de génération de l'offre sous le curseur (consigne
             injectée dans les prompts lettre et CV au prochain g / L)
  [b #7aa2f7]T[/]       Enregistrer dans le suivi SANS générer (aucun appel API, aucun
             dossier — pour les offres qui n'ont besoin ni de lettre ni de CV)
  [b #7aa2f7]o[/]       Ouvrir l'offre dans le navigateur
  [b #7aa2f7]O[/]       Ouvrir le dossier de la candidature générée
  (la file est conservée entre deux lancements de la TUI — data/gen_queue.json)

[b]Onglet Candidatures[/b]
  [b #7aa2f7]↑ / ↓[/]   Naviguer dans les candidatures générées
  [b #7aa2f7]entrée[/]  Basculer l'aperçu lettre ↔ CV (HTML brut)
  [b #7aa2f7]e[/]       Ouvrir lettre.md dans l'éditeur (retouches)
  [b #7aa2f7]p[/]       Régénérer lettre.pdf depuis lettre.md (après retouche)
  [b #7aa2f7]u[/]       Régénérer cv_adapte.pdf depuis cv_adapte.html
  [b #7aa2f7]o[/]       Ouvrir l'offre dans le navigateur
  [b #7aa2f7]O[/]       Ouvrir le dossier de la candidature
  (les candidatures envoyées ou plus sont surlignées en vert)

[b]Onglet Suivi[/b]
  [b #7aa2f7]s[/]  Statut → envoyée (sent)
  [b #7aa2f7]a[/]  Statut → réponse reçue (answered)
  [b #7aa2f7]i[/]  Statut → entretien (interview)
  [b #7aa2f7]j[/]  Statut → refusée (rejected)
  [b #7aa2f7]v[/]  Statut → acceptée (accepted)
  [b #7aa2f7]z[/]  Statut → annulée (offre qui n'existe plus)
  [b #7aa2f7]n[/]  Éditer la note de la candidature
  [b #7aa2f7]o[/]  Ouvrir l'offre dans le navigateur
  (envoyée en jaune · réponse en ambre · entretien en violet ·
   acceptée en vert · refusée en rouge · annulée en gris)
  (le panneau de droite affiche la timeline de la candidature sous le curseur)

[b]Onglet Stats[/b]
  Retour de candidature (taux d'acceptation, taux de réponse,
  délai moyen), localisation des stages, histogramme des
  candidatures au cours du temps, parcours des candidatures
  (arbre de statuts) et répartition mensuelle des statuts atteints.
  [b #7aa2f7]f[/]  Exporter le diagramme de Sankey du flux (HTML, ouvert dans le navigateur)
  [b #7aa2f7]t[/]  Échelle de temps des graphes (histogramme, courbes par pays) : jour · semaine · mois

[b]Onglet Réglages[/b]
  Liste les fichiers de configuration du pipeline (clés API, sujet de
  recherche, profil, sources, prompts, templates, données du CV) avec
  leur état (personnalisé · défaut du paquet · absent) et leur date de
  modification, plus deux lignes dédiées : « Sources de scraping » et « Modèles Claude ».
  Fortement recommandé : le prompt de scoring livré (config/prompts/filter.txt)
  et le pré-filtre de mots-clés (search.yaml) sont génériques — les
  personnaliser avec des exemples et mots-clés réels de ta recherche
  améliore nettement la précision du classement des offres.
  [b #7aa2f7]↑ / ↓[/]   Naviguer
  [b #7aa2f7]entrée[/]  Sur une ligne de fichier : ouvrir dans l'éditeur (copie d'abord le
             défaut du paquet vers la config utilisateur si besoin, avant édition).
             Sur la ligne « Sources de scraping » : activer/désactiver
             individuellement une source (espace pour basculer dans la modale,
             échap pour fermer — écrit immédiatement dans config/sources.yaml,
             indépendant des groupes --with/--without)
             Sur la ligne « Modèles Claude » : choisir le modèle du filtre, de la
             lettre et du CV (entrée sur une tâche : dernier Haiku/Sonnet/Opus
             automatique ou modèle épinglé ; R : rafraîchir la liste ; échap :
             fermer — écrit immédiatement dans config/search.yaml)
  [b #7aa2f7]r[/]       Recharger après édition (relit search.yaml, invalide le cache)
"""


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", "Fermer")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static(HELP_TEXT)
            yield Label("[dim]échap pour fermer[/dim]")


class NoteScreen(ModalScreen[str]):
    """Saisie d'une note pour une candidature."""

    BINDINGS = [Binding("escape", "dismiss", "Annuler")]

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="note-box"):
            yield Label("Note (entrée pour valider, échap pour annuler) :")
            yield Input(value=self._initial, id="note-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class GenNotesScreen(ModalScreen[str]):
    """Consigne de génération d'une offre (`gen_notes`) — injectée dans les prompts
    lettre et CV au prochain g / L. Sur le modèle de `ProfileScreen` (jobsearch/wizard.py) :
    un TextArea ne se soumet pas sur `entrée` (utile pour les retours à la ligne), d'où
    `ctrl+s` en `priority=True`."""

    BINDINGS = [
        Binding("escape", "dismiss", "Annuler"),
        Binding("ctrl+s", "submit", "Valider", priority=True),
    ]

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="gen-notes-box"):
            yield Label("Consigne de génération pour cette offre (ctrl+s pour valider, échap pour annuler) :")
            yield TextArea(self._initial, id="gen-notes-input")

    def on_mount(self) -> None:
        self.query_one("#gen-notes-input", TextArea).focus()

    def action_submit(self) -> None:
        self.dismiss(self.query_one("#gen-notes-input", TextArea).text)


class ManualEntryScreen(ModalScreen):
    """Ajout manuel d'une candidature depuis un fichier HTML ou une URL directe."""

    BINDINGS = [Binding("escape", "dismiss", "Annuler")]

    def compose(self) -> ComposeResult:
        with Vertical(id="manual-box"):
            yield Label("Ajouter une candidature manuelle — fichier HTML OU URL (entrée pour valider) :")
            yield Input(placeholder="Chemin fichier HTML (ex: ~/Downloads/offre.html)", id="me-file")
            yield Input(placeholder="… ou URL directe", id="me-url")
            yield Input(placeholder="URL source (optionnel, référencée dans la lettre)", id="me-source")
            yield Input(placeholder="Entreprise (optionnel, surcharge l'extraction)", id="me-company")
            yield Input(placeholder="Titre (optionnel, surcharge l'extraction)", id="me-title")
            yield Label("", id="me-error")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        file_val = self.query_one("#me-file", Input).value.strip()
        url_val = self.query_one("#me-url", Input).value.strip()
        error = self.query_one("#me-error", Label)
        if bool(file_val) == bool(url_val):
            error.update("[red]Renseigne soit un fichier, soit une URL (un seul des deux).[/red]")
            return
        self.dismiss({
            "file": file_val,
            "url": url_val,
            "url_source": self.query_one("#me-source", Input).value.strip(),
            "company": self.query_one("#me-company", Input).value.strip(),
            "title": self.query_one("#me-title", Input).value.strip(),
        })


class SessionPickerScreen(ModalScreen[str]):
    """Choix d'une session de scrape (une date) parmi celles présentes dans les offres."""

    BINDINGS = [Binding("escape", "dismiss", "Annuler")]

    def __init__(self, sessions: list[tuple[str, int]]) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="session-box"):
            yield Label("Choisir une session de scrape (entrée pour valider, échap pour annuler) :")
            option_list = OptionList(
                *[Option(f"{d} — {n} offre(s)", id=d) for d, n in self._sessions]
            )
            yield option_list

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class DuplicatePickerScreen(ModalScreen[str]):
    """Choix de l'offre candidatée dont l'offre courante est un doublon.

    `same_company` et `others` : deux listes distinctes de (offer_id, label)
    calculées par l'appelant (`action_mark_duplicate`). Les options de
    `same_company` sont numérotées 1-9 et sélectionnables directement au
    clavier.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Annuler"),
        *[Binding(str(n), f"pick_number({n})", show=False) for n in range(1, 10)],
    ]

    def __init__(
        self,
        same_company: list[tuple[str, str]],
        others: list[tuple[str, str]],
        title: str,
        company: str = "",
    ) -> None:
        super().__init__()
        self._same_company = same_company
        self._others = others
        self._title = title
        self._company = company
        self._numbered_ids = [offer_id for offer_id, _ in same_company[:9]]

    def compose(self) -> ComposeResult:
        with Vertical(id="dup-box"):
            yield Label(f"{self._title} (entrée pour valider · 1-9 pour la même entreprise · échap pour annuler) :")
            options: list[Option | None] = []
            if self._same_company:
                options.append(Option(f"── Même entreprise ({self._company}) ──", disabled=True))
                for i, (offer_id, label) in enumerate(self._same_company, start=1):
                    prefix = f"{i}. " if i <= 9 else ""
                    options.append(Option(f"{prefix}{label}", id=offer_id))
            if self._same_company and self._others:
                options.append(None)
            if self._others:
                options.append(Option("── Autres entreprises ──", disabled=True))
                for offer_id, label in self._others:
                    options.append(Option(label, id=offer_id))
            option_list = OptionList(*options)
            yield option_list

    def action_pick_number(self, n: int) -> None:
        if n <= len(self._numbered_ids):
            self.dismiss(self._numbered_ids[n - 1])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class InvalidReasonScreen(ModalScreen[str]):
    """Choix du motif de marquage « offre invalide » — calquée sur DuplicatePickerScreen.

    Motifs numérotés 1-4 (celui de `INVALID_REASONS`, toujours ≤ 9) et sélectionnables
    directement au clavier, comme les cibles « même entreprise » du picker de doublons.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Annuler"),
        *[Binding(str(n), f"pick_number({n})", show=False) for n in range(1, len(INVALID_REASONS) + 1)],
    ]

    def __init__(self, title: str = "") -> None:
        super().__init__()
        self._title = title
        self._codes = list(INVALID_REASONS.keys())

    def compose(self) -> ComposeResult:
        with Vertical(id="invalid-box"):
            yield Label(f"{self._title} (entrée pour valider · 1-{len(self._codes)} au clavier · échap pour annuler) :")
            options = [
                Option(f"{i}. {INVALID_REASONS[code]}", id=code)
                for i, code in enumerate(self._codes, start=1)
            ]
            yield OptionList(*options)

    def action_pick_number(self, n: int) -> None:
        if n <= len(self._codes):
            self.dismiss(self._codes[n - 1])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class ConfirmScreen(ModalScreen[bool]):
    """Confirmation oui/non générique (garde-fou inter-entreprises, enregistrement sans génération)."""

    BINDINGS = [
        Binding("escape", "dismiss_no", "Annuler"),
        # `enter` valide : sans lui, la modale semblait figée — le réflexe est
        # d'appuyer sur entrée, pas sur `y`.
        Binding("enter", "dismiss_yes", "Confirmer", show=False),
        Binding("y", "dismiss_yes", "Confirmer", show=False),
        Binding("n", "dismiss_no", "Annuler", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._message)
            yield Label("\\[entrée / y] Confirmer   \\[n / échap] Annuler")

    def action_dismiss_yes(self) -> None:
        self.dismiss(True)

    def action_dismiss_no(self) -> None:
        self.dismiss(False)


class SourcesScreen(ModalScreen):
    """Activation/désactivation individuelle des sources de scraping.

    Chaque bascule (`espace`) écrit immédiatement sur `config/sources.yaml`
    via `jobsearch.sources_toggle.set_source_enabled` (édition ligne à ligne,
    commentaires préservés) — pas de bouton "valider" ni d'état intermédiaire
    à confirmer, contrairement aux autres modales de saisie.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Fermer"),
        Binding("space", "toggle_source", "Activer/désactiver", show=False),
    ]

    def __init__(self, config_dir: str = PATHS.config_dir) -> None:
        super().__init__()
        self._config_dir = config_dir

    def compose(self) -> ComposeResult:
        with Vertical(id="sources-box"):
            yield Label("Sources de scraping (espace : activer/désactiver · échap : fermer) :")
            yield DataTable(id="sources-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        table.add_columns("État", "Nom", "Type", "Pays", "Groupe")
        self.refresh_table()

    def refresh_table(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        # Préserve la ligne sous le curseur, comme `StageTUI._keep_cursor` — cette
        # modale n'a pas accès à cette méthode (définie sur `StageTUI`), on
        # réimplémente donc la même logique en local plutôt que de la partager.
        previous = None
        if table.row_count:
            key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            previous = str(key.value)
        table.clear()
        for s in load_sources_state(self._config_dir):
            state_cell = "[green]✓[/green]" if s["enabled"] else "[red]✗[/red]"
            group_cell = f"{s['group']} (optionnelle)" if s["optional"] else ""
            table.add_row(
                *_cells([state_cell, s["name"], s["type"], s["country"], group_cell]),
                key=s["name"],
            )
        if previous:
            try:
                table.move_cursor(row=table.get_row_index(previous))
            except (KeyError, RowDoesNotExist):
                pass  # la source n'existe plus (fichier édité entre-temps)

    def action_toggle_source(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        if table.row_count == 0:
            return
        key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        name = str(key.value)
        current = next(
            (s for s in load_sources_state(self._config_dir) if s["name"] == name), None
        )
        if current is None:
            return
        set_source_enabled(name, not current["enabled"], self._config_dir)
        self.refresh_table()


class ModelChoiceScreen(ModalScreen[str]):
    """Liste de choix d'un modèle pour une tâche — renvoie la spécification choisie
    (`auto:sonnet`, `claude-sonnet-5-5`…). L'entrée « Modèle local » est grisée (phase ultérieure)."""

    BINDINGS = [
        Binding("escape", "dismiss", "Annuler"),
    ]

    def __init__(self, task_label: str, current: str) -> None:
        super().__init__()
        self._task_label = task_label
        self._current = current

    def compose(self) -> ComposeResult:
        from jobsearch.models import list_available

        with Vertical(id="models-choice-box"):
            yield Label(f"Modèle pour {self._task_label} (entrée : choisir · échap : annuler) :")
            options = [
                Option("Dernier Haiku (auto)", id="auto:haiku"),
                Option("Dernier Sonnet (auto)", id="auto:sonnet"),
                Option("Dernier Opus (auto)", id="auto:opus"),
            ]
            for m in list_available():
                options.append(Option(f"{m.get('display_name') or m['id']}  [dim]{m['id']}[/dim]", id=m["id"]))
            options.append(Option("Modèle local (bientôt)", id="local", disabled=True))
            option_list = OptionList(*options)
            yield option_list

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        for i in range(option_list.option_count):
            if option_list.get_option_at_index(i).id == self._current:
                option_list.highlighted = i
                break
        else:
            option_list.highlighted = 0
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id and event.option.id != "local":
            self.dismiss(event.option.id)


class ModelsScreen(ModalScreen):
    """Choix du modèle Claude de chaque tâche (filtre, lettre, CV).

    Chaque choix est écrit immédiatement dans `search.yaml` (`jobsearch.models.set_task_model`,
    édition ligne à ligne, commentaires préservés), comme `SourcesScreen`.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Fermer"),
        # Pas de conflit avec « une touche = un sens » : `R` n'est lié nulle part ailleurs
        # (`r` = recharger, en minuscule, au niveau de l'app).
        Binding("R", "refresh_models", "Rafraîchir la liste", show=False),
    ]

    def __init__(self, config_dir: str = PATHS.config_dir) -> None:
        super().__init__()
        self._config_dir = config_dir

    def compose(self) -> ComposeResult:
        with Vertical(id="models-box"):
            yield Label("Modèles Claude (entrée : changer · R : rafraîchir la liste · échap : fermer) :")
            yield DataTable(id="models-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#models-table", DataTable)
        table.add_columns("Tâche", "Réglage", "Modèle résolu")
        self.refresh_table()

    def _search(self):
        return Search(load_search(self._config_dir))

    def refresh_table(self) -> None:
        from jobsearch.models import ModelSpecError, TASKS, TASK_LABELS, resolve_model

        table = self.query_one("#models-table", DataTable)
        previous = None
        if table.row_count:
            key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            previous = str(key.value)
        table.clear()
        search = self._search()
        for task in TASKS:
            spec = search.get("models", task, "model", default="")
            try:
                resolved = resolve_model(spec, refresh=False)
            except ModelSpecError as e:
                resolved = f"[red]{e}[/red]"
            table.add_row(*_cells([TASK_LABELS[task][0].upper() + TASK_LABELS[task][1:], spec, resolved]), key=task)
        if previous:
            try:
                table.move_cursor(row=table.get_row_index(previous))
            except (KeyError, RowDoesNotExist):
                pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        from jobsearch.models import TASK_LABELS

        event.stop()
        task = str(event.row_key.value)
        current = self._search().get("models", task, "model", default="")

        def _apply(spec: str | None) -> None:
            if not spec:
                return
            from jobsearch.models import set_task_model

            set_task_model(task, spec, self._config_dir)
            get_search.cache_clear()  # même mécanisme que `r` (action_reload)
            self.refresh_table()

        self.app.push_screen(ModelChoiceScreen(TASK_LABELS[task], current), _apply)

    def action_refresh_models(self) -> None:
        from jobsearch.models import list_available

        n = len(list_available(refresh=True))
        self.notify(f"{n} modèle(s) dans la liste." if n else "Liste indisponible (clé API absente ou hors ligne).")
        self.refresh_table()


class StageTUI(App):
    TITLE = "JobSearch — pipeline de stage"

    CSS = """
    #detail {
        width: 45%;
        border-left: solid $accent;
        padding: 0 1;
    }
    #offers-table { width: 55%; }
    #letters-table { width: 42%; }
    #track-table { width: 60%; }
    #track-detail {
        width: 40%;
        border-left: solid $accent;
        padding: 0 1;
    }
    #settings-table { width: 60%; }
    #settings-detail {
        width: 40%;
        border-left: solid $accent;
        padding: 0 1;
    }
    #stats-header { padding: 1 1 0 1; height: auto; }
    .stats-section {
        border: round $accent;
        height: auto;
        padding: 0 1;
        margin: 1 1 0 1;
    }
    .stats-left { width: 42%; height: auto; padding: 0 1; }
    .stats-right { width: 1fr; height: auto; padding: 0 1; }
    #stats-heatmap { padding: 1 1; }
    #stats-months { padding: 1 1; }
    #stats-country-time { padding: 1 1; }
    #letter-detail {
        width: 58%;
        border-left: solid $accent;
        padding: 0 1;
    }
    #help-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        /* La plus longue ligne de HELP_TEXT (balises Rich retirées) mesure 102
           caractères ; bordure (2) + padding horizontal (4) => largeur utile de
           width - 6. 112 laisse une marge confortable sans repliement. */
        width: 112;
        max-width: 95%;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    #note-box, #manual-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 70;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    #manual-box Input { margin-bottom: 1; }
    #gen-notes-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    #gen-notes-input { height: 10; margin-top: 1; }
    #me-error { color: $error; height: auto; }
    #session-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 50;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    #dup-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    #confirm-box {
        background: $surface;
        border: round $error;
        padding: 1 2;
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    #invalid-box {
        background: $surface;
        border: round $error;
        padding: 1 2;
        width: 70;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    #sources-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    #models-box, #models-choice-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    HelpScreen, NoteScreen, GenNotesScreen, ManualEntryScreen, SessionPickerScreen, DuplicatePickerScreen, ConfirmScreen, InvalidReasonScreen, SourcesScreen, ModelsScreen, ModelChoiceScreen { align: center middle; }
    #bottom-bar { dock: bottom; height: auto; }
    #status-bar { height: 1; background: $panel; color: $text-muted; }
    #update-banner { height: 1; background: $panel; color: $warning; display: none; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quitter"),
        Binding("question_mark", "help", "Aide"),
        Binding("r", "reload", "Recharger"),
        Binding("c", "open_console", "Console API"),
        Binding("S", "scrape", "Scrape"),
        # ^S : scrape incluant les sources FR-only (groupe 'fr', ex: HelloWork).
        Binding("ctrl+s", "scrape_fr", show=False),
        Binding("1", "tab('offers')", show=False),
        Binding("2", "tab('gen')", show=False),
        Binding("3", "tab('letters')", show=False),
        Binding("4", "tab('track')", show=False),
        Binding("5", "tab('stats')", show=False),
        Binding("6", "tab('settings')", show=False),
        Binding("e", "edit_letter", show=False),
        Binding("p", "render_letter_pdf", show=False),
        Binding("u", "render_cv_pdf", show=False),
        Binding("space", "toggle_select", "Cocher", show=False),
        # `o` = ouvrir l'OFFRE (navigateur), `O` = ouvrir le DOSSIER de candidature —
        # sens unique sur tous les onglets concernés (1/2/3/4 pour `o`, 2/3 pour `O`) :
        # ne jamais réutiliser ces touches pour autre chose sur un nouvel onglet.
        Binding("o", "open_url", "Ouvrir l'offre", show=False),
        Binding("O", "open_offer_url", "Ouvrir le dossier", show=False),
        Binding("g", "generate", "Générer", show=False),
        Binding("L", "generate_letter_only", "Lettre seule", show=False),
        Binding("A", "add_manual", "Ajout manuel", show=False),
        Binding("x", "remove_from_queue", "Retirer de la file", show=False),
        Binding("N", "edit_gen_notes", show=False),
        Binding("T", "record_only", show=False),
        Binding("plus,equals_sign", "threshold(1)", show=False),
        Binding("minus", "threshold(-1)", show=False),
        Binding("h", "toggle_hide_done", show=False),
        Binding("m", "mark_duplicate", show=False),
        # `X` = marquer/démarquer invalide (onglet Offres uniquement) — dissocié de
        # `i`, qui vaut désormais uniquement le statut "entretien" (onglet Suivi),
        # comme les autres touches de la famille de statuts s/a/i/j/v/z.
        Binding("X", "mark_invalid", show=False),
        Binding("d", "cycle_period", show=False),
        Binding("D", "pick_session", show=False),
        Binding("n", "edit_note", show=False),
        Binding("s", "sent_or_scrape", show=False),
        Binding("a", "set_status('answered')", show=False),
        Binding("i", "set_status('interview')", show=False),
        Binding("j", "set_status('rejected')", show=False),
        Binding("v", "set_status('accepted')", show=False),
        Binding("z", "set_status('cancelled')", show=False),
        Binding("f", "export_flow", show=False),
        Binding("t", "cycle_stats_scale", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.offers: dict[str, dict] = {}
        self.selected: set[str] = set(load_queue())
        self.gen_checked: set[str] = set()  # coches de l'onglet Génération, filtre optionnel — non persisté
        self.threshold = get_search().threshold
        self.hide_done = False
        self._stats_scale = "week"  # échelle des graphes temporels de l'onglet Stats : day | week | month
        self.period = "all"  # "all" | "session" | "7d" | "30d"
        self.session_date: str | None = None
        self.gen_status: dict[str, tuple[str, str]] = {}  # id -> (icone, info/dossier)
        # Indicateur de scrape en cours (spinner dans la barre de statut)
        self._scrape_phase: str | None = None
        self._scrape_started = 0.0
        self._spinner_i = 0
        self._spinner_timer = None

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="offers"):
            with TabPane("1 · Offres", id="offers"):
                with Horizontal():
                    yield HighlightDataTable(id="offers-table", cursor_type="row")
                    yield VerticalScroll(Static("", id="detail-text"), id="detail")
            with TabPane("2 · Génération", id="gen"):
                yield HighlightDataTable(id="gen-table", cursor_type="row")
            with TabPane("3 · Candidatures", id="letters"):
                with Horizontal():
                    yield HighlightDataTable(id="letters-table", cursor_type="row")
                    yield VerticalScroll(Markdown("", id="letter-preview"), id="letter-detail")
            with TabPane("4 · Suivi", id="track"):
                with Horizontal():
                    yield HighlightDataTable(id="track-table", cursor_type="row")
                    yield VerticalScroll(Static("", id="track-detail-text"), id="track-detail")
            with TabPane("5 · Stats", id="stats"):
                with VerticalScroll(id="stats-scroll"):
                    yield Static("", id="stats-header")
                    with Horizontal(id="stats-return", classes="stats-section"):
                        yield Static("", id="stats-return-text", classes="stats-left")
                        yield Static("", id="stats-status-bars", classes="stats-right")
                    with Horizontal(id="stats-loc", classes="stats-section"):
                        yield Static("", id="stats-loc-text", classes="stats-left")
                        yield Static("", id="stats-loc-bars", classes="stats-right")
                    yield Static("", id="stats-heatmap", classes="stats-section")
                    yield Static("", id="stats-months", classes="stats-section")
                    yield Static("", id="stats-country-time", classes="stats-section")
                    yield Static("", id="stats-flow", classes="stats-section")
                    yield Static("", id="stats-flow-months", classes="stats-section")
            with TabPane("6 · Réglages", id="settings"):
                with Vertical():
                    yield Static(
                        "[b]Recommandé[/b] : personnalise le prompt de scoring (config/prompts/filter.txt) "
                        "et le pré-filtre (search.yaml) avec des exemples et mots-clés réels de ta recherche — "
                        "le barème livré est générique, un jeu d'exemples calibré donne un classement nettement plus précis. "
                        "[dim]Ligne « Sources de scraping » : `entrée` pour activer/désactiver "
                        "individuellement une source.[/dim]",
                        id="settings-banner",
                    )
                    with Horizontal():
                        yield HighlightDataTable(id="settings-table", cursor_type="row")
                        yield VerticalScroll(Static("", id="settings-detail-text"), id="settings-detail")
        # Barre de statut + Footer regroupés dans un conteneur docké : deux widgets
        # `dock: bottom` frères se superposent sur la même ligne (l'un masque l'autre).
        with Vertical(id="bottom-bar"):
            # Bannière discrète et persistante (distincte de #status-bar, qui affiche
            # des messages transitoires et serait écrasée en quelques secondes) —
            # masquée (`display: none`) tant qu'aucune mise à jour n'est disponible.
            yield Static("", id="update-banner")
            yield Static("", id="status-bar")
            yield Footer()

    def on_mount(self) -> None:
        self.query_one("#offers-table", DataTable).add_columns(
            "✓", "Score", "Titre", "Entreprise", "Lieu", "Pays", "Source", "Vu le"
        )
        self.query_one("#gen-table", DataTable).add_columns(
            "✓", "État", "Titre", "Entreprise", "Note", "Dossier / info"
        )
        self.query_one("#letters-table", DataTable).add_columns(
            "Dossier", "Lettre", "PDF", "CV PDF", "Modifiée"
        )
        self.query_one("#track-table", DataTable).add_columns(
            "ID", "Statut", "Entreprise", "Titre", "Générée", "Envoyée", "Notes"
        )
        self.query_one("#settings-table", DataTable).add_columns(
            "Rôle", "État", "Modifié le", "Emplacement"
        )
        self.query_one("#stats-return").border_title = "Retour de candidature"
        self.query_one("#stats-loc").border_title = "Localisation des stages"
        self.query_one("#stats-heatmap").border_title = "Activité jour par jour"
        self.query_one("#stats-months").border_title = "Candidatures au cours du temps"
        self.query_one("#stats-country-time").border_title = "Répartition par pays au cours du temps"
        self.query_one("#stats-flow").border_title = "Parcours des candidatures"
        self.query_one("#stats-flow-months").border_title = "Statuts atteints par mois"
        self._check_update_banner()
        self.action_reload()

    def _check_update_banner(self) -> None:
        """Alerte persistante « mise à jour disponible », discrète (pas de temporisation
        15 min ici, contrairement à la CLI : la TUI reste ouverte, pas relancée à chaque
        commande). `status_notice()` avale déjà toute exception réseau."""
        from jobsearch.updates import status_notice

        from jobsearch.models import model_change_notice

        notice = status_notice()
        model_notice = model_change_notice()  # avale déjà toute exception, affiché une fois
        banner = self.query_one("#update-banner", Static)
        if notice or model_notice:
            text = " · ".join(n for n in (f"⬆ {notice}" if notice else "", f"⟳ {model_notice}" if model_notice else "") if n)
            banner.update(f" {text}")
            banner.styles.display = "block"
        else:
            banner.update("")
            banner.styles.display = "none"

    # ---------- data ----------

    def action_reload(self) -> None:
        # Après une édition de search.yaml depuis l'onglet Réglages, `get_search()` (lru_cache)
        # et `self.threshold` (capturé une fois dans __init__) doivent être invalidés ici :
        # sinon `r` ne suffit pas à répercuter un seuil de scoring modifié.
        get_search.cache_clear()
        self.threshold = get_search().threshold
        self.offers = {o["id"]: o for o in load_filtered_offers(PATHS.data_dir)}
        self.selected &= set(self.offers)
        self._persist_queue()
        self.refresh_offers()
        self.refresh_gen()
        self.refresh_letters()
        self.refresh_track()
        self.refresh_stats()
        self.refresh_settings()
        self.notify_status(f"{len(self.offers)} offre(s) filtrée(s) chargée(s).")

    def _generated_ids(self) -> set[str]:
        return {oid for oid, (icon, _) in self.gen_status.items() if icon == "✅"}

    def _persist_queue(self) -> None:
        save_queue(self.selected - self._generated_ids())

    def _applied_ids(self) -> set[str]:
        return {a["offer_id"] for a in load_applications()}

    def _scrape_sessions(self) -> list[str]:
        """Dates `date_scraped` distinctes des offres chargées, triées décroissant."""
        dates = set()
        for o in self.offers.values():
            d = o.get("date_scraped", "")
            if d and len(d) == 10 and d[4] == "-" and d[7] == "-":
                dates.add(d)
        return sorted(dates, reverse=True)

    def _latest_session(self) -> str | None:
        sessions = self._scrape_sessions()
        return sessions[0] if sessions else None

    def _period_cutoff(self) -> tuple[str | None, str | None]:
        """Retourne (exact_date, min_date) selon self.period. Comparaison de chaînes ISO."""
        if self.period == "session":
            return (self.session_date or self._latest_session(), None)
        if self.period == "7d":
            return (None, (date.today() - timedelta(days=7)).isoformat())
        if self.period == "30d":
            return (None, (date.today() - timedelta(days=30)).isoformat())
        return (None, None)

    def _suspected_companies(self) -> set[str]:
        """Entreprises normalisées ayant déjà une candidature — pour l'indicateur `?`."""
        return {
            _normalize_company(a.get("company", ""))
            for a in load_applications()
            if a.get("company")
        }

    def refresh_offers(self) -> None:
        table = self.query_one("#offers-table", HighlightDataTable)
        applied = self._applied_ids()
        suspected_companies = self._suspected_companies()
        latest_session = self._latest_session()
        exact_date, min_date = self._period_cutoff()
        rows = [
            o for o in self.offers.values()
            if o["filter_result"].get("score", 0) >= self.threshold
            and not (self.hide_done and (o["id"] in applied or o.get("duplicate_of") or o.get("invalid")))
            and (exact_date is None or o.get("date_scraped", "") == exact_date)
            and (min_date is None or o.get("date_scraped", "") >= min_date)
        ]
        rows.sort(key=lambda o: o["filter_result"].get("score", 0), reverse=True)
        table.row_styles = {
            o["id"]: Style(bgcolor=NEW_OFFER_HIGHLIGHT_COLOR)
            for o in rows if latest_session and o.get("date_scraped", "") == latest_session
        }
        table.row_styles.update({
            o["id"]: Style(bgcolor=SUSPECTED_HIGHLIGHT_COLOR)
            for o in rows
            if not o.get("duplicate_of")
            and o["id"] not in applied
            and _normalize_company(o.get("company", "")) in suspected_companies
        })
        table.row_styles.update({
            o["id"]: Style(bgcolor=DUPLICATE_HIGHLIGHT_COLOR)
            for o in rows if o.get("duplicate_of")
        })
        table.row_styles.update({
            o["id"]: Style(bgcolor=INVALID_HIGHLIGHT_COLOR)
            for o in rows if o.get("invalid")
        })
        table.row_styles.update({
            o["id"]: Style(color="black", bgcolor="green3")
            for o in rows if o["id"] in self.selected
        })
        with self._keep_cursor("offers-table"):
            table.clear()
            for o in rows:
                # Le style de ligne (ci-dessus) et ce marqueur doivent suivre EXACTEMENT
                # la même condition/le même ordre — un décalage avait déjà fait surligner
                # 22 offres pour 6 réellement concernées (cf. CLAUDE.md).
                selected = o["id"] in self.selected
                if selected:
                    mark = "▶"
                elif o.get("invalid"):
                    mark = "⛔"
                elif o.get("duplicate_of"):
                    mark = "⧉"
                elif o["id"] in applied:
                    mark = "✓"
                elif _normalize_company(o.get("company", "")) in suspected_companies:
                    mark = "?"
                else:
                    mark = ""
                table.add_row(
                    *_cells([
                        mark,
                        o["filter_result"].get("score", "?"),
                        o.get("title", "")[:50],
                        (o.get("company", "") or "?")[:24],
                        o.get("location", "")[:20],
                        o.get("country", ""),
                        o.get("source", "")[:18],
                        o.get("date_scraped", ""),
                    ]),
                    key=o["id"],
                )
        hidden = " · candidatées masquées" if self.hide_done else ""
        if self.period == "session":
            period_txt = f" · session {exact_date}" if exact_date else ""
        elif self.period == "7d":
            period_txt = " · 7 derniers jours"
        elif self.period == "30d":
            period_txt = " · 30 derniers jours"
        else:
            period_txt = ""
        n_duplicates = sum(1 for o in rows if o.get("duplicate_of"))
        dup_txt = f" · {n_duplicates} doublon(s)" if n_duplicates else ""
        n_invalid = sum(1 for o in rows if o.get("invalid"))
        invalid_txt = f" · {n_invalid} invalide(s)" if n_invalid else ""
        self.sub_title = (
            f"seuil ≥ {self.threshold}{period_txt} · {len(rows)} offre(s){hidden}"
            f" · {len(self.selected)} cochée(s){dup_txt}{invalid_txt}"
        )
        self.update_detail()

    def refresh_gen(self) -> None:
        table = self.query_one("#gen-table", HighlightDataTable)
        table.row_styles = {
            oid: Style(color="black", bgcolor="green3")
            for oid in self.selected if oid in self.gen_checked
        }
        with self._keep_cursor("gen-table"):
            table.clear()
            for oid in sorted(self.selected):
                o = self.offers.get(oid, {})
                icon, info = self.gen_status.get(oid, ("·", "en attente — g pour lancer"))
                mark = "▶" if oid in self.gen_checked else ""
                note = (o.get("gen_notes", "") or "").replace("\n", " ")[:40]
                table.add_row(
                    mark, icon, o.get("title", "?")[:50], (o.get("company", "") or "?")[:24], note,
                    info[:120], key=oid,
                )

    def _candidature_dirs(self) -> list[str]:
        base = os.path.join(PATHS.output_dir, "candidatures")
        if not os.path.isdir(base):
            return []
        dirs = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        return sorted(dirs, key=os.path.getmtime, reverse=True)

    def refresh_letters(self) -> None:
        import datetime

        table = self.query_one("#letters-table", HighlightDataTable)
        status_by_offer = {a["offer_id"]: a["status"] for a in load_applications()}
        dirs = self._candidature_dirs()

        def _letter_row_style(folder: str):
            status = status_by_offer.get(offer_id_from_folder(folder), "generated")
            if status == "cancelled":
                return Style(color="grey50", bgcolor=STATUS_COLORS["cancelled"])
            if status != "generated":
                return Style(color="white", bgcolor=SENT_HIGHLIGHT_COLOR)
            return None

        table.row_styles = {
            folder: style
            for folder in dirs
            if (style := _letter_row_style(folder)) is not None
        }
        with self._keep_cursor("letters-table"):
            table.clear()
            for folder in dirs:
                md = os.path.join(folder, "lettre.md")
                pdf = os.path.join(folder, "lettre.pdf")
                cv_pdf = os.path.join(folder, "cv_adapte.pdf")
                mtime = ""
                if os.path.exists(md):
                    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(md)).strftime("%d/%m %H:%M")
                    # PDF plus vieux que le .md retouché → à régénérer
                    stale = os.path.exists(pdf) and os.path.getmtime(pdf) < os.path.getmtime(md)
                else:
                    stale = False
                table.add_row(
                    os.path.basename(folder)[:34],
                    "✓" if os.path.exists(md) else "—",
                    "⚠ à régénérer (p)" if stale else ("✓" if os.path.exists(pdf) else "—"),
                    "✓" if os.path.exists(cv_pdf) else "—",
                    mtime,
                    key=folder,
                )
        self.update_letter_preview(show_cv=self._preview_cv)

    def update_letter_preview(self, show_cv: bool = False) -> None:
        folder = self._current_row_key("letters-table")
        widget = self.query_one("#letter-preview", Markdown)
        if not folder:
            widget.update("")
            return
        if show_cv:
            path = os.path.join(folder, "cv_adapte.html")
            if os.path.exists(path):
                content = strip_html(open(path, encoding="utf-8").read(), max_chars=4000)
                widget.update(f"### CV adapté (texte extrait)\n\n{content}")
            else:
                widget.update("*Pas de cv_adapte.html dans ce dossier.*")
            return
        path = os.path.join(folder, "lettre.md")
        if os.path.exists(path):
            widget.update(open(path, encoding="utf-8").read())
        else:
            widget.update("*Pas de lettre.md dans ce dossier.*")

    def _letter_offer_info(self, folder: str) -> dict:
        """Retrouve entreprise/titre/lieu pour le rendu PDF : offre JSON via l'id du dossier,
        sinon commentaires de lettre.md, sinon nom du dossier."""
        offer_id = offer_id_from_folder(folder)
        offer_path = os.path.join(PATHS.data_dir, "offers", f"{offer_id}.json")
        if os.path.exists(offer_path):
            with open(offer_path, encoding="utf-8") as f:
                o = json.load(f)
            return {"company": o.get("company", ""), "title": o.get("title", ""), "location": o.get("location", "")}
        info = {"company": os.path.basename(folder).rsplit("_", 1)[0].replace("-", " ").title(), "title": "", "location": ""}
        md = os.path.join(folder, "lettre.md")
        if os.path.exists(md):
            import re
            head = open(md, encoding="utf-8").read(500)
            m = re.search(r"<!-- Offre : (.*?) — (.*?) -->", head)
            if m:
                info["title"], info["company"] = m.group(1), m.group(2) or info["company"]
        return info

    def refresh_track(self) -> None:
        table = self.query_one("#track-table", HighlightDataTable)
        apps = load_applications()
        table.row_styles = {
            a["offer_id"]: TRACK_STATUS_STYLES[a["status"]]
            for a in apps
            if a["status"] in TRACK_STATUS_STYLES
        }
        with self._keep_cursor("track-table"):
            table.clear()
            for app in apps:
                table.add_row(
                    *_cells([
                        app["offer_id"],
                        app["status"],
                        app["company"][:22],
                        app["title"][:40],
                        app.get("generated_at", "") or "",
                        app.get("sent_at", "") or "",
                        (app.get("notes", "") or "")[:40],
                    ]),
                    key=app["offer_id"],
                )
        self.update_track_detail()

    def update_track_detail(self) -> None:
        oid = self._current_row_key("track-table")
        widget = self.query_one("#track-detail-text", Static)
        app = next((a for a in load_applications() if a["offer_id"] == oid), None)
        if not app:
            widget.update("")
            return
        parts = [
            f"[b]{app.get('company', '') or '?'}[/b]",
            app.get("title", ""),
            f"[dim]{app.get('url', '')}[/dim]",
            "",
        ]
        history = ensure_history(app)
        has_inferred = False
        prev_dt = None
        for event in history:
            status = event["status"]
            label = _STATUS_LABELS.get(status, status)
            at = event["at"]
            inferred = event.get("inferred", False)
            has_inferred = has_inferred or inferred
            suffix = "~" if inferred else ""
            delta = ""
            try:
                cur_dt = datetime.fromisoformat(at)
                if prev_dt is not None:
                    delta = f"   (+{(cur_dt - prev_dt).days} j)"
                prev_dt = cur_dt
            except ValueError:
                pass
            line = f"{label}{suffix:<1}  {at[:10]}{delta}"
            color = STATUS_COLORS.get(status)
            parts.append(f"[{color}]{line}[/{color}]" if color else line)
        if has_inferred:
            parts += ["", "[dim]~ date déduite (heure inconnue, avant l'introduction de l'historique)[/dim]"]
        widget.update("\n".join(parts))

    # ---------- onglet réglages ----------

    def refresh_settings(self) -> None:
        table = self.query_one("#settings-table", HighlightDataTable)
        with self._keep_cursor("settings-table"):
            table.clear()
            for entry in settings_files():
                state = entry["state"]
                if state == "personnalisé":
                    state_cell = "[green]personnalisé[/green]"
                elif state == "défaut du paquet":
                    state_cell = "[dim]défaut du paquet[/dim]"
                elif state == "action":
                    # Ligne "Sources de scraping" : pas un état de fichier, pas de rouge trompeur.
                    state_cell = "[dim]—[/dim]"
                else:
                    state_cell = "[red]absent[/red]"
                mtime = entry["mtime"]
                mtime_cell = mtime.strftime("%Y-%m-%d %H:%M") if mtime else ""
                table.add_row(
                    *_cells([entry["role"], state_cell, mtime_cell, entry["path"]]),
                    key=entry["path"],
                )
        self.update_settings_detail()

    def update_settings_detail(self) -> None:
        widget = self.query_one("#settings-detail-text", Static)
        path = self._current_row_key("settings-table")
        entry = next((e for e in settings_files() if e["path"] == path), None)
        if not entry:
            widget.update("")
            return
        if entry.get("kind") == "models":
            widget.update(
                f"[b]{entry['role']}[/b]\n\n"
                "Choisir le modèle Claude de chaque tâche (filtre, lettre, CV) : "
                "« dernier Haiku/Sonnet/Opus » (suit la famille, avec un avis au changement) "
                "ou un modèle épinglé (config/search.yaml — écriture immédiate).\n\n"
                "[dim]entrée : gérer les modèles[/dim]"
            )
            return
        if entry.get("kind") == "sources":
            widget.update(
                f"[b]{entry['role']}[/b]\n\n"
                "Activer/désactiver individuellement une source de scraping "
                "(config/sources.yaml — écriture immédiate, commentaires préservés).\n\n"
                "[dim]entrée : gérer les sources[/dim]"
            )
            return
        parts = [
            f"[b]{entry['role']}[/b]",
            f"[dim]{entry['path']}[/dim]",
            "",
            f"État : {entry['state']}",
        ]
        if entry["mtime"]:
            parts.append(f"Modifié le : {entry['mtime'].strftime('%Y-%m-%d %H:%M')}")
        parts += ["", "[dim]entrée : ouvrir dans l'éditeur[/dim]"]
        widget.update("\n".join(parts))

    def action_edit_setting(self) -> None:
        if self._active_tab() != "settings":
            return
        path = self._current_row_key("settings-table")
        if not path:
            return
        if path == SOURCES_SETTINGS_KEY:
            # Ligne dédiée : pas un fichier, ouvre la modale de gestion des sources
            # au lieu de tenter de lancer un éditeur sur un chemin qui n'existe pas.
            self.push_screen(SourcesScreen())
            return
        if path == MODELS_SETTINGS_KEY:
            self.push_screen(ModelsScreen())
            return
        entry = next((e for e in settings_files() if e["path"] == path), None)
        if entry is None:
            return

        target = entry["path"]
        # Piège n°2 : `PATHS.prompt()`/`PATHS.template()` peuvent renvoyer un chemin DANS
        # le paquet (défaut livré). Éditer ce chemin directement modifierait les défauts
        # livrés au lieu de la config personnelle — on copie d'abord vers l'emplacement
        # utilisateur si la copie personnalisée n'existe pas encore, puis on édite la copie.
        if entry["kind"] == "prompt" and entry["state"] != "personnalisé" and os.path.exists(target):
            user_dir = PATHS.user_prompts_dir
            os.makedirs(user_dir, exist_ok=True)
            user_path = os.path.join(user_dir, entry["name"])
            if not os.path.exists(user_path):
                import shutil

                shutil.copyfile(target, user_path)
            target = user_path
        elif entry["kind"] == "template" and entry["state"] != "personnalisé" and os.path.exists(target):
            user_dir = PATHS.user_templates_dir
            os.makedirs(user_dir, exist_ok=True)
            user_path = os.path.join(user_dir, entry["name"])
            if not os.path.exists(user_path):
                import shutil

                shutil.copyfile(target, user_path)
            target = user_path
        elif not os.path.exists(target):
            # Fichier "plain" absent (ex. .env jamais créé) : on crée un fichier vide
            # pour que l'éditeur ait quelque chose à ouvrir.
            os.makedirs(os.path.dirname(target), exist_ok=True)
            open(target, "a", encoding="utf-8").close()

        self._open_in_editor(target)
        self.notify_status(f"{os.path.basename(target)} ouvert dans l'éditeur — `r` pour recharger après édition.")
        self.refresh_settings()

    def _open_in_editor(self, path: str) -> None:
        """Ouvre `path` dans l'éditeur — portable, contrairement à l'ancien `open` figé macOS.

        Priorité à $VISUAL puis $EDITOR (convention Unix). S'ils sont définis, on suppose
        un éditeur terminal (vim, nano…) et on suspend la TUI (`App.suspend()`, Textual
        8.x) le temps de son exécution — sinon il écrit par-dessus l'affichage Textual et
        le corrompt. Sans $VISUAL/$EDITOR, on retombe sur l'ouverture GUI non bloquante
        selon la plateforme (`open` macOS / `xdg-open` Linux / `start` Windows).
        """
        import shlex

        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if editor:
            # posix=False sous Windows : sinon shlex mange les `\` de `C:\...\code.exe`.
            cmd = shlex.split(editor, posix=not sys.platform.startswith("win")) + [path]
            try:
                with self.suspend():
                    subprocess.run(cmd, check=False)
            except OSError as e:
                self.notify_status(f"Éditeur introuvable ({editor}) : {type(e).__name__}: {e}")
            return

        self._open_with_system(path)

    def _open_with_system(self, path: str) -> None:
        """Ouvre `path` (fichier ou dossier) avec l'application par défaut de l'OS.

        Seul point d'appel des ouvreurs système de la TUI : `open` n'existe que sur
        macOS — un `subprocess.run(["open", ...])` en dur lève `FileNotFoundError`
        (que `check=False` ne rattrape pas) et fait planter la TUI sous Linux et
        Windows. `Popen` + sorties vers DEVNULL : `xdg-open` peut écrire sur le
        terminal et corrompre l'affichage Textual, et ne doit pas bloquer la TUI.
        Un ouvreur absent (Linux sans bureau) donne un message, jamais un crash.
        """
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
                return
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen(
                [opener, path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            self.notify_status(f"Impossible d'ouvrir {path} : {type(e).__name__}: {e}")

    def refresh_stats(self) -> None:
        stats = compute_stats(PATHS.data_dir)
        header = self.query_one("#stats-header", Static)
        return_text = self.query_one("#stats-return-text", Static)
        status_bars = self.query_one("#stats-status-bars", Static)
        loc_text = self.query_one("#stats-loc-text", Static)
        loc_bars = self.query_one("#stats-loc-bars", Static)
        months_widget = self.query_one("#stats-months", Static)
        heatmap_widget = self.query_one("#stats-heatmap", Static)
        country_time_widget = self.query_one("#stats-country-time", Static)
        flow_widget = self.query_one("#stats-flow", Static)
        flow_months_widget = self.query_one("#stats-flow-months", Static)

        if stats["total"] == 0:
            header.update("[b]Statistiques[/b]  [dim]— aucune candidature enregistrée pour l'instant.[/dim]")
            for w in (
                return_text, status_bars, loc_text, loc_bars, months_widget, heatmap_widget,
                country_time_widget, flow_widget, flow_months_widget,
            ):
                w.update("")
            return

        c = stats["coarse"]
        total = stats["total"]
        header.update(
            f"[b]Statistiques — {total} candidature(s)[/b]   "
            f"[green]Acceptée {c['Acceptée']}[/green] · [red]Refusée {c['Refusée']}[/red] · "
            f"[yellow]En attente {c['En attente']}[/yellow] · [grey50]Annulée {c['Annulée']}[/grey50]"
        )

        # --- Section 1 : retour de candidature ---
        r = stats["rates"]
        def _pct(v):
            return f"{round(100 * v)} %" if v is not None else "—"
        delai = f"{r['delai_moyen_jours']:.0f} j (sur {r['n_delai']})" if r["delai_moyen_jours"] is not None else "—"
        return_text.update(
            f"[b]Taux d'acceptation :[/b] {_pct(r['taux_acceptation'])}\n"
            f"[dim](parmi les décisions finales)[/dim]\n\n"
            f"[b]Taux de réponse :[/b] {_pct(r['taux_reponse'])}\n"
            f"[dim]({r['responded']} réponse(s) / {r['sent']} envoyée(s))[/dim]\n\n"
            f"[b]Délai moyen de réponse :[/b] {delai}"
        )
        lw = max(len("En attente"), 3)
        status_bars.update("\n".join([
            _hbar("Acceptée", c["Acceptée"], total, lw, style="green"),
            _hbar("Refusée", c["Refusée"], total, lw, style="red"),
            _hbar("En attente", c["En attente"], total, lw, style="yellow"),
            _hbar("Annulée", c["Annulée"], total, lw, style="grey50"),
        ]))

        # --- Section 2 : localisation ---
        locs = list(stats["by_location"].items())
        n_pays = len(locs)
        top_pays = locs[0][0] if locs else "—"
        city_counts: Counter = Counter()
        for info in stats["by_place"].values():
            for city, n in info["cities"].items():
                if city != "Non précisée":
                    city_counts[city] += n
        top_city = city_counts.most_common(1)[0][0] if city_counts else "—"
        loc_text.update(
            f"Pays distincts : [b]{n_pays}[/b]\n"
            f"Villes distinctes : [b]{len(city_counts)}[/b]\n\n"
            f"Pays le plus fréquent :\n[b]{top_pays}[/b]\n\n"
            f"Ville la plus fréquente :\n[b]{top_city}[/b]"
        )
        loc_bars.update(render_location_tree(stats["by_place"], total))

        # --- Section 3 : histogramme temporel ---
        scale = self._stats_scale
        heatmap_widget.update(render_activity_heatmap(stats["by_day"]))
        # `[t]` doit être échappé (balise Rich inconnue sinon avalée).
        months_widget.update(
            render_time_histogram(_bucket_counts(stats["by_day"], scale), scale)
            + f"\n\n[dim]\\[t] échelle : jour · semaine · mois (actuelle : {_SCALES[scale]['label']})[/dim]"
        )
        country_time_widget.update(render_country_timeline(stats["by_day_country"], scale))

        # --- Section 4 : parcours des candidatures (flux) ---
        apps = load_applications()
        tree = flow_tree(apps)
        flow_widget.update(
            # `[f]` doit être échappé : Rich l'interpréterait comme une balise de style
            # inconnue et l'avalerait silencieusement (le raccourci disparaissait du rendu).
            render_flow_tree(tree) + "\n\n[dim]\\[f] exporter le diagramme de Sankey (HTML)[/dim]"
        )

        # --- Section 5 : statuts atteints par mois ---
        flow_months_widget.update(render_month_status_bars(by_month_status(apps)))

    # ---------- helpers ----------

    def notify_status(self, message: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(f" {message}")
        except NoMatches:
            pass  # app en cours de fermeture (ex. tick de spinner résiduel)

    # ---------- indicateur de scrape en cours ----------

    def _start_spinner(self, phase: str) -> None:
        """Démarre le spinner de la barre de statut (scrape en cours)."""
        self._scrape_phase = phase
        self._scrape_started = time.monotonic()
        self._spinner_i = 0
        if self._spinner_timer is None:
            self._spinner_timer = self.set_interval(0.12, self._tick_spinner)
        self._tick_spinner()

    def _set_phase(self, phase: str) -> None:
        """Change le libellé d'étape affiché à côté du spinner."""
        self._scrape_phase = phase

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._scrape_phase = None

    def _tick_spinner(self) -> None:
        if self._scrape_phase is None:
            return
        frame = SPINNER_FRAMES[self._spinner_i % len(SPINNER_FRAMES)]
        self._spinner_i += 1
        elapsed = int(time.monotonic() - self._scrape_started)
        clock = f"{elapsed // 60}:{elapsed % 60:02d}"
        self.notify_status(f"[cyan]{frame}[/cyan] {self._scrape_phase} — {clock} écoulé (la TUI reste utilisable)")

    def _active_tab(self) -> str:
        return self.query_one(TabbedContent).active

    @contextlib.contextmanager
    def _keep_cursor(self, table_id: str):
        """Préserve la ligne sous le curseur pendant une reconstruction de table.

        `DataTable.clear()` + `add_row()` ramène le curseur en haut : sans ça, changer
        le statut d'une candidature (ou cocher une offre) faisait sauter la sélection
        sur la première ligne, obligeant à retrouver sa place à chaque action.
        """
        previous = self._current_row_key(table_id)
        yield
        if previous is None:
            return
        table = self.query_one(f"#{table_id}", DataTable)
        try:
            table.move_cursor(row=table.get_row_index(previous))
        except (KeyError, RowDoesNotExist):
            pass  # la ligne a disparu de la liste (filtrée, supprimée…)

    def _current_row_key(self, table_id: str) -> str | None:
        table = self.query_one(f"#{table_id}", DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return str(row_key.value)

    def update_detail(self) -> None:
        oid = self._current_row_key("offers-table")
        widget = self.query_one("#detail-text", Static)
        if not oid or oid not in self.offers:
            widget.update("")
            return
        o = self.offers[oid]
        fr = o.get("filter_result", {})
        parts = [
            f"[b]{o.get('title', '')}[/b]",
            f"{o.get('company', '') or '?'} — {o.get('location', '')}",
            f"[dim]{o.get('url', '')}[/dim]",
            "",
            f"[b]Score : {fr.get('score', '?')}/10[/b]",
            fr.get("raison", ""),
        ]
        if fr.get("points_forts"):
            parts += ["", "[green]✅ " + " · ".join(fr["points_forts"]) + "[/green]"]
        if fr.get("points_faibles"):
            parts += ["[yellow]⚠️ " + " · ".join(fr["points_faibles"]) + "[/yellow]"]
        target_id = o.get("duplicate_of")
        if target_id:
            target_app = next((a for a in load_applications() if a["offer_id"] == target_id), None)
            if target_app:
                # `\[⧉]` : le crochet littéral serait sinon interprété comme balise Rich.
                parts += [
                    "",
                    f"[magenta]⧉ Doublon de {target_id} — {target_app.get('company', '?')} · "
                    f"{target_app.get('title', '?')} ({target_app.get('status', '?')})[/magenta]",
                ]
            else:
                parts += ["", f"[magenta]⧉ Doublon de {target_id}[/magenta]"]
        else:
            suspects = suggest_targets(o, load_applications())
            if suspects:
                # `\[?]` : le crochet littéral serait sinon interprété comme balise Rich.
                parts += ["", "[#b39ddb]\\[?] Doublon possible de :[/#b39ddb]"]
                for a in suspects:
                    parts.append(
                        f"[#b39ddb]   {a['offer_id']} — {a.get('company', '?')} · "
                        f"{a.get('title', '?')} ({a.get('status', '?')})[/#b39ddb]"
                    )
        invalid = o.get("invalid")
        if invalid:
            reason_label = INVALID_REASONS.get(invalid.get("reason", ""), invalid.get("reason", "?"))
            at = invalid.get("at", "")
            parts += ["", f"[red]⛔ Invalide — {reason_label}{f' (le {at})' if at else ''}[/red]"]
        desc = strip_html(o.get("description", ""), max_chars=1200)
        if desc:
            parts += ["", "[dim]" + desc + "[/dim]"]
        widget.update("\n".join(parts))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "offers-table":
            self.update_detail()
        elif event.data_table.id == "letters-table":
            self._preview_cv = False
            self.update_letter_preview()
        elif event.data_table.id == "track-table":
            self.update_track_detail()
        elif event.data_table.id == "settings-table":
            self.update_settings_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "letters-table":
            self.action_toggle_preview()
        elif event.data_table.id == "settings-table":
            self.action_edit_setting()

    # ---------- actions ----------

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_open_console(self) -> None:
        webbrowser.open(CONSOLE_URL)
        self.notify_status("Claude Console ouverte dans le navigateur.")

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_threshold(self, delta: int) -> None:
        if self._active_tab() != "offers":
            return
        self.threshold = max(0, min(10, self.threshold + delta))
        self.refresh_offers()

    def action_toggle_hide_done(self) -> None:
        if self._active_tab() != "offers":
            return
        self.hide_done = not self.hide_done
        self.refresh_offers()

    def action_mark_duplicate(self) -> None:
        if self._active_tab() != "offers":
            return
        oid = self._current_row_key("offers-table")
        if not oid:
            return
        offer = self.offers.get(oid)
        if not offer:
            return

        if offer.get("duplicate_of"):
            try:
                clear_duplicate(oid)
            except ValueError as e:
                self.notify_status(f"Erreur : {e}")
                return
            offer.pop("duplicate_of", None)
            self.refresh_offers()
            self.notify_status(f"Marquage doublon retiré pour {oid}.")
            return

        apps = load_applications()
        matches = suggest_targets(offer, apps)
        company_norm = _normalize_company(offer.get("company", ""))
        same_company = [a for a in matches]
        others = [
            a for a in apps
            if _normalize_company(a.get("company", "")) != company_norm
        ]
        candidates = same_company + others
        if not candidates:
            self.notify_status("Aucune candidature existante à proposer comme cible.")
            return

        def _label(a: dict) -> str:
            title = (a.get("title", "") or "?")[:70]
            return f"{a.get('company', '?')} — {title} ({a.get('status', '?')})"

        same_company_opts = [(a["offer_id"], _label(a)) for a in same_company]
        others_opts = [(a["offer_id"], _label(a)) for a in others]
        apps_by_id = {a["offer_id"]: a for a in candidates}
        offer_title = offer.get("title", "") or "?"
        offer_company = offer.get("company", "") or "?"
        modal_title = f"Marquer « {offer_title} » ({offer_company}) comme doublon de :"

        def _apply(target_id: str) -> None:
            try:
                mark_duplicate(oid, target_id)
            except ValueError as e:
                self.notify_status(f"Erreur : {e}")
                return
            self.offers[oid]["duplicate_of"] = target_id
            self.refresh_offers()
            self.notify_status(f"{oid} marquée comme doublon de {target_id}.")

        def _pick(target_id: str | None) -> None:
            if not target_id:
                return
            target_app = apps_by_id.get(target_id, {})
            target_company = target_app.get("company", "") or "?"
            if _normalize_company(target_company) != company_norm:
                message = (
                    f"« {offer_company} » ≠ « {target_company} » — "
                    "confirmer le marquage comme doublon malgré des entreprises différentes ?"
                )

                def _confirm(ok: bool) -> None:
                    if ok:
                        _apply(target_id)

                self.push_screen(ConfirmScreen(message), _confirm)
                return
            _apply(target_id)

        self.push_screen(
            DuplicatePickerScreen(same_company_opts, others_opts, modal_title, offer_company),
            _pick,
        )

    def action_mark_invalid(self) -> None:
        if self._active_tab() != "offers":
            return
        oid = self._current_row_key("offers-table")
        if not oid:
            return
        offer = self.offers.get(oid)
        if not offer:
            return

        if offer.get("invalid"):
            try:
                clear_invalid(oid)
            except ValueError as e:
                self.notify_status(f"Erreur : {e}")
                return
            offer.pop("invalid", None)
            self.refresh_offers()
            self.notify_status(f"Marquage invalide retiré pour {oid}.")
            return

        offer_title = offer.get("title", "") or "?"
        offer_company = offer.get("company", "") or "?"
        modal_title = f"Marquer « {offer_title} » ({offer_company}) comme invalide — motif :"

        def _apply(reason: str | None) -> None:
            if not reason:
                return
            try:
                # On reprend l'offre RETOURNÉE par mark_invalid plutôt que de
                # reconstruire le champ à la main : elle porte aussi `at`, que le
                # panneau de détail affiche — sinon la date manquait jusqu'au
                # prochain rechargement (`r`).
                self.offers[oid] = mark_invalid(oid, reason)
            except ValueError as e:
                self.notify_status(f"Erreur : {e}")
                return
            # Une offre écartée n'a plus rien à faire dans la file de génération.
            self.selected.discard(oid)
            self.gen_checked.discard(oid)
            self._persist_queue()
            self.refresh_offers()
            self.refresh_gen()
            self.notify_status(f"{oid} marquée invalide ({INVALID_REASONS.get(reason, reason)}).")

        self.push_screen(InvalidReasonScreen(modal_title), _apply)

    def action_cycle_period(self) -> None:
        if self._active_tab() != "offers":
            return
        order = ["all", "session", "7d", "30d"]
        idx = order.index(self.period) if self.period in order else 0
        self.period = order[(idx + 1) % len(order)]
        if self.period == "all":
            self.session_date = None
        self.refresh_offers()

    def action_pick_session(self) -> None:
        if self._active_tab() != "offers":
            return
        sessions = self._scrape_sessions()
        if not sessions:
            self.notify_status("Aucune session de scrape disponible.")
            return
        counts = {}
        for o in self.offers.values():
            d = o.get("date_scraped", "")
            if d:
                counts[d] = counts.get(d, 0) + 1
        session_list = [(d, counts.get(d, 0)) for d in sessions]

        def _pick(session: str | None) -> None:
            if not session:
                return
            self.session_date = session
            self.period = "session"
            self.refresh_offers()

        self.push_screen(SessionPickerScreen(session_list), _pick)

    def action_toggle_select(self) -> None:
        tab = self._active_tab()
        if tab == "offers":
            oid = self._current_row_key("offers-table")
            if not oid:
                return
            if oid in self.selected:
                self.selected.discard(oid)
            else:
                self.selected.add(oid)
            self._persist_queue()
            self.refresh_offers()
            self.refresh_gen()
        elif tab == "gen":
            oid = self._current_row_key("gen-table")
            if not oid:
                return
            if oid in self.gen_checked:
                self.gen_checked.discard(oid)
            else:
                self.gen_checked.add(oid)
            self.refresh_gen()
        else:
            return

    def action_open_url(self) -> None:
        # `o` a un sens unique dans toute la TUI : ouvrir l'OFFRE dans le navigateur
        # (onglets 1, 2, 3, 4). Ne jamais y remettre une action "dossier"/"modale" —
        # c'est précisément la confusion qui a motivé cette harmonisation.
        tab = self._active_tab()
        if tab == "offers":
            oid = self._current_row_key("offers-table")
            url = self.offers.get(oid, {}).get("url") if oid else None
            if url:
                webbrowser.open(url)
        elif tab == "gen":
            oid = self._current_row_key("gen-table")
            url = self.offers.get(oid, {}).get("url") if oid else None
            if url:
                webbrowser.open(url)
        elif tab == "letters":
            folder = self._current_row_key("letters-table")
            if not folder:
                return
            offer_id = offer_id_from_folder(folder)
            for app in load_applications():
                if app["offer_id"] == offer_id and app.get("url"):
                    webbrowser.open(app["url"])
                    break
        elif tab == "track":
            oid = self._current_row_key("track-table")
            for app in load_applications():
                if app["offer_id"] == oid and app.get("url"):
                    webbrowser.open(app["url"])
                    break

    def action_open_offer_url(self) -> None:
        # `O` a un sens unique : ouvrir le DOSSIER de la candidature (onglets 2, 3).
        tab = self._active_tab()
        if tab == "gen":
            oid = self._current_row_key("gen-table")
            if oid and self.gen_status.get(oid, ("", ""))[0] == "✅":
                # Le 2e champ de gen_status n'est un chemin de dossier qu'après une
                # génération réelle (g/L) — après `T` (enregistrement sans génération),
                # c'est un message texte : `isdir` évite d'ouvrir n'importe quoi.
                target = self.gen_status[oid][1]
                if os.path.isdir(target):
                    self._open_with_system(target)
            return
        if tab != "letters":
            return
        folder = self._current_row_key("letters-table")
        if folder:
            self._open_with_system(folder)

    # ---------- onglet candidatures ----------

    _preview_cv = False

    def action_toggle_preview(self) -> None:
        if self._active_tab() != "letters":
            return
        self._preview_cv = not self._preview_cv
        self.update_letter_preview(show_cv=self._preview_cv)

    def action_edit_letter(self) -> None:
        if self._active_tab() != "letters":
            return
        folder = self._current_row_key("letters-table")
        if not folder:
            return
        md = os.path.join(folder, "lettre.md")
        if not os.path.exists(md):
            self.notify_status("Pas de lettre.md dans ce dossier.")
            return
        self._open_in_editor(md)
        self.notify_status(f"lettre.md ouverte dans l'éditeur — retouche puis `p` pour régénérer le PDF.")

    def action_render_letter_pdf(self) -> None:
        if self._active_tab() != "letters":
            return
        folder = self._current_row_key("letters-table")
        if not folder:
            return
        md = os.path.join(folder, "lettre.md")
        if not os.path.exists(md):
            self.notify_status("Pas de lettre.md dans ce dossier.")
            return
        try:
            from jobsearch.cv_pdf import load_cv_data
            from jobsearch.letter_pdf import render_letter_pdf

            info = self._letter_offer_info(folder)
            render_letter_pdf(
                PATHS.template("cover_letter.html"),
                load_cv_data(PATHS.cv_dir),
                open(md, encoding="utf-8").read(),
                output_path=os.path.join(folder, "lettre.pdf"),
                company_name=info["company"],
                company_location=info["location"],
                position_title=info["title"],
            )
        except Exception as e:
            self.notify_status(f"Erreur rendu PDF : {type(e).__name__}: {e}")
            return
        self.refresh_letters()
        self.notify_status(f"lettre.pdf régénérée dans {os.path.basename(folder)}.")

    def action_render_cv_pdf(self) -> None:
        if self._active_tab() != "letters":
            return
        folder = self._current_row_key("letters-table")
        if not folder:
            return
        html_path = os.path.join(folder, "cv_adapte.html")
        if not os.path.exists(html_path):
            self.notify_status("Pas de cv_adapte.html dans ce dossier.")
            return
        try:
            from jobsearch.cv_pdf import write_cv_pdf

            write_cv_pdf(open(html_path, encoding="utf-8").read(), os.path.join(folder, "cv_adapte.pdf"))
        except Exception as e:
            self.notify_status(f"Erreur rendu PDF : {type(e).__name__}: {e}")
            return
        self.refresh_letters()
        self.notify_status(f"cv_adapte.pdf régénéré dans {os.path.basename(folder)}.")

    def action_add_manual(self) -> None:
        if self._active_tab() != "gen":
            return
        self.push_screen(ManualEntryScreen(), self._submit_manual)

    def _submit_manual(self, data: dict | None) -> None:
        if not data:
            return
        self.notify_status("Extraction en cours...")
        self.add_manual_entry(data)

    @work(thread=True)
    def add_manual_entry(self, data: dict) -> None:
        from jobsearch.manual import create_stub_from_file, create_stub_from_url

        try:
            if data["file"]:
                offer = create_stub_from_file(
                    data["file"], data.get("url_source", ""), data.get("title", ""), data.get("company", "")
                )
            else:
                offer = create_stub_from_url(
                    data["url"], data.get("title", ""), data.get("company", "")
                )
        except Exception as e:
            self.call_from_thread(self.notify_status, f"Erreur extraction : {type(e).__name__}: {e}")
            return

        from jobsearch.scraper import save_offer

        offer = {**offer, "filter_result": {"score": 10, "reason": "Ajout manuel via TUI"}}
        # Le stub sauvegardé par create_stub_from_file/url n'a pas filter_result :
        # load_filtered_offers l'ignorerait sinon dès le prochain rechargement (r).
        save_offer(offer, PATHS.data_dir)

        def _register() -> None:
            self.offers[offer["id"]] = offer
            self.selected.add(offer["id"])
            self.gen_status.pop(offer["id"], None)  # état par défaut « en attente »
            self._persist_queue()
            self.refresh_offers()
            self.refresh_gen()
            self.notify_status(
                f"Offre « {offer['title']} » ajoutée — g (lettre + CV) ou L (lettre seule)."
            )

        self.call_from_thread(_register)

    def action_remove_from_queue(self) -> None:
        if self._active_tab() != "gen":
            return
        targets = self.gen_checked & self.selected
        if not targets:
            oid = self._current_row_key("gen-table")
            targets = {oid} if oid else set()
        if not targets:
            return
        in_progress = {oid for oid in targets if self.gen_status.get(oid, ("·",))[0] == "⏳"}
        targets -= in_progress
        for oid in targets:
            self.selected.discard(oid)
            self.gen_checked.discard(oid)
            self.gen_status.pop(oid, None)
        self._persist_queue()
        self.refresh_gen()
        self.refresh_offers()
        msg = f"{len(targets)} offre(s) retirée(s) de la file."
        if in_progress:
            msg += f" ({len(in_progress)} en cours ignorée(s))"
        self.notify_status(msg)

    def action_edit_gen_notes(self) -> None:
        if self._active_tab() != "gen":
            return
        oid = self._current_row_key("gen-table")
        if not oid:
            return
        current = self.offers.get(oid, {}).get("gen_notes", "") or get_gen_notes(oid)

        def _save(notes: str | None) -> None:
            if notes is None:
                return
            offer = set_gen_notes(oid, notes)
            if oid in self.offers:
                self.offers[oid] = offer
            self.refresh_gen()
            self.notify_status(
                f"Note enregistrée pour {oid}." if notes.strip() else f"Note retirée pour {oid}."
            )

        self.push_screen(GenNotesScreen(current), _save)

    def action_record_only(self) -> None:
        """`T` — enregistre dans le suivi sans générer ni lettre ni CV (aucun appel API)."""
        if self._active_tab() != "gen":
            return
        targets = self.gen_checked & self.selected
        if not targets:
            oid = self._current_row_key("gen-table")
            targets = {oid} if oid else set()
        # Même exclusion que action_remove_from_queue : ne jamais toucher une génération
        # en cours (⏳), et une offre déjà enregistrée (✅) n'a rien à refaire.
        targets = {oid for oid in targets if self.gen_status.get(oid, ("·",))[0] not in ("⏳", "✅")}
        if not targets:
            return

        def _confirm(ok: bool) -> None:
            if not ok:
                return
            for oid in targets:
                offer = self.offers.get(oid)
                if not offer:
                    continue
                record_generated(offer, PATHS.data_dir)
                self.gen_status[oid] = ("✅", "enregistrée dans le suivi — sans lettre ni CV")
                self.gen_checked.discard(oid)
            self.refresh_gen()
            self.refresh_track()
            self.refresh_stats()
            self.refresh_offers()
            self._persist_queue()
            self.notify_status(f"{len(targets)} offre(s) enregistrée(s) dans le suivi, sans lettre ni CV.")

        n = len(targets)
        message = (
            f"Enregistrer {n} offre(s) dans le suivi sans générer ni lettre ni CV ?\n"
            "Aucun appel API, aucun dossier ne sera créé dans output/candidatures/."
        )
        self.push_screen(ConfirmScreen(message), _confirm)

    def action_generate(self) -> None:
        self._start_generation(letter_only=False)

    def action_generate_letter_only(self) -> None:
        self._start_generation(letter_only=True)

    def _start_generation(self, letter_only: bool) -> None:
        tab = self._active_tab()
        if tab == "offers":
            if self.selected:
                self.action_tab("gen")
            return
        if tab != "gen":
            return
        pool = (self.gen_checked & self.selected) or self.selected
        todo = [oid for oid in sorted(pool) if self.gen_status.get(oid, ("·",))[0] not in ("⏳", "✅")]
        if not todo:
            self.notify_status("Rien à générer (tout est déjà fait ou en cours).")
            return
        info = "génération lettre seule..." if letter_only else "génération en cours..."
        for oid in todo:
            self.gen_status[oid] = ("⏳", info)
        self.refresh_gen()
        self.run_generation(todo, letter_only)

    @work(thread=True, exclusive=True)
    def run_generation(self, offer_ids: list[str], letter_only: bool = False) -> None:
        from jobsearch.generator import generate

        for oid in offer_ids:
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                    folder = generate(oid, letter_only=letter_only)
                self.gen_status[oid] = ("✅", folder)
                self.gen_checked.discard(oid)
            except SystemExit:
                out = buffer.getvalue().strip()
                self.gen_status[oid] = ("❌", out.splitlines()[-1] if out else "erreur (voir python -m jobsearch.generator " + oid + ")")
            except Exception as e:
                self.gen_status[oid] = ("❌", f"{type(e).__name__}: {e}".replace("\n", " ")[:120])
            self.call_from_thread(self.refresh_gen)
            self.call_from_thread(self.refresh_track)
            self.call_from_thread(self.refresh_stats)
        done = sum(1 for oid in offer_ids if self.gen_status[oid][0] == "✅")
        self.call_from_thread(self._persist_queue)
        self.call_from_thread(
            self.notify_status,
            f"Génération terminée : {done}/{len(offer_ids)} réussie(s). (c = Claude Console si problème de crédit)",
        )

    @work(thread=True, exclusive=True, group="scrape")
    def run_scrape(self, include_groups: set[str] | None = None) -> None:
        buffer = io.StringIO()
        try:
            from jobsearch.filter import filter_all
            from jobsearch.report import generate_report
            from jobsearch.scraper import scrape_all

            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                new = scrape_all(include_groups=include_groups)
                self.call_from_thread(self._set_phase, f"Filtrage Claude ({len(new)} nouvelle(s))…")

                def _batch_progress(done, total, batch_id, resumed):
                    prefix = "Reprise du batch précédent" if resumed else "Batch Claude"
                    self.call_from_thread(
                        self._set_phase,
                        f"{prefix} : {done}/{total} traitées — peut prendre jusqu'à ~1 h ; "
                        "fermer ne perd rien (repris au prochain scrape)",
                    )

                filtered, pertinent = filter_all(threshold=self.threshold, progress=_batch_progress)
                self.call_from_thread(self._set_phase, "Génération du rapport…")
                report = generate_report(threshold=self.threshold)
            msg = f"Scrape terminé : {len(new)} nouvelle(s), {filtered} filtrée(s), {pertinent} pertinente(s). Rapport : {report}"
        except SystemExit:
            msg = "Scrape interrompu : " + (buffer.getvalue().strip().splitlines()[-1] if buffer.getvalue().strip() else "erreur")
        except BaseException as e:
            msg = f"Erreur pendant le scrape : {type(e).__name__}: {e}"
        self.call_from_thread(self._stop_spinner)
        self.call_from_thread(self.action_reload)
        self.call_from_thread(self.notify_status, msg)

    def _launch_scrape(self, include_groups: set[str] | None, label: str) -> None:
        if self._scrape_phase is not None:
            # Toast et non barre de statut : celle-ci est réécrite par le spinner à chaque tick.
            self.notify("Un scrape est déjà en cours.", title="Scrape", severity="warning")
            return
        self._start_spinner(label)
        self.notify("Scrape + filtre + rapport lancés (plusieurs minutes).", title="Scrape")
        self.run_scrape(include_groups=include_groups)

    def action_scrape(self) -> None:
        self._launch_scrape(None, "Scrape des sources…")

    def action_scrape_fr(self) -> None:
        """`Ctrl+S` : scrape incluant les sources FR-only (groupe 'fr', ex: HelloWork)."""
        from jobsearch.config import PATHS, get_search

        groups = set(get_search().enabled_groups) | {"fr"}
        self._launch_scrape(groups, "Scrape des sources (+ FR)…")

    def action_sent_or_scrape(self) -> None:
        """`s` : statut « envoyée » dans l'onglet Suivi, lancement du scrape ailleurs."""
        if self._active_tab() == "track":
            self.action_set_status("sent")
        else:
            self.action_scrape()

    def action_set_status(self, status: str) -> None:
        if self._active_tab() != "track":
            return
        oid = self._current_row_key("track-table")
        if not oid:
            return
        try:
            mark_status(oid, status)
        except (KeyError, ValueError) as e:
            self.notify_status(f"Erreur : {e}")
            return
        self.refresh_track()
        self.refresh_stats()
        self.notify_status(f"{oid} → {status}")

    def action_edit_note(self) -> None:
        if self._active_tab() != "track":
            return
        oid = self._current_row_key("track-table")
        if not oid:
            return
        current = next((a.get("notes", "") for a in load_applications() if a["offer_id"] == oid), "")

        def save(note: str | None) -> None:
            if note is None:
                return
            from jobsearch.tracker import set_note
            set_note(oid, note)
            self.refresh_track()
            self.notify_status(f"Note enregistrée pour {oid}.")

        self.push_screen(NoteScreen(current), save)

    def action_cycle_stats_scale(self) -> None:
        if self._active_tab() != "stats":
            return
        i = _SCALE_CYCLE.index(self._stats_scale)
        self._stats_scale = _SCALE_CYCLE[(i + 1) % len(_SCALE_CYCLE)]
        self.refresh_stats()

    def action_export_flow(self) -> None:
        if self._active_tab() != "stats":
            return
        try:
            path = write_flow_html(DEFAULT_FLOW_PATH, load_applications())
            # `as_uri()` et non "file://" + chemin : sous Windows, ce dernier donne
            # une URI malformée (`file://C:\\Users\\...`).
            webbrowser.open(Path(path).resolve().as_uri())
        except Exception as e:
            self.notify_status(f"Erreur export Sankey : {type(e).__name__}: {e}")
            return
        self.notify_status(f"Diagramme de flux exporté : {path}")


def _warn_if_empty_home() -> bool:
    """Garde-fou : n'affiche jamais silencieusement « 0 offre » si les données existent
    encore dans le dépôt (ancien emplacement) mais n'ont pas été copiées vers `JOBSEARCH_HOME`.
    Retourne True si la TUI doit s'arrêter avant de se lancer."""
    from jobsearch.config import PACKAGE_ROOT, JOBSEARCH_HOME, JOBSEARCH_HOME_EXPLICIT

    # Une racine choisie explicitement (`JOBSEARCH_HOME=…`) est un geste délibéré —
    # bac à sable de test, seconde recherche… Les données restées dans le dépôt
    # appartiennent à la racine PAR DÉFAUT : proposer de les y migrer n'aurait aucun
    # sens ici, et bloquerait le lancement. Même règle que `_check_setup` (cli.py).
    if JOBSEARCH_HOME_EXPLICIT:
        return False

    home_offers = os.path.isdir(PATHS.offers_dir) and any(
        f.endswith(".json") for f in os.listdir(PATHS.offers_dir)
    )
    if home_offers:
        return False

    legacy_offers_dir = os.path.join(PACKAGE_ROOT, "data", "offers")
    legacy_count = (
        len([f for f in os.listdir(legacy_offers_dir) if f.endswith(".json")])
        if os.path.isdir(legacy_offers_dir)
        else 0
    )
    if legacy_count:
        print(f"⚠️  {JOBSEARCH_HOME} vide ou absent : 0 offre y serait affichée.")
        print(f"    Le dépôt contient encore {legacy_count} offre(s) dans {legacy_offers_dir}")
        print("    (données pas encore migrées vers le nouvel emplacement, pas perdues).")
        print()
        print("    Lance d'abord :  jobsearch --migrate         (aperçu, n'écrit rien)")
        print("    Puis :           jobsearch --migrate --apply (copie réelle)")
        return True
    return False


def run_tui() -> None:
    if _warn_if_empty_home():
        return
    StageTUI().run()


if __name__ == "__main__":
    run_tui()
