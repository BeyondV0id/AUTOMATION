---
description: Render cold emails and save them to Gmail Drafts
---
Draft outreach. Nothing is sent.

1. If `config/outreach.toml` does not exist, stop and say:
   "Copy `config/outreach.example.toml` to `config/outreach.toml` and fill the
   identity block first." Do not create it with placeholder values.
2. Run `python -m resume_pipeline.outreach draft --limit 10`.
   If `$ARGUMENTS` is a number, use that as the limit instead: `--limit N`.
3. Run `python -m resume_pipeline.outreach list` and show the drafts.
4. Tell the user the drafts are in Gmail Drafts, ready to read and send from
   Gmail. Sending from here (`outreach send`) needs an explicit `--yes`.

If the command reports that the identity is incomplete or the app password is
missing, report exactly which one is missing. Never fill in a sender address or
name on the user's behalf.
