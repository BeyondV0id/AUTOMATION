"""Drive one application form end to end, stopping before submit.

The browser comes from :mod:`resume_pipeline.browser`, so the backend is a
setting rather than an import. Filling still needs the playwright backend: it
is the only one that reads inside the cross-origin iframes Greenhouse and Lever
build their forms out of.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import REPORTS
from ..browser import BrowserError, open_session
from ..browser.flows import (
    ATS_PATTERNS,
    click_next,
    detect_ats,
    dismiss_cookies,
    find_submit_buttons,
    nudge_scroll,
)
from .answers import AnswerBook
from .fields import Field, scan
from .fill import FillResult, fill_form, find_unfilled_required

APPLY_REPORTS = REPORTS / "apply"

__all__ = [
    "ATS_PATTERNS",
    "RunReport",
    "detect_ats",
    "run_application",
    "summary_markdown",
    "write_report",
]


@dataclass
class RunReport:
    url: str
    ats: str
    ats_note: str
    started: str
    timings: dict[str, float] = dc_field(default_factory=dict)
    fields: list[dict[str, Any]] = dc_field(default_factory=list)
    unfilled_required: list[dict[str, Any]] = dc_field(default_factory=list)
    submit_buttons: list[str] = dc_field(default_factory=list)
    screenshots: list[str] = dc_field(default_factory=list)
    steps: int = 1
    resume: str = ""
    backend: str = "playwright"
    error: str = ""

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.fields:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "ats": self.ats,
            "ats_note": self.ats_note,
            "started": self.started,
            "backend": self.backend,
            "steps": self.steps,
            "resume": self.resume,
            "timings_ms": {k: round(v, 1) for k, v in self.timings.items()},
            "counts": self.counts(),
            "fields": self.fields,
            "unfilled_required": self.unfilled_required,
            "submit_buttons": self.submit_buttons,
            "screenshots": self.screenshots,
            "error": self.error,
        }


def _screenshot(session, slug: str, index: int) -> str:
    APPLY_REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = APPLY_REPORTS / f"{slug}-{stamp}-step{index}.png"
    try:
        return session.screenshot(path, full_page=True)
    except Exception:
        return ""


def run_application(
    url: str,
    book: AnswerBook,
    *,
    resume_path: Path | None = None,
    cover_letter_path: Path | None = None,
    headless: bool = True,
    steps: int = 1,
    slow_mo: int = 0,
    timeout: int = 45,
    slug: str = "application",
    keep_open: bool = False,
    allow_widgets: bool = True,
    backend: str = "playwright",
) -> RunReport:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = RunReport(url=url, ats="unknown", ats_note="", started=started, backend=backend)
    report.resume = str(resume_path) if resume_path else ""

    t0 = time.perf_counter()
    session = None
    try:
        session = open_session(
            backend=backend, headless=headless, slow_mo=slow_mo, timeout_ms=timeout * 1000
        )
        if session.kind != "playwright":
            raise BrowserError(
                "form filling needs backend='playwright'. It is the only backend that reads "
                "inside cross-origin iframes, which Greenhouse and Lever forms use. To watch a "
                "run, set headless=false instead of switching backends."
            )

        page = session.page
        t = time.perf_counter()
        session.goto(url, timeout * 1000)
        nudge_scroll(session, 2)
        report.timings["navigate"] = (time.perf_counter() - t) * 1000
        report.ats, report.ats_note = detect_ats(session.url() or url, session)
        dismiss_cookies(session)

        total_scan = total_fill = 0.0
        all_results: list[FillResult] = []
        for step_index in range(1, max(1, steps) + 1):
            t = time.perf_counter()
            fields = scan(page)
            total_scan += (time.perf_counter() - t) * 1000

            t = time.perf_counter()
            results = fill_form(
                page,
                fields,
                book,
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
                allow_widgets=allow_widgets,
            )
            total_fill += (time.perf_counter() - t) * 1000
            all_results.extend(results)
            report.steps = step_index

            shot = _screenshot(session, slug, step_index)
            if shot:
                report.screenshots.append(shot)

            if step_index < steps and click_next(session):
                continue
            break

        report.timings["scan"] = total_scan
        report.timings["fill"] = total_fill
        report.timings["total"] = (time.perf_counter() - t0) * 1000
        report.fields = [r.as_dict() for r in all_results]
        report.unfilled_required = [
            f.describe() for f in find_unfilled_required(scan(page), all_results)
        ]
        report.submit_buttons = find_submit_buttons(session)

        # Final screenshot with every field in its filled state.
        shot = _screenshot(session, slug, 99)
        if shot:
            report.screenshots.append(shot)

        if keep_open:
            print("\nForm filled. Browser stays open. Press Enter to close it (nothing is submitted).")
            input()
    except Exception as exc:  # noqa: BLE001 - surface into the report
        report.error = f"{type(exc).__name__}: {exc}"
        report.timings.setdefault("total", (time.perf_counter() - t0) * 1000)
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    return report


def write_report(report: RunReport, directory: Path | None = None) -> Path:
    directory = directory or APPLY_REPORTS
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", report.url.lower())[-40:].strip("-")
    path = directory / f"apply-{safe}-{stamp}.json"
    path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    return path


def summary_markdown(report: RunReport) -> str:
    counts = report.counts()
    lines = [
        f"# Application fill: {report.ats}",
        "",
        f"- URL: {report.url}",
        f"- Backend: {report.backend}",
        f"- Steps filled: {report.steps}",
        f"- Scan: {report.timings.get('scan', 0):.0f} ms | Fill: {report.timings.get('fill', 0):.0f} ms | Total: {report.timings.get('total', 0):.0f} ms",
        f"- Result: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
    ]
    if report.error:
        lines += ["## Stopped", f"- {report.error}", ""]
    if report.unfilled_required:
        lines.append("## Needs a human")
        for item in report.unfilled_required:
            lines.append(f"- {item['label']} ({item['kind']}, required)")
        lines.append("")
    if report.submit_buttons:
        lines.append("## Submit found, not pressed")
        lines += [f"- {label}" for label in report.submit_buttons]
        lines.append("")
    return "\n".join(lines)
