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
- **WeasyPrint** (PDF rendering) needs Pango installed at the system level: `brew install pango` on macOS; `apt install libpango-1.0-0 libpangoft2-1.0-0` on Debian/Ubuntu (usually already present on a desktop); on Windows, install [MSYS2](https://www.msys2.org), run `pacman -S mingw-w64-x86_64-pango`, then set `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin`. Without it, `--generate` still writes the letter (`.md`) and the CV preview (`.html`) and records the application, but skips the PDFs with an explanatory message.
- **Windows**: use `jobsearch --setup` (`setup.sh` needs bash), and preferably Windows Terminal for the TUI. `--service` (auto-start) is macOS-only (launchd).

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
| **1 · Offers** | Table of scored offers, sortable, with a detail panel. Check offers (`space`) to queue them for generation — checked rows highlighted in green, offers from the latest scrape session in dark blue. The `✓` column cascades through 5 states: `▶` checked > `⛔` marked invalid (dead/closed/wrong contract type — press `X` to mark, pick a reason) > `⧉` confirmed manual duplicate > `✓` already applied > `?` suspected duplicate (same company, not yet resolved — press `m` to confirm). |
| **2 · Generation** | The generation queue, persisted in `data/gen_queue.json`. Check rows to restrict `g`/`L` to a subset — otherwise all pending offers are processed. Runs letter + CV generation (or letter only) in the background. `N` attaches a per-offer note (`gen_notes`) injected into the letter and CV prompts; `T` records an offer straight into tracking with no letter/CV at all — no API call, no folder — for offers that don't need one (online form, generic CV). `o` opens the offer in the browser, `O` opens the generated application folder. |
| **3 · Applications** | Browse `output/candidatures/`, preview the letter/CV, regenerate the PDFs after manual edits. `o` opens the offer in the browser, `O` opens the application folder. |
| **4 · Tracking** | Statuses and notes from `applications.json` (sent / answered / interview / accepted / rejected / cancelled, each colour-coded). The right panel shows the timeline of the selected application, rebuilt from its history — dates inferred from older data are suffixed with `~`. |
| **5 · Stats** | Response metrics (acceptance/response rate, average delay), status breakdown, offer locations as a country → cities tree, a GitHub-style daily activity heatmap, an applications-over-time histogram and applications per country over time (line chart), the latter two on a shared day/week/month scale (`t` cycles it), and a status-flow tree of the paths actually taken. Press `f` to export the flow as a self-contained Sankey HTML diagram. |
| **6 · Settings** | Every editable config file (`.env`, `search.yaml`, `profile.yaml`, `sources.yaml`, the 3 prompts, the 2 HTML templates, `cv_data.yaml`), plus two dedicated rows, "Scraping sources" and "Claude models", with each file's state (customized / package default / missing). `enter` on a file row opens it in your editor; a banner reminds you to personalize the scoring prompt and pre-filter. `enter` on the "Scraping sources" row opens a modal to enable/disable individual scraping sources (`space` to toggle, `escape` to close) — written immediately to `sources.yaml`, independent from the `fr` group toggled with `--with`/`--without`. `enter` on the "Claude models" row opens a modal listing the model of each task (filter, letter, CV): `enter` on a task offers "Latest Haiku/Sonnet/Opus (auto)" or a pinned model, `R` refreshes the model list, `escape` closes — written immediately to `search.yaml`. |

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
| Offers | `X` | Mark/unmark as invalid (pick a reason: closed/cancelled/wrong contract type/other; also drops the offer from the generation queue; refused if the offer already has an application) |
| Generation | `space` | Check/uncheck a queue row |
| Generation | `g` | Generate letter + CV |
| Generation | `L` | Generate letter only |
| Generation | `A` | Add a manual offer (HTML file or URL) to the queue |
| Generation | `x` | Remove from the queue |
| Generation | `N` | Edit the generation note for the offer under the cursor (`gen_notes`, injected into the letter/CV prompts on the next `g`/`L`) |
| Generation | `T` | Record in tracking WITHOUT generating (no API call, no folder — for offers that need neither letter nor CV) |
| Generation | `o` | Open offer in browser |
| Generation | `O` | Open generated application folder |
| Applications | `enter` | Toggle letter ↔ CV preview |
| Applications | `e` | Open `lettre.md` in editor |
| Applications | `p` | Regenerate `lettre.pdf` |
| Applications | `u` | Regenerate `cv_adapte.pdf` |
| Applications | `o` | Open offer in browser |
| Applications | `O` | Open application folder |
| Tracking | `s`/`a`/`i`/`j`/`v`/`z` | Status → sent / answered / interview / rejected / accepted / cancelled |
| Tracking | `n` | Edit note |
| Tracking | `o` | Open offer in browser |
| Stats | `f` | Export the status-flow Sankey diagram |
| Stats | `t` | Cycle the time scale of the histogram and per-country chart (day · week · month) |
| Settings | `enter` | On a file row: open it in your editor. On the "Scraping sources" row: open the source-management modal. On the "Claude models" row: open the per-task model modal (`R` refreshes the list) |

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
jobsearch --applications --json            # tracking as JSON (same shape as the API)
jobsearch --serve                          # HTTP API + mobile web view (extra `jobsearch[server]`)
jobsearch --mcp                            # MCP server for Claude Code / Claude app (extra `jobsearch[mcp]`)
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

## Remote access — phone and agents

Install the optional extra, then start the server:

```bash
pipx install 'jobsearch[server]' ...        # or: pip install -e '.[server]'
jobsearch --serve                           # 127.0.0.1:8000, read-only
```

It serves two things from the same process:

- **A mobile web view** — applications, their status and timeline, recent offers, and the status-flow diagram. Plain HTML, one column, no JavaScript: it works in Safari on a phone, where no terminal is available. Applications and offers can be sorted server-side (`?sort=`/`?dir=` — company, status, generated date for applications; score, scraped date, company for offers) and offers filtered by scrape session (`?session=YYYY-MM-DD`) — a `<select>` + submit button, still no JS. Status changes on an application page go through a dropdown + "Save" button rather than six separate buttons. The stats page shows everything the tracker already computes: status breakdown, coarse categories, response/acceptance rates and average delay, locations, and applications per month — on top of the Sankey flow diagram.
- **A JSON API** (`/api/applications`, `/api/offers`, `/api/stats`) so any agent can answer questions about your job search. `?q=` searches company, title **and the free-text notes** — deadlines, interview dates and recruiter names live there rather than in dedicated fields, so an agent can narrow to the relevant applications server-side instead of reading them all. Same sort/filter parameters as the web pages.
- **Editable notes from the phone** — jot a deadline or an interview date on the application page (requires `--allow-write`). `GET /api/applications/{id}` returns a single joined object — the application, the offer, and the cover letter already sent — which is what an agent needs to draft a follow-up email without stitching three sources together.

### Security

- Listens on `127.0.0.1` by default.
- Set `JOBSEARCH_API_TOKEN` in `config/.env` to require a token (`X-API-Token` header for the API, a cookie for the pages — open `http://<host>:8000/?token=<your token>` once and the token is dropped from the URL immediately).
- **The server refuses to start** on a non-loopback address when no token is set.
- Writes (status changes) require `--allow-write`; without it the endpoint returns 403.

### Exposing it to your phone

Use **Tailscale**: put the machine and the phone on the same tailnet, then bind to the machine's Tailscale address:

```bash
jobsearch --serve --host 100.x.y.z --allow-write
```

No port is opened to the internet and traffic is encrypted end to end. Note that the machine must be awake to answer — `JOBSEARCH_HOME` makes it easy to move the whole setup to an always-on Raspberry Pi or a small VPS if that becomes a problem.

### Making it persistent — launchd service (macOS)

Running `jobsearch --serve` by hand in a terminal doesn't survive a closed session or a reboot. `jobsearch --service` manages a user-level launchd agent (`com.jobsearch.server`) that keeps it running, starts it at login, and restarts it if it crashes:

```bash
jobsearch --service install --host 100.x.y.z --port 8767 --allow-write   # writes + loads the plist
jobsearch --service status     # plist present?, agent loaded?, process alive?, listening address
jobsearch --service logs       # tail of the service log (JOBSEARCH_HOME/data/server.log)
jobsearch --service restart    # after editing config, or if it seems stuck
jobsearch --service uninstall  # unloads and removes the plist
```

`install` reuses the same `--host`/`--port`/`--allow-write` flags as `--serve`. `status` is the command to run when "it doesn't work anymore" — it reports the plist path, whether the agent is loaded, whether the process is actually alive, and the address it's configured to listen on. The plist points at the `jobsearch` executable resolved at install time and logs both stdout and stderr to `JOBSEARCH_HOME/data/server.log` — without this, a service that crashed left no trace at all. This is macOS-only (launchd); on other platforms `--service` explains the situation and points at the systemd `--user` equivalent instead of failing with a raw traceback.

### MCP server — Claude Code / Claude app

**Install the extra and register the server** (user scope — available in every conversation, not just this repo):

```bash
pipx inject jobsearch mcp          # or: pip install -e '.[mcp]'
claude mcp add -s user jobsearch -- ~/.local/bin/jobsearch --mcp
claude mcp list                    # should report "Connected"
```

Append `--allow-write` to allow status changes from a conversation; it is off by default.


For an MCP client (Claude Code, the Claude app running on the same machine) to answer questions about your job search directly, without going through the HTTP API:

```bash
pipx install 'jobsearch[mcp]' ...        # or: pip install -e '.[mcp]'
jobsearch --mcp                          # stdio transport, read-only
jobsearch --mcp --allow-write            # also allow status changes
```

Exactly **4 tools**, deliberately few (every declared tool costs context in *every* conversation the client has, not just the ones about job hunting):

| Tool | Role |
|---|---|
| `list_applications` | filter by `q`/`status`/`company`/`since`/`limit` — `q` ALSO searches free-text notes |
| `get_application` | full dossier: application + offer + cover letter already sent |
| `search_offers` | collected offers not yet applied to — `q`/`min_score`/`since`/`limit` |
| `mark_application_status` | changes the status; rejected without `--allow-write` |

Same principle as the HTTP API: deadlines, interview dates and recruiter names live in the free-text notes field, not in dedicated fields — `q` searches there too. The expected usage pattern is two-step: narrow server-side (`list_applications(q=...)`), then read the small resulting subset — never fetch every application.

Example client configuration (`claude_desktop_config.json` or the Claude Code equivalent, stdio transport):

```json
{
  "mcpServers": {
    "jobsearch": {
      "command": "jobsearch",
      "args": ["--mcp"]
    }
  }
}
```

A `streamable-http` transport also exists (`--mcp --mcp-transport streamable-http --host H --port P`) for a remote agent, with the same guard as `--serve` (refuses to start on a non-loopback address without `JOBSEARCH_API_TOKEN`) — but **the MCP SDK 2.2.0 doesn't actually authenticate requests on this transport via a shared token** (only a full OAuth mechanism is supported). Never expose it bare on the internet: only behind a trusted tunnel (Tailscale), the same way as `--serve --host <tailscale address>`.

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

Filtering uses Claude Haiku through the Message Batches API by default (−50% on tokens). Generation (letter + CV) uses Claude Sonnet, one-shot per offer. Closing the terminal while filtering does not lose anything: the pending batch is saved and resumed (not billed again) on the next scrape. Reload credits at [console.anthropic.com](https://console.anthropic.com) if needed — scraping itself works without any credit.

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

### Claude models

Each task (filter, letter, CV) has its own model in `search.yaml` (`models.<task>.model`), editable from the TUI Settings tab ("Claude models" row):

- `auto:haiku` / `auto:sonnet` / `auto:opus` follows the latest model of the family. The list comes from `client.models.list()` (free), cached for 24h in `data/models_cache.json`; when the resolved model changes, a one-time notice is shown in the TUI banner and the CLI output. Offline or without an API key, the last known resolution (then a built-in default) is used — it never blocks a command.
- An exact identifier (e.g. `claude-sonnet-5-5`) pins the model.
- `local:<model>` is reserved for a future local-model phase and is rejected for now.
- `max_tokens` includes the reasoning tokens of recent models (Sonnet 5.5 thinks by default): keep the 16000 default for letter and CV. `effort` is optional and absent by default because Haiku 4.5 rejects it.
- Letter and CV calls on non-Haiku models use the server-side refusal fallback (falls back to a plain call if the API rejects it). Never used for the filter batch.

### Update alert

`jobsearch` checks the public repo's latest release (`api.github.com`, anonymous, stdlib only) and lets you know when a newer version is out — in the CLI output (throttled to once every 15 minutes), in the TUI's status bar, and in the web view's footer. Fully non-blocking: the network check is cached for 24h in `data/update_check.json`, and any failure (offline, rate-limited, unexpected response) is silently ignored — it never delays or breaks a command. The message points to `pipx upgrade jobsearch`.

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
