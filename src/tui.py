"""Interface TUI (Textual) — revue des offres, génération, suivi des candidatures.

La TUI est une simple vue sur les fichiers du pipeline : elle relit l'état
courant depuis data/ à chaque lancement (et sur `r`), n'écrit que via les
fonctions métier existantes, et ne persiste aucun état propre.
"""
import contextlib
import io
import json
import os
import subprocess
import time
import webbrowser
from datetime import date, timedelta

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
from textual.widgets.option_list import Option

from src.filter import strip_html
from src.report import load_filtered_offers
from src.tracker import compute_stats, load_applications, mark_status


def _cells(values):
    return [str(v) for v in values]


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
    return f"{label:<{label_w}} {color}{bar}{close} {value:>2} ({pct:>3}%)"


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
SENT_HIGHLIGHT_COLOR = "#1e4620"
# Surlignage des candidatures en attente de réponse (onglet Suivi) — jaune, modifiable ici.
PENDING_HIGHLIGHT_COLOR = "#584b00"
# Surlignage des offres de la dernière session de scrape (onglet Offres) — bleu foncé, modifiable ici.
NEW_OFFER_HIGHLIGHT_COLOR = "#1b3a5c"
# Statuts considérés comme « en attente de réponse » (candidature envoyée, décision non tombée).
PENDING_STATUSES = {"sent", "answered", "interview"}

CONSOLE_URL = "https://console.anthropic.com/settings/billing"

# Animation du spinner affiché dans la barre de statut pendant un scrape.
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

STATUS_KEYS = {
    "s": "sent",
    "a": "answered",
    "i": "interview",
    "j": "rejected",
    "v": "accepted",
}

HELP_TEXT = """\
[b]Raccourcis globaux[/b]
  1 / 2 / 3 / 4 / 5   Onglets : Offres · Génération · Candidatures · Suivi · Stats
  r           Recharger les données depuis le disque
  S / s / ^S  Lancer scrape + filtre + rapport (spinner dans la barre de statut)
              (`s` = statut « envoyée » dans l'onglet Suivi uniquement)
  c           Ouvrir la Claude Console (recharge crédit API)
  ?           Cette aide
  q           Quitter

[b]Onglet Offres[/b]
  ↑ / ↓       Naviguer
  espace      Cocher / décocher l'offre pour génération
  o           Ouvrir l'offre dans le navigateur
  + / -       Augmenter / diminuer le seuil de score
  h           Masquer / afficher les offres déjà candidatées
  d           Cycler la période affichée (tout · session · 7j · 30j)
  D           Choisir une session de scrape précise
  g           Passer à l'onglet Génération
  (les offres de la dernière session sont surlignées en bleu)

[b]Onglet Génération[/b]
  g           Lancer la génération (lettre + CV) des offres cochées
  L           Générer la lettre seule (sans CV adapté — économie d'un appel API)
  A           Ajouter une offre manuelle (fichier HTML ou URL) à la liste, puis g / L
  o           Ouvrir le dossier de la candidature générée

[b]Onglet Candidatures[/b]
  ↑ / ↓       Naviguer dans les candidatures générées
  entrée      Basculer l'aperçu lettre ↔ CV (HTML brut)
  e           Ouvrir lettre.md dans l'éditeur (retouches)
  p           Régénérer lettre.pdf depuis lettre.md (après retouche)
  u           Régénérer cv_adapte.pdf depuis cv_adapte.html
  o           Ouvrir le dossier de la candidature
  O           Ouvrir l'offre dans le navigateur
  (les candidatures envoyées ou plus sont surlignées en vert)

[b]Onglet Suivi[/b]
  s           Statut → envoyée (sent)
  a           Statut → réponse reçue (answered)
  i           Statut → entretien (interview)
  j           Statut → refusée (rejected)
  v           Statut → acceptée (accepted)
  n           Éditer la note de la candidature
  o           Ouvrir l'offre dans le navigateur
  (en attente de réponse en jaune, refusées en rouge)

[b]Onglet Stats[/b]
  Retour de candidature (taux d'acceptation, taux de réponse,
  délai moyen), localisation des stages, et histogramme des
  candidatures au cours du temps (lecture seule).
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


class StageTUI(App):
    TITLE = "Pipeline de stage"

    CSS = """
    #detail {
        width: 45%;
        border-left: solid $accent;
        padding: 0 1;
    }
    #offers-table { width: 55%; }
    #letters-table { width: 42%; }
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
    #help-box, #note-box, #manual-box {
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
    HelpScreen, NoteScreen, ManualEntryScreen, SessionPickerScreen { align: center middle; }
    #bottom-bar { dock: bottom; height: 2; }
    #status-bar { height: 1; background: $panel; color: $text-muted; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quitter"),
        Binding("question_mark", "help", "Aide"),
        Binding("r", "reload", "Recharger"),
        Binding("c", "open_console", "Console API"),
        Binding("S", "scrape", "Scrape"),
        # Alias : certains terminaux ne transmettent pas shift+lettre à Textual.
        Binding("ctrl+s", "scrape", show=False),
        Binding("1", "tab('offers')", show=False),
        Binding("2", "tab('gen')", show=False),
        Binding("3", "tab('letters')", show=False),
        Binding("4", "tab('track')", show=False),
        Binding("5", "tab('stats')", show=False),
        Binding("e", "edit_letter", show=False),
        Binding("p", "render_letter_pdf", show=False),
        Binding("u", "render_cv_pdf", show=False),
        Binding("space", "toggle_select", "Cocher", show=False),
        Binding("o", "open_url", "Ouvrir", show=False),
        Binding("O", "open_offer_url", "Ouvrir l'offre", show=False),
        Binding("g", "generate", "Générer", show=False),
        Binding("L", "generate_letter_only", "Lettre seule", show=False),
        Binding("A", "add_manual", "Ajout manuel", show=False),
        Binding("plus,equals_sign", "threshold(1)", show=False),
        Binding("minus", "threshold(-1)", show=False),
        Binding("h", "toggle_hide_done", show=False),
        Binding("d", "cycle_period", show=False),
        Binding("D", "pick_session", show=False),
        Binding("n", "edit_note", show=False),
        Binding("s", "sent_or_scrape", show=False),
        Binding("a", "set_status('answered')", show=False),
        Binding("i", "set_status('interview')", show=False),
        Binding("j", "set_status('rejected')", show=False),
        Binding("v", "set_status('accepted')", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.offers: dict[str, dict] = {}
        self.selected: set[str] = set()
        self.threshold = 6
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
                yield DataTable(id="gen-table", cursor_type="row")
            with TabPane("3 · Candidatures", id="letters"):
                with Horizontal():
                    yield HighlightDataTable(id="letters-table", cursor_type="row")
                    yield VerticalScroll(Markdown("", id="letter-preview"), id="letter-detail")
            with TabPane("4 · Suivi", id="track"):
                yield HighlightDataTable(id="track-table", cursor_type="row")
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
            "État", "Titre", "Entreprise", "Dossier / info"
        )
        self.query_one("#letters-table", DataTable).add_columns(
            "Dossier", "Lettre", "PDF", "CV PDF", "Modifiée"
        )
        self.query_one("#track-table", DataTable).add_columns(
            "ID", "Statut", "Entreprise", "Titre", "Générée", "Envoyée", "Notes"
        )
        self.query_one("#stats-return").border_title = "Retour de candidature"
        self.query_one("#stats-loc").border_title = "Localisation des stages"
        self.query_one("#stats-months").border_title = "Candidatures au cours du temps"
        self.action_reload()

    # ---------- data ----------

    def action_reload(self) -> None:
        self.offers = {o["id"]: o for o in load_filtered_offers("data")}
        self.selected &= set(self.offers)
        self.refresh_offers()
        self.refresh_gen()
        self.refresh_letters()
        self.refresh_track()
        self.refresh_stats()
        self.notify_status(f"{len(self.offers)} offre(s) filtrée(s) chargée(s).")

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

    def refresh_offers(self) -> None:
        table = self.query_one("#offers-table", HighlightDataTable)
        applied = self._applied_ids()
        latest_session = self._latest_session()
        exact_date, min_date = self._period_cutoff()
        rows = [
            o for o in self.offers.values()
            if o["filter_result"].get("score", 0) >= self.threshold
            and not (self.hide_done and o["id"] in applied)
            and (exact_date is None or o.get("date_scraped", "") == exact_date)
            and (min_date is None or o.get("date_scraped", "") >= min_date)
        ]
        rows.sort(key=lambda o: o["filter_result"].get("score", 0), reverse=True)
        table.row_styles = {
            o["id"]: Style(bgcolor=NEW_OFFER_HIGHLIGHT_COLOR)
            for o in rows if latest_session and o.get("date_scraped", "") == latest_session
        }
        table.row_styles.update({
            o["id"]: Style(color="black", bgcolor="green3")
            for o in rows if o["id"] in self.selected
        })
        table.clear()
        for o in rows:
            selected = o["id"] in self.selected
            mark = "▶" if selected else ("✓" if o["id"] in applied else "")
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
        self.sub_title = (
            f"seuil ≥ {self.threshold}{period_txt} · {len(rows)} offre(s){hidden} · {len(self.selected)} cochée(s)"
        )
        self.update_detail()

    def refresh_gen(self) -> None:
        table = self.query_one("#gen-table", DataTable)
        table.clear()
        for oid in sorted(self.selected):
            o = self.offers.get(oid, {})
            icon, info = self.gen_status.get(oid, ("·", "en attente — g pour lancer"))
            table.add_row(icon, o.get("title", "?")[:50], (o.get("company", "") or "?")[:24], info[:120], key=oid)

    def _candidature_dirs(self) -> list[str]:
        base = os.path.join("output", "candidatures")
        if not os.path.isdir(base):
            return []
        dirs = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        return sorted(dirs, key=os.path.getmtime, reverse=True)

    def refresh_letters(self) -> None:
        import datetime

        table = self.query_one("#letters-table", HighlightDataTable)
        # Préserve le dossier sous le curseur pour le restaurer après reconstruction
        # (sinon `p`/`u` renvoient le curseur en haut → aperçu et surbrillance perdus).
        previous = self._current_row_key("letters-table")
        status_by_offer = {a["offer_id"]: a["status"] for a in load_applications()}
        dirs = self._candidature_dirs()
        table.row_styles = {
            folder: Style(color="white", bgcolor=SENT_HIGHLIGHT_COLOR)
            for folder in dirs
            if status_by_offer.get(os.path.basename(folder).rsplit("_", 1)[-1], "generated") != "generated"
        }
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
        if previous is not None:
            try:
                table.move_cursor(row=table.get_row_index(previous))
            except KeyError:
                pass  # le dossier n'existe plus dans la liste
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
        offer_path = os.path.join("data", "offers", f"{offer_id}.json")
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
            **{
                a["offer_id"]: Style(color="white", bgcolor=PENDING_HIGHLIGHT_COLOR)
                for a in apps if a["status"] in PENDING_STATUSES
            },
            **{
                a["offer_id"]: Style(color="white", bgcolor="dark_red")
                for a in apps if a["status"] == "rejected"
            },
        }
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

    def refresh_stats(self) -> None:
        stats = compute_stats("data")
        header = self.query_one("#stats-header", Static)
        return_text = self.query_one("#stats-return-text", Static)
        status_bars = self.query_one("#stats-status-bars", Static)
        loc_text = self.query_one("#stats-loc-text", Static)
        loc_bars = self.query_one("#stats-loc-bars", Static)
        months_widget = self.query_one("#stats-months", Static)

        if stats["total"] == 0:
            header.update("[b]Statistiques[/b]  [dim]— aucune candidature enregistrée pour l'instant.[/dim]")
            for w in (return_text, status_bars, loc_text, loc_bars, months_widget):
                w.update("")
            return

        c = stats["coarse"]
        total = stats["total"]
        header.update(
            f"[b]Statistiques — {total} candidature(s)[/b]   "
            f"[green]Accepté {c['Accepté']}[/green] · [red]Refusé {c['Refusé']}[/red] · "
            f"[yellow]En attente {c['En attente']}[/yellow]"
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
            _hbar("Accepté", c["Accepté"], total, lw, style="green"),
            _hbar("Refusé", c["Refusé"], total, lw, style="red"),
            _hbar("En attente", c["En attente"], total, lw, style="yellow"),
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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "letters-table":
            self.action_toggle_preview()

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
        if self._active_tab() != "offers":
            return
        oid = self._current_row_key("offers-table")
        if not oid:
            return
        if oid in self.selected:
            self.selected.discard(oid)
        else:
            self.selected.add(oid)
        self.refresh_offers()
        self.refresh_gen()

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
            from src.cv_pdf import load_cv_data
            from src.letter_pdf import render_letter_pdf

            info = self._letter_offer_info(folder)
            render_letter_pdf(
                os.path.join("config", "templates", "cover_letter.html"),
                load_cv_data("cv_master"),
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
            from src.cv_pdf import write_cv_pdf

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
        from src.manual import create_stub_from_file, create_stub_from_url

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

        offer = {**offer, "filter_result": {"score": 10, "reason": "Ajout manuel via TUI"}}

        def _register() -> None:
            self.offers[offer["id"]] = offer
            self.selected.add(offer["id"])
            self.gen_status.pop(offer["id"], None)  # état par défaut « en attente »
            self.refresh_offers()
            self.refresh_gen()
            self.notify_status(
                f"Offre « {offer['title']} » ajoutée — g (lettre + CV) ou L (lettre seule)."
            )

        self.call_from_thread(_register)

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
        todo = [oid for oid in sorted(self.selected) if self.gen_status.get(oid, ("·",))[0] not in ("⏳", "✅")]
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
        from src.generator import generate

        for oid in offer_ids:
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                    folder = generate(oid, letter_only=letter_only)
                self.gen_status[oid] = ("✅", folder)
            except SystemExit:
                out = buffer.getvalue().strip()
                self.gen_status[oid] = ("❌", out.splitlines()[-1] if out else "erreur (voir python -m src.generator " + oid + ")")
            except Exception as e:
                self.gen_status[oid] = ("❌", f"{type(e).__name__}: {e}".replace("\n", " ")[:120])
            self.call_from_thread(self.refresh_gen)
            self.call_from_thread(self.refresh_track)
            self.call_from_thread(self.refresh_stats)
        done = sum(1 for oid in offer_ids if self.gen_status[oid][0] == "✅")
        self.call_from_thread(
            self.notify_status,
            f"Génération terminée : {done}/{len(offer_ids)} réussie(s). (c = Claude Console si problème de crédit)",
        )

    @work(thread=True, exclusive=True, group="scrape")
    def run_scrape(self) -> None:
        buffer = io.StringIO()
        try:
            from src.filter import filter_all
            from src.report import generate_report
            from src.scraper import scrape_all

            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                new = scrape_all()
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

    def action_scrape(self) -> None:
        if self._scrape_phase is not None:
            # Toast et non barre de statut : celle-ci est réécrite par le spinner à chaque tick.
            self.notify("Un scrape est déjà en cours.", title="Scrape", severity="warning")
            return
        self._start_spinner("Scrape des sources…")
        self.notify("Scrape + filtre + rapport lancés (plusieurs minutes).", title="Scrape")
        self.run_scrape()

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
            from src.tracker import save_applications
            save_applications(apps)
            self.refresh_track()
            self.notify_status(f"Note enregistrée pour {oid}.")

        self.push_screen(NoteScreen(current), save)


def run_tui() -> None:
    StageTUI().run()


if __name__ == "__main__":
    run_tui()
