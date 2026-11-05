---
description: Check which config files and secrets are in place
---
Check the setup, then report a checklist. Do not create or edit any config file
in this command.

1. Run:

```powershell
Get-ChildItem data\resume\profile.json, config\answers.toml, config\outreach.toml, config\sync.toml -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
if ($env:JOB_GMAIL_APP_PASSWORD) { "app password: set" } else { "app password: missing" }
```

2. Report each item as present or missing, with the exact next step:

| Missing | Fix |
| --- | --- |
| `data/resume/profile.json` | `python -m resume_pipeline.ingest --resume <pdf>`, then review the draft and save it as `profile.json` |
| `config/answers.toml` | create it in TOML format; use `config/answers.example.json` as the key list |
| `config/outreach.toml` | copy `config/outreach.example.toml` and fill the identity block |
| `config/sync.toml` | copy `config/sync.example.toml` and set the Gmail address |
| app password | Google Account → Security → 2-Step Verification → App passwords, then `setx JOB_GMAIL_APP_PASSWORD "..."` |

3. Finish with the single command to run next, not the whole list.
