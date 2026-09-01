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
from datetime import date, datetime, timedelta

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
)
from textual.widgets.data_table import RowDoesNotExist
from textual.widgets.option_list import Option

from jobsearch.duplicates import (
    _normalize_company,
    clear_duplicate,
    load_offer,
    mark_duplicate,
    suggest_targets,
)
from jobsearch.filter import strip_html
from jobsearch.flow import DEFAULT_FLOW_PATH, by_month_status, flow_tree, write_flow_html
from jobsearch.report import load_filtered_offers
from jobsearch.tracker import (
    STATUS_COLORS,
    STATUSES,
    _STATUS_LABELS,
    compute_stats,
    ensure_history,
    load_applications,
    mark_status,
)
from jobsearch.config import PATHS, get_search, settings_files


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


def render_month_histogram(by_month, height=7, col_w=7):
    """Histogramme vertical ASCII des candidatures par mois (inspiré d'AsciiStogram)."""
    if not by_month:
        return "[dim]Aucune donnée temporelle.[/dim]"
    months = list(by_month.keys())
    values = [by_month[m] for m in months]
    top = max(values)
    # labels d'axe Y (valeur max en haut, 0 en bas) alignés à droite
    y_w = len(str(top))
    lines = []
    for level in range(height, 0, -1):
        threshold = top * level / height
        axis = f"{top:>{y_w}} " if level == height else " " * (y_w + 1)
        cells = "".join(
            ("█" * (col_w - 2)).center(col_w) if v >= threshold else " " * col_w
            for v in values
        )
        lines.append(f"{axis}┤{cells}")
    # axe X + labels de mois (MM/YY)
    lines.append(f"{0:>{y_w}} └" + "─" * (col_w * len(months)))
    labels = "".join(_short_month(m).center(col_w) for m in months)
    lines.append(" " * (y_w + 2) + labels)
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

[b]Onglet Offres[/b]
  [b #7aa2f7]↑ / ↓[/]   Naviguer
  [b #7aa2f7]espace[/]  Cocher / décocher l'offre pour génération
  [b #7aa2f7]o[/]       Ouvrir l'offre dans le navigateur
  [b #7aa2f7]+ / -[/]   Augmenter / diminuer le seuil de score
  [b #7aa2f7]h[/]       Masquer / afficher les offres déjà candidatées (et les doublons marqués)
  [b #7aa2f7]d[/]       Cycler la période affichée (tout · session · 7j · 30j)
  [b #7aa2f7]D[/]       Choisir une session de scrape précise
  [b #7aa2f7]g[/]       Passer à l'onglet Génération
  [b #7aa2f7]m[/]       Marquer/démarquer l'offre comme doublon d'une candidature existante
                     (`?` = entreprise déjà candidatée, à vérifier — pas forcément un doublon)
  (les offres de la dernière session sont surlignées en bleu)

[b]Onglet Génération[/b]
  [b #7aa2f7]espace[/]  Cocher / décocher une offre (filtre optionnel pour g / L, sinon traite tout)
  [b #7aa2f7]g[/]       Lancer la génération (lettre + CV)
  [b #7aa2f7]L[/]       Générer la lettre seule (sans CV adapté — économie d'un appel API)
  [b #7aa2f7]A[/]       Ajouter une offre manuelle (fichier HTML ou URL) à la liste, puis g / L
  [b #7aa2f7]x[/]       Retirer de la file (lignes cochées, sinon la ligne sous le curseur)
  [b #7aa2f7]o[/]       Ouvrir le dossier de la candidature générée
  (la file est conservée entre deux lancements de la TUI — data/gen_queue.json)

[b]Onglet Candidatures[/b]
  [b #7aa2f7]↑ / ↓[/]   Naviguer dans les candidatures générées
  [b #7aa2f7]entrée[/]  Basculer l'aperçu lettre ↔ CV (HTML brut)
  [b #7aa2f7]e[/]       Ouvrir lettre.md dans l'éditeur (retouches)
  [b #7aa2f7]p[/]       Régénérer lettre.pdf depuis lettre.md (après retouche)
  [b #7aa2f7]u[/]       Régénérer cv_adapte.pdf depuis cv_adapte.html
  [b #7aa2f7]o[/]       Ouvrir le dossier de la candidature
  [b #7aa2f7]O[/]       Ouvrir l'offre dans le navigateur
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

[b]Onglet Réglages[/b]
  Liste les fichiers de configuration du pipeline (clés API, sujet de
  recherche, profil, sources, prompts, templates, données du CV) avec
  leur état (personnalisé · défaut du paquet · absent) et leur date de
  modification.
  [b #7aa2f7]↑ / ↓[/]   Naviguer
  [b #7aa2f7]entrée[/]  Ouvrir le fichier dans l'éditeur (copie d'abord le défaut du
             paquet vers la config utilisateur si besoin, avant édition)
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


class ConfirmScreen(ModalScreen[bool]):
    """Confirmation oui/non générique (utilisée pour le garde-fou inter-entreprises)."""

    BINDINGS = [
        Binding("escape", "dismiss_no", "Annuler"),
        Binding("y", "dismiss_yes", "Confirmer", show=False),
        Binding("n", "dismiss_no", "Annuler", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._message)
            yield Label("[y] Confirmer   [n / échap] Annuler")

    def action_dismiss_yes(self) -> None:
        self.dismiss(True)

    def action_dismiss_no(self) -> None:
        self.dismiss(False)


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
    #stats-months { padding: 1 1; }
    #letter-detail {
        width: 58%;
        border-left: solid $accent;
        padding: 0 1;
    }
    #help-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 96;
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
    HelpScreen, NoteScreen, ManualEntryScreen, SessionPickerScreen, DuplicatePickerScreen, ConfirmScreen { align: center middle; }
    #bottom-bar { dock: bottom; height: 2; }
    #status-bar { height: 1; background: $panel; color: $text-muted; }
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
        Binding("o", "open_url", "Ouvrir", show=False),
        Binding("O", "open_offer_url", "Ouvrir l'offre", show=False),
        Binding("g", "generate", "Générer", show=False),
        Binding("L", "generate_letter_only", "Lettre seule", show=False),
        Binding("A", "add_manual", "Ajout manuel", show=False),
        Binding("x", "remove_from_queue", "Retirer de la file", show=False),
        Binding("plus,equals_sign", "threshold(1)", show=False),
        Binding("minus", "threshold(-1)", show=False),
        Binding("h", "toggle_hide_done", show=False),
        Binding("m", "mark_duplicate", show=False),
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
    ]

    def __init__(self) -> None:
        super().__init__()
        self.offers: dict[str, dict] = {}
        self.selected: set[str] = set(load_queue())
        self.gen_checked: set[str] = set()  # coches de l'onglet Génération, filtre optionnel — non persisté
        self.threshold = get_search().threshold
        self.hide_done = False
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
                    yield Static("", id="stats-months", classes="stats-section")
                    yield Static("", id="stats-flow", classes="stats-section")
                    yield Static("", id="stats-flow-months", classes="stats-section")
            with TabPane("6 · Réglages", id="settings"):
                with Horizontal():
                    yield HighlightDataTable(id="settings-table", cursor_type="row")
                    yield VerticalScroll(Static("", id="settings-detail-text"), id="settings-detail")
        # Barre de statut + Footer regroupés dans un conteneur docké : deux widgets
        # `dock: bottom` frères se superposent sur la même ligne (l'un masque l'autre).
        with Vertical(id="bottom-bar"):
            yield Static("", id="status-bar")
            yield Footer()

    def on_mount(self) -> None:
        self.query_one("#offers-table", DataTable).add_columns(
            "✓", "Score", "Titre", "Entreprise", "Lieu", "Pays", "Source", "Vu le"
        )
        self.query_one("#gen-table", DataTable).add_columns(
            "✓", "État", "Titre", "Entreprise", "Dossier / info"
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
        self.query_one("#stats-months").border_title = "Candidatures au cours du temps"
        self.query_one("#stats-flow").border_title = "Parcours des candidatures"
        self.query_one("#stats-flow-months").border_title = "Statuts atteints par mois"
        self.action_reload()

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
            and not (self.hide_done and (o["id"] in applied or o.get("duplicate_of")))
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
            o["id"]: Style(color="black", bgcolor="green3")
            for o in rows if o["id"] in self.selected
        })
        with self._keep_cursor("offers-table"):
            table.clear()
            for o in rows:
                selected = o["id"] in self.selected
                if selected:
                    mark = "▶"
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
        self.sub_title = (
            f"seuil ≥ {self.threshold}{period_txt} · {len(rows)} offre(s){hidden}"
            f" · {len(self.selected)} cochée(s){dup_txt}"
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
                table.add_row(
                    mark, icon, o.get("title", "?")[:50], (o.get("company", "") or "?")[:24], info[:120], key=oid
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
            status = status_by_offer.get(os.path.basename(folder).rsplit("_", 1)[-1], "generated")
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
        offer_id = os.path.basename(folder).rsplit("_", 1)[-1]
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
            cmd = shlex.split(editor) + [path]
            with self.suspend():
                subprocess.run(cmd, check=False)
            return

        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", path], check=False)

    def refresh_stats(self) -> None:
        stats = compute_stats(PATHS.data_dir)
        header = self.query_one("#stats-header", Static)
        return_text = self.query_one("#stats-return-text", Static)
        status_bars = self.query_one("#stats-status-bars", Static)
        loc_text = self.query_one("#stats-loc-text", Static)
        loc_bars = self.query_one("#stats-loc-bars", Static)
        months_widget = self.query_one("#stats-months", Static)
        flow_widget = self.query_one("#stats-flow", Static)
        flow_months_widget = self.query_one("#stats-flow-months", Static)

        if stats["total"] == 0:
            header.update("[b]Statistiques[/b]  [dim]— aucune candidature enregistrée pour l'instant.[/dim]")
            for w in (return_text, status_bars, loc_text, loc_bars, months_widget, flow_widget, flow_months_widget):
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
        loc_text.update(
            f"Pays distincts : [b]{n_pays}[/b]\n\n"
            f"Le plus fréquent :\n[b]{top_pays}[/b]"
        )
        loc_lw = min(18, max((len(l) for l, _ in locs[:8]), default=3))
        loc_bars.update("\n".join(
            _hbar(l[:loc_lw], v, total, loc_lw, style="cyan") for l, v in locs[:8]
        ))

        # --- Section 3 : histogramme temporel ---
        months_widget.update(render_month_histogram(stats["by_month"]))

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
        tab = self._active_tab()
        if tab == "offers":
            oid = self._current_row_key("offers-table")
            url = self.offers.get(oid, {}).get("url") if oid else None
            if url:
                webbrowser.open(url)
        elif tab == "gen":
            oid = self._current_row_key("gen-table")
            if oid and self.gen_status.get(oid, ("", ""))[0] == "✅":
                subprocess.run(["open", self.gen_status[oid][1]], check=False)
        elif tab == "letters":
            folder = self._current_row_key("letters-table")
            if folder:
                subprocess.run(["open", folder], check=False)
        elif tab == "track":
            oid = self._current_row_key("track-table")
            for app in load_applications():
                if app["offer_id"] == oid and app.get("url"):
                    webbrowser.open(app["url"])
                    break

    def action_open_offer_url(self) -> None:
        if self._active_tab() != "letters":
            return
        folder = self._current_row_key("letters-table")
        if not folder:
            return
        offer_id = os.path.basename(folder).rsplit("_", 1)[-1]
        for app in load_applications():
            if app["offer_id"] == offer_id and app.get("url"):
                webbrowser.open(app["url"])
                break

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
        subprocess.run(["open", md], check=False)
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
                filtered, pertinent = filter_all(threshold=self.threshold)
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
            apps = load_applications()
            for app in apps:
                if app["offer_id"] == oid:
                    app["notes"] = note
            from jobsearch.tracker import save_applications
            save_applications(apps)
            self.refresh_track()
            self.notify_status(f"Note enregistrée pour {oid}.")

        self.push_screen(NoteScreen(current), save)

    def action_export_flow(self) -> None:
        if self._active_tab() != "stats":
            return
        try:
            path = write_flow_html(DEFAULT_FLOW_PATH, load_applications())
            webbrowser.open("file://" + os.path.abspath(path))
        except Exception as e:
            self.notify_status(f"Erreur export Sankey : {type(e).__name__}: {e}")
            return
        self.notify_status(f"Diagramme de flux exporté : {path}")


def _warn_if_empty_home() -> bool:
    """Garde-fou : n'affiche jamais silencieusement « 0 offre » si les données existent
    encore dans le dépôt (ancien emplacement) mais n'ont pas été copiées vers `JOBSEARCH_HOME`.
    Retourne True si la TUI doit s'arrêter avant de se lancer."""
    from jobsearch.config import PACKAGE_ROOT, JOBSEARCH_HOME

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
