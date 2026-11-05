---
description: Collect company contact addresses into the contact list
---
Build or extend the contact list.

1. Run `python -m resume_pipeline.outreach collect --fetch-pages --limit 50`.
   Add `--min-score 40` if the backlog is large and only strong matches matter.
2. Run `python -m resume_pipeline.outreach contacts --limit 30` to show what
   was found.
3. Report honestly: how many addresses came from posting text, how many from
   employer pages, and how many jobs still have none.

Do not invent an address from a name pattern. A guessed address bounces and
damages the sending account. If coverage is low, say so and suggest adding
target companies under `[ats]` in `config/jobs.toml`, which at least yields
employer domains.
