"""One command for the daily run: discover, digest, export.

    python run.py            # everything
    python run.py --fast     # skip YC company pages, feeds only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def step(name: str, args: list[str]) -> bool:
    print(f"\n== {name} ==", flush=True)
    started = time.time()
    result = subprocess.run([sys.executable, *args], cwd=ROOT)
    elapsed = time.time() - started
    if result.returncode != 0:
        print(f"!! {name} failed after {elapsed:.1f}s", flush=True)
        return False
    print(f"   done in {elapsed:.1f}s", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily job search run")
    parser.add_argument("--config", default="config/jobs.toml")
    parser.add_argument("--profile", default="config/profile.json")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--fast", action="store_true", help="Feeds only, no YC, ATS boards, or Naukri")
    parser.add_argument("--no-yc", action="store_true", help="Skip the YC directory")
    parser.add_argument("--no-ats", action="store_true", help="Skip Greenhouse/Lever/Ashby boards")
    parser.add_argument("--no-linkedin", action="store_true", help="Skip LinkedIn")
    parser.add_argument("--no-internshala", action="store_true", help="Skip Internshala")
    parser.add_argument("--no-engg", action="store_true", help="Skip the engg.space MCP search")
    parser.add_argument("--no-naukri", action="store_true", help="Skip Naukri (the browser source)")
    parser.add_argument("--no-gmafj", action="store_true", help="Skip GiveMeAFuckingJ*b")
    parser.add_argument("--no-hiringcafe", action="store_true", help="Skip HiringCafe (the browser source)")
    parser.add_argument("--no-hn", action="store_true", help="Skip Hacker News Who is hiring")
    parser.add_argument("--no-browser", action="store_true", help="Skip every source that needs a browser")
    parser.add_argument("--no-collect", action="store_true", help="Skip contact address collection")
    parser.add_argument("--collect-pages", action="store_true", help="Also read job and employer pages for addresses (slower)")
    args = parser.parse_args()

    config: dict = {}
    if not args.fast:
        config_path = ROOT / args.config
        if config_path.exists():
            import tomllib

            config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    discover_args = ["-m", "resume_pipeline.discover", "--config", args.config, "--profile", args.profile]
    if args.min_score is not None:
        discover_args += ["--min-score", str(args.min_score)]
    if args.fast:
        discover_args += ["--no-yc", "--no-ats", "--no-naukri", "--no-hiringcafe"]
    for flag, enabled in (
        ("--no-yc", args.no_yc),
        ("--no-ats", args.no_ats),
        ("--no-linkedin", args.no_linkedin),
        ("--no-internshala", args.no_internshala),
        ("--no-engg", args.no_engg),
        ("--no-naukri", args.no_naukri),
        ("--no-gmafj", args.no_gmafj),
        ("--no-hiringcafe", args.no_hiringcafe),
        ("--no-hn", args.no_hn),
        ("--no-browser", args.no_browser),
    ):
        if enabled and flag not in discover_args:
            discover_args.append(flag)

    if not step("discover", discover_args):
        return 1

    if not args.no_collect:
        collect_args = ["-m", "resume_pipeline.outreach", "collect"]
        if args.collect_pages:
            collect_args.append("--fetch-pages")
        # A missing address is not a failed run, so this step never aborts.
        step("contacts", collect_args)

    if not step("digest", ["-m", "resume_pipeline.digest"]):
        return 1
    if not step("excel", ["-m", "resume_pipeline.export_excel"]):
        return 1

    reports = ROOT / "reports"
    digest_files = sorted(reports.glob("job-digest-*.md"))
    if digest_files:
        print(f"\nLatest digest: {digest_files[-1]}")
    print(f"Excel sheet:   {ROOT / 'data' / 'jobs.xlsx'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
