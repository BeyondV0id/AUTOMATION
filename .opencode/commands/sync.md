---
description: Import Excel edits and read application mail from Gmail
---
Sync the tracker in both directions.

1. Run `python -m resume_pipeline.sync --all`.
2. Report each section separately: Excel import (status, note, contact edits),
   Gmail (messages read, status changes, suggestions, unmatched), and the
   Excel export row count.
3. For every status change, show company, old status, new status, and the
   intent confidence. Do not hide low-confidence suggestions.

Gmail requires `[gmail].address` in `config/sync.toml` and
`JOB_GMAIL_APP_PASSWORD` in the environment. If either is missing, say which one
and skip Gmail without failing the rest of the sync. Excel edits always win.
