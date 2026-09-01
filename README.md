# User Guide — Internship Search Pipeline

## Prerequisites

- Python 3.11+
- An Anthropic API key (for filtering and generation phases)
- macOS / Linux (cron commands are for macOS/Linux)

---

## Installation

```bash
# Recommended: install as an isolated CLI tool
pipx install git+https://github.com/<you>/jobsearch

# Or, for local development (inside this repo's venv):
pip install -e .
```

WeasyPrint (PDF rendering) requires a system dependency: Pango/Cairo — `brew install pango` on macOS. This is the main installation pitfall; without it, `--letter-pdf` / `--generate` will fail at the PDF step even though everything else works.

Either way, you get a global `jobsearch` command. First launch (`jobsearch --scrape`, `--tui`, …) detects that `JOBSEARCH_HOME` (`~/.jobsearch/` by default) is missing its configuration and offers to run `setup.sh`, which copies `.example` files from the package to their final names there and tells you which ones to edit next.

> **Note:** `setup.sh` never overwrites an existing file.

**Runs from any directory.** Two separate roots: the code and its default resources (prompts, HTML templates, fonts) live in the package (`jobsearch/resources/`, accessed via `importlib.resources` so they're found however the package was installed); your personal config (`search.yaml`, `profile.yaml`, `sources.yaml`, `cv_data.yaml`, `.env`) and your data (`data/`, `output/`) live under `JOBSEARCH_HOME`, which defaults to `~/.jobsearch/` — `jobsearch --applications` works the same whether you run it from the repo or from anywhere else. Override with the `JOBSEARCH_HOME` environment variable if needed. Run `python -m jobsearch.config` to display the effective paths.

**Prompts and HTML templates are editable content, not fixed resources.** The 3 scoring/generation prompts (`config/prompts/*.txt`) and the 2 HTML templates (`config/templates/cover_letter.html`, `cv_en.html`) are resolved with priority given to `JOBSEARCH_HOME/config/`, falling back automatically to the package defaults if you haven't customized them — no action required on an existing install. To customize them, copy the defaults into `JOBSEARCH_HOME/config/prompts/` / `.../templates/` (or run `jobsearch --migrate --apply`, which copies them there without ever overwriting an existing file) and edit the copies. Only the fonts always stay in the package.

**Upgrading from an older version where data lived inside the repo?** Run `jobsearch --migrate` for a dry run (prints what would be copied, writes nothing), then `jobsearch --migrate --apply` to actually copy your config and data to `JOBSEARCH_HOME`. This only ever copies — it never moves or deletes the originals in the repository; the command verifies file counts and checksums after copying and tells you where the originals are so you can remove them yourself once you're satisfied.

---

## Complete Workflow

The pipeline unfolds in 3 steps:

```
Step 1 — Scrape    : Collects offers from RSS feeds
Step 2 — Filter    : Claude scores each offer (0-10) based on your profile
Step 3 — Report    : Generates a Markdown report with relevant offers

            ↓ You read the report, check the offers you're interested in

Step 4 — Application : Claude generates cover letter + adapted CV
```

---

## Main Commands

| Command | Description |
|---|---|
| `jobsearch --scrape` | Run steps 1-2-3 (collect + filter + report) |
| `jobsearch --scrape --threshold 7` | Same with relevance threshold of 7/10 |
| `jobsearch --scrape --with fr` | Also include FR-only sources (group `fr`, e.g. HelloWork, WTTJ FR), disabled by default |
| `jobsearch --scrape --without fr` | Exclude a group even if enabled by default in `search.yaml` (`sources.enabled_groups`) |
| `jobsearch --generate <offer_id>` | Generate application for a specific offer |
| `jobsearch --mark-duplicate <offer_id> <target_id>` | Mark `<offer_id>` as a manual duplicate of `<target_id>` (an offer you already applied to) |
| `jobsearch --unmark-duplicate <offer_id>` | Undo a duplicate marking |
| `jobsearch --migrate` | Dry run: shows what would be copied from the repo to `JOBSEARCH_HOME`, writes nothing |
| `jobsearch --migrate --apply` | Actually copies personal config and data to `JOBSEARCH_HOME` (never overwrites, never deletes the originals) |

### Modules in Isolation (for debugging)

| Command | Description |
|---|---|
| `python -m jobsearch.scraper` | Collect only (without filter or report) |
| `python -m jobsearch.filter` | Filter already-collected offers |
| `python -m jobsearch.report` | Generate report from already-filtered offers |
| `python -m jobsearch.generator <offer_id>` | Generate application for an offer |

---

## Interactive Interface (TUI)

Instead of chaining commands, you can control everything from a terminal interface:

```bash
jobsearch --tui
```

**Important principle:** the TUI is just a **view** on the pipeline's files. It creates almost no hidden state: reports, `data/offers/*.json` and `data/applications.json` remain readable and editable without it. So you can resume work anytime (scrape Monday, review Wednesday) — the TUI re-reads current state on startup and with the `r` key. The one exception is the Generation tab's queue, persisted in `data/gen_queue.json`.

### The 6 Tabs

| Tab | Role |
|---|---|
| **1 · Offers** | Table of filtered offers, sortable by score, with detail panel. You check offers to add to the generation queue — checked rows are **highlighted in green**. Offers from the latest scrape session are **highlighted in dark blue**; the displayed period (all / latest session / last 7 days / last 30 days) can be filtered with `d`/`D`. The `✓` column is a 4-state cascade: `▶` checked > `⧉` confirmed manual duplicate > `✓` already applied > `?` suspected duplicate (same company already applied to, not yet resolved) — press `m` to confirm/undo via a picker. |
| **2 · Generation** | The generation queue, persisted across TUI restarts in `data/gen_queue.json`. Check rows with `space` to restrict `g`/`L` to that subset — with nothing checked, `g`/`L` process all pending offers. `x` removes rows from the queue. Launches letter + CV generation (or letter only) as a background task. |
| **3 · Applications** | List of `output/candidatures/` directories, letter/CV preview, **PDF regeneration after manual editing**. |
| **4 · Tracking** | Statuses and notes of applications (`applications.json`). Each status has its own colour: **sent** in yellow, **answered** in amber, **interview** in purple, **accepted** in green, **rejected** in red, **cancelled** in dark grey with dimmed text. The right-hand panel shows the individual timeline of the application under the cursor, rebuilt from its `history` (status → datetime log), with the day gap between each event — dates inferred from the old point-in-time fields (before `history` existed) are suffixed with `~`. |
| **5 · Stats** | Response metrics (acceptance rate, response rate, average response time), status breakdown, internship locations, and an applications-over-time histogram. Cancelled applications are excluded from the rates and average response time, counted separately in a "Cancelled" category. Also: a tree view of the application journey (status flow) and a monthly breakdown of statuses reached. Press `f` to export the corresponding Sankey diagram as a self-contained HTML file (`output/stats/flux.html`), opened in your browser. |
| **6 · Settings** | Table listing every editable config file — `.env`, `search.yaml`, `profile.yaml`, `sources.yaml`, the 3 prompts, the 2 HTML templates, `cv_data.yaml` — with its role, **state** (customized / package default / missing) and last-modified date. Press `enter` on a row to open it in your editor: for a prompt/template still at the package default, the default is copied to your user config **before** opening it, so you never edit the files shipped with the package. Uses `$VISUAL`/`$EDITOR` if set (suspending the TUI for a terminal editor), otherwise falls back to `open`/`xdg-open`/`start` depending on your OS. Press `r` afterwards to reload — this also clears the cached `search.yaml` values, so a changed scoring threshold takes effect immediately. |

### Duplicate Offers

Automated deduplication can't catch every cross-source repost (e.g. the same internship reappearing under a slightly different title on another job board). Rather than guess, the pipeline follows the principle "**the machine suggests, the user confirms**": offers from a company you already applied to are flagged with `?` in the Offers tab, and pressing `m` opens a picker of your applications to that company (or others) to confirm the match in one keystroke — nothing is ever auto-marked.

### Cover Letter Editing Workflow

This is the main use of the Applications tab:

1. Go to tab **3 · Applications**, select the application.
2. Press `e` → `lettre.md` opens in your default editor.
3. Edit the text, save.
4. Return to the TUI, press `p` → the PDF is regenerated from the `.md`.

The "PDF" column displays **"⚠ needs regeneration"** when the `.md` is newer than the PDF, to spot at a glance what's left to do.

### Keyboard Shortcuts (also accessible via `?` in the TUI)

| Context | Key | Action |
|---|---|---|
| Global | `1`/`2`/`3`/`4`/`5`/`6` | Switch tab (Offers / Generation / Applications / Tracking / Stats / Settings) |
| Global | `r` | Reload data from disk |
| Global | `S` / `s` | Run scrape + filter + report (spinner + current step + timer in the status bar). `s` only triggers the scrape outside the Tracking tab, where it means "sent"; `s` is an alias of `S` |
| Global | `Ctrl+S` | Same, also including FR-only sources (group `fr`, e.g. HelloWork, WTTJ FR) — equivalent to `jobsearch --scrape --with fr` |
| Global | `c` | Open Claude Console (reload API credits) |
| Global | `?` | Show help (list of shortcuts) |
| Global | `q` | Quit |
| Offers | `space` | Check/uncheck offer for generation |
| Offers | `o` | Open offer in browser |
| Offers | `+`/`-` | Adjust displayed score threshold |
| Offers | `h` | Hide/show already-applied offers (and marked duplicates) |
| Offers | `d` | Cycle displayed period (all · latest session · 7 days · 30 days) |
| Offers | `D` | Pick a specific scrape session |
| Offers | `g` | Go to Generation tab |
| Offers | `m` | Mark/unmark the offer as a duplicate of an existing application (picker; the machine suggests via company name, the user confirms) |
| Generation | `space` | Check/uncheck an offer in the queue (optional filter for `g`/`L`) |
| Generation | `g` | Generate letter + CV (checked rows, or all pending offers) |
| Generation | `L` | Generate **letter only** (no CV — 1 Sonnet call instead of 2) |
| Generation | `x` | Remove from the queue (checked rows, or the row under the cursor) |
| Generation | `o` | Open application folder |
| Applications | `enter` | Toggle letter ↔ CV preview |
| Applications | `e` | Open `lettre.md` in editor |
| Applications | `p` | Regenerate `lettre.pdf` from `lettre.md` |
| Applications | `u` | Regenerate `cv_adapte.pdf` from `cv_adapte.html` |
| Applications | `o` | Open application folder |
| Tracking | `s`/`a`/`i`/`j`/`v`/`z` | Status → sent / answered / interview / rejected / accepted / cancelled |
| Tracking | `n` | Edit note |
| Tracking | `o` | Open offer in browser |
| Stats | `f` | Export the status-flow Sankey diagram (HTML, opened in browser) |
| Settings | `enter` | Open the config file under the cursor in your editor (`$VISUAL`/`$EDITOR`, else `open`/`xdg-open`/`start`) |

---

## Example of a Typical Session

```bash
# Run the complete pipeline
jobsearch --scrape

# → The report is generated in data/rapport_YYYY-MM-DD.md
# → Read the report, note the ID of interesting offers

# Generate application for an offer
jobsearch --generate 0151e52f

# → Files are in output/candidatures/<company-name>_<id>/
#   - lettre.md     : cover letter ready to review/adapt
#   - cv_adapte.md  : CV reorganized by offer keywords
```

---

## Automation Every 48 Hours (macOS/Linux)

To run scraping and filtering automatically every 48 hours:

```bash
# Open the cron editor
crontab -e
```

Add this line (adjust the path if needed):

```
0 8 */2 * * cd "path/to/Script" && source venv/bin/activate (only if you use a venv) && jobsearch --scrape >> data/cron.log 2>&1
```

What it does: every 2 days at 8:00 AM, the pipeline runs and results are appended to `data/cron.log`.

---

## Customization

### Change Your Search Subject
Edit `config/search.yaml` (see the commented `config/search.example.yaml`):
- Contract type (internship, permanent, etc.), minimum duration, availability date
- Geographic preferences, domains, score threshold, scoring bonus/malus
- Pre-filter keywords (the fastest lever to pivot to a different contract type — e.g. swap `intern`/`internship` for `permanent`/`full-time`)
- Claude models/tokens and default scraping parameters

To fully change subject (e.g. from internship search to full-time job search), also start from an empty `data/` folder — the packaged `filter.txt` scoring prompt now ships with generic examples that adapt to any subject via placeholders, so there is nothing to rewrite by hand to get started. For a sharper scoring signal, replace those examples with 8-14 real offers representative of your search in `config/prompts/filter.txt` (takes priority over the package default). See "Changer de sujet de recherche" in `CLAUDE.md` for the full process.

### Change Your Candidate Profile
Edit `config/profile.yaml` — this only holds your identity (education, technical skills, languages), independent of the search subject.

### Add RSS Sources
Edit `config/sources.yaml` — add a block:
```yaml
- name: "Descriptive Name"
  url: "https://link-to-feed.rss"
  type: rss
  country: "NL"  # ISO country code
```

### Welcome to the Jungle (WTTJ)
Type `wttj` — no API key needed (public v3 API). NL entries are active by default, FR entries live in the `fr` group (`--with fr`). Pivoting to another country/query is a config-only edit:
```yaml
- name: "WTTJ - Intern NL"
  type: wttj
  country: "NL"
  params:
    job_title: "intern"
    locations:
      country: "Netherlands"
      country_code: "NL"
      label: "Pays-Bas"
      latitude: 52.25
      longitude: 5.75
    contract_types: [internship, apprenticeship]
    pages: 3
```

### Update Your CV
Edit `cv_master/cv_data.yaml` — this is **the** source of truth (structured data: experiences, projects, skills, personal info). This file feeds both the "master" CV and the adapted CV generated by Claude. The file `cv_master/cv_master.md` is **auto-regenerated** from the YAML (via `python -m jobsearch.cv_sync`, and with each application generation) — don't edit it by hand.

### Adjust Prompts
- `config/prompts/filter.txt` — modify scoring criteria
- `config/prompts/cover_letter.txt` — adjust letter style/structure (to modify to change the date mentionned in the letter)
- `config/prompts/cv_adapt.txt` — change how CV is reorganized

### Customize PDF Templates (CV & Letter)

PDF appearance is controlled by two HTML templates in `config/templates/`:

| Template | Used For | Rendered By |
|---|---|---|
| `cv_en.html` | The CV (master and adapted) | `jobsearch/cv_pdf.py` |
| `cover_letter.html` | The cover letter | `jobsearch/letter_pdf.py` |

**How it works:** each template contains `{{NAME}}` markers that the code replaces with real values, then WeasyPrint converts the HTML to PDF. You can freely modify the CSS (colors, fonts, margins, layout) as long as you **preserve the `{{...}}` markers** where content must appear.

**Available Markers — CV (`cv_en.html`) :**
`{{FULL_NAME}}`, `{{HEADLINE}}`, `{{LOCATION}}`, `{{EMAIL}}`, `{{GITHUB}}`, `{{EXPERIENCE_ENTRIES}}`, `{{PROJECT_ENTRIES}}`, `{{EDU_SCHOOL}}`, `{{EDU_DATES}}`, `{{EDU_DETAIL}}`, `{{START_DATE}}`, `{{DURATION}}`, `{{MOBILITY}}`, `{{SKILLS_PROFICIENT}}`, `{{SKILLS_FAMILIAR}}`, `{{SKILLS_TOOLS}}`, `{{SKILLS_DOMAINS}}`, `{{LANG1_NAME}}`/`{{LANG1_LEVEL}}`, `{{LANG2_NAME}}`/`{{LANG2_LEVEL}}`, `{{OTHER_INFO}}`, `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}`.

**Available Markers — Letter (`cover_letter.html`) :**
`{{FULL_NAME}}`, `{{SENDER_LOCATION}}`, `{{EMAIL}}`, `{{COMPANY_NAME}}`, `{{COMPANY_LOCATION}}`, `{{COMPANY_DEPT_LINE}}`, `{{POSITION_TITLE}}`, `{{DATE}}`, `{{SALUTATION}}`, `{{BODY}}`, `{{CLOSING_WORD}}`, `{{SIGNATURE_IMG}}`, `{{ACCENT_COLOR}}`/`{{ACCENT_LIGHT}}`.

**Accent Color:** controlled by `personal.accent_enabled` in `cv_data.yaml` (green if `true`, gray if `false`). Hex values are injected into `{{ACCENT_COLOR}}` / `{{ACCENT_LIGHT}}` — to change the tint, modify them in `jobsearch/cv_pdf.py` / `jobsearch/letter_pdf.py`.

**Fonts:** `.ttf` files (DM Sans, DM Serif Display) are in `config/templates/fonts/` and loaded via `@font-face`. PDF generation therefore works offline. To change fonts, drop the `.ttf` in this folder and adjust the `@font-face` rules at the top of the template.

**Letter Signature:** the image `files/letter/signature.png` is inserted automatically via `{{SIGNATURE_IMG}}` (if the file exists). Its size is controlled with the CSS rule `.cl-signature` (property `height`) in `cover_letter.html`. ⚠️ Keep it small enough so the letter **fits on one page**.

**Test a template change** (without consuming API):
- Letter: `jobsearch --letter-pdf <a letter.md> --company "Test"` — or press `p` in the TUI on an existing application.
- Master CV: `python files/cv/generate_cv.py` → `files/cv/cv_nathan_albalat_en.pdf`.
- Adapted CV of an existing application: press `u` in the TUI.

---

## Manual Application (WTTJ, LinkedIn, etc.)

For offers on non-automatically-scrappable sites (WTTJ, LinkedIn, Jobteaser…), two modes allow you to generate letter + CV directly from your browser session.

### From an HTML File (Recommended for WTTJ)

WTTJ is blocked from direct scraping. Workflow:

1. Open the offer in your browser
2. **File → Save As → "Complete Web Page"** → save as `.html`
3. Run the command:

```bash
jobsearch --from-file ~/Downloads/offre_wttj.html \
               --url-source "https://www.welcometothejungle.com/fr/companies/..." \
               --company "Company Name"
```

The `--url-source` argument is optional but useful — it's included in the generated letter to reference the offer.

### From a Direct URL (Simple Sites)

For sites accessible without JavaScript (no CloudFront):

```bash
jobsearch --from-url https://www.somesite.com/jobs/stage-dev-python
```

### Overrides if Extraction Fails

If the title or company isn't extracted correctly automatically:

```bash
jobsearch --from-file offre.html \
               --title "Python Developer Intern" \
               --company "Criteo"
```

### What it Does

The program extracts the title, company, location and description from the page (via JSON-LD, `__NEXT_DATA__`, or HTML heuristics), then directly generates `lettre.md` + `cv_adapte.md` in `output/candidatures/` — without a filtering step.

---

## Email Notifications (Optional)

To receive a summary email each time new relevant offers are found:

1. Enable 2-step verification on your Google account
2. Generate an **App Password** at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Add these lines to your `.env`:

```
NOTIFY_EMAIL=your_email@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_account@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # App Password 16 characters
```

The pipeline automatically sends an email after each `--scrape` if `NOTIFY_EMAIL` is set.  
If the variable is empty or missing, no notification is sent.

---

## Troubleshooting

**"ANTHROPIC_API_KEY missing"**
→ Check that the `.env` file exists at the root and contains `ANTHROPIC_API_KEY=sk-ant-...`

**"Your credit balance is too low"**
→ Reload credits at [console.anthropic.com](https://console.anthropic.com). Scraping (step 1) works without credits.

**"0 new offer(s) collected"**
→ Normal if the pipeline has run recently — offers are deduplicated. Wait 48 hours or add new sources to `sources.yaml`.

**"No filtered offers found" at report time**
→ The filter must have run first. Run `jobsearch --scrape` in complete order.

**Report contains few relevant offers**
→ Lower the threshold: `jobsearch --scrape --threshold 5`

**offer_id not found for --generate**
→ The ID is the JSON filename without `.json` in `data/offers/` (e.g., `0151e52f`)

---

## License

MIT — see [LICENSE](LICENSE).
