"""Fill every queued job in one run.

A job is queued when its tracker status is ``new`` or ``review`` and it has a
URL. Each job is opened once; if the stored URL is a posting page, an ATS link
on that page is followed before filling. A job is not retried after a completed
fill unless ``--retry`` is passed.

Nothing is submitted. Results are written to ``reports/apply/`` and a markdown
summary lists what is ready for your review.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import DATA, REPORTS
from .answers import AnswerBook
from .runner import RunReport, run_application, summary_markdown, write_report

APPLY_REPORTS = REPORTS / "apply"
DB = DATA / "applications.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS apply_attempts (
    job_id      TEXT PRIMARY KEY,
    url         TEXT,
    ats         TEXT,
    status      TEXT,
    filled      INTEGER,
    needs_human INTEGER,
    report      TEXT,
    attempted_at TEXT
)
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def select_queue(limit: int, min_score: float | None, retry: bool) -> list[dict]:
    query = """
        SELECT j.* FROM jobs j
        WHERE j.status IN ('new', 'review')
          AND j.url IS NOT NULL AND j.url != ''
    """
    params: list[object] = []
    if min_score is not None:
        query += " AND COALESCE(j.match_score, 0) >= ?"
        params.append(min_score)
    if not retry:
        query += " AND j.id NOT IN (SELECT job_id FROM apply_attempts WHERE status = 'filled')"
    query += " ORDER BY COALESCE(j.match_score, 0) DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def _record(job_id: str, report: RunReport, report_path: Path | None) -> None:
    counts = report.counts()
    status = "failed" if report.error else ("filled" if report.fields or report.submit_buttons else "no_form")
    with _connect() as conn:
        conn.execute(
            """INSERT INTO apply_attempts (job_id, url, ats, status, filled, needs_human, report, attempted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                 url=excluded.url, ats=excluded.ats, status=excluded.status,
                 filled=excluded.filled, needs_human=excluded.needs_human,
                 report=excluded.report, attempted_at=excluded.attempted_at""",
            (
                job_id,
                report.url,
                report.ats,
                status,
                counts.get("filled", 0) + counts.get("selected", 0) + counts.get("uploaded", 0) + counts.get("checked", 0),
                counts.get("needs_human", 0),
                str(report_path) if report_path else "",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )


def run_queue(
    book: AnswerBook,
    *,
    resume_path: Path | None = None,
    limit: int = 10,
    min_score: float | None = None,
    retry: bool = False,
    headless: bool = True,
    steps: int = 1,
    pause: float = 2.0,
) -> list[tuple[dict, RunReport, Path | None]]:
    jobs = select_queue(limit, min_score, retry)
    results: list[tuple[dict, RunReport, Path | None]] = []
    for index, job in enumerate(jobs, start=1):
        slug = str(job["id"]).replace(":", "-")[:60]
        print(f"[{index}/{len(jobs)}] {job['company']} — {job['title'][:70]}")
        report = run_application(
            job["url"],
            book,
            resume_path=resume_path,
            headless=headless,
            steps=steps,
            slug=slug,
            follow_apply=True,
        )
        path = write_report(report) if (report.fields or report.error or report.submit_buttons) else None
        _record(job["id"], report, path)
        counts = report.counts()
        print(
            f"    {report.ats}: filled {counts.get('filled', 0) + counts.get('selected', 0) + counts.get('uploaded', 0)}"
            f", needs human {counts.get('needs_human', 0)}"
            + (f", error {report.error}" if report.error else "")
        )
        results.append((job, report, path))
        if index < len(jobs):
            time.sleep(pause)
    return results


def queue_summary(results: list[tuple[dict, RunReport, Path | None]]) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Application queue — {stamp}", ""]
    ready = [r for r in results if not r[1].error and r[1].submit_buttons]
    failed = [r for r in results if r[1].error]
    empty = [r for r in results if not r[1].error and not r[1].fields and not r[1].submit_buttons]

    lines.append(f"- Filled and ready to submit: {len(ready)}")
    lines.append(f"- No application form found: {len(empty)}")
    lines.append(f"- Failed: {len(failed)}")
    lines.append("")

    if ready:
        lines += ["## Ready to submit", ""]
        for job, report, path in ready:
            counts = report.counts()
            needs = counts.get("needs_human", 0)
            lines.append(f"### {job['company']} — {job['title']}")
            lines.append(f"- Apply: {report.url}")
            lines.append(
                f"- Filled {sum(counts.get(k, 0) for k in ('filled', 'selected', 'uploaded', 'checked'))} controls"
                + (f", {needs} need a human" if needs else ", nothing outstanding")
            )
            if report.screenshots:
                lines.append(f"- Screenshot: {report.screenshots[-1]}")
            if path:
                lines.append(f"- Report: {path}")
            if needs:
                for item in report.unfilled_required:
                    lines.append(f"    - {item['label']}")
            lines.append("")

    if empty:
        lines += ["## No form found", ""]
        for job, _, _ in empty:
            lines.append(f"- {job['company']} — {job['title']}: {job['url']}")
        lines.append("")

    if failed:
        lines += ["## Failed", ""]
        for job, report, _ in failed:
            lines.append(f"- {job['company']} — {job['title']}: {report.error}")
        lines.append("")

    lines += ["---", "", "Nothing was submitted. Review each screenshot and press Submit yourself."]
    return "\n".join(lines)


def write_queue_summary(results: list[tuple[dict, RunReport, Path | None]]) -> Path:
    APPLY_REPORTS.mkdir(parents=True, exist_ok=True)
    path = APPLY_REPORTS / f"queue-{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(queue_summary(results), encoding="utf-8")
    return path
