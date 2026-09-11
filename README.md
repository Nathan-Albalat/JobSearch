# JobSearch

An automated pipeline that scrapes job/internship offers from RSS feeds and job board APIs, scores each one with Claude Haiku against your search criteria, generates a Markdown report, and — for the offers you pick — drafts a tailored cover letter and adapted CV PDF with Claude Sonnet.

**JobSearch is general-purpose**, not tied to tech: the setup wizard ships 7 sector presets (software, healthcare, law, sales, marketing, engineering, education) plus free-form input for anything else. Use it for an internship, a permanent job, or any other type of contract, in any country.

---

## Installation

```bash
pipx install git+https://github.com/Nathan-Albalat/JobSearch
jobsearch --setup
jobsearch --tui
```

That's it — `jobsearch --setup` walks you through an interactive wizard (API key, search subject, scoring prompt, candidate profile), and `jobsearch --tui` opens the interactive interface.

This works on any Python 3.11+ (including 3.12/3.13/3.14) and covers every source except LinkedIn/Indeed. A couple of things worth knowing:

- **LinkedIn / Indeed** go through the optional `[jobspy]` extra, which requires Python ≤ 3.11 (`python-jobspy` pins an old numpy with no wheels beyond 3.11):
  ```bash
  pipx install "jobsearch[jobspy] @ git+https://github.com/Nathan-Albalat/JobSearch" --python python3.11
  ```
  Without it, these two sources are simply skipped with an explanatory message — not an error, the rest of the scrape runs normally.
- **WeasyPrint** (PDF rendering) needs Pango/Cairo installed at the system level: `brew install pango` on macOS. Without it, `--generate` / `--letter-pdf` fail at the PDF step, though everything else works.

For local development instead of `pipx`, use `pip install -e .` (or `pip install -e '.[jobspy]'`) inside this repo's venv.

### Updating

```bash
pipx upgrade jobsearch
```

Your config and data are preserved: they live under `~/.jobsearch/`, outside the package. A prompt or template you customised keeps your version; the ones you never touched pick up improvements automatically. The `[jobspy]` extra is preserved too — pipx remembers the original install spec, so there's nothing to re-specify.

### About the setup wizard

`jobsearch --setup` is a standalone Textual app in 7 steps: an overview of the tool, your Anthropic API key (optional at this stage), your search subject (contract type, minimum duration, priority countries, domains, score threshold), the pre-filter (pick a pre-filled keyword set for your sector, or type your own), the scoring prompt (pre-filled from your answers so your first scrape isn't scored against someone else's search), your candidate profile, and a recap of what's configured. It never overwrites an existing file without asking, and is safe to re-run. `sources.yaml` (the actual scraping sources — search queries, RSS feeds) is generated too, from that same sector and country choice — no extra question asked, no source left tied to the author's original search (a software internship in the Netherlands).

**Strongly recommended after setup:** personalize the scoring prompt and pre-filter keywords further from the Settings tab of the TUI (key `6`) with a few real offer examples from your own search — it meaningfully improves ranking accuracy.

**Data location:** your config and data live under `JOBSEARCH_HOME`, which defaults to `~/.jobsearch/` (override with the `JOBSEARCH_HOME` environment variable). `jobsearch` works the same from any directory. Run `python -m jobsearch.config` to see the effective paths, or `python -m jobsearch.config --check-prompts` / `--check-wizard-defaults` for offline sanity checks.

**Upgrading from an older, repo-local install?** Run `jobsearch --migrate` for a dry run, then `jobsearch --migrate --apply` to copy your config and data to `JOBSEARCH_HOME` (never overwrites, never deletes the originals).

---

## The TUI

```bash
jobsearch --tui
```

The TUI is a **view** on the pipeline's files, not a database of its own: reports, `data/offers/*.json` and `data/applications.json` stay readable and editable without it, and it re-reads current state on startup and on `r`. The only thing it persists is the Generation tab's queue (`data/gen_queue.json`), so you can resume anytime — scrape Monday, review Wednesday.

### The 6 tabs

| Tab | Role |
|---|---|
| **1 · Offers** | Table of scored offers, sortable, with a detail panel. Check offers (`space`) to queue them for generation — checked rows highlighted in green, offers from the latest scrape session in dark blue. The `✓` column cascades through 5 states: `▶` checked > `⛔` marked invalid (dead/closed/wrong contract type — press `i` to mark, pick a reason) > `⧉` confirmed manual duplicate > `✓` already applied > `?` suspected duplicate (same company, not yet resolved — press `m` to confirm). |
| **2 · Generation** | The generation queue, persisted in `data/gen_queue.json`. Check rows to restrict `g`/`L` to a subset — otherwise all pending offers are processed. Runs letter + CV generation (or letter only) in the background. `N` attaches a per-offer note (`gen_notes`) injected into the letter and CV prompts; `M` records an offer straight into tracking with no letter/CV at all — no API call, no folder — for offers that don't need one (online form, generic CV). `o` opens the offer in the browser, `O` opens the generated application folder. |
| **3 · Applications** | Browse `output/candidatures/`, preview the letter/CV, regenerate the PDFs after manual edits. |
| **4 · Tracking** | Statuses and notes from `applications.json` (sent / answered / interview / accepted / rejected / cancelled, each colour-coded). The right panel shows the timeline of the selected application, rebuilt from its history — dates inferred from older data are suffixed with `~`. |
| **5 · Stats** | Response metrics (acceptance/response rate, average delay), status breakdown, offer locations, an applications-over-time histogram, and a status-flow tree. Press `f` to export the flow as a self-contained Sankey HTML diagram. |
| **6 · Settings** | Every editable config file (`.env`, `search.yaml`, `profile.yaml`, `sources.yaml`, the 3 prompts, the 2 HTML templates, `cv_data.yaml`) with its state (customized / package default / missing). `enter` opens it in your editor; a banner reminds you to personalize the scoring prompt and pre-filter. `o` opens a modal to enable/disable individual scraping sources (`space` to toggle, `escape` to close) — written immediately to `sources.yaml`, independent from the `fr` group toggled with `--with`/`--without`. |

### Cover letter editing workflow

The main use of the Applications tab: press `e` to open `lettre.md` in your editor, edit and save, come back and press `p` to regenerate the PDF. The "PDF" column shows **"⚠ needs regeneration"** whenever the `.md` is newer than the PDF.

### Keyboard shortcuts (also via `?` in the TUI)

| Context | Key | Action |
|---|---|---|
| Global | `1`-`6` | Switch tab |
| Global | `r` | Reload data from disk |
| Global | `S` / `s` | Run scrape + filter + report |
| Global | `Ctrl+S` | Same, also including FR-only sources (group `fr`) |
| Global | `c` | Open the Claude Console (reload API credits) |
| Global | `?` | Help |
| Global | `q` | Quit |
| Offers | `space` | Check/uncheck for generation |
| Offers | `o` | Open in browser |
| Offers | `+`/`-` | Adjust score threshold |
| Offers | `h` | Hide/show already-applied offers, marked duplicates and invalid offers |
| Offers | `d` / `D` | Cycle / pick displayed period |
| Offers | `g` | Go to Generation tab |
| Offers | `m` | Mark/unmark as duplicate of an existing application |
| Offers | `i` | Mark/unmark as invalid (pick a reason: closed/cancelled/wrong contract type/other; also drops the offer from the generation queue; refused if the offer already has an application) |
| Generation | `space` | Check/uncheck a queue row |
| Generation | `g` | Generate letter + CV |
| Generation | `L` | Generate letter only |
| Generation | `A` | Add a manual offer (HTML file or URL) to the queue |
| Generation | `x` | Remove from the queue |
| Generation | `N` | Edit the generation note for the offer under the cursor (`gen_notes`, injected into the letter/CV prompts on the next `g`/`L`) |
| Generation | `M` | Record in tracking WITHOUT generating (no API call, no folder — for offers that need neither letter nor CV) |
| Generation | `o` | Open offer in browser |
| Generation | `O` | Open generated application folder |
| Applications | `enter` | Toggle letter ↔ CV preview |
| Applications | `e` | Open `lettre.md` in editor |
| Applications | `p` | Regenerate `lettre.pdf` |
| Applications | `u` | Regenerate `cv_adapte.pdf` |
| Applications | `o` / `O` | Open folder / open offer in browser |
| Tracking | `s`/`a`/`i`/`j`/`v`/`z` | Status → sent / answered / interview / rejected / accepted / cancelled |
| Tracking | `n` | Edit note |
| Tracking | `o` | Open offer in browser |
| Stats | `f` | Export the status-flow Sankey diagram |
| Settings | `enter` | Open the config file under the cursor |
| Settings | `o` | Manage scraping sources (enable/disable individually, `space` to toggle) |

### Duplicate offers

Cross-source deduplication can't catch every repost. The pipeline follows "the machine suggests, the user confirms": offers from a company you already applied to are flagged `?`, and `m` opens a picker to confirm the match in one keystroke.

### Invalid offers

Marking a duplicate requires a target application to point at. Some offers just die on their own — closed, cancelled, or turning out to be the wrong contract type — with nothing to link them to. `i` (Offers tab) marks these as invalid instead: pick a reason from the modal, and the offer drops out of the "to apply" bucket of the report and out of the generation queue, staying visible with a `⛔` marker (hide it too with `h`). Refused for offers that already have an application — use the "cancelled" status (`z`, Tracking tab) for those instead.

---

## The CLI

```bash
jobsearch --setup                          # interactive first-install wizard
jobsearch --scrape                         # collect + filter + report
jobsearch --scrape --threshold 7           # custom relevance threshold
jobsearch --scrape --with fr               # also include FR-only sources (HelloWork, WTTJ FR)
jobsearch --scrape --without fr            # disable a group enabled by default
jobsearch --generate <offer_id>            # generate letter + adapted CV for an offer
jobsearch --generate <offer_id> --letter-only  # letter only (1 Sonnet call instead of 2)
jobsearch --generate <offer_id> --notes "..."  # one-off instruction injected into the letter and CV prompts
jobsearch --record <offer_id>              # record in tracking without generating anything (no API call)
jobsearch --tui                            # interactive interface
jobsearch --applications                   # tracking table
jobsearch --mark <offer_id> <status>       # update an application's status
jobsearch --mark-duplicate <offer_id> <target_id>  # mark as duplicate of an existing application
jobsearch --unmark-duplicate <offer_id>
jobsearch --mark-invalid <offer_id> --reason closed|cancelled|contract|other  # mark a dead/off-topic offer
jobsearch --unmark-invalid <offer_id>
jobsearch --migrate [--apply]              # copy config/data from a repo-local install to JOBSEARCH_HOME
jobsearch --from-file <path.html> [--url-source URL] [--company NAME] [--title TITLE]
jobsearch --from-url <url>
jobsearch --letter-pdf <letter.md> --company "Name" [--title ...] [--location ...] [--dept ...] [--output out.pdf]
```

Full option list and examples: `jobsearch --help`.

### Modules in isolation (debugging)

```bash
python -m jobsearch.scraper     # collect only
python -m jobsearch.filter      # filter already-collected offers
python -m jobsearch.report      # generate report from already-filtered offers
python -m jobsearch.generator <offer_id>
```

### Manual application (WTTJ, LinkedIn, etc.)

For offers on sites that can't be scraped automatically, save the page and generate the application directly:

```bash
# Save As "Complete Web Page" from your browser, then:
jobsearch --from-file ~/Downloads/offer.html --url-source "https://..." --company "Company Name"

# Or, for JS-free sites:
jobsearch --from-url https://example.com/jobs/offer

# Override extraction if it's wrong:
jobsearch --from-file offer.html --title "Software Engineer Intern" --company "Criteo"
```

---

## Additional information

### The pipeline, step by step

```
Step 1 — Scrape       : collect offers from RSS feeds and APIs
Step 2 — Filter       : Claude Haiku scores each offer (0-10) against your criteria
Step 3 — Report       : Markdown report of the relevant offers

            ↓ You read the report, note the offers you're interested in

Step 4 — Application  : Claude Sonnet drafts a cover letter + adapted CV
```

### API cost

Filtering uses Claude Haiku through the Message Batches API by default (−50% on tokens). Generation (letter + CV) uses Claude Sonnet, one-shot per offer. Reload credits at [console.anthropic.com](https://console.anthropic.com) if needed — scraping itself works without any credit.

### Automation every 48 hours (macOS/Linux)

```bash
crontab -e
```

```
0 8 */2 * * jobsearch --scrape >> ~/.jobsearch/data/cron.log 2>&1
```

### Customization

- **Search subject** — `search.yaml` (contract type, duration, geography, domains, score threshold, pre-filter keywords). For a real change of subject (e.g. internship → permanent job), also clear out `data/offers/` so old offers aren't scored against a stale subject.
- **Candidate profile** — `profile.yaml` (education, skills, languages). Skills are free-form category/detail pairs, not tied to tech.
- **CV data** — `cv_data.yaml`, the single source of truth for both the master CV and the adapted CV; `cv_master.md` is auto-regenerated from it, never edit it by hand.
- **Prompts** — `config/prompts/{filter,cover_letter,cv_adapt}.txt`.
- **PDF templates** — `config/templates/{cv_en,cover_letter}.html`, using `{{MARKER}}` placeholders replaced before WeasyPrint renders the PDF. Fonts are bundled locally (offline rendering); a signature image at `files/letter/signature.png` is inserted automatically if present.
- **Sources** — `sources.yaml` is generated by the setup wizard from your sector and priority countries (search queries, RSS feeds, LinkedIn/Indeed/Adzuna/WTTJ/HelloWork geography — see `jobsearch/resources/source_presets.yaml`); re-running `jobsearch --setup` regenerates it. To pivot by hand instead: add RSS feeds, or edit the `jobspy`/`adzuna`/`wttj`/`hellowork` entries (country, search term/job title, contract types) in `sources.yaml`. Each source can also be individually turned on/off with `enabled: false` (defaults to active) — either directly in the file, or from the Settings tab's source manager (`6`, then `o`).

Any of these can be edited directly, or from the TUI's Settings tab (`6`).

### Email notifications (optional)

Add to `.env` to receive a summary email after each `--scrape`:

```
NOTIFY_EMAIL=your_email@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_account@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Gmail App Password
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| "ANTHROPIC_API_KEY missing" | Check `.env` contains `ANTHROPIC_API_KEY=sk-ant-...` |
| "Your credit balance is too low" | Reload at [console.anthropic.com](https://console.anthropic.com) — scraping still works without credits |
| "0 new offer(s) collected" | Normal if the pipeline ran recently (dedup); wait 48h or add sources |
| "No filtered offers found" at report time | Run `jobsearch --scrape` in full, the filter must run first |
| Few relevant offers in the report | Lower the threshold: `jobsearch --scrape --threshold 5` |
| `offer_id` not found for `--generate` | It's the JSON filename without `.json` in `data/offers/` |

### License

MIT — see [LICENSE](LICENSE).
