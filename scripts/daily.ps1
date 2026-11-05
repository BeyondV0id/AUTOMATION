Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Full daily run: public feeds, YC hiring companies, ATS boards, digest, Excel.
# Use `python run.py --fast` for feeds only.
python run.py @args
