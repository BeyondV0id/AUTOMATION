---
description: Show the strongest pending jobs with their job ids
---
Rank the current backlog.

1. Run `python scripts/status.py --top 15`.
   If `$ARGUMENTS` is a number, use that as the limit instead: `--top N`.
2. Present the rows as a compact table: score, company, title, location,
   source, job id. Keep the job id visible; the user needs it for `/tailor`.
3. Add one line: how many jobs are still pending overall
   (`python scripts/status.py`).

Do not open every posting. This is triage from stored scores only. If every
score is `-`, say that scoring had no profile to work with and point at
`data/resume/profile.json`. Once the profile exists, re-score the backlog with
`python scripts/rank.py`.
