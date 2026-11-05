"""CLI: fill one job application form and stop before submit.

    python -m resume_pipeline.apply --url https://boards.greenhouse.io/...
    python -m resume_pipeline.apply --job arbeitnow:... --url https://...
    python -m resume_pipeline.apply --url ... --headless --steps 3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from .. import DATA, RESUMES, ROOT
from ..browser import load_config
from .answers import load_answers
from .runner import run_application, summary_markdown, write_report

DEFAULT_PROFILE = DATA / "resume" / "profile.json"


def _job_from_db(job_id: str) -> dict | None:
    db = DATA / "applications.db"
    if not db.exists():
        return None
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def _find_resume(job_id: str | None) -> Path | None:
    if job_id:
        tailor = RESUMES / "generated" / "job-specific" / job_id
        if tailor.exists():
            pdfs = sorted(tailor.rglob("*.pdf"))
            if pdfs:
                return pdfs[0]
    for candidate in [RESUMES / "generated" / "master.pdf", *sorted((RESUMES / "generated").glob("*.pdf"))]:
        if candidate.exists():
            return candidate
    fallback = sorted((DATA / "resume").glob("*.pdf"))
    return fallback[0] if fallback else None


def _print_table(report) -> None:
    rows = report.fields
    if not rows:
        print("No form fields found on the page.")
        return
    width_label = min(42, max((len(r["label"]) for r in rows), default=10))
    print(f"\n{'FIELD'.ljust(width_label)}  {'KIND':9}  {'STATUS':11}  VALUE")
    print("-" * (width_label + 48))
    for r in rows:
        label = r["label"][:width_label]
        value = (r["value"] or r["detail"] or "")[:60]
        print(f"{label.ljust(width_label)}  {r['kind'][:9].ljust(9)}  {r['status'][:11].ljust(11)}  {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m resume_pipeline.apply", description=__doc__)
    parser.add_argument("--url", help="application form URL")
    parser.add_argument("--job", help="job id from data/applications.db (used for resume + status)")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--answers", type=Path, default=ROOT / "config" / "answers.toml")
    parser.add_argument("--resume-file", type=Path)
    parser.add_argument("--cover-letter", type=Path)
    parser.add_argument("--headless", action="store_true", help="run without a visible window")
    parser.add_argument(
        "--backend",
        choices=["playwright", "ego", "auto"],
        default=None,
        help="browser backend; defaults to config/browser.toml (playwright)",
    )
    parser.add_argument("--steps", type=int, default=1, help="fill up to N wizard steps (never submits)")
    parser.add_argument("--slow-mo", type=int, default=0, help="milliseconds between actions")
    parser.add_argument("--timeout", type=int, default=45, help="navigation timeout in seconds")
    parser.add_argument("--keep-open", action="store_true", help="leave the browser open for manual review")
    parser.add_argument("--no-widgets", action="store_true", help="skip custom dropdown widgets")
    parser.add_argument(
        "--mark",
        choices=["review", "applied"],
        help="update the job status after a successful fill",
    )
    args = parser.parse_args(argv)

    job = _job_from_db(args.job) if args.job else None
    url = args.url or (job or {}).get("url")
    if not url:
        parser.error("provide --url or --job with a stored URL")

    if not args.profile.exists():
        print(
            f"No verified profile at {args.profile}.\n"
            "The filler can still run, but it will only use config/answers.toml.\n"
        )

    book = load_answers(args.profile if args.profile.exists() else None, args.answers)
    if not book.answers and not book.questions:
        print("No answers configured. Copy config/answers.example.json to config/answers.toml first.", file=sys.stderr)
        return 2

    resume = args.resume_file or _find_resume(args.job)
    slug = (args.job or "application").replace(":", "-")[:60]

    report = run_application(
        url,
        book,
        resume_path=resume,
        cover_letter_path=args.cover_letter,
        headless=args.headless,
        steps=max(1, min(args.steps, 10)),
        slow_mo=args.slow_mo,
        timeout=args.timeout,
        slug=slug,
        keep_open=args.keep_open,
        allow_widgets=not args.no_widgets,
        backend=args.backend or load_config().backend,
    )

    print(f"\nATS: {report.ats} ({report.ats_note})")
    t = report.timings
    print(f"Timing: navigate {t.get('navigate', 0):.0f} ms | scan {t.get('scan', 0):.0f} ms | fill {t.get('fill', 0):.0f} ms | total {t.get('total', 0):.0f} ms")
    _print_table(report)

    counts = report.counts()
    print(f"\nSummary: {counts}")
    if report.error:
        print(f"Stopped: {report.error}")
    if report.unfilled_required:
        print("Needs a human:")
        for item in report.unfilled_required:
            print(f"  - {item['label']} ({item['kind']}, required)")
    if report.submit_buttons:
        print("Submit control found and left untouched: " + ", ".join(report.submit_buttons))

    path = write_report(report)
    md = path.with_suffix(".md")
    md.write_text(summary_markdown(report), encoding="utf-8")
    print(f"Report: {path}")

    if args.mark and args.job and not report.error:
        with sqlite3.connect(DATA / "applications.db") as conn:
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (args.mark, args.job))
        print(f"Job status set to '{args.mark}'.")

    return 0 if not report.error else 1


if __name__ == "__main__":
    raise SystemExit(main())
