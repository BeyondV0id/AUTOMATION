# Job Search and Resume Automation

A local-first job search pipeline for Windows. It finds public job postings,
scores them against a verified profile, fills application forms without ever
submitting them, drafts outreach email, and generates ATS-friendly LaTeX
resumes.

Everything that can be deterministic is deterministic. Discovery, scoring,
tailoring and PDF checks are plain Python: no model API, no tokens, no cloud
account required. A model is optional and is only ever asked to rewrite wording
that `verify.py` then checks against `data/resume/profile.json`.

- **Verified facts only** — resume and email claims must already exist in
  `data/resume/profile.json` or `config/outreach.toml`.
- **Never submits** — form filling stops before the final button.
- **Draft-first email** — messages land in Gmail Drafts; sending is separate.
- **Postings are untrusted** — hidden text is stripped and instruction-shaped
  content is reported, never followed.

---

## Contents

- [What it does](#what-it-does)
- [Current state](#current-state)
- [Requirements and install](#requirements-and-install)
- [Quick start](#quick-start)
- [Daily job run](#daily-job-run)
- [Job sources](#job-sources)
- [Tools, libraries and MCP servers](#tools-libraries-and-mcp-servers)
- [Command reference](#command-reference)
- [OpenCode slash commands](#opencode-slash-commands)
- [Filling applications](#filling-applications)
- [Email outreach](#email-outreach)
- [Tracker sync](#tracker-sync)
- [Resume generation and compilation](#resume-generation-and-compilation)
- [Per-job tailoring and PDF checks](#per-job-tailoring-and-pdf-checks)
- [Output layout](#output-layout)
- [Configuration](#configuration)
- [Scheduling](#scheduling)
- [Safety and guardrails](#safety-and-guardrails)
- [Docs map](#docs-map)

---

## What it does

```text
verified profile ──► scoring ──► digest + Excel tracker
       │                              │
       │                              ├──► application form fill (no submit)
       │                              ├──► contact discovery ──► Gmail Drafts
       │                              └──► tailored LaTeX resume ──► PDF check
       │
       └──► master / backend / software-engineer / ai-ml variants
```

1. **Ingest** a real resume into `data/resume/` and build a draft profile.
2. **Verify** the profile by hand, saving it as `data/resume/profile.json`.
3. **Discover** jobs from public feeds, ATS boards, and two browser sources.
4. **Score** every job locally with rules (role 30, skills 30, location 15,
   seniority 25). Postings that ask for more experience than the profile has are
   capped below eligible jobs.
5. **Track** jobs in SQLite and a formatted `data/jobs.xlsx`.
6. **Tailor** three resume angles per posting and fact-check every overlay.
7. **Fill** application forms and stop before submit.
8. **Draft** cold email into Gmail Drafts.
9. **Sync** status back from Excel edits and application mail.

## Current state

Checked on 2026-11-05 with `python scripts/doctor.py`:

| Item | State |
| --- | --- |
| Python | 3.13.5 (project requires 3.11+) |
| `data/resume/profile.json` | **missing** — only `profile.draft.json` exists |
| Tracked jobs | 954, all `new`, **0 scored** |
| Contact emails | 0 |
| Config files | `outreach.toml`, `sync.toml`, `answers.toml`, `browser.toml` present |
| Gmail sync | configured; `JOB_GMAIL_APP_PASSWORD` not set |
| Google Sheets tracker | configured, token valid |
| Browser backends | Playwright installed; ego lite detected |
| Local LaTeX engine | none; compilation uses LaTeX.to |
| Tests | **141 passing** |

Scoring, tailoring, form filling and resume generation stay blocked until a
reviewed `data/resume/profile.json` exists. Discovery and tracking work today.

## Requirements and install

- Windows, PowerShell.
- Python 3.11+ (3.13 is what this workspace runs).
- Run `python`, never `python3`.

Base install needs no third-party packages. Optional extras:

```powershell
pip install openpyxl                 # data/jobs.xlsx
pip install playwright               # browser sources and form filling
python -m playwright install chromium
pip install pypdf pymupdf            # PDF text layer and geometry checks
```

Or install from `pyproject.toml`:

```powershell
pip install -e ".[excel,browser,pdf]"
```

No TeX distribution is needed. `compile.py` uses a local engine if one exists,
otherwise it compiles through LaTeX.to (TeX Live in WebAssembly inside headless
Chromium, with no document upload).

## Quick start

```powershell
# 1. Build a draft profile from the real resume
python -m resume_pipeline.ingest --resume "data/resume/g_harsha_resume.pdf"

# 2. Review data/resume/profile.draft.json, fill in real experience, skills,
#    bullets and education, then save it as data/resume/profile.json.
#    The draft is never used for generation.

# 3. Generate and compile the four standard variants
python -m resume_pipeline.generate --profile data/resume/profile.json --variant all
python -m resume_pipeline.compile --all

# 4. Discover and score jobs
python run.py
```

Check what is configured at any time:

```powershell
python scripts/doctor.py
python scripts/status.py
```

## Daily job run

```powershell
python run.py                 # all sources, contacts, digest, Excel
python run.py --fast          # public feeds only
python run.py --no-browser    # skip Naukri and HiringCafe
python run.py --no-naukri     # keep HiringCafe only
python run.py --min-score 60  # keep only strong matches in the digest
python run.py --collect-pages # also read employer pages for contact addresses
```

Each run:

1. Collects jobs from the sources below.
2. Scores each job locally with rules. No API call, no model, no tokens.
3. Collects contact addresses from posting text.
4. Writes `reports/job-digest-YYYY-MM-DD.md`.
5. Rewrites `data/jobs.xlsx`, sorted by match score.

Edit the `Status` column in `data/jobs.xlsx` (`new`, `review`, `applied`,
`interview`, `offer`, `rejected`). Statuses live in SQLite, so regenerating the
sheet never overwrites your tracking.

## Job sources

Eleven source types, all public and unauthenticated:

| Source | Access | Browser? | Notes |
| --- | --- | --- | --- |
| Arbeitnow | Public JSON API | No | `data` array |
| Remotive | Public remote-jobs JSON API | No | `jobs` array |
| Himalayas | Public jobs JSON API | No | `jobs` array |
| LinkedIn | Logged-out guest search HTML | No | 10 results per page; may throttle |
| Internshala | Server-rendered category pages | No | Internships; stipend and duration |
| Naukri | Rendered Chromium page | **Yes, headful** | Headless gets "Access Denied" |
| GiveMeAFuckingJ\*b | Static JSON catalogue (`/jobs/index.txt`, `/jobs/<chunk>.json`) | No | Catalogue lags about a month |
| HiringCafe | Next.js `classic.json` data route through Chromium | **Yes, headful** | Cloudflare challenge |
| engg.space | Public **MCP** server, `search_jobs` over JSON-RPC | No | Returns employer apply URLs |
| Y Combinator | `yc-oss` company mirror + public YC company pages | No | Mirror has companies, not postings |
| Greenhouse / Lever / Ashby | Public board JSON | No | Add board slugs in `config/jobs.toml` |

Naukri and HiringCafe must run in a logged-in interactive desktop session.
Use `--no-browser` or `--fast` on a headless machine.

Source selection and keyword/location filters live in `config/jobs.toml`.
Every source feeds through `net.py`, which keeps a disk cache in `data/cache/`
and spaces requests 1.5 seconds apart per host.

Full details, including endpoints and returned fields, are in
[`docs/TOOLS_AND_SOURCES.md`](docs/TOOLS_AND_SOURCES.md).

## Tools, libraries and MCP servers

| Tool | Kind | Used for |
| --- | --- | --- |
| Python 3.11+ standard library | Runtime | Whole pipeline |
| `openpyxl` | Library | Read/write `data/jobs.xlsx` |
| Playwright (Python) | Library | Naukri, HiringCafe, form filling |
| `pypdf` | Library | PDF text layer, ATS keyword extraction |
| `PyMuPDF` (`fitz`) | Library | PDF geometry and layout checks |
| LaTeX.to | Remote WASM compiler | PDF output when no local TeX exists |
| Gmail SMTP + IMAP | Protocol | Save drafts, optional send, mail sync |
| Google Sheets API | REST | Push tracker to the Sheets copy |
| Notion API | REST | Optional one-way tracker view |
| **engg.space MCP** | MCP server (remote) | Job discovery from engineering career boards |
| **Google Workspace MCP** | MCP server (local, `workspace-mcp`) | Gmail drafts and Sheets, via OAuth |
| **Playwright MCP** | MCP server (host) | Interactive browser automation outside the pipeline |
| **ego lite / `ego-browser`** | Host CLI | Alternative supervised browser backend |

The pipeline itself calls **one MCP** — engg.space — from
`resume_pipeline/source_engg.py`. The Google Workspace MCP is configured in
OpenCode with `gmail:drafts` and `sheets:full` scopes; it deliberately cannot
send mail. Sending, when used, happens through local SMTP with the app password.

## Command reference

### Jobs and tracking

```powershell
python run.py
python scripts/status.py
python scripts/status.py --top 15
python scripts/status.py --find acme
python scripts/rank.py                     # re-score the backlog, show top 20
python scripts/rank.py --level internship,fresher --min-score 50
python scripts/rank.py --explain <job-id>
python -m resume_pipeline.discover --config config/jobs.toml
python -m resume_pipeline.digest
python -m resume_pipeline.export_excel
python -m resume_pipeline.sync --all
python -m resume_pipeline.sync --gmail --excel
python scripts/push_tracker.py --dry-run
```

### Resumes

```powershell
python -m resume_pipeline.ingest --resume C:\path\to\resume.pdf
python -m resume_pipeline.generate --profile data/resume/profile.json --variant all
python -m resume_pipeline.compile --all
python -m resume_pipeline.compile --all --remote    # force LaTeX.to
python -m resume_pipeline.compile --all --local     # refuse LaTeX.to
python -m resume_pipeline.make_variants --job-id "<job-id>"
python -m resume_pipeline.tailor --job-id "<job-id>"
```

### Applications and email

```powershell
python -m resume_pipeline.apply --job "<job-id>" --steps 1 --keep-open
python -m resume_pipeline.apply --url https://boards.greenhouse.io/... --headless

python -m resume_pipeline.outreach collect --limit 100
python -m resume_pipeline.outreach collect --limit 100 --fetch-pages
python -m resume_pipeline.outreach contacts
python -m resume_pipeline.outreach draft --limit 10
python -m resume_pipeline.outreach list
python -m resume_pipeline.outreach show <job-id>
```

### Diagnostics and tests

```powershell
python scripts/doctor.py
python scripts/google.py status
python scripts/google.py refresh
python scripts/browser_smoke.py --url https://example.com
python -m resume_pipeline.apply.bench
python -m pytest -q
```

## OpenCode slash commands

| Command | Does |
| --- | --- |
| `/scrape` | Discover jobs, collect contacts, rebuild digest and Excel |
| `/rank` | Strongest pending jobs, with the job ids `/tailor` needs |
| `/tailor <job-id or url>` | Three tailored resume variants, compiled and inspected |
| `/apply <job-id or url>` | Fill one application form, stop before submit |
| `/outreach` | Collect company contact addresses |
| `/draft [n]` | Render cold emails into Gmail Drafts |
| `/sync` | Import Excel edits and read application mail |
| `/status` | Tracker counts |
| `/setup` | What is configured, what is missing, next command |

Commands live in `.opencode/commands/`. Shared rules live in
`.opencode/skills/job-search/SKILL.md`. `AGENTS.md` points at the source files
instead of repeating them.

## Filling applications

`apply` reads the page in one DOM pass, classifies every control, and fills it
from `config/answers.toml` plus the verified profile. It handles text inputs,
native selects, custom comboboxes, radios, checkboxes and file uploads.

```powershell
python -m resume_pipeline.apply --job "<job-id>" --steps 1 --keep-open
```

Supported ATS families include Greenhouse, Lever, Ashby, Workday,
SmartRecruiters, Workable, Breezy, Recruitee, Teamtailor, Jobvite, iCIMS,
Taleo, Personio, Rippling, JazzHR and BambooHR. Greenhouse and Lever forms sit
inside cross-origin iframes, which is why the `playwright` backend is required
for filling.

The final Submit button is never clicked. Results, screenshots and the list of
fields that still need a human land in `reports/apply/`.

`config/browser.toml` picks the backend:

- `playwright` — own Chromium, persistent profile, can read iframes and upload
  files. Used for unattended form filling.
- `ego` — drives the signed-in ego lite browser through `ego-browser`, so every
  click is visible. One document at a time.
- `auto` — Playwright when installed, otherwise ego.

## Email outreach

Discovery collects contact addresses, and `draft` turns them into Gmail
Drafts. Nothing is sent automatically.

```powershell
python -m resume_pipeline.outreach collect --limit 100 --fetch-pages
python -m resume_pipeline.outreach draft --limit 10
python -m resume_pipeline.outreach list
```

Addresses come from three places, in order, and are never guessed from a name
pattern:

1. The `Contact Email` column in `data/jobs.xlsx`.
2. Addresses in the posting text, ranked so `careers@` beats `support@`.
3. Job and employer `/contact` and `/careers` pages with `--fetch-pages`.

Gmail's hard cap is 500 recipients per day for a free account. Defaults stay
well below it: 20 per day, one per company, 20 seconds apart. `send` requires
`--yes` or an interactive confirmation and only touches rows already stored as
`draft`.

Setup:

1. Turn on 2-Step Verification.
2. Google Account → Security → App passwords → create one for Mail.
3. Set the password in the environment, never in a config file:

```powershell
$env:JOB_GMAIL_APP_PASSWORD = "abcd efgh ijkl mnop"
setx JOB_GMAIL_APP_PASSWORD "abcd efgh ijkl mnop"
```

4. Fill `config/outreach.toml` and `config/answers.toml` from verified data.

## Tracker sync

```powershell
python -m resume_pipeline.sync --all
```

- **Excel** — imports `Status`, `Notes` and `Contact Email` edits. Human edits
  win over database values.
- **Gmail** — reads application mail over IMAP for 21 days, classifies intent
  (`interview`, `offer`, `rejected`), and moves a status only when confidence
  clears the threshold.
- **Notion** — optional, one-way publish. Disabled until a database is shared
  with the integration.
- **Sheets** — `python scripts/push_tracker.py` copies the tracker into the
  Google Sheet, preserving hand-edited columns and sheet-only rows.

## Resume generation and compilation

Every `resume.tex` uses Jake's Resume layout: `\documentclass[letterpaper,11pt]{article}`,
a shared preamble, then `\resumeSubheading`, `\resumeProjectHeading` and
`\resumeItem` blocks. The preamble is copied beside each source, so every folder
compiles unchanged in Overleaf.

Standard variants:

- `master`
- `backend`
- `software-engineer`
- `ai-ml`

Role tags on experience and project records control filtering. Untagged
records stay included, so verified information is never silently dropped.

`compile.py` picks a compiler in this order:

1. Local `latexmk`, `pdflatex`, `xelatex`, `lualatex` or `tectonic`.
2. LaTeX.to, which runs TeX Live in WebAssembly inside headless Chromium. The
   document is not uploaded; only the site and TeX blocks are downloaded.

A result only counts as passed when the PDF exists and starts with `%PDF-`.
Details land in `reports/compile-report.json`.

## Per-job tailoring and PDF checks

`make_variants` produces three versions of one resume:

| Strategy | What changes |
| --- | --- |
| `ats-match` | Mirrors posting vocabulary; skills ordered by mention frequency |
| `impact-led` | Measurable bullets first; three or four bullets per role |
| `role-fit` | Closest roles and projects first; summary targets the role |

```powershell
python -m resume_pipeline.make_variants --job-id "<job-id>" --compile --fit-pages 1 --inspect --ats-report
```

- `--fit-pages N` compiles, reads the page count, and removes the lowest-value
  bullet until the PDF fits. Relevance, metrics, vocabulary uniqueness and lead
  position decide value. The last bullet of a record is protected.
- `--inspect` checks the text layer and geometry: pages, contact strings,
  garbled glyphs, non-ASCII date ranges, holes, early endings, footer
  collisions, orphaned headings.
- `--ats-report` extracts PDF text like an ATS and marks each posting keyword
  `covered`, `synonym-only`, `missing (have it)` or `missing (gap)`. Only
  `missing (have it)` is actionable. A gap is never stuffed into a resume.

`posting.py` strips HTML, script/style blocks, hidden nodes and invisible
Unicode before scoring or tailoring, and reports instruction-shaped text. The
report is written under `posting`; it is never followed.

Date ranges use an ASCII hyphen (`2022 - 2024`), not a LaTeX en dash, because
some ATS imports lose non-ASCII separators.

## Output layout

```text
data/resume/                 source copy, extracted text, draft and verified profile
data/applications.db         jobs, contacts, applications, outreach, email events
data/jobs.xlsx               Excel tracker, sorted by match score
data/cache/                  HTTP cache for job sources
data/browser-profile/        Playwright persistent profile
data/tailoring/<job>/        overlays, prompts and fitted overlays
resumes/jakes-resume.tex     shared preamble
resumes/generated/           master, role variants, job-specific sources and PDFs
reports/job-digest-*.md      daily digest
reports/apply/               application fill reports and screenshots
reports/compile-report.json  compiler results
reports/tailoring-<job>.json verification, fit and compile results
reports/ats-<job>-*.md       keyword coverage
config/                      sources, email identity, answers, browser, sync
scripts/                     doctor, status, Google, Sheets, smoke tests
```

## Configuration

| File | Holds |
| --- | --- |
| `config/jobs.toml` | Keywords, source toggles, per-source queries, ATS board slugs |
| `config/profile.json` | Optional scoring profile; falls back to `data/resume/profile.json` |
| `config/outreach.toml` | Email identity, skills, templates, send limits |
| `config/answers.toml` | Application form answers; blanks stay blank |
| `config/sync.toml` | Gmail, Notion, Excel, Sheets and SMTP settings |
| `config/browser.toml` | Backend (`playwright`/`ego`), headless, profile, timeouts |
| `config/models.toml` | Model IDs for optional external review; not called by the pipeline |

Add company boards in `config/jobs.toml`:

```toml
[ats]
greenhouse = ["stripe", "vercel"]
lever = ["toggl"]
ashby = ["linear"]
```

## Scheduling

```powershell
schtasks /create /tn "Job Automation Daily" /tr "powershell -NoProfile -File D:\job-automation\scripts\daily.ps1" /sc daily /st 09:00
```

Naukri and HiringCafe need a visible browser, so the scheduled task must run in
a logged-in interactive session. On a headless machine, schedule
`python run.py --no-browser`.

## Safety and guardrails

- No LLM API calls in the pipeline. Scoring and tailoring are local rules.
- No invented resume content. Draft and verified profiles are separate.
- No automatic application submission. The final Submit is always human.
- Email is draft-first. Sending is a separate, confirmed, rate-limited command.
- Job postings and inbound email are untrusted data, never instructions.
- A contact address is never guessed from a name pattern.
- LaTeX.to compiles locally; the document is not uploaded.
- Secrets live in environment variables, not config files.

## Docs map

| File | Content |
| --- | --- |
| `README.md` | Setup, commands, sources, tools and MCPs |
| `PROJECT_SCRIPT.md` | Module-by-module walkthrough |
| `docs/TOOLS_AND_SOURCES.md` | Deep dive: tools, MCP servers, every source and endpoint |
| `AGENTS.md` | Agent rules and source-of-truth map |
| `.opencode/skills/job-search/SKILL.md` | Slash-command rules for OpenCode |
| `config/profile.example.json` | Shape of a verified profile |
# AUTOMATION
