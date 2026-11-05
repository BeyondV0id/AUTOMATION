---
description: Fill one application form and stop before submit
---
`$ARGUMENTS` is a job id or a posting URL. Resolve it first.

1. If it is a URL or a company name, run
   `python scripts/status.py --find "$ARGUMENTS"` and take the id.
2. Run:

```powershell
python -m resume_pipeline.apply --job "<id>" --steps 1 --keep-open
```

3. Report: the ATS detected, how many fields were filled, which required fields
   still need a human, and the report path under `reports/apply/`.
4. State plainly that nothing was submitted and the browser was left open for
   the user to review and press Submit.

Hard rule: never click a submit control, and never re-run with different flags
to get past a field you cannot answer. Ask the user instead.
