# What This Project Does

A local, token-free job search and resume pipeline. It finds public job
postings, scores them against a verified profile, tracks applications in SQLite
and Excel, fills application forms without submitting them, drafts outreach
email, and generates ATS-friendly LaTeX resumes without inventing facts.

The pipeline itself makes no LLM API calls. A model (OpenCode/Codex) can
optionally rewrite resume wording, but every rewrite is checked against
`data/resume/profile.json` before it is allowed to render.

---

## The Big Picture

```text
                    ┌───────────────────────┐
                    │  Your real resume     │
                    └──────────┬────────────┘
                               │ ingest
                               ▼
                 ┌──────────────────────────┐
                 │ raw_text.txt             │
                 │ profile.draft.json       │  ← human reviews/fixes
                 │ profile.json  (verified) │
                 └────────────┬─────────────┘
                              │
        ┌─────────────────────┴──────────────────────┐
        │                                            │
        ▼                                            ▼
┌──────────────────┐                     ┌────────────────────────┐
│ JOB DISCOVERY    │                     │ RESUME GENERATION      │
│ 12 source types  │                     │ LaTeX variants         │
└────────┬─────────┘                     └───────────┬────────────┘
         │ score (rules, 0-100)                      │
         ▼                                           ▼
┌──────────────────┐                     ┌────────────────────────┐
│ applications.db  │                     │ resumes/generated/     │
│ jobs + status    │                     │ master / backend /     │
└────────┬─────────┘                     │ SWE / AI-ML            │
         │                               └────────────────────────┘
         ├──────► reports/job-digest-YYYY-MM-DD.md
         ├──────► data/jobs.xlsx   (edit Status/Notes/Contact Email)
         │
         ├──────► APPLY: fill form, never submit ──► reports/apply/
         │
         ├──────► OUTREACH: contacts + Gmail Drafts
         │
         └──────► TAILOR: ats-match / impact-led / role-fit
                  verify.py fact check → PDF fit + inspect + ATS report
```

---

## 1. Resume Ingest and Verification

`resume_pipeline/ingest.py`

- Copies the resume into `data/resume/`.
- Extracts raw text from `.txt`, `.md`, `.tex`, `.docx` (zipped XML) or `.pdf`
  (needs `pypdf`).
- Writes `raw_text.txt` and a skeleton `profile.draft.json` containing only what
  can be detected safely: email, links, and the first line as a name.
- The draft is never used for generation. It must be reviewed, filled with real
  experience, skills, bullets and education, then saved as
  `data/resume/profile.json`.

`resume_pipeline/profile.py`

- Refuses to load a profile with an empty `identity.name`.
- `record_matches()` keeps untagged resume records and only filters records
  explicitly tagged for another role, so verified data is not silently dropped.

---

## 2. Job Discovery

`run.py` → `resume_pipeline/discover.py`

Twelve source types, all public and unauthenticated:

| Source | Module | How it is read |
| --- | --- | --- |
| Arbeitnow | `discover.py` | Public job-board JSON API |
| Remotive | `discover.py` | Public remote-jobs JSON API |
| Himalayas | `discover.py` | Public jobs JSON API |
| LinkedIn | `source_linkedin.py` | Logged-out guest search HTML |
| Internshala | `source_internshala.py` | Server-rendered internship categories |
| Naukri | `source_naukri.py` | Playwright-driven visible Chromium |
| GiveMeAFuckingJ\*b | `source_gmafj.py` | Static catalogue JSON, filtered locally |
| Hacker News | `source_hn.py` | Algolia mirror of the monthly "Who is hiring?" thread |
| HiringCafe | `source_hiringcafe.py` | Next.js data route through Chromium |
| engg.space | `source_engg.py` | Public MCP server, `search_jobs` over JSON-RPC |
| Y Combinator | `source_yc.py` | `yc-oss` mirror + public YC company pages |
| Greenhouse / Lever / Ashby | `source_ats.py` | Public ATS board JSON |

Supporting modules:

- `net.py` — disk cache in `data/cache/`, 1.5 s spacing per host, POST caching
  for MCP. Repeat runs cost no network.
- `source_yc.py` — filters to active, recent, hiring YC companies, parses the
  embedded page data, skips paused postings. `yc-oss` publishes company records
  only, so postings still come from each company page.
- `source_ats.py` — normalizes Greenhouse/Lever/Ashby postings and strips HTML.
- `source_linkedin.py` — parses public guest cards; canonical URLs, 10 per page.
- `source_internshala.py` — parses internship cards, skills, stipend, duration
  and work-from-home flags.
- `source_naukri.py` — drives Chromium through Playwright and reads rendered
  cards. Headful only; headless gets "Access Denied".
- `source_engg.py` — initializes the engg.space MCP server and calls
  `search_jobs`; prefers the employer's `apply_url`.
- `source_gmafj.py` — reads the static catalogue, filters by keyword, country
  and age, then fetches detail chunks. The site has no company field, so the
  employer is inferred from the apply URL host.
- `source_hn.py` — finds the current `whoishiring` thread through Algolia and
  parses one top-level comment per role. Free text, so missing fields stay
  empty rather than being guessed.
- `source_hiringcafe.py` — boots Chromium past Cloudflare, reads the Next.js
  `buildId`, then calls the `classic.json` data route. Hits carry company,
  location, seniority, technical tools, requirements and the employer's apply
  URL.

Filtering and storage:

1. Jobs must match at least one keyword and, optionally, one location.
2. Each job gets a stable ID, so re-runs update instead of duplicating.
3. Jobs are stored in `data/applications.db` with `status = 'new'`.
4. On conflict, the existing `status` is preserved. Tracking edits are never
   overwritten.

---

## 3. Deterministic Scoring

`resume_pipeline/match.py` — 100 points total:

| Dimension | Weight | Logic |
| --- | --- | --- |
| Role | 30 | Exact/partial title overlap with `preferences.roles`; off-topic titles score 0 |
| Skills | 30 | Share of posting skills already in the profile |
| Location | 15 | Target location or remote preference |
| Seniority | 25 | Detected from title and description: internship, fresher, junior, mid, senior, unknown |

`level.py` reads seniority conservatively. When a posting does not state a
level, the answer is `unknown` and it is treated as a maybe. A posting asking
for more years than the profile supports is capped at 35, so it can still be
read but never reaches the top of the list. `preferences.max_years_required`
overrides the inferred limit.

Outputs per job: `match_score`, `match_reason`, `missing_skills`, `level`,
`years_required`, `eligible`.

`scripts/rank.py` re-scores the whole backlog and writes those columns. Because
scoring is local, it is free to run after every discovery.

If no profile exists, jobs are stored unscored. That is the current state.

---

## 4. Daily Output

`run.py` runs four steps:

1. `discover` — collect, score, upsert.
2. `contacts` — `resume_pipeline.outreach collect` (never aborts the run).
3. `digest` — write `reports/job-digest-YYYY-MM-DD.md`, best new/review jobs.
4. `excel` — rewrite `data/jobs.xlsx`, sorted by score, with color-coded match
   cells, hyperlinks, filters and a Status dropdown.

Status values: `new`, `review`, `applied`, `interview`, `offer`, `rejected`.

Statuses live in SQLite, so the Excel sheet can be regenerated without losing
edits. `scripts/status.py` prints counts and shortlists.

---

## 5. Browser Layer

`resume_pipeline/browser/`

One small interface, two backends:

| Backend | Module | Strengths |
| --- | --- | --- |
| `playwright` | `playwright_backend.py` | Own Chromium, persistent profile, reads cross-origin iframes, uploads files |
| `ego` | `ego_backend.py` | Drives the signed-in ego lite browser through the `ego-browser` CLI; every click is visible |
| `auto` | `__init__.py` | Playwright when installed, otherwise ego |

`config/browser.toml` selects the backend, headless mode, profile directory,
timeouts and the ego task space. Playwright is the only backend that can fill
Greenhouse and Lever forms, because those forms live inside cross-origin
iframes.

`flows.py` holds repeated browser steps in one round trip: ATS detection for 16
families, cookie dismissal, scrolling, and submit-button discovery. The submit
matcher exists only to *report and avoid* submit controls.

`scripts/browser_smoke.py` proves a backend works before a real form is trusted
with it. It opens a page, reports what it sees, and saves a screenshot.

---

## 6. Application Form Filling

`resume_pipeline/apply/`

| Module | Job |
| --- | --- |
| `fields.py` | Reads every control in one DOM pass; tags it with `data-jaf-id` |
| `fill.py` | Fills in one batched DOM pass, then retries failures with real actions |
| `answers.py` | Merges verified profile + explicit answers into one flat map |
| `runner.py` | Drives one form end to end and writes a report |
| `queue.py` | Batch selector for queued jobs (library helper, not exposed as a CLI yet) |
| `bench.py` | Local benchmark of a 22-control fill |
| `__main__.py` | CLI |

Supported ATS families: Greenhouse, Lever, Ashby, Workday, SmartRecruiters,
Workable, Breezy, Recruitee, Teamtailor, Jobvite, iCIMS, Taleo, Personio,
Rippling, JazzHR, BambooHR.

Rules:

- Password, hidden, submit, reset, button, image, range and color inputs are
  blocked outright.
- Sensitive fields (SSN, card number, CVV, passport, bank account) are never
  filled.
- Unknown answers stay blank rather than guessed.
- Submit controls are found and reported, never clicked.
- Each run writes JSON and Markdown to `reports/apply/`, with screenshots for
  the first and last step.

`queue.py` selects jobs whose status is `new` or `review` and that have a URL.
It is a library helper; batch filling is not exposed as a CLI yet.

---

## 7. Email Outreach

`resume_pipeline/outreach/`

| Module | Job |
| --- | --- |
| `collect.py` | Three passes for a contact address: posting text, job page, employer contact pages |
| `compose.py` | Renders application/cold email from config, posting and verified facts only |
| `send.py` | SMTP send and IMAP draft append using the Gmail app password |
| `store.py` | `contacts` and `outreach` tables; one draft per (job, kind) |
| `__main__.py` | CLI: `collect`, `contacts`, `draft`, `list`, `show`, `send` |

Address ranking prefers `careers@`, `jobs@`, `hr@` and similar role inboxes over
`support@`, `sales@` and `press@`. Dead addresses (`no-reply`, `privacy@`,
`legal@`, Sentry noise) are discarded. A guess is never constructed from a name
pattern.

Drafts are rendered once and reused. `--force` is the only way to replace one.
`send` only touches rows already stored as `draft`, requires `--yes` or an
interactive confirmation, and enforces a daily cap and a per-company cap.

---

## 8. Tracker Sync

`resume_pipeline/sync/`

| Module | Job |
| --- | --- |
| `excel.py` | Imports `Status`, `Notes`, `Contact Email` edits; human edits win |
| `gmail.py` | IMAP reader for application mail, 21-day window by default |
| `intent.py` | Classifies rejection / offer / interview / assessment / other |
| `match_email.py` | Ties a message to one job using company, domain and title |
| `notion.py` | Optional one-way publish to a Notion database |
| `store.py` | `email_events`, `notion_pages` and shared helpers |
| `__main__.py` | CLI: `--all`, `--gmail`, `--excel`, `--notion`, `--dry-run` |

Intent is ordered by authority: a rejection outranks an interview mention, and
an offer outranks the word "interview" that offer mail usually contains. A
status only moves when the intent is clear and confidence clears the threshold.
Ambiguous mail is left unmatched and shown for review.

---

## 9. Resume Generation

`latex.py` + `generate.py` + `compile.py` + `latexto.py`

- Renders Jake's Resume layout: centered header, `\resumeSubheading`,
  `\resumeProjectHeading`, `\resumeItem`, section rules.
- Shared preamble in `resumes/jakes-resume.tex` is copied beside every source,
  so each folder is self-contained and compiles unchanged in Overleaf.
- Four variants: `master`, `backend`, `software-engineer`, `ai-ml`.
- Role tags decide which records appear.
- All profile text is escaped (`&`, `%`, `_`, `#`, `$`, braces, `~`, `^`), so a
  stray character cannot break the build.
- `compile.py` finds `latexmk`, `pdflatex`, `xelatex`, `lualatex` or `tectonic`;
  otherwise it compiles through LaTeX.to. It reports errors and warnings, and
  only calls a PDF produced when it starts with `%PDF-`.

---

## 10. Per-Job Tailoring with a Fact Guard

`make_variants.py` + `tailor_variants.py` + `verify.py` + `tailor.py`

For one posting, three angles:

| Strategy | What changes |
| --- | --- |
| `ats-match` | Mirrors posting vocabulary; skills ordered by mention frequency |
| `impact-led` | Measurable bullets first; three or four bullets per role |
| `role-fit` | Closest roles/projects first; summary targets the role |

Before tailoring, `posting.py` cleans the description: tags, script/style blocks
and hidden nodes are removed, invisible Unicode is stripped, and
instruction-shaped text is reported. Posting instructions are never followed.

Two modes:

- **Deterministic** (default): only reorders and reselects existing text.
- **Overlay**: a model-written `<strategy>.json` replaces wording.

Jobs come from `data/applications.db` (`--job-id`) or a JSON file (`--job`).
`jobfile.py` is shared by both CLIs.

`verify.py` rejects an overlay if it:

- Introduces a number not present in the profile.
- Mentions a tool or skill the profile does not contain.
- References experience or a project absent from the profile.
- Writes a bullet sharing no fact with its source.
- Exceeds length limits (320 chars per bullet, 800 per summary).

A rejected overlay is reported and skipped. **Text may change, facts may not.**

### PDF verification and page fitting

`--fit-pages N` compiles, reads the page count, and cuts the lowest-value
bullet until the PDF fits. The score combines posting relevance, measurable
results, vocabulary uniqueness and lead position. The last bullet of a record
is protected, and a model overlay is deep-copied before cutting.

`--inspect` checks what a successful compile does not:

| File | Checks |
| --- | --- |
| `pdfcheck.py` | Page count, required contact strings, text-layer length, `(cid:)`/replacement glyphs, non-ASCII date ranges |
| `layout.py` | Holes over 100 pt, early page endings, empty final pages, footer-band text, orphaned headers and headings |

`--ats-report` extracts PDF text the way an ATS does and scores posting
keywords into four states: `covered`, `synonym-only`, `missing (have it)`,
`missing (gap)`. Only the third is actionable; a gap stays a gap.

Date ranges use an ASCII hyphen, not a LaTeX en dash, because some ATS imports
(notably Workday) lose non-ASCII separators. The preamble loads `needspace` when
available so entry headings keep their first lines.

Outputs:

```text
data/tailoring/<job>/<strategy>.json        overlay
data/tailoring/<job>/<strategy>.prompt.md   model rewrite brief
data/tailoring/<job>/<strategy>.fitted.json overlay after relevance-weighted cuts
resumes/generated/job-specific/<job>/<strategy>/resume.tex
reports/tailoring-<job>.json                verification + compile + fit results
reports/ats-<job>-<strategy>.md             keyword coverage
```

---

## 11. Tools, Libraries and MCP Servers

### Core tools

| Tool | Where | Purpose |
| --- | --- | --- |
| Python 3.11+ stdlib | everywhere | HTTP, SQLite, TOML, IMAP, SMTP, regex, CLI |
| `openpyxl` | `export_excel.py`, `sync/excel.py` | `data/jobs.xlsx` |
| Playwright (Python) | `source_naukri`, `source_hiringcafe`, `browser/` | Browser automation and form filling |
| `pypdf` | `pdfcheck.py`, `ats.py` | PDF text layer and ATS extraction |
| `PyMuPDF` | `layout.py` | PDF geometry |
| LaTeX.to | `latexto.py` | TeX Live in WebAssembly when no local engine exists |
| Gmail SMTP/IMAP | `outreach/send.py`, `sync/gmail.py` | Drafts, optional send, mail sync |
| Google Sheets API | `scripts/push_tracker.py` | Sheet copy of the tracker |
| Notion API | `sync/notion.py` | Optional one-way publish |

All optional dependencies are declared in `pyproject.toml`. Missing ones report
`skipped` instead of producing a false pass.

### MCP servers

| MCP server | Transport | Used by | Purpose |
| --- | --- | --- | --- |
| engg.space | Remote HTTP JSON-RPC at `https://www.engg.space/mcp` | `source_engg.py` | `initialize` + `tools/call search_jobs`; returns employer apply URLs |
| Google Workspace | Local `workspace-mcp` via `uvx`, scopes `gmail:drafts` + `sheets:full` | OpenCode host + `scripts/push_tracker.py` token reuse | Gmail drafts and Sheets; cannot send mail by scope |
| Playwright MCP | Local `npx @playwright/mcp` | OpenCode host | Interactive browser work outside the pipeline |
| ego lite | Host CLI `ego-browser` | `browser/ego_backend.py` | Supervised browser backend for logged-in pages |

Only engg.space is called by the Python pipeline. The others are host-level
tools; the code paths that matter use the Playwright library and the
`ego-browser` CLI directly.

Full detail, including endpoints and data fields, is in
[`docs/TOOLS_AND_SOURCES.md`](docs/TOOLS_AND_SOURCES.md).

---

## 12. Commands

```powershell
# One-time: build a verified profile
python -m resume_pipeline.ingest --resume C:\path\to\resume.pdf

# Generate and compile resume variants
python -m resume_pipeline.generate --profile data/resume/profile.json --variant all
python -m resume_pipeline.compile --all
python -m resume_pipeline.compile --all --remote
python -m resume_pipeline.compile --all --local

# Daily job run
python run.py
python run.py --fast
python run.py --no-browser
python run.py --no-hn
python run.py --min-score 60

# Status and diagnostics
python scripts/doctor.py
python scripts/status.py --top 15
python scripts/rank.py --level internship,fresher --min-score 50
python scripts/google.py status
python scripts/browser_smoke.py --url https://example.com

# Tailor for one job
python -m resume_pipeline.make_variants --job-id "<job-id>" --compile --fit-pages 1 --inspect --ats-report
python -m resume_pipeline.tailor --job-id "<job-id>"

# Fill an application, never submit
python -m resume_pipeline.apply --job "<job-id>" --steps 1 --keep-open

# Outreach
python -m resume_pipeline.outreach collect --limit 100 --fetch-pages
python -m resume_pipeline.outreach draft --limit 10

# Sync
python -m resume_pipeline.sync --all
python scripts/push_tracker.py --dry-run

# PDF audit on its own
python -m resume_pipeline.pdfcheck <pdf> --pages 1 --contains "<email>"
python -m resume_pipeline.layout <pdf>
python -m resume_pipeline.ats --job-id "<job-id>" --pdf <pdf>

# Tests
python -m pytest -q
```

---

## 13. File Map

```text
run.py                         one-command daily pipeline
resume_pipeline/
  __init__.py                  ROOT / DATA / RESUMES / REPORTS paths
  ingest.py                    resume -> raw text + draft profile
  profile.py                   safe profile loading, role tag filtering
  discover.py                  source orchestration, filters, SQLite upsert
  net.py                       cached, rate-limited HTTP
  source_yc.py                 YC hiring directory + company pages
  source_ats.py                Greenhouse / Lever / Ashby boards
  source_linkedin.py           LinkedIn guest search
  source_internshala.py        Internshala internship categories
  source_naukri.py             Naukri via visible Playwright browser
  source_engg.py               engg.space MCP search
  source_gmafj.py              GiveMeAFuckingJ*b static catalogue
  source_hn.py                 Hacker News "Who is hiring?" thread
  source_hiringcafe.py         HiringCafe Next.js data route
  match.py                     rule-based 0-100 scoring
  level.py                     seniority detection from title and description
  digest.py                    daily Markdown digest
  export_excel.py              formatted jobs.xlsx
  latex.py                     Jake's Resume renderer + variants
  generate.py                  CLI for variants
  compile.py                   local engine or LaTeX.to
  latexto.py                   LaTeX.to client (headless Chromium)
  tailor.py                    single job-specific resume
  jobfile.py                   job from JSON or applications.db
  tailor_variants.py           three strategies + prompt packet
  verify.py                    fact-check overlays
  make_variants.py             CLI for multi-strategy tailoring
  pdfcheck.py                  page count + text-layer check
  layout.py                    PDF geometry checks
  ats.py                       keyword coverage (4 states)
  relevance.py                 relevance-weighted bullet cutting
  posting.py                   HTML/hidden-text cleaning + flags
  browser/
    __init__.py                backend selection, Session interface
    playwright_backend.py      own Chromium, iframes, uploads
    ego_backend.py             ego lite CLI backend
    flows.py                   ATS detection, cookies, scan helpers
  apply/
    __main__.py                apply CLI
    fields.py                  one-pass control scan + classification
    fill.py                    batched fill, no submit
    answers.py                 profile + answers merge
    runner.py                  one form, report, screenshots
    queue.py                   queued-job selector (library helper)
    bench.py                   local fill benchmark
  outreach/
    __main__.py                collect / draft / list / show / send
    collect.py                 three-pass address discovery
    compose.py                 render from config + verified facts
    send.py                    SMTP + IMAP drafts
    store.py                   contacts and outreach tables
  sync/
    __main__.py                --all / --gmail / --excel / --notion
    excel.py                   import human edits
    gmail.py                   IMAP reader
    intent.py                  status classification
    match_email.py             email -> job matching
    notion.py                  optional one-way publish
    store.py                   event storage
config/
  jobs.toml                    keywords, sources, YC settings, ATS boards
  browser.toml                 playwright / ego backend settings
  outreach.toml                email identity, skills, limits
  answers.toml                 form answers
  sync.toml                    Gmail, Notion, Excel, Sheets, SMTP
  models.toml                  model IDs for optional review
  profile.example.json         shape of a verified profile
resumes/jakes-resume.tex       shared preamble
data/                          SQLite DB, Excel, cache, resume, tailoring
reports/                       digests, apply, compile, tailoring, ATS
scripts/
  daily.ps1                    Task Scheduler entry point
  doctor.py                    what is configured and what is missing
  status.py                    tracker counts and shortlists
  rank.py                      re-score the backlog with level filters
  google.py                    Google token status / refresh
  push_tracker.py              push tracker to Google Sheets
  browser_smoke.py             prove a browser backend works
tests/                         141 tests: sources, matching, tailoring,
                               PDF/ATS, posting guard, apply, browser,
                               outreach, sync
```

---

## 14. Guardrails

- No LLM API calls in the pipeline; scoring and tailoring are local rules.
- No resume content is invented. Draft and verified profiles are separate.
- No automatic application submission. Form filling stops before Submit.
- Email is draft-first, and sending is a separate confirmed command.
- Job postings and inbound email are untrusted data, never instructions.
- No contact address is guessed from a name pattern.
- No cloud upload: LaTeX.to compiles in a local browser page.
- Secrets live in environment variables, never in tracked config.

---

## 15. Current State

Observed on 2026-11-05:

- `data/applications.db` holds **954 jobs**, all `new`, **0 scored**, because no
  verified `data/resume/profile.json` exists yet.
- By source: Arbeitnow 480, Internshala 182, LinkedIn 100, engg.space 91,
  Naukri 60, Himalayas 26, YC 8, Remotive 7.
- `reports/job-digest-2026-11-04.md` and `data/jobs.xlsx` exist.
- No local LaTeX engine is installed; compilation goes through LaTeX.to.
- `python scripts/doctor.py` reports 13 ok, 2 warn (app password missing, no
  applications yet), 1 missing (`profile.json`).
- **141 tests pass.**
- Playwright and ego lite are both available. The configured backend is
  Playwright.
- The Google Workspace MCP token is valid; the Sheets tracker is reachable.
