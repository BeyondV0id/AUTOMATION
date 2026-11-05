"""One command that says what is configured and what is missing.

    python scripts/doctor.py

Prints a compact table instead of making you re-derive the state of the repo,
the database, the Google token, and the two browsers every session.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DATA = ROOT / "data"
TOKENS = Path.home() / ".config" / "opencode" / "google-credentials"

OK = "ok"
WARN = "warn"
MISSING = "missing"

rows: list[tuple[str, str, str]] = []


def add(state: str, name: str, detail: str) -> None:
    rows.append((state, name, detail))


def check_files() -> None:
    for name in ("outreach.toml", "sync.toml", "answers.toml", "browser.toml"):
        path = CONFIG / name
        add(OK if path.exists() else MISSING, f"config/{name}", "" if path.exists() else "copy from .example")


def check_profile() -> None:
    profile = DATA / "resume" / "profile.json"
    draft = DATA / "resume" / "profile.draft.json"
    if profile.exists():
        add(OK, "profile.json", "verified")
    elif draft.exists():
        add(MISSING, "profile.json", "only profile.draft.json exists; scoring and tailoring are blocked")
    else:
        add(MISSING, "profile.json", "no resume data at all")


def check_db() -> None:
    db = DATA / "applications.db"
    if not db.exists():
        add(MISSING, "applications.db", "run python run.py")
        return
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        try:
            jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            scored = conn.execute("SELECT COUNT(*) FROM jobs WHERE match_score IS NOT NULL").fetchone()[0]
            contacts = conn.execute("SELECT COUNT(*) FROM jobs WHERE contact_email IS NOT NULL AND contact_email != ''").fetchone()[0]
            applied = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            add(OK, "jobs", f"{jobs} rows, {scored} scored, {contacts} with a contact email")
            add(OK if applied else WARN, "applications", f"{applied} rows")
        except sqlite3.OperationalError as exc:
            add(MISSING, "jobs table", str(exc))


def check_google() -> None:
    sync = CONFIG / "sync.toml"
    address = ""
    spreadsheet = ""
    if sync.exists():
        data = tomllib.loads(sync.read_text(encoding="utf-8"))
        address = str((data.get("gmail") or {}).get("address") or "")
        spreadsheet = str((data.get("sheets") or {}).get("spreadsheet_id") or "")

    if not address:
        add(MISSING, "gmail address", "set [gmail].address in config/sync.toml")
        return

    add(OK if address == "the.harsha.mail@gmail.com" else WARN, "gmail address", address)
    add(OK if spreadsheet else WARN, "sheets tracker", spreadsheet or "no [sheets].spreadsheet_id")

    token_file = TOKENS / f"{address}.json"
    if not token_file.exists():
        add(MISSING, "google token", f"none at {token_file}; run start_google_auth")
    else:
        creds = json.loads(token_file.read_text(encoding="utf-8"))
        expiry = str(creds.get("expiry") or "")
        scopes = len(creds.get("scopes") or [])
        left = ""
        try:
            when = datetime.fromisoformat(expiry)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            delta = when - datetime.now(timezone.utc)
            minutes = int(delta.total_seconds() // 60)
            left = f", access token {minutes}m left" if minutes > 0 else ", access token EXPIRED (refresh is automatic)"
        except ValueError:
            pass
        add(OK, "google token", f"{scopes} scopes{left}")

        # The refresh token dies 7 days after consent while the app is in Testing.
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            token_file.stat().st_mtime, tz=timezone.utc
        )
        days_left = 7 - age.days
        add(
            OK if days_left > 1 else WARN,
            "refresh window",
            f"{max(0, days_left)}d left of the 7-day Testing window",
        )

    add(OK if os.environ.get("JOB_GMAIL_APP_PASSWORD") else WARN, "JOB_GMAIL_APP_PASSWORD",
        "set" if os.environ.get("JOB_GMAIL_APP_PASSWORD") else "not set; SMTP drafts and sending are off")


def check_browsers() -> None:
    try:
        import playwright  # noqa: F401

        add(OK, "playwright", "installed")
    except ImportError:
        add(MISSING, "playwright", "pip install playwright && python -m playwright install chromium")

    exe = shutil.which("ego-browser")
    if not exe:
        base = Path(r"C:\Program Files\Citro Labs\ego lite\Application")
        found = sorted(base.glob("*/ego-browser.exe")) if base.exists() else []
        exe = str(found[-1]) if found else ""
    add(OK if exe else MISSING, "ego-browser", exe or "not found")

    browser_cfg = CONFIG / "browser.toml"
    if browser_cfg.exists():
        backend = str((tomllib.loads(browser_cfg.read_text(encoding="utf-8")).get("browser") or {}).get("backend") or "playwright")
        add(OK, "browser backend", backend)


def check_python() -> None:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    add(OK if sys.version_info >= (3, 11) else WARN, "python", version)


def main() -> int:
    check_python()
    check_files()
    check_profile()
    check_db()
    check_google()
    check_browsers()

    width = max(len(name) for _, name, _ in rows)
    for state, name, detail in rows:
        print(f"{state.upper():8} {name.ljust(width)}  {detail}")

    missing = sum(1 for state, _, _ in rows if state == MISSING)
    warns = sum(1 for state, _, _ in rows if state == WARN)
    print(f"\n{len(rows)} checks: {len(rows) - missing - warns} ok, {warns} warn, {missing} missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
