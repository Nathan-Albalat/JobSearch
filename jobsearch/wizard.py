"""Assistant d'installation interactif (`jobsearch --setup`).

App Textual autonome (distincte de `jobsearch.tui.StageTUI`) qui accompagne
un tiers dans les 6 étapes de première configuration : présentation, clé
API, sujet de recherche, prompt de scoring (non sautable), profil, récap.

Motif « aperçu puis confirmation » repris de `jobsearch/migrate.py` : rien
n'est écrit sur le disque sans que le contenu ait été montré, et un fichier
déjà présent n'est jamais écrasé sans confirmation explicite. Navigation :
`échap` revient à l'étape précédente (les réponses déjà saisies sont
conservées) — sauf sur la première étape, où `échap` quitte l'assistant.
Toutes les écritures sont différées jusqu'à la confirmation du récapitulatif
final : revenir en arrière et re-valider ne redemande donc jamais de
confirmation d'écrasement sur un fichier que l'assistant vient lui-même
d'écrire.
"""
from __future__ import annotations

import asyncio
import os
import re

import yaml
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, Static

from jobsearch.config import PACKAGE_ROOT, PATHS

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


class _Back:
    """Sentinelle : « revenir à l'étape précédente », distincte des données
    d'un écran et de l'annulation complète de l'assistant."""

    def __repr__(self) -> str:  # pragma: no cover - confort de debug
        return "BACK"


BACK = _Back()


# ---------------------------------------------------------------------------
# Aide à la génération de contenu (fonctions pures, testables sans TUI)
# ---------------------------------------------------------------------------


def parse_csv(value: str, default: list[str] | None = None) -> list[str]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items if items else list(default or [])


def _yaml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _section_span(lines: list[str], section: str) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{section}:"):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j] and not lines[j][0].isspace():
            end = j
            break
    return start, end


def _get_scalar(text: str, section: str, key: str) -> str | None:
    """Lit la valeur scalaire courante de `section.key` (déquotée si besoin),
    utile pour composer une nouvelle valeur à partir de l'existante (ex:
    `geo.notes`) sans écraser le texte déjà présent dans le `.example.yaml`."""
    lines = text.split("\n")
    span = _section_span(lines, section)
    if span is None:
        return None
    start, end = span
    pat = re.compile(rf"^\s*{re.escape(key)}:\s*(?P<val>[^#]*?)\s*(?:#.*)?$")
    for i in range(start + 1, end):
        m = pat.match(lines[i])
        if m:
            val = m.group("val").strip()
            if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                val = val[1:-1]
            return val
    return None


def _set_scalar(text: str, section: str, key: str, new_value: str) -> str:
    lines = text.split("\n")
    span = _section_span(lines, section)
    if span is None:
        return text
    start, end = span
    # Le commentaire de fin de ligne est conservé : il documente le RÉGLAGE, pas la
    # valeur d'exemple (ex. « applicable: true  # false pour un CDI : neutralise les
    # règles de durée » reste utile une fois la valeur choisie). Seules exceptions
    # écartées plus bas : les commentaires qui ne sont qu'une liste d'exemples.
    pat = re.compile(rf"^(\s*){re.escape(key)}:\s*(?P<val>[^#]*?)\s*(?P<comment>#.*)?$")
    for i in range(start + 1, end):
        m = pat.match(lines[i])
        if m:
            comment = m.group("comment") or ""
            # « # ex: … » n'énumère que des valeurs possibles : devenu inutile une fois
            # le choix fait, et trompeur s'il suggère autre chose que la valeur retenue.
            if re.match(r"#\s*ex\s*:", comment, re.I):
                comment = ""
            line = f"{m.group(1)}{key}: {new_value}"
            lines[i] = f"{line}   {comment}" if comment else line
            break
    return "\n".join(lines)


def _set_list(text: str, section: str, key: str, items: list[str]) -> str:
    lines = text.split("\n")
    span = _section_span(lines, section)
    if span is None:
        return text
    start, end = span
    key_pat = re.compile(rf"^(\s*){re.escape(key)}:\s*$")
    for i in range(start + 1, end):
        m = key_pat.match(lines[i])
        if not m:
            continue
        indent = m.group(1)
        item_indent = indent + "  "
        j = i + 1
        while j < end and lines[j].startswith(item_indent + "-"):
            j += 1
        if items:
            new_items = [f"{item_indent}- {_yaml_str(it)}" for it in items]
            lines[i + 1:j] = new_items
        else:
            lines[i] = f"{indent}{key}: []"
            del lines[i + 1:j]
        break
    return "\n".join(lines)


def render_search_yaml(values: dict) -> str:
    """Génère `search.yaml` en substituant les valeurs de l'assistant dans le
    `.example.yaml` du dépôt — préserve les ~200 lignes de commentaires
    d'origine (jamais de `yaml.safe_dump`, qui les détruirait)."""
    example_path = os.path.join(PACKAGE_ROOT, "config", "search.example.yaml")
    if os.path.exists(example_path):
        with open(example_path, encoding="utf-8") as f:
            text = f.read()
    else:
        text = (
            "# search.yaml — généré par jobsearch --setup (search.example.yaml introuvable,\n"
            "# les clés absentes retombent sur les valeurs par défaut de jobsearch/config.py).\n"
            "sujet:\n  type_contrat: \"stage\"\nduree:\n  applicable: true\n"
            "  min_semaines: 22\n  texte: \"22 semaines minimum\"\n"
            "geo:\n  priorite: []\n  notes: \"\"\ndomaines:\n  liste: []\nscoring:\n  seuil: 6\n"
        )

    text = _set_scalar(text, "sujet", "type_contrat", _yaml_str(values["type_contrat"]))
    text = _set_scalar(text, "duree", "applicable", "true" if values["duree_applicable"] else "false")
    if values["duree_applicable"]:
        text = _set_scalar(text, "duree", "min_semaines", str(values["duree_semaines"]))
        text = _set_scalar(
            text, "duree", "texte", _yaml_str(f"{values['duree_semaines']} semaines minimum")
        )
    text = _set_list(text, "geo", "priorite", values["pays"])
    region_ville = (values.get("region_ville") or "").strip()
    if region_ville:
        # Reste cohérent avec `geo.priorite`/`secondaire` : on ne touche pas à la
        # géographie de pays ni à `geo.details` (barème), on précise seulement la
        # contrainte de localité dans `notes`, elle aussi injectée dans le prompt.
        current_notes = _get_scalar(text, "geo", "notes") or ""
        addition = f"Priorité géographique locale : {region_ville}."
        new_notes = f"{current_notes} {addition}".strip() if current_notes else addition
        text = _set_scalar(text, "geo", "notes", _yaml_str(new_notes))
    text = _set_list(text, "domaines", "liste", values["domaines"])
    text = _set_scalar(text, "scoring", "seuil", str(values["seuil"]))
    return text


def render_filter_prompt(values: dict) -> str:
    """Part du défaut générique livré avec le paquet et le paramètre avec les
    réponses de l'étape « sujet » — non sautable (décision utilisateur)."""
    base_path = os.path.join(PATHS.package_prompts_dir, "filter.txt")
    with open(base_path, encoding="utf-8") as f:
        base = f.read()

    domaines_txt = ", ".join(values["domaines"]) if values["domaines"] else "(non renseigné)"
    contrat_txt = values["type_contrat"] or "(non renseigné)"
    region_ville = (values.get("region_ville") or "").strip()
    region_line = (
        f"Région/Ville prioritaire (renseignée à l'installation) : {region_ville}\n"
        if region_ville
        else ""
    )
    extra = (
        "## Repères issus de la configuration initiale\n"
        f"Type de contrat recherché (renseigné à l'installation) : {contrat_txt}\n"
        f"Domaines prioritaires (renseignés à l'installation) : {domaines_txt}\n"
        f"{region_line}"
        "Ces valeurs recoupent celles de config/search.yaml (placeholders {type_contrat}/{domaines}\n"
        "utilisés ci-dessus) — ce bloc sert de repère lisible, à affiner librement depuis\n"
        "l'onglet Réglages (touche 6) une fois quelques offres scorées.\n\n"
    )
    marker = "## Considérations géographiques détaillées"
    if marker in base:
        base = base.replace(marker, extra + marker, 1)
    else:
        base = base + "\n" + extra
    return base


def render_profile_yaml(values: dict) -> str:
    langues = []
    for item in values["langues"]:
        if ":" in item:
            langue, niveau = item.split(":", 1)
        else:
            langue, niveau = item, ""
        langues.append({"langue": langue.strip(), "niveau": niveau.strip()})

    data = {
        "formation": {
            "ecole": values["ecole"],
            "niveau": values["niveau"],
            "specialite": values["specialite"],
            "diplome_prevu": values["diplome_prevu"],
        },
        "competences_techniques": {
            "langages_maitrise": values["langages_maitrise"],
            "langages_pratiques": values["langages_pratiques"],
            "frameworks": values["frameworks"],
            "outils": values["outils"],
            "domaines_ml": [],
        },
        "langues": langues,
    }
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def check_api_key(key: str) -> tuple[bool, str]:
    """Appel minimal (métadonnées, non facturé) pour valider une clé API."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        client.models.list(limit=1)
        return True, "Clé API valide."
    except Exception as e:  # pragma: no cover - dépend du réseau/de la clé
        return False, f"Clé invalide ou erreur réseau : {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------

WELCOME_TEXT = """\
[b]Bienvenue dans l'assistant d'installation de JobSearch.[/b]

JobSearch automatise la recherche d'un contrat (stage, alternance, CDI…) :
collecte des offres (RSS, APIs, LinkedIn/Indeed…), filtrage par pertinence
via Claude Haiku (scoring 0-10), rapport, puis lettre de motivation et CV
adapté via Claude Sonnet pour les offres retenues.

La porte d'entrée du quotidien est l'interface interactive :
  [b]jobsearch --tui[/b]

Coût API : le filtrage (Haiku) est peu coûteux, quelques centimes pour des
dizaines d'offres. La génération de candidature (Sonnet) coûte plus cher à
l'appel — à réserver aux offres qui t'intéressent vraiment.

Cet assistant configure, dans l'ordre : ta clé API, ton sujet de recherche,
le prompt de scoring (obligatoire — sans lui, le scoring reste calibré sur
un exemple générique), puis ton profil. Rien n'est écrit sur le disque avant
le récapitulatif final ; \\[échap] revient à l'étape précédente à tout moment.

[dim]entrée : continuer   ·   échap : annuler[/dim]
"""


class WelcomeScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("enter", "go", "Continuer"),
        Binding("escape", "cancel", "Annuler"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="wizard-box"):
            yield Static(WELCOME_TEXT)

    def action_go(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class OverwriteConfirmScreen(ModalScreen[bool]):
    """Confirmation oui/non générique (validation clé API, écrasement de fichier)."""

    BINDINGS = [
        Binding("enter", "yes", "Oui", show=False),
        Binding("y", "yes", "Oui", show=False),
        Binding("n", "no", "Non", show=False),
        Binding("escape", "no", "Non"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-box"):
            yield Label(self._message)
            yield Label("\\[entrée / y] Oui   \\[n / échap] Non")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class InfoScreen(ModalScreen[None]):
    """Affiche un message (résultat de validation de clé…) — entrée pour continuer."""

    BINDINGS = [Binding("enter", "go", "Continuer"), Binding("escape", "go", "Continuer")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-box"):
            yield Label(self._message)
            yield Label("[dim]entrée pour continuer[/dim]")

    def action_go(self) -> None:
        self.dismiss(None)


class ApiKeyScreen(ModalScreen[dict | _Back]):
    """Étape 2 — Clé API. Sautable (Input vide = pas de clé renseignée)."""

    BINDINGS = [Binding("escape", "back", "Retour")]

    def __init__(self, initial_key: str = "") -> None:
        super().__init__()
        self._initial_key = initial_key

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-box"):
            yield Label(
                "Clé API Anthropic (console.anthropic.com/settings/keys).\n"
                "Laisse vide et appuie sur entrée pour passer cette étape.\n"
                "entrée : valider   ·   échap : retour à l'étape précédente"
            )
            yield Input(
                value=self._initial_key, password=True, placeholder="sk-ant-...", id="key-input"
            )

    def on_mount(self) -> None:
        self.query_one("#key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        key = self.query_one("#key-input", Input).value.strip()
        self.dismiss({"key": key})

    def action_back(self) -> None:
        self.dismiss(BACK)


SEARCH_DEFAULTS = {
    "type_contrat": "stage",
    "duree_applicable": True,
    "duree_semaines": 22,
    "pays": ["Pays-Bas"],
    "domaines": ["Informatique générale"],
    "seuil": 6,
    "region_ville": "",
}


class SearchScreen(ModalScreen[dict | _Back]):
    """Étape 3 — Sujet de recherche."""

    BINDINGS = [Binding("escape", "back", "Retour")]

    def __init__(self, initial: dict | None = None) -> None:
        super().__init__()
        self._initial = {**SEARCH_DEFAULTS, **(initial or {})}

    def compose(self) -> ComposeResult:
        v = self._initial
        with VerticalScroll(id="wizard-box"):
            yield Label(
                "Sujet de recherche — entrée dans n'importe quel champ valide le formulaire.\n"
                "échap : retour à l'étape précédente (les réponses saisies sont conservées)."
            )
            yield Label("Type de contrat (ex: stage, CDI, alternance) :")
            yield Input(value=v["type_contrat"], id="s-contrat")
            yield Label("Durée minimale applicable ? (o/n) :")
            yield Input(value="o" if v["duree_applicable"] else "n", id="s-duree-app")
            yield Label("Durée minimale, en semaines :")
            yield Input(value=str(v["duree_semaines"]), id="s-duree-sem")
            yield Label("Pays prioritaires (séparés par des virgules) :")
            yield Input(value=", ".join(v["pays"]), id="s-pays")
            yield Label("Région / Ville prioritaire (optionnel — laisser vide si sans importance) :")
            yield Input(value=v["region_ville"], id="s-region")
            yield Label("Domaines visés (séparés par des virgules) :")
            yield Input(value=", ".join(v["domaines"]), id="s-domaines")
            yield Label("Seuil de score de pertinence (0-10) :")
            yield Input(value=str(v["seuil"]), id="s-seuil")
            yield Label("", id="s-error")

    def on_mount(self) -> None:
        self.query_one("#s-contrat", Input).focus()

    def _collect(self) -> dict | None:
        error = self.query_one("#s-error", Label)
        try:
            duree_sem = int(self.query_one("#s-duree-sem", Input).value.strip() or "22")
            seuil = int(self.query_one("#s-seuil", Input).value.strip() or "6")
        except ValueError:
            error.update("[red]Durée et seuil doivent être des nombres entiers.[/red]")
            return None
        contrat = self.query_one("#s-contrat", Input).value.strip() or "stage"
        duree_app = self.query_one("#s-duree-app", Input).value.strip().lower() not in ("n", "non", "no")
        pays = parse_csv(self.query_one("#s-pays", Input).value, ["Pays-Bas"])
        region_ville = self.query_one("#s-region", Input).value.strip()
        domaines = parse_csv(self.query_one("#s-domaines", Input).value, ["Informatique générale"])
        return {
            "type_contrat": contrat,
            "duree_applicable": duree_app,
            "duree_semaines": duree_sem,
            "pays": pays,
            "region_ville": region_ville,
            "domaines": domaines,
            "seuil": seuil,
        }

    def on_input_submitted(self, event: Input.Submitted) -> None:
        values = self._collect()
        if values is not None:
            self.dismiss(values)

    def action_back(self) -> None:
        self.dismiss(BACK)


class PromptPreviewScreen(ModalScreen[bool | _Back]):
    """Étape 4 — Prompt de scoring. Non sautable : impossible de continuer sans
    le valider, mais on peut toujours remonter à l'étape « sujet »."""

    BINDINGS = [
        Binding("enter", "go", "Continuer"),
        Binding("escape", "back", "Retour"),
    ]

    def __init__(self, prompt_text: str) -> None:
        super().__init__()
        self._prompt_text = prompt_text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="wizard-box-wide"):
            yield Label(
                "[b]Étape obligatoire[/b] — aperçu du prompt de scoring, paramétré avec tes réponses.\n"
                "Point de départ, affinable ensuite depuis l'onglet Réglages (touche 6) de la TUI.\n"
                "entrée : continuer   ·   échap : retour à l'étape sujet"
            )
            yield Static(self._prompt_text)

    def action_go(self) -> None:
        self.dismiss(True)

    def action_back(self) -> None:
        self.dismiss(BACK)


PROFILE_DEFAULTS = {
    "ecole": "Mon École d'Ingénieurs",
    "niveau": "Ingénieur 5ème année",
    "specialite": "Informatique",
    "diplome_prevu": "2027",
    "langages_maitrise": ["Python", "Java", "C"],
    "langages_pratiques": ["JavaScript", "TypeScript", "SQL"],
    "frameworks": [],
    "outils": ["Git", "Docker", "Linux"],
    "langues": ["Français:Natif", "Anglais:Courant (B2/C1)"],
}


class ProfileScreen(ModalScreen[dict | _Back]):
    """Étape 5 — Profil du candidat."""

    BINDINGS = [Binding("escape", "back", "Retour")]

    def __init__(self, initial: dict | None = None) -> None:
        super().__init__()
        self._initial = {**PROFILE_DEFAULTS, **(initial or {})}

    def compose(self) -> ComposeResult:
        v = self._initial
        with VerticalScroll(id="wizard-box"):
            yield Label(
                "Profil du candidat — entrée pour valider.\n"
                "échap : retour à l'étape précédente."
            )
            yield Label("École :")
            yield Input(value=v["ecole"], id="p-ecole")
            yield Label("Niveau (ex: Ingénieur 5ème année) :")
            yield Input(value=v["niveau"], id="p-niveau")
            yield Label("Spécialité :")
            yield Input(value=v["specialite"], id="p-specialite")
            yield Label("Diplôme prévu (année) :")
            yield Input(value=v["diplome_prevu"], id="p-diplome")
            yield Label("Langages maîtrisés (séparés par des virgules) :")
            yield Input(value=", ".join(v["langages_maitrise"]), id="p-langages-m")
            yield Label("Langages pratiqués (séparés par des virgules) :")
            yield Input(value=", ".join(v["langages_pratiques"]), id="p-langages-p")
            yield Label("Frameworks (séparés par des virgules) :")
            yield Input(value=", ".join(v["frameworks"]), id="p-frameworks")
            yield Label("Outils (séparés par des virgules) :")
            yield Input(value=", ".join(v["outils"]), id="p-outils")
            yield Label("Langues (format \"Langue:Niveau\", séparées par des virgules) :")
            yield Input(value=", ".join(v["langues"]), id="p-langues")

    def on_mount(self) -> None:
        self.query_one("#p-ecole", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss({
            "ecole": self.query_one("#p-ecole", Input).value.strip(),
            "niveau": self.query_one("#p-niveau", Input).value.strip(),
            "specialite": self.query_one("#p-specialite", Input).value.strip(),
            "diplome_prevu": self.query_one("#p-diplome", Input).value.strip(),
            "langages_maitrise": parse_csv(self.query_one("#p-langages-m", Input).value),
            "langages_pratiques": parse_csv(self.query_one("#p-langages-p", Input).value),
            "frameworks": parse_csv(self.query_one("#p-frameworks", Input).value),
            "outils": parse_csv(self.query_one("#p-outils", Input).value),
            "langues": parse_csv(self.query_one("#p-langues", Input).value),
        })

    def action_back(self) -> None:
        self.dismiss(BACK)


class ConfirmWriteScreen(ModalScreen[bool | _Back]):
    """Étape 6 — Récapitulatif PRÉ-écriture : rien n'est encore écrit sur le
    disque à ce stade. Confirmer déclenche la phase d'écriture (avec, le cas
    échéant, une confirmation d'écrasement par fichier déjà présent)."""

    BINDINGS = [
        Binding("enter", "go", "Écrire"),
        Binding("escape", "back", "Retour"),
    ]

    def __init__(self, plan: list[tuple[str, str]]) -> None:
        super().__init__()
        self._plan = plan

    def compose(self) -> ComposeResult:
        lines = ["[b]Récapitulatif[/b]\n", "Sur confirmation, les fichiers suivants seront écrits"]
        lines.append("(une confirmation d'écrasement sera demandée pour ceux déjà présents) :\n")
        for label, path in self._plan:
            marker = " [dim](existe déjà)[/dim]" if os.path.exists(path) else ""
            lines.append(f"  · {label} — {path}{marker}")
        lines.append(
            "\nReste optionnel, à personnaliser plus tard (onglet Réglages, touche 6) :"
            "\n  - cv_master/cv_data.yaml (CV PDF)"
            "\n  - config/templates/*.html (charte graphique CV/lettre)"
            "\n  - Adzuna (ADZUNA_APP_ID/KEY dans .env)"
            "\n  - notifications email (SMTP dans .env)"
        )
        lines.append("\n[dim]entrée : écrire ces fichiers   ·   échap : retour à l'étape profil[/dim]")
        with VerticalScroll(id="wizard-box"):
            yield Static("\n".join(lines))

    def action_go(self) -> None:
        self.dismiss(True)

    def action_back(self) -> None:
        self.dismiss(BACK)


class SummaryScreen(ModalScreen[None]):
    """Écran final, affiché après l'écriture effective des fichiers."""

    BINDINGS = [Binding("enter", "go", "Terminer"), Binding("escape", "go", "Terminer")]

    def __init__(self, written: list[tuple[str, str]], skipped: list[str]) -> None:
        super().__init__()
        self._written = written
        self._skipped = skipped

    def compose(self) -> ComposeResult:
        lines = ["[b]Terminé[/b]\n", "Configuré :"]
        for label, path in self._written:
            lines.append(f"  ✓ {label} — {path}")
        if self._skipped:
            lines.append("\nNon écrit (fichier existant conservé) :")
            for label in self._skipped:
                lines.append(f"  · {label}")
        lines.append("\nProchaine commande :\n  [b]jobsearch --scrape[/b]\n(ou jobsearch --tui puis 'S')")
        lines.append("\n[dim]entrée pour terminer[/dim]")
        with VerticalScroll(id="wizard-box"):
            yield Static("\n".join(lines))

    def action_go(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class SetupWizard(App):
    TITLE = "Installation de JobSearch"

    CSS = """
    WelcomeScreen, OverwriteConfirmScreen, InfoScreen, ApiKeyScreen, SearchScreen,
    PromptPreviewScreen, ProfileScreen, ConfirmWriteScreen, SummaryScreen {
        align: center middle;
    }
    #wizard-box {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 90;
        max-width: 95%;
        height: auto;
        max-height: 90%;
    }
    #wizard-box-wide {
        background: $surface;
        border: round $accent;
        padding: 1 2;
        width: 100;
        max-width: 98%;
        height: auto;
        max-height: 92%;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._written: list[tuple[str, str]] = []
        self._skipped: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("")
        yield Footer()

    def on_mount(self) -> None:
        self.run_wizard()

    async def _write_with_confirm(self, path: str, content: str, label: str) -> bool:
        if os.path.exists(path):
            ok = await self.push_screen_wait(
                OverwriteConfirmScreen(
                    f"{label} existe déjà :\n{path}\n\nÉcraser avec la nouvelle configuration ?"
                )
            )
            if not ok:
                self._skipped.append(label)
                return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self._written.append((label, path))
        return True

    def _build_write_items(self, answers: dict) -> list[tuple[str, str, str]]:
        """(label, path, contenu) — dérivé des réponses collectées. Recalculé
        au moment de l'écriture (étape 6), jamais avant."""
        items: list[tuple[str, str, str]] = []
        key = answers.get("key", "")
        if key:
            items.append(("Clé API (.env)", PATHS.env_path, f"ANTHROPIC_API_KEY={key}\n"))
        search_values = answers["search_values"]
        items.append((
            "Sujet de recherche (search.yaml)",
            os.path.join(PATHS.config_dir, "search.yaml"),
            render_search_yaml(search_values),
        ))
        items.append((
            "Prompt de scoring (filter.txt)",
            os.path.join(PATHS.user_prompts_dir, "filter.txt"),
            render_filter_prompt(search_values),
        ))
        items.append((
            "Profil (profile.yaml)",
            os.path.join(PATHS.config_dir, "profile.yaml"),
            render_profile_yaml(answers["profile_values"]),
        ))
        return items

    @work
    async def run_wizard(self) -> None:
        answers: dict = {}
        step = 0
        while True:
            if step == 0:
                proceed = await self.push_screen_wait(WelcomeScreen())
                if not proceed:
                    self.exit()
                    return
                step = 1

            elif step == 1:
                result = await self.push_screen_wait(ApiKeyScreen(initial_key=answers.get("key", "")))
                if result is BACK:
                    step = 0
                    continue
                answers["key"] = result["key"]
                if answers["key"]:
                    do_validate = await self.push_screen_wait(
                        OverwriteConfirmScreen(
                            "Valider la clé via un appel API minimal (métadonnées, non facturé) ?"
                        )
                    )
                    if do_validate:
                        ok, msg = await asyncio.to_thread(check_api_key, answers["key"])
                        await self.push_screen_wait(InfoScreen(msg))
                step = 2

            elif step == 2:
                result = await self.push_screen_wait(SearchScreen(initial=answers.get("search_values")))
                if result is BACK:
                    step = 1
                    continue
                answers["search_values"] = result
                step = 3

            elif step == 3:
                prompt_text = render_filter_prompt(answers["search_values"])
                confirmed = await self.push_screen_wait(PromptPreviewScreen(prompt_text))
                if confirmed is BACK:
                    step = 2
                    continue
                step = 4

            elif step == 4:
                result = await self.push_screen_wait(ProfileScreen(initial=answers.get("profile_values")))
                if result is BACK:
                    step = 3
                    continue
                answers["profile_values"] = result
                step = 5

            elif step == 5:
                plan = [(label, path) for label, path, _content in self._build_write_items(answers)]
                confirmed = await self.push_screen_wait(ConfirmWriteScreen(plan))
                if confirmed is BACK:
                    step = 4
                    continue
                step = 6

            elif step == 6:
                for label, path, content in self._build_write_items(answers):
                    await self._write_with_confirm(path, content, label)
                await self.push_screen_wait(SummaryScreen(self._written, self._skipped))
                self.exit()
                return


def run_setup() -> None:
    SetupWizard().run()


if __name__ == "__main__":
    run_setup()
