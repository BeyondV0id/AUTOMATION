"""Tracker counts and shortlists. Text only, no UI.

    python scripts/status.py
    python scripts/status.py --top 15
    python scripts/status.py --find acme
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "applications.db"
STATUSES = ["new", "review", "applied", "interview", "offer", "rejected"]
APPLIED = ("applied", "interview", "offer", "rejected")


def connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit(f"No tracker at {DB}. Run `python run.py` first.")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def show_counts(conn: sqlite3.Connection) -> None:
    counts = {row["status"]: row["n"] for row in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    total = sum(counts.values())
    applied = sum(counts.get(s, 0) for s in APPLIED)
    print(f"Tracked {total}   applied {applied}   pending {counts.get('new', 0) + counts.get('review', 0)}")
    for status in STATUSES:
        print(f"  {status:10} {counts.get(status, 0)}")
    try:
        rows = {row["status"]: row["n"] for row in conn.execute("SELECT status, COUNT(*) AS n FROM outreach GROUP BY status")}
        print(f"Email: {rows.get('draft', 0)} drafts, {rows.get('sent', 0)} sent, {rows.get('failed', 0)} failed")
    except sqlite3.OperationalError:
        pass


def show_top(conn: sqlite3.Connection, limit: int, min_score: float | None, level: str | None = None) -> None:
    query = (
        "SELECT id, company, title, location, source, status, match_score, level, years_required FROM jobs "
        "WHERE status IN ('new', 'review')"
    )
    params: list[object] = []
    if min_score is not None:
        query += " AND COALESCE(match_score, 0) >= ?"
        params.append(min_score)
    if level:
        wanted = [item.strip().lower() for item in level.split(",") if item.strip()]
        query += f" AND level IN ({','.join('?' * len(wanted))})"
        params += wanted
    # Internships and fresher roles first, then the rest by score.
    query += (
        " ORDER BY CASE level WHEN 'internship' THEN 0 WHEN 'fresher' THEN 1 WHEN 'junior' THEN 2 "
        "ELSE 3 END, COALESCE(match_score, -1) DESC, discovered_at DESC LIMIT ?"
    )
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("Nothing pending. Run `python run.py`.")
        return
    print(f"{'SCORE':>5}  {'LEVEL':10} {'COMPANY':26} {'TITLE':42} {'LOCATION':18} {'SOURCE':12} ID")
    print("-" * 140)
    for row in rows:
        score = f"{round(row['match_score'])}" if row["match_score"] is not None else "-"
        print(
            f"{score:>5}  {(row['level'] or '-'):10} {(row['company'] or '')[:26]:26} "
            f"{(row['title'] or '')[:42]:42} {(row['location'] or '')[:18]:18} "
            f"{(row['source'] or '')[:12]:12} {row['id']}"
        )


def show_find(conn: sqlite3.Connection, text: str, limit: int = 10) -> None:
    like = f"%{text}%"
    rows = conn.execute(
        "SELECT id, company, title, url, status, match_score FROM jobs "
        "WHERE url LIKE ? OR company LIKE ? OR title LIKE ? OR id LIKE ? "
        "ORDER BY COALESCE(match_score, -1) DESC LIMIT ?",
        (like, like, like, like, limit),
    ).fetchall()
    if not rows:
        print(f"No job matching {text!r}.")
        return
    for row in rows:
        score = f"{round(row['match_score'])}" if row["match_score"] is not None else "-"
        print(f"{score:>4}  {row['status'] or 'new':10} {row['company']} — {row['title']}")
        print(f"      {row['id']}")
        print(f"      {row['url']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tracker counts and shortlists")
    parser.add_argument("--top", type=int, metavar="N", help="list N pending jobs by score")
    parser.add_argument("--find", metavar="TEXT", help="find a job id by company, title, or URL")
    parser.add_argument("--min-score", type=float)
    parser.add_argument("--level", help="comma list: internship,fresher,junior,mid,senior,unknown")
    args = parser.parse_args()

    conn = connect()
    try:
        if args.find:
            show_find(conn, args.find)
        elif args.top:
            show_top(conn, args.top, args.min_score, args.level)
        else:
            show_counts(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
