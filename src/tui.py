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
import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Markdown, Static, TabbedContent, TabPane

from src.filter import strip_html
from src.report import load_filtered_offers
from src.tracker import load_applications, mark_status

CONSOLE_URL = "https://console.anthropic.com/settings/billing"

STATUS_KEYS = {
    "s": "sent",
    "a": "answered",
    "i": "interview",
    "j": "rejected",
    "v": "accepted",
}

HELP_TEXT = """\
[b]Raccourcis globaux[/b]
  1 / 2 / 3 / 4   Onglets : Offres · Génération · Candidatures · Suivi
  r           Recharger les données depuis le disque
  S           Lancer scrape + filtre + rapport
  c           Ouvrir la Claude Console (recharge crédit API)
  ?           Cette aide
  q           Quitter

[b]Onglet Offres[/b]
  ↑ / ↓       Naviguer
  espace      Cocher / décocher l'offre pour génération
  o           Ouvrir l'offre dans le navigateur
  + / -       Augmenter / diminuer le seuil de score
  h           Masquer / afficher les offres déjà candidatées
  g           Passer à l'onglet Génération

[b]Onglet Génération[/b]
  g           Lancer la génération (lettre + CV) des offres cochées
  L           Générer la lettre seule (sans CV adapté — économie d'un appel API)
  o           Ouvrir le dossier de la candidature générée

[b]Onglet Candidatures[/b]
  ↑ / ↓       Naviguer dans les candidatures générées
  entrée      Basculer l'aperçu lettre ↔ CV (HTML brut)
  e           Ouvrir lettre.md dans l'éditeur (retouches)
  p           Régénérer lettre.pdf depuis lettre.md (après retouche)
  u           Régénérer cv_adapte.pdf depuis cv_adapte.html
  o           Ouvrir le dossier de la candidature

[b]Onglet Suivi[/b]
  s           Statut → envoyée (sent)
  a           Statut → réponse reçue (answered)
  i           Statut → entretien (interview)
  j           Statut → refusée (rejected)
  v           Statut → acceptée (accepted)
  n           Éditer la note de la candidature
  o           Ouvrir l'offre dans le navigateur
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
    #letter-detail {
        width: 58%;
        border-left: solid $accent;
        padding: 0 1;
    }
    #help-box, #note-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 70;
        height: auto;
        max-height: 90%;
        align: center middle;
    }
    HelpScreen, NoteScreen { align: center middle; }
    #status-bar { dock: bottom; height: 1; background: $panel; color: $text-muted; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quitter"),
        Binding("question_mark", "help", "Aide"),
        Binding("r", "reload", "Recharger"),
        Binding("c", "open_console", "Console API"),
        Binding("S", "scrape", "Scrape"),
        Binding("1", "tab('offers')", show=False),
        Binding("2", "tab('gen')", show=False),
        Binding("3", "tab('letters')", show=False),
        Binding("4", "tab('track')", show=False),
        Binding("e", "edit_letter", show=False),
        Binding("p", "render_letter_pdf", show=False),
        Binding("u", "render_cv_pdf", show=False),
        Binding("space", "toggle_select", "Cocher", show=False),
        Binding("o", "open_url", "Ouvrir", show=False),
        Binding("g", "generate", "Générer", show=False),
        Binding("L", "generate_letter_only", "Lettre seule", show=False),
        Binding("plus,equals_sign", "threshold(1)", show=False),
        Binding("minus", "threshold(-1)", show=False),
        Binding("h", "toggle_hide_done", show=False),
        Binding("n", "edit_note", show=False),
        Binding("s", "set_status('sent')", show=False),
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
        self.gen_status: dict[str, tuple[str, str]] = {}  # id -> (icone, info/dossier)

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="offers"):
            with TabPane("1 · Offres", id="offers"):
                with Horizontal():
                    yield DataTable(id="offers-table", cursor_type="row")
                    yield VerticalScroll(Static("", id="detail-text"), id="detail")
            with TabPane("2 · Génération", id="gen"):
                yield DataTable(id="gen-table", cursor_type="row")
            with TabPane("3 · Candidatures", id="letters"):
                with Horizontal():
                    yield DataTable(id="letters-table", cursor_type="row")
                    yield VerticalScroll(Markdown("", id="letter-preview"), id="letter-detail")
            with TabPane("4 · Suivi", id="track"):
                yield DataTable(id="track-table", cursor_type="row")
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#offers-table", DataTable).add_columns(
            "✓", "Score", "Titre", "Entreprise", "Lieu", "Pays", "Source"
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
        self.action_reload()

    # ---------- data ----------

    def action_reload(self) -> None:
        self.offers = {o["id"]: o for o in load_filtered_offers("data")}
        self.selected &= set(self.offers)
        self.refresh_offers()
        self.refresh_gen()
        self.refresh_letters()
        self.refresh_track()
        self.notify_status(f"{len(self.offers)} offre(s) filtrée(s) chargée(s).")

    def _applied_ids(self) -> set[str]:
        return {a["offer_id"] for a in load_applications()}

    def refresh_offers(self) -> None:
        table = self.query_one("#offers-table", DataTable)
        table.clear()
        applied = self._applied_ids()
        rows = [
            o for o in self.offers.values()
            if o["filter_result"].get("score", 0) >= self.threshold
            and not (self.hide_done and o["id"] in applied)
        ]
        rows.sort(key=lambda o: o["filter_result"].get("score", 0), reverse=True)
        for o in rows:
            mark = "▶" if o["id"] in self.selected else ("✓" if o["id"] in applied else "")
            table.add_row(
                mark,
                str(o["filter_result"].get("score", "?")),
                o.get("title", "")[:50],
                (o.get("company", "") or "?")[:24],
                o.get("location", "")[:20],
                o.get("country", ""),
                o.get("source", "")[:28],
                key=o["id"],
            )
        hidden = " · candidatées masquées" if self.hide_done else ""
        self.sub_title = f"seuil ≥ {self.threshold} · {len(rows)} offre(s){hidden} · {len(self.selected)} cochée(s)"
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

        table = self.query_one("#letters-table", DataTable)
        # Préserve le dossier sous le curseur pour le restaurer après reconstruction
        # (sinon `p`/`u` renvoient le curseur en haut → aperçu et surbrillance perdus).
        previous = self._current_row_key("letters-table")
        table.clear()
        for folder in self._candidature_dirs():
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
        table = self.query_one("#track-table", DataTable)
        table.clear()
        for app in load_applications():
            table.add_row(
                app["offer_id"],
                app["status"],
                app["company"][:22],
                app["title"][:40],
                app.get("generated_at", "") or "",
                app.get("sent_at", "") or "",
                (app.get("notes", "") or "")[:40],
                key=app["offer_id"],
            )

    # ---------- helpers ----------

    def notify_status(self, message: str) -> None:
        self.query_one("#status-bar", Static).update(f" {message}")

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
            from src.letter_pdf import render_letter_html, write_letter_pdf

            info = self._letter_offer_info(folder)
            html = render_letter_html(
                os.path.join("config", "templates", "cover_letter.html"),
                load_cv_data("cv_master"),
                open(md, encoding="utf-8").read(),
                company_name=info["company"],
                company_location=info["location"],
                position_title=info["title"],
            )
            write_letter_pdf(html, os.path.join(folder, "lettre.pdf"))
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
        done = sum(1 for oid in offer_ids if self.gen_status[oid][0] == "✅")
        self.call_from_thread(
            self.notify_status,
            f"Génération terminée : {done}/{len(offer_ids)} réussie(s). (c = Claude Console si problème de crédit)",
        )

    @work(thread=True, exclusive=True, group="scrape")
    def run_scrape(self) -> None:
        from src.filter import filter_all
        from src.report import generate_report
        from src.scraper import scrape_all

        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                new = scrape_all()
                filtered, pertinent = filter_all(threshold=self.threshold)
                report = generate_report(threshold=self.threshold)
            msg = f"Scrape terminé : {len(new)} nouvelle(s), {filtered} filtrée(s), {pertinent} pertinente(s). Rapport : {report}"
        except SystemExit:
            msg = "Scrape interrompu : " + (buffer.getvalue().strip().splitlines()[-1] if buffer.getvalue().strip() else "erreur")
        except Exception as e:
            msg = f"Erreur pendant le scrape : {e}"
        self.call_from_thread(self.action_reload)
        self.call_from_thread(self.notify_status, msg)

    def action_scrape(self) -> None:
        self.notify_status("Scrape + filtre + rapport en cours (peut prendre plusieurs minutes)...")
        self.run_scrape()

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
