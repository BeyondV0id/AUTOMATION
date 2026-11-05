---
description: Discover new jobs, collect contacts, rebuild the digest and Excel
---
Run the daily pipeline in the project root.

1. Run `python run.py $ARGUMENTS`.
   - No arguments: every source.
   - `--fast`: public feeds only, no YC, ATS boards or Naukri.
   - `--no-naukri`: use on a headless machine; Naukri needs a visible window.
   - `--no-browser`: skip Naukri and HiringCafe together.
   - `--collect-pages`: also read employer pages for contact addresses (slower).
   - `--min-score 60`: keep only strong matches in the digest.
2. Read the last 30 lines of output. If a step fails, show the failing command
   and its error, then stop. Do not retry silently and do not edit the database.
3. Report in four lines: jobs added, contacts found, digest path, Excel path.

If Naukri is blocked, say so and suggest `--no-naukri`; do not treat it as a
failure of the whole run.
