"""Storage for outgoing email.

One row per (job, kind). A draft is rendered once and then reused, so a second
``draft`` run never silently rewrites text you already read. ``--force`` is the
only way to replace one.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .. import DATA

DB = DATA / "applications.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'application',
    to_addr    TEXT NOT NULL,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft',
    message_id TEXT,
    error      TEXT,
    created_at TEXT,
    sent_at    TEXT,
    UNIQUE(job_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach(status);

-- One row per address found while scraping. A company can appear more than once.
CREATE TABLE IF NOT EXISTS contacts (
    email    TEXT PRIMARY KEY,
    company  TEXT,
    domain   TEXT,
    job_id   TEXT,
    source   TEXT,
    found_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company);
"""

# Columns added to `jobs` after the table already existed in the wild.
JOB_COLUMNS = {"contact_email": "TEXT"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "jobs" in tables:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for name, kind in JOB_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
    conn.commit()
    return conn


@contextmanager
def session(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_schema(path: Path | None = None) -> None:
    with session(path):
        pass


def existing(job_id: str, kind: str, path: Path | None = None) -> dict | None:
    with session(path) as conn:
        row = conn.execute(
            "SELECT * FROM outreach WHERE job_id = ? AND kind = ?", (job_id, kind)
        ).fetchone()
    return dict(row) if row else None


def save_draft(
    *,
    job_id: str,
    kind: str,
    to_addr: str,
    subject: str,
    body: str,
    force: bool = False,
    path: Path | None = None,
) -> bool:
    """Insert a draft. Returns False when one already exists and force is off."""
    with session(path) as conn:
        current = conn.execute(
            "SELECT status FROM outreach WHERE job_id = ? AND kind = ?", (job_id, kind)
        ).fetchone()
        # A sent message is never turned back into a draft, even with --force.
        if current and current["status"] == "sent":
            return False
        if current and not force:
            return False
        conn.execute(
            """INSERT INTO outreach (job_id, kind, to_addr, subject, body, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'draft', ?)
               ON CONFLICT(job_id, kind) DO UPDATE SET
                 to_addr = excluded.to_addr,
                 subject = excluded.subject,
                 body = excluded.body,
                 status = 'draft',
                 error = NULL,
                 created_at = excluded.created_at""",
            (job_id, kind, to_addr, subject, body, now()),
        )
    return True


def mark_sent(job_id: str, kind: str, message_id: str, path: Path | None = None) -> None:
    with session(path) as conn:
        conn.execute(
            "UPDATE outreach SET status = 'sent', message_id = ?, error = NULL, sent_at = ? "
            "WHERE job_id = ? AND kind = ?",
            (message_id, now(), job_id, kind),
        )


def mark_failed(job_id: str, kind: str, error: str, path: Path | None = None) -> None:
    with session(path) as conn:
        conn.execute(
            "UPDATE outreach SET status = 'failed', error = ? WHERE job_id = ? AND kind = ?",
            (error[:500], job_id, kind),
        )


def rows(status: str | None = None, limit: int | None = None, path: Path | None = None) -> list[dict]:
    query = (
        "SELECT o.*, j.company, j.title, j.url FROM outreach o "
        "LEFT JOIN jobs j ON j.id = o.job_id "
    )
    params: list[object] = []
    if status:
        query += "WHERE o.status = ? "
        params.append(status)
    query += "ORDER BY o.created_at DESC, o.id DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    with session(path) as conn:
        return [dict(row) for row in conn.execute(query, params)]


def sent_today(path: Path | None = None) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with session(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM outreach WHERE status = 'sent' AND substr(sent_at, 1, 10) = ?",
            (day,),
        ).fetchone()
    return int(row["n"] if row else 0)


def company_sent(company: str, path: Path | None = None) -> int:
    with session(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM outreach o JOIN jobs j ON j.id = o.job_id "
            "WHERE o.status = 'sent' AND lower(j.company) = lower(?)",
            (company or "",),
        ).fetchone()
    return int(row["n"] if row else 0)


def counts(path: Path | None = None) -> dict[str, int]:
    with session(path) as conn:
        return {
            row["status"]: row["n"]
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM outreach GROUP BY status")
        }


def set_job_contact(job_id: str, email: str, path: Path | None = None) -> None:
    with session(path) as conn:
        conn.execute("UPDATE jobs SET contact_email = ? WHERE id = ?", (email, job_id))


def save_contact(
    *,
    email: str,
    company: str,
    domain: str,
    job_id: str,
    source: str,
    path: Path | None = None,
) -> None:
    with session(path) as conn:
        conn.execute(
            """INSERT INTO contacts (email, company, domain, job_id, source, found_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                 company = COALESCE(NULLIF(contacts.company, ''), excluded.company),
                 domain  = COALESCE(NULLIF(contacts.domain, ''), excluded.domain)""",
            (email, company, domain, job_id, source, now()),
        )


def all_contacts(path: Path | None = None) -> list[dict]:
    with session(path) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT c.*, j.title, j.status FROM contacts c "
                "LEFT JOIN jobs j ON j.id = c.job_id ORDER BY c.found_at DESC"
            )
        ]
