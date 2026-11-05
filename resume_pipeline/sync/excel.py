"""Read Status/Notes edits back from data/jobs.xlsx.

Without this step, Excel edits are overwritten by the next export. Human edits
win over everything else, including backwards status changes you make on
purpose.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import DATA

EXCEL = DATA / "jobs.xlsx"
DB = DATA / "applications.db"
STATUS_OPTIONS = ["new", "review", "applied", "interview", "offer", "rejected"]


def _norm(value: object) -> str:
    return str(value).strip().lower() if value is not None else ""


def _header_map(sheet) -> dict[str, int]:
    headers: dict[str, int] = {}
    for cell in sheet[1]:
        if cell.value is not None:
            headers[_norm(cell.value)] = cell.column - 1
    return headers


def import_edits(path: Path | None = None, db_path: Path | None = None) -> dict:
    from openpyxl import load_workbook

    path = path or EXCEL
    db_path = db_path or DB
    if not path.exists():
        return {"error": f"{path} not found", "status_changes": 0, "note_changes": 0}

    workbook = load_workbook(path, data_only=True)
    sheet = workbook.active
    headers = _header_map(sheet)

    status_col = headers.get("status")
    if status_col is None:
        return {"error": "no Status column", "status_changes": 0, "note_changes": 0}

    id_col = headers.get("job id") or headers.get("job_id") or headers.get("id")
    notes_col = headers.get("notes")
    company_col = headers.get("company")
    title_col = headers.get("title")
    contact_col = headers.get("contact email")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    has_contact = "contact_email" in {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if contact_col is not None and not has_contact:
        conn.execute("ALTER TABLE jobs ADD COLUMN contact_email TEXT")
        has_contact = True

    by_id = {row["id"]: dict(row) for row in conn.execute("SELECT id, company, title, status FROM jobs")}
    by_label = {
        (_norm(row["company"]), _norm(row["title"])): row["id"] for row in by_id.values()
    }
    notes_now = {
        row["job_id"]: row["notes"] or ""
        for row in conn.execute("SELECT job_id, notes FROM applications")
    }

    status_changes = 0
    note_changes = 0
    contact_changes = 0
    invalid: list[str] = []
    missing: list[str] = []

    for row in sheet.iter_rows(min_row=2):
        def value(index: int | None):
            if index is None or index >= len(row):
                return None
            return row[index].value

        job_id = str(value(id_col)).strip() if id_col is not None and value(id_col) else ""
        if job_id not in by_id:
            job_id = by_label.get((_norm(value(company_col)), _norm(value(title_col))), "")
        if not job_id:
            label = f"{value(company_col) or '?'} — {value(title_col) or '?'}"
            missing.append(label)
            continue

        current = by_id[job_id]
        status = _norm(value(status_col)) or (current["status"] or "new")
        notes = value(notes_col)
        notes = str(notes).strip() if notes is not None else ""

        if status not in STATUS_OPTIONS:
            invalid.append(f"{current['company']}: {status}")
            continue

        changed_status = status != (current["status"] or "new")
        changed_notes = notes != (notes_now.get(job_id, "") or "").strip()
        if not changed_status and not changed_notes:
            continue

        if changed_status:
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
            status_changes += 1
        if changed_notes:
            note_changes += 1

        if contact_col is not None and has_contact:
            contact = str(value(contact_col) or "").strip()
            current_contact = conn.execute(
                "SELECT contact_email FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()["contact_email"] or ""
            if contact != current_contact:
                conn.execute("UPDATE jobs SET contact_email = ? WHERE id = ?", (contact, job_id))
                contact_changes += 1

        conn.execute(
            """INSERT INTO applications (job_id, status, applied_at, notes) VALUES (?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                 status = excluded.status,
                 notes = excluded.notes,
                 applied_at = COALESCE(applications.applied_at, excluded.applied_at)""",
            (job_id, status, None, notes),
        )

    conn.commit()
    conn.close()
    workbook.close()
    return {
        "status_changes": status_changes,
        "note_changes": note_changes,
        "contact_changes": contact_changes,
        "invalid": invalid,
        "missing": missing,
    }
