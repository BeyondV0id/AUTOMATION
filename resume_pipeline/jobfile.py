"""Load a job record from a JSON file or the discovery database.

``data/jobs/`` is only a scratch folder; discovered postings live in
``data/applications.db``. Both CLIs accept either form so a job can be
tailored straight from the database without exporting a file first.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import DATA

DEFAULT_DB = DATA / "applications.db"


def from_db(job_id: str, db_path: str | Path | None = None) -> dict[str, Any]:
    """Return one job row, with its JSON-encoded skills decoded."""
    database = Path(db_path) if db_path else DEFAULT_DB
    if not database.exists():
        raise FileNotFoundError(f"Job database not found: {database}")
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"No job with id {job_id!r} in {database}")
    job = dict(row)
    if isinstance(job.get("skills"), str):
        try:
            job["skills"] = json.loads(job["skills"])
        except ValueError:
            job["skills"] = []
    return job


def load(
    job_path: str | Path | None = None,
    job_id: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load a job from ``--job`` (JSON) or ``--job-id`` (SQLite)."""
    if job_path:
        path = Path(job_path)
        if not path.exists():
            raise FileNotFoundError(f"Job file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    if job_id:
        return from_db(job_id, db_path)
    raise ValueError("Provide a job JSON path or a database job id")
