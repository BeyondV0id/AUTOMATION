---
description: Build tailored resume variants for one job and verify the PDF
---
`$ARGUMENTS` is a job id or a posting URL. Resolve it first.

1. If `$ARGUMENTS` looks like a URL or a company name, run
   `python scripts/status.py --find "$ARGUMENTS"` and take the id from the best
   match. If several match, list them and ask which one.
2. Run:

```powershell
python -m resume_pipeline.make_variants --job-id "<id>" --compile --fit-pages 1 --inspect --ats-report
```

3. Report the three strategy folders, the compiled PDF paths, the page count,
   the inspection result, and the ATS keyword summary.
4. Only report `missing (have it)` keywords as actionable. Never add a
   `missing (gap)` keyword to a resume: that would be a fabricated claim.

If compilation fails, show the compiler output and stop. Do not hand-edit the
generated `.tex` to force a pass.
