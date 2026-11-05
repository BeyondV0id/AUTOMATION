# Tools, MCP Servers and Job Sources

This page is the reference for **what the pipeline runs on**, **which MCP
servers it talks to**, and **where every job posting comes from**. For setup and
commands, see [`../README.md`](../README.md). For the module walkthrough, see
[`../PROJECT_SCRIPT.md`](../PROJECT_SCRIPT.md).

---

## 1. Runtime and libraries

| Component | Version / form | Required? | Used for |
| --- | --- | --- | --- |
| Windows + PowerShell | — | Yes | Target environment; scheduled runs |
| Python | 3.11+ (workspace runs 3.13.5) | Yes | Everything |
| `openpyxl` | ≥ 3.1 | Optional | `data/jobs.xlsx` export and edit import |
| `playwright` (Python) | ≥ 1.40 | Optional | Naukri, HiringCafe, form filling |
| Chromium for Playwright | installed by `python -m playwright install chromium` | Optional | Browser sources and forms |
| `pypdf` | ≥ 4 | Optional | PDF text layer, ATS keyword extraction |
| `PyMuPDF` (`fitz`) | ≥ 1.24 | Optional | PDF geometry and layout checks |
| LaTeX.to | remote WebAssembly TeX Live | Fallback | Compilation when no local engine exists |
| Local TeX | `latexmk`, `pdflatex`, `xelatex`, `lualatex`, `tectonic` | Optional | Preferred compiler when installed |

Optional extras are declared in `pyproject.toml`:

```toml
[project.optional-dependencies]
excel   = ["openpyxl>=3.1"]
browser = ["playwright>=1.40"]
pdf     = ["pypdf>=4", "pymupdf>=1.24"]
```

Missing optional packages degrade to `skipped` in reports. They do not create a
false pass.

### Protocols used directly

| Protocol | Module | Purpose |
| --- | --- | --- |
| HTTP / HTTPS | `net.py` | Public feeds, ATS boards, employer pages, Notion, Sheets |
| MCP JSON-RPC over HTTP | `source_engg.py` | engg.space `search_jobs` |
| SMTP (SSL, port 465) | `outreach/send.py` | Send mail and append to Drafts |
| IMAP (SSL, port 993) | `sync/gmail.py` | Read application mail |
| SQLite | `discover.py`, `outreach/store.py`, `sync/store.py` | All local state |
| Playwright CDP / browser automation | `browser/`, sources | Rendered pages and forms |
| `ego-browser` CLI | `browser/ego_backend.py` | Supervised signed-in browsing |

---

## 2. MCP servers

### 2.1 engg.space — used by the pipeline

| Property | Value |
| --- | --- |
| Endpoint | `https://www.engg.space/mcp` |
| Transport | HTTP JSON-RPC (`initialize`, then `tools/call`) |
| Auth | None; read-only |
| Called from | `resume_pipeline/source_engg.py` |
| Tool used | `search_jobs` |
| Config | `[engg]` in `config/jobs.toml` (`limit`, `posted_within_days`, `queries`) |
| Returns | Title, company, location, description, and the employer's `apply_url` |
| Caching | Same `net.py` disk cache and 1.5 s per-host throttle as every source |

The module implements only the small part of MCP it needs. It accepts either a
JSON body or a server-sent-events body and extracts the JSON-RPC result. This is
the **only MCP server the Python pipeline calls**.

Why it matters: engg.space reads company career boards and returns the
employer's own apply URL, which is what makes those jobs usable by `/apply`
instead of landing on an aggregator redirect.

### 2.2 Google Workspace MCP — Gmail drafts and Sheets

| Property | Value |
| --- | --- |
| Package | `workspace-mcp` via `uvx` |
| Transport | Local stdio MCP server |
| Scopes | `gmail:drafts`, `sheets:full` |
| Account | configured Gmail account |
| Token store | `~/.config/opencode/google-credentials/` |
| Host config | `~/.config/opencode/opencode.jsonc` (outside this repo) |
| Used by | OpenCode host tools, and `scripts/push_tracker.py` reuses the token |

The scope is deliberately `gmail:drafts`, not `gmail:full` or `gmail:send`.
Even if a tool call asked to send, the OAuth grant cannot send. Sending happens
only through local SMTP with the app password, as a separate confirmed command.

`scripts/google.py status` reports token health. Testing-mode OAuth refresh
tokens expire after 7 days; `scripts/google.py refresh` refreshes one.

### 2.3 Playwright MCP — interactive browser work

| Property | Value |
| --- | --- |
| Package | `npx @playwright/mcp@latest` |
| Transport | Local stdio MCP server |
| Used by | OpenCode host, for manual/interactive browsing |
| Pipeline equivalent | The `playwright` Python library in `resume_pipeline/browser/` and the Naukri/HiringCafe sources |

The pipeline does not call the Playwright MCP server. It uses the same engine
through the Python API so that runs are scriptable, cacheable and testable.

### 2.4 ego lite — supervised browser backend (CLI, not MCP)

| Property | Value |
| --- | --- |
| Binary | `ego-browser.exe` under `C:\Program Files\Citro Labs\ego lite\Application\<version>` |
| Config | `[browser]` in `config/browser.toml` (`ego_space`, `ego_profile`, `ego_exe`) |
| Used by | `resume_pipeline/browser/ego_backend.py` |
| Strength | Drives the browser you are already signed into; every click is visible |
| Limit | One document at a time; cannot reach inside cross-origin iframes |

Select it with `backend = "ego"` or `--backend ego`, or let `auto` fall back to
it when Playwright is unavailable.

### 2.5 Other MCP servers in this OpenCode install

`rainbrand` and `paper` are configured at the host level and are **not used by
this project**. They are brand-asset and design-canvas tools. Listing them here
prevents confusion when an agent sees them in the tool catalog.

### MCP summary

| MCP / tool | In pipeline? | Purpose |
| --- | --- | --- |
| engg.space | **Yes** | Job search from engineering career boards |
| Google Workspace | Indirect | Gmail drafts, Sheets tracker, OAuth token for `push_tracker.py` |
| Playwright MCP | No (host) | Interactive browser automation |
| ego lite | Yes (backend) | Supervised signed-in browser |
| rainbrand / paper | No | Unrelated host tools |

---

## 3. Job sources

All sources are public and unauthenticated. None requires an API key.

### 3.1 Public JSON feeds

| Source | Endpoint | Fields used | Notes |
| --- | --- | --- | --- |
| Arbeitnow | `https://www.arbeitnow.com/api/job-board-api` | `data[]`: title, company, location, url, description, slug | Largest single feed in the current DB (480) |
| Remotive | `https://remotive.com/api/remote-jobs?limit=100` | `jobs[]` | Remote-only |
| Himalayas | `https://himalayas.app/jobs/api` | `jobs[]` | Remote-heavy |

Implemented in `discover.py::fetch_feed_sources`.

### 3.2 Server-rendered HTML

| Source | Endpoint | Config | Notes |
| --- | --- | --- | --- |
| LinkedIn | `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` and `/jobs/search/` | `[linkedin]` keywords, location, `max_pages` | Logged-out only; may throttle; canonical job URLs |
| Internshala | `https://internshala.com` category pages | `[internshala]` categories, `max_pages` | Internships; skills, stipend, duration, WFH flag |

### 3.3 Static catalogue

| Source | Endpoint | Config | Notes |
| --- | --- | --- | --- |
| GiveMeAFuckingJ\*b | `https://www.givemeafuckingjob.com/jobs/index.txt` + `/jobs/<chunk>.json` | `[gmafj]` keywords, countries, remote, `days_old`, `max_jobs` | Delta-encoded index filtered locally before detail chunks; no company field, inferred from apply host |

The index is a few megabytes, cached for a day. A chunk is named after the
nearest job at or above it whose id hashes low, the same rule the site's own
search uses.

### 3.4 Browser-rendered sources

Both need a **visible** window and a logged-in interactive desktop session.

| Source | How it is read | Why headful | Config |
| --- | --- | --- | --- |
| Naukri | Playwright Chromium reads rendered cards | Headless returns "Access Denied" | `[naukri]`, `headful = true`, `wait_ms = 6000` |
| HiringCafe | Read `buildId` from `__NEXT_DATA__`, then `GET /_next/data/<buildId>/classic.json?searchState=<json>&page=N` | Cloudflare challenge clears only with a real window | `[hiringcafe]`, `headful = true`, `wait_ms = 9000` |

HiringCafe hits carry structured fields: company, location, seniority,
technical tools, requirements summary and the employer's apply URL.

Disable either with its `[sources]` flag, `--no-naukri`, `--no-hiringcafe`,
`--no-browser`, or `--fast`.

### 3.5 Hacker News "Who is hiring?"

| Property | Value |
| --- | --- |
| Thread search | `https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=24` |
| Thread comments | `https://hn.algolia.com/api/v1/items/<thread_id>` |
| Config | `[hn]` keywords, `max_comments`, `days_old`, `remote` |
| Auth | None |
| Browser | No |

Every month the `whoishiring` account posts a thread and founders add one
top-level comment per open role. `source_hn.py` finds the current thread, reads
up to `max_comments` comments, and parses the common shapes:

```text
Acme | Backend Engineer | Remote (US) | https://acme.dev/jobs
Acme is hiring a founding engineer in Bengaluru (on-site)
**Company:** Acme  **Role:** Full-stack developer  **Location:** Remote
```

The comments are free text, so a field that is not present stays empty. Nothing
is inferred. Turn the source off with `[sources].hn = false` or `--no-hn`.

### 3.6 MCP source

| Source | Endpoint | Config | Notes |
| --- | --- | --- | --- |
| engg.space | `https://www.engg.space/mcp` | `[engg]` `limit`, `posted_within_days`, `queries` | JSON-RPC `initialize` + `tools/call search_jobs`; returns employer apply URLs |

### 3.7 Y Combinator

| Piece | Endpoint | Notes |
| --- | --- | --- |
| Hiring companies | `https://yc-oss.github.io/api/companies/hiring.json` | Publishes company records, not postings |
| Company pages | `https://www.ycombinator.com/companies/<slug>` | Embedded `data-page` JSON carries `jobPostings` |

`source_yc.py` filters to active, recent, hiring companies (default: India and
Fully Remote, last 2 years), reads a bounded number of company pages per run,
caches them, and spreads the rest across later runs. Paused postings are
skipped.

### 3.8 ATS boards

Configured in `config/jobs.toml`:

```toml
[ats]
greenhouse = ["stripe", "vercel"]
lever = ["toggl"]
ashby = ["linear"]
```

| ATS | Endpoint |
| --- | --- |
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true` |
| Lever | `https://api.lever.co/v0/postings/<board>?mode=json` |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/<board>` |

`source_ats.py` normalizes all three and strips HTML from descriptions.

### 3.9 Source summary

| Source | Type | Auth | Browser | Current rows |
| --- | --- | --- | --- | --- |
| Arbeitnow | JSON API | None | No | 480 |
| Remotive | JSON API | None | No | 7 |
| Himalayas | JSON API | None | No | 26 |
| LinkedIn | Guest HTML | None | No | 100 |
| Internshala | HTML categories | None | No | 182 |
| Naukri | Rendered page | None | **Yes** | 60 |
| GiveMeAFuckingJ\*b | Static JSON | None | No | 0 |
| Hacker News | Algolia JSON | None | No | 0 |
| HiringCafe | Next.js data route | None | **Yes** | 0 |
| engg.space | MCP | None | No | 91 |
| Y Combinator | Mirror + pages | None | No | 8 |
| Greenhouse / Lever / Ashby | ATS JSON | None | No | 0 |

Counts are from `data/applications.db` on 2026-11-05: 954 jobs total.

---

## 4. Where data is stored

| Store | Path | Contents |
| --- | --- | --- |
| SQLite | `data/applications.db` | `jobs`, `applications`, `contacts`, `outreach`, `email_events`, `notion_pages`; `apply_attempts` is created on the first apply run |
| Excel | `data/jobs.xlsx` | Human tracker: Status, Notes, Contact Email |
| HTTP cache | `data/cache/*.json` | Every fetched URL, hashed, 12-hour default TTL |
| Playwright profile | `data/browser-profile/` | Persistent Chromium session |
| Resume data | `data/resume/` | Source PDF, `raw_text.txt`, `profile.draft.json`, `profile.json` |
| Tailoring overlays | `data/tailoring/<job>/` | Strategy JSON, prompt, fitted JSON |
| Generated resumes | `resumes/generated/` | Variants and job-specific `.tex`/`.pdf` |
| Reports | `reports/` | Digests, apply reports, compile, tailoring, ATS |

SQLite is the source of truth for status. Excel is a two-way view: exports
rewrite the sheet, and `sync` imports human edits back. A status is never
overwritten by discovery.

---

## 5. Credentials and environment

| Variable | Required for | Where it is set |
| --- | --- | --- |
| `JOB_GMAIL_APP_PASSWORD` | Gmail drafts, optional send, IMAP sync | Environment only; never a config file |
| `JOB_NOTION_TOKEN` | Optional Notion publish | Environment only |
| Google OAuth token | Sheets push, Google Workspace MCP | `~/.config/opencode/google-credentials/` |
| `LATEXTO_PROFILE` | Override LaTeX.to cache directory | Optional |

Setup notes:

- Gmail app passwords require 2-Step Verification.
- The Google Workspace MCP is configured in the host OpenCode config, not in
  this repository. The repo only names the account and the spreadsheet ID in
  `config/sync.toml`.
- `scripts/doctor.py` reports which of these are present without printing
  secrets.

---

## 6. What is deliberately not used

| Not used | Why |
| --- | --- |
| LLM APIs in the pipeline | Scoring and tailoring are deterministic and token-free |
| Paid job-board APIs | Every source here is public |
| Logged-in LinkedIn scraping | Guest search only; no account or cookie |
| Guessed email addresses | A bounce costs sender reputation |
| `gmail:send` OAuth scope | Sending stays a separate, confirmed, rate-limited action |
| Cloud PDF upload | LaTeX.to compiles locally in Chromium |
| Auto-submit | The final Submit button is always human |

---

## 7. Quick verification commands

```powershell
python scripts/doctor.py                  # config, DB, tokens, browsers
python scripts/status.py                  # tracker counts
python scripts/google.py status           # OAuth token health
python scripts/browser_smoke.py --url https://example.com
python -m pytest -q                       # 169 tests
```

If a source is blocked, the run reports it and continues. Naukri and HiringCafe
are the only sources expected to fail without a visible desktop session.
