"""Verified-data job discovery and LaTeX resume generation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESUMES = ROOT / "resumes"
REPORTS = ROOT / "reports"

for _path in (DATA / "resume", DATA / "jobs", ROOT / "resumes" / "generated", REPORTS):
    _path.mkdir(parents=True, exist_ok=True)
