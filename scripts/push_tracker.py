"""Push the local job database into the Google Sheets tracker.

    python scripts/push_tracker.py
    python scripts/push_tracker.py --dry-run
    python scripts/push_tracker.py --limit 25

The OAuth token workspace-mcp already stored is reused, so there is nothing to
set up. Columns the sheet owner edits by hand (status, applied date, resume
file, contact, cold email, notes) win over the database: a refresh never erases
manual work. Rows that exist only in the sheet are kept and appended at the end.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "applications.db"
SYNC = ROOT / "config" / "sync.toml"
SHEET = "Jobs"

HEADERS = [
    "Job ID",
    "Company",
    "Role",
    "Location",
    "Source",
    "URL",
    "Score",
    "Status",
    "Applied On",
    "Resume File",
    "Contact Name",
    "Contact Email",
    "Cold Email",
    "Last Event",
    "Last Event On",
    "Notes",
]

# Column indexes whose sheet value is kept when the sheet already has one.
EDITABLE = {7, 8, 9, 10, 11, 12, 15}


def fail(message: str) -> "NoReturn":  # type: ignore[name-defined]
    raise SystemExit(message)


def load_settings() -> tuple[str, str, Path]:
    if not SYNC.exists():
        fail(f"No {SYNC}. Copy config/sync.example.toml first.")
    data = tomllib.loads(SYNC.read_text(encoding="utf-8"))
    sheets = data.get("sheets") or {}
    spreadsheet_id = str(sheets.get("spreadsheet_id") or "").strip()
    address = str((data.get("gmail") or {}).get("address") or "").strip()
    if not spreadsheet_id:
        fail("Set [sheets].spreadsheet_id in config/sync.toml.")
    if not address:
        fail("Set [gmail].address in config/sync.toml.")

    credentials = Path(
        (data.get("sheets") or {}).get("credentials_dir")
        or Path.home() / ".config" / "opencode" / "google-credentials"
    )
    token_file = credentials / f"{address}.json"
    if not token_file.exists():
        fail(f"No OAuth token at {token_file}. Run the google-workspace auth first.")
    return spreadsheet_id, address, token_file


def access_token(token_file: Path) -> str:
    creds = json.loads(token_file.read_text(encoding="utf-8"))
    body = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    request = urllib.request.Request(
        creds.get("token_uri") or "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        fail(f"Token refresh failed ({exc.code}): {detail}")

    if payload.get("refresh_token"):
        creds["refresh_token"] = payload["refresh_token"]
        token_file.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    return payload["access_token"]


def api(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        fail(f"Sheets API {method} {url} failed ({exc.code}): {detail}")
    return json.loads(raw) if raw else {}


def db_rows(limit: int | None) -> list[list[str]]:
    if not DB.exists():
        fail(f"No tracker at {DB}. Run `python run.py` first.")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT j.id, j.company, j.title, j.location, j.source, j.url,"
        " j.match_score, j.status, j.contact_email, a.applied_at"
        " FROM jobs j LEFT JOIN applications a ON a.job_id = j.id"
        " ORDER BY j.company COLLATE NOCASE, j.title COLLATE NOCASE"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows: list[list[str]] = []
    for row in conn.execute(sql):
        rows.append(
            [
                str(row["id"] or ""),
                str(row["company"] or ""),
                str(row["title"] or ""),
                str(row["location"] or ""),
                str(row["source"] or ""),
                str(row["url"] or ""),
                "" if row["match_score"] is None else f"{float(row['match_score']):.3f}",
                str(row["status"] or "new"),
                str(row["applied_at"] or ""),
                "",
                "",
                str(row["contact_email"] or ""),
                "",
                "",
                "",
                "",
            ]
        )
    conn.close()
    return rows


def existing_rows(spreadsheet_id: str, token: str) -> dict[str, list[str]]:
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        f"/values/{SHEET}!A1:P100000?majorDimension=ROWS"
    )
    payload = api("GET", url, token)
    values = payload.get("values") or []
    found: dict[str, list[str]] = {}
    for row in values[1:]:
        if not row or not str(row[0]).strip():
            continue
        found[str(row[0]).strip()] = [str(cell) for cell in row]
    return found


def merge(rows: list[list[str]], kept: dict[str, list[str]]) -> tuple[list[list[str]], int]:
    merged: list[list[str]] = []
    preserved = 0
    seen: set[str] = set()
    for row in rows:
        job_id = row[0]
        seen.add(job_id)
        old = kept.get(job_id)
        if old:
            for index in EDITABLE:
                if index < len(old) and str(old[index]).strip():
                    if row[index] != old[index]:
                        preserved += 1
                    row[index] = old[index]
        merged.append(row)

    orphans = [row for key, row in kept.items() if key not in seen]
    for row in orphans:
        row = row + [""] * (len(HEADERS) - len(row))
        merged.append(row[: len(HEADERS)])
    return merged, preserved + len(orphans)


def push(spreadsheet_id: str, token: str, rows: list[list[str]]) -> None:
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values"
    api("POST", f"{base}/{SHEET}!A2:P100000:clear", token, {})
    last = len(rows) + 1
    api(
        "PUT",
        f"{base}/{SHEET}!A2:P{last}?valueInputOption=RAW",
        token,
        {"range": f"{SHEET}!A2:P{last}", "majorDimension": "ROWS", "values": rows},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Push jobs into the Google Sheets tracker.")
    parser.add_argument("--limit", type=int, default=None, help="only push the first N jobs")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = parser.parse_args(argv)

    spreadsheet_id, address, token_file = load_settings()
    rows = db_rows(args.limit)
    if not rows:
        print("Nothing to push: the jobs table is empty.")
        return 0

    token = access_token(token_file)
    kept = existing_rows(spreadsheet_id, token)
    merged, preserved = merge(rows, kept)

    print(f"{address} -> {spreadsheet_id}")
    print(f"jobs {len(rows)}   sheet rows before {len(kept)}   manual values kept {preserved}")
    if args.dry_run:
        print("dry run, nothing written")
        return 0

    push(spreadsheet_id, token, merged)
    print(f"wrote {len(merged)} rows to {SHEET}!A2:P{len(merged) + 1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
