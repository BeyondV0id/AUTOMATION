---
description: Tracker counts and email status
---
Show the current state.

1. Run `python scripts/status.py`.
2. Report one line each for: tracked, applied, interviewing, offers, rejected,
   pending, and email drafts/sent/failed.
3. If the user asked for detail, follow with `python scripts/status.py --top 10`.

This is a read-only view. Do not change any status here; statuses come from the
Excel sheet, Gmail sync, or a sent application.
