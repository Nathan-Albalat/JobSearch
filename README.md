# JobSearch

JobSearch collects job offers from RSS feeds and job-board APIs, scores each one against your search with Claude, and writes a report of the best matches. For the offers you choose, it drafts a tailored cover letter and CV as PDFs, then helps you track your applications.

It works for any sector (7 presets: software, healthcare, law, sales, marketing, engineering, education, or your own keywords), any contract type and any country.

---

## Installation

```bash
pipx install git+https://github.com/Nathan-Albalat/JobSearch
jobsearch --setup     # guided setup
jobsearch --tui       # interactive interface
```

Requires Python 3.11 or later. Optional extras:

- **LinkedIn / Indeed**: the `[jobspy]` extra, which requires Python 3.11 exactly. Without it, these two sources are skipped with a message.
  ```bash
  pipx install "jobsearch[jobspy] @ git+https://github.com/Nathan-Albalat/JobSearch" --python python3.11
  ```
- **PDF output** needs the Pango library:
  - macOS: `brew install pango`
  - Debian/Ubuntu: `apt install libpango-1.0-0 libpangoft2-1.0-0`
  - Windows: install [MSYS2](https://www.msys2.org), run `pacman -S mingw-w64-x86_64-pango`, then set `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin`

  Without Pango, the letter is still written (as Markdown) and the application recorded; only the PDFs are skipped.
- **Windows**: use Windows Terminal for the TUI. The background service (`--service`) is macOS-only.

For development: `pip install -e .` inside the repo's virtualenv.

### Updating

```bash
jobsearch --upgrade
```

With pipx, this runs the upgrade for you. On Windows, or for a cloned repo or plain pip install, it prints the command to run instead. Your config and data are kept, and so are the extras you installed.

### First setup

`jobsearch --setup` asks for your Anthropic API key, your search (contract type, duration, countries, domains, score threshold), your sector, and your profile. It then writes the config files, including the scraping sources for your sector and country. It never overwrites a file without asking, so it is safe to re-run.

Afterwards, it's worth adding a few real offer examples from your search to the scoring prompt (Settings tab, key `6`): it noticeably improves the ranking.

Config and data live in `~/.jobsearch/` (change it with the `JOBSEARCH_HOME` environment variable). `python -m jobsearch.config` shows the paths in use.

---

## How it works

```
1. Scrape       collect offers from RSS feeds and job-board APIs
2. Filter       Claude Haiku scores each offer from 0 to 10
3. Report       Markdown report of the relevant offers
                ↓ you pick the offers you want
4. Apply        Claude Sonnet drafts a cover letter and an adapted CV
```

Steps 1 to 3 run with `jobsearch --scrape` (or `S` in the TUI). Step 4 runs per offer.

---

## The TUI

```bash
jobsearch --tui
```

The TUI reads and writes the same files as the CLI (`data/offers/`, `data/applications.json`), so you can stop and resume at any time.

| Tab | What it's for |
|---|---|
| **1 · Offers** | Scored offers with a detail panel. Check offers with `space` to queue them for generation. |
| **2 · Generation** | The generation queue: generate letter + CV (`g`) or the letter only (`L`), add per-offer instructions (`N`), or record an application without generating anything (`T`). |
| **3 · Applications** | Generated letters and CVs. Edit a letter (`e`), then regenerate its PDF (`p`). |
| **4 · Tracking** | Application statuses, notes, and the timeline of each application. |
| **5 · Stats** | Response rates, activity heatmap, locations by country and city, applications over time, and the paths applications took. |
| **6 · Settings** | Open any config file in your editor, turn scraping sources on and off, and choose the Claude model for each task. |

### Keyboard shortcuts

Press `?` in the TUI for the full list.

| Tab | Key | Action |
|---|---|---|
| All | `1`–`6` | Switch tab |
| All | `r` | Reload data from disk |
| All | `S` / `s` | Scrape + filter + report (`Ctrl+S` also includes the French-only sources) |
| All | `c` | Open the Claude Console (API credits) |
| All | `q` | Quit |
| Offers | `space` | Queue for generation |
| Offers | `o` | Open the offer in the browser |
| Offers | `+` / `-` | Raise / lower the score threshold |
| Offers | `h` | Hide / show applied, duplicate and invalid offers |
| Offers | `d` / `D` | Change the displayed period / pick a scrape session |
| Offers | `m` | Mark as a duplicate of an existing application |
| Offers | `X` | Mark as invalid (closed, cancelled, wrong contract…) |
| Generation | `g` / `L` | Generate letter + CV / letter only |
| Generation | `A` | Add an offer by hand (HTML file or URL) |
| Generation | `N` | Add an instruction for this offer's letter and CV |
| Generation | `T` | Record the application without generating anything |
| Generation | `x` | Remove from the queue |
| Applications | `enter` | Switch preview between letter and CV |
| Applications | `e` / `p` | Edit the letter / regenerate its PDF |
| Applications | `u` | Regenerate the CV PDF |
| Tracking | `s` `a` `i` `j` `v` `z` | Status: sent, answered, interview, rejected, accepted, cancelled |
| Tracking | `backspace` | Undo the last status change |
| Tracking | `n` | Edit the note |
| Stats | `t` | Time scale: day, week, month |
| Stats | `f` | Export the status-flow (Sankey) diagram |
| Settings | `enter` | Open the file, or the sources / models manager |

`o` always opens the offer in the browser, and `O` always opens the application folder.

### Duplicate and invalid offers

The same offer is sometimes posted on several sites. When an offer comes from a company you already applied to, it is flagged `?`: press `m` to confirm it is a duplicate.

An offer that is closed, cancelled or not the right contract can be marked invalid with `X`. It then leaves the "to apply" list. For an offer you already applied to, use the "cancelled" status (`z`) instead.

---

## The CLI

```bash
jobsearch --scrape                          # collect + filter + report
jobsearch --scrape --threshold 7            # custom score threshold
jobsearch --scrape --with fr                # also use the French-only sources
jobsearch --generate <offer_id>             # letter + adapted CV
jobsearch --generate <offer_id> --letter-only
jobsearch --generate <offer_id> --notes "Insist on project X"
jobsearch --record <offer_id>               # record the application, generate nothing
jobsearch --applications                    # application table (--json for JSON)
jobsearch --mark <offer_id> <status>        # change a status
jobsearch --undo-status <offer_id>          # undo the last status change
jobsearch --mark-duplicate <offer_id> <target_id>
jobsearch --mark-invalid <offer_id> --reason closed|cancelled|contract|other
jobsearch --letter-pdf <letter.md> --company "Name"   # render a letter PDF on demand
```

`offer_id` is the name of the offer's file in `data/offers/`, without `.json`. `jobsearch --help` lists every option.

### Offers from other sites

For an offer the scraper can't reach, save the page from your browser ("Web page, complete") and generate from it:

```bash
jobsearch --from-file ~/Downloads/offer.html --url-source "https://..." --company "Company"
jobsearch --from-url https://example.com/jobs/offer     # sites that work without JavaScript
```

Add `--title` or `--company` if the extracted values are wrong.

---

## Remote access

### Web view and API

```bash
pipx inject jobsearch fastapi uvicorn python-multipart
jobsearch --serve                  # http://127.0.0.1:8000, read-only
```

This serves a mobile-friendly web view (applications, timelines, offers, stats) and a JSON API (`/api/applications`, `/api/offers`, `/api/stats`). The `q` search parameter also looks in your notes, where deadlines, interview dates and recruiter names usually live.

**Security:**
- The server only listens on your own machine by default.
- To reach it from another device, set `JOBSEARCH_API_TOKEN` in `config/.env`, then open `http://<host>:8000/?token=<token>` once. The server refuses to start on a network address without a token.
- Changing statuses or notes requires `--allow-write`.

**From your phone**, the simplest option is [Tailscale](https://tailscale.com): put the computer and the phone on the same tailnet, then run

```bash
jobsearch --serve --host 100.x.y.z --allow-write
```

No port is opened to the internet. The computer must stay awake to answer.

### Keeping it running (macOS)

`jobsearch --service` runs the server in the background, starts it at login and restarts it if it crashes:

```bash
jobsearch --service install --host 100.x.y.z --port 8767 --allow-write
jobsearch --service status      # is it running, and on which address?
jobsearch --service logs
jobsearch --service restart     # e.g. after an update
jobsearch --service uninstall
```

### MCP server (Claude Code, Claude app)

This lets Claude answer questions about your applications directly:

```bash
pipx inject jobsearch mcp
claude mcp add -s user jobsearch -- ~/.local/bin/jobsearch --mcp
```

It exposes four tools: `list_applications`, `get_application` (the application, the offer and the letter you sent), `search_offers` and `mark_application_status`. Status changes are disabled unless you add `--allow-write` to the command.

A network transport exists (`--mcp-transport streamable-http`) but does not check the token itself: only use it behind Tailscale.

---

## Configuration

Every file can be opened from the Settings tab (`6`):

| File | Content |
|---|---|
| `config/search.yaml` | Your search: contract, duration, countries, domains, threshold, pre-filter keywords, models |
| `config/profile.yaml` | Your profile: education, skills, languages |
| `config/sources.yaml` | Scraping sources (generated by `--setup` for your sector and country) |
| `config/prompts/*.txt` | Prompts for scoring, the cover letter and the CV |
| `config/templates/*.html` | PDF layouts for the CV and the letter |
| `cv_master/cv_data.yaml` | Your CV content, used for both the master CV and the adapted CVs |
| `config/.env` | API keys |

If you change your search completely (e.g. from an internship to a permanent job), empty `data/offers/` too, so old offers aren't scored against the old search.

### Claude models

Each task (filtering, letter, CV) has its own model, set from the Settings tab or in `search.yaml`:

- `auto:haiku`, `auto:sonnet` or `auto:opus` follows the newest model of that family, with a notice when it changes.
- An exact name such as `claude-sonnet-5-5` pins the model.

Recent models count their reasoning in `max_tokens`: keep the default of 16000 for the letter and the CV.

### Cost

Filtering sends offers in batches, which halves the price. A batch can take up to an hour. If you close the terminal meanwhile, nothing is lost: it is resumed at the next scrape and not billed again. Generation costs a few cents per application. Collecting offers is free.

### Email summary (optional)

Add to `config/.env` to receive an email after each scrape:

```
NOTIFY_EMAIL=you@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Gmail app password
```

### Scraping automatically (macOS/Linux)

Every two days at 8 am, with `crontab -e`:

```
0 8 */2 * * jobsearch --scrape >> ~/.jobsearch/data/cron.log 2>&1
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "ANTHROPIC_API_KEY manquant" (missing) | Add `ANTHROPIC_API_KEY=sk-ant-...` to `config/.env` |
| "Your credit balance is too low" | Add credits at [console.anthropic.com](https://console.anthropic.com). Collecting offers still works. |
| The scrape looks stuck at the filtering step | It is waiting for the batch, which can take up to an hour. The status bar shows its progress. |
| "0 nouvelle(s) offre(s) collectée(s)" (0 new offers) | Normal if you scraped recently: offers already seen are skipped. |
| Few relevant offers | Lower the threshold: `jobsearch --scrape --threshold 5` |

## License

MIT, see [LICENSE](LICENSE).
