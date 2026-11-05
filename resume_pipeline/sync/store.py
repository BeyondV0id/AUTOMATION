"""Tracker storage shared by Gmail sync, Excel import, and Notion publish.

`jobs.status` stays the single source of truth. Every email is recorded in
`email_events` even when it changes nothing, so a wrong decision is auditable.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import DATA
from .intent import RANK, next_status

DB = DATA / "applications.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY,
    status TEXT,
    applied_at TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS email_events (
    message_id      TEXT PRIMARY KEY,
    received_at     TEXT,
    sender          TEXT,
    subject         TEXT,
    job_id          TEXT,
    intent          TEXT,
    confidence      REAL,
    previous_status TEXT,
    new_status      TEXT,
    applied         INTEGER DEFAULT 0,
    reason          TEXT,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_events_job ON email_events(job_id);
CREATE TABLE IF NOT EXISTS notion_pages (
    job_id    TEXT PRIMARY KEY,
    page_id   TEXT,
    synced_at TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: Path | None = None):
    """Commit on success, always close (Windows keeps the file locked otherwise)."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_job(job_id: str, path: Path | None = None) -> dict | None:
    with session(path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def all_jobs(path: Path | None = None) -> list[dict]:
    with session(path) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM jobs")]


def has_event(message_id: str, path: Path | None = None) -> bool:
    if not message_id:
        return False
    with session(path) as conn:
        return conn.execute("SELECT 1 FROM email_events WHERE message_id = ?", (message_id,)).fetchone() is not None


def set_status(job_id: str, status: str, path: Path | None = None) -> None:
    """Update the job and keep the legacy applications row consistent."""
    with session(path) as conn:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        applied_at = now() if status == "applied" else None
        conn.execute(
            """INSERT INTO applications (job_id, status, applied_at, notes) VALUES (?, ?, ?, NULL)
               ON CONFLICT(job_id) DO UPDATE SET
                 status = excluded.status,
                 applied_at = COALESCE(applications.applied_at, excluded.applied_at)""",
            (job_id, status, applied_at),
        )


def record_event(
    *,
    message_id: str,
    received_at: str,
    sender: str,
    subject: str,
    job_id: str | None,
    intent: str,
    confidence: float,
    previous_status: str,
    proposed_status: str,
    applied: bool,
    reason: str,
    path: Path | None = None,
) -> None:
    with session(path) as conn:
        conn.execute(
            """INSERT INTO email_events
               (message_id, received_at, sender, subject, job_id, intent, confidence,
                previous_status, new_status, applied, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO NOTHING""",
            (
                message_id, received_at, sender, subject, job_id, intent, confidence,
                previous_status, proposed_status, int(applied), reason, now(),
            ),
        )


def apply_intent(
    job: dict,
    intent_name: str,
    confidence: float,
    *,
    threshold: float,
    dry_run: bool = False,
    path: Path | None = None,
) -> tuple[str, bool, str]:
    """Return (proposed_status, changed, reason)."""
    current = job.get("status") or "new"
    proposed = next_status(current, intent_name)
    if proposed == current:
        return current, False, "no change"
    if confidence < threshold:
        return proposed, False, f"confidence {confidence:.2f} below {threshold:.2f}"
    if dry_run:
        return proposed, False, "dry run"
    set_status(job["id"], proposed, path)
    return proposed, True, "status updated"


def notion_page(job_id: str, path: Path | None = None) -> str | None:
    with session(path) as conn:
        row = conn.execute("SELECT page_id FROM notion_pages WHERE job_id = ?", (job_id,)).fetchone()
    return row["page_id"] if row else None


def save_notion_page(job_id: str, page_id: str, path: Path | None = None) -> None:
    with session(path) as conn:
        conn.execute(
            """INSERT INTO notion_pages (job_id, page_id, synced_at) VALUES (?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET page_id = excluded.page_id, synced_at = excluded.synced_at""",
            (job_id, page_id, now()),
        )


def status_counts(path: Path | None = None) -> dict[str, int]:
    with session(path) as conn:
        return {row["status"]: row["n"] for row in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
