"""Re-score every stored job against the resume profile.

    python scripts/rank.py                       # rescore all, show the top 20
    python scripts/rank.py --top 30
    python scripts/rank.py --level internship,fresher
    python scripts/rank.py --remote-only --min-score 60
    python scripts/rank.py --explain arbeitnow:123
    python scripts/rank.py --no-write             # preview without touching the DB

Scoring is local and free, so this can run after every discovery without
spending a token. It writes match_score, match_reason, missing_skills, level,
years_required and eligible, which the tracker, the digest and the sheet read.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_pipeline.match import INDIA_WORDS, REMOTE_LOCKED, score_job  # noqa: E402

DB = ROOT / "data" / "applications.db"
PROFILES = (ROOT / "data" / "resume" / "profile.json", ROOT / "config" / "profile.json")
COLUMNS = {"level": "TEXT", "years_required": "INTEGER", "eligible": "INTEGER"}
REMOTE_WORDS = ("remote", "work from home", "wfh", "anywhere", "worldwide")


def connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit(f"No tracker at {DB}. Run `python run.py` first.")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, kind in COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
    conn.commit()
    return conn


def load_profile(path: Path | None = None) -> dict:
    for candidate in ([path] if path else list(PROFILES)):
        if candidate and candidate.exists():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    raise SystemExit(
        "No profile to score with. Expected data/resume/profile.json "
        "(or config/profile.json)."
    )


def job_row(row: sqlite3.Row) -> dict:
    try:
        skills = json.loads(row["skills"]) if row["skills"] else []
    except (TypeError, ValueError):
        skills = []
    return {
        "id": row["id"],
        "source": row["source"] or "",
        "company": row["company"] or "",
        "title": row["title"] or "",
        "location": row["location"] or "",
        "url": row["url"] or "",
        "description": row["description"] or "",
        "skills": skills if isinstance(skills, list) else [],
    }


def rescore(conn: sqlite3.Connection, profile: dict, write: bool = True) -> list[dict[str, Any]]:
    """Score every job. Returns the scored rows, highest score first."""
    results: list[dict[str, Any]] = []
    updates = []
    for row in conn.execute("SELECT * FROM jobs").fetchall():
        job = job_row(row)
        scored = score_job(job, profile)
        results.append({**job, **scored})
        updates.append(
            (
                scored["score"],
                scored["reason"],
                scored["missing"],
                scored["level"],
                scored["years_required"],
                int(scored["eligible"]),
                job["id"],
            )
        )
    if write:
        conn.executemany(
            "UPDATE jobs SET match_score = ?, match_reason = ?, missing_skills = ?, "
            "level = ?, years_required = ?, eligible = ? WHERE id = ?",
            updates,
        )
        conn.commit()
    results.sort(key=lambda item: (item["score"] is not None, item["score"] or 0), reverse=True)
    return results


def select(results: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    wanted = {item.strip().lower() for item in (args.level or "").split(",") if item.strip()}
    sources = {item.strip().lower() for item in (getattr(args, "source", "") or "").split(",") if item.strip()}
    priority = [
        item.strip().lower()
        for item in ("" if args.no_priority else (args.priority or "")).split(",")
        if item.strip()
    ]
    picked: list[dict[str, Any]] = []
    for item in results:
        if wanted and item["level"] not in wanted:
            continue
        if sources and item["source"] not in sources:
            continue
        if args.remote_only and not any(word in item["location"].lower() for word in REMOTE_WORDS):
            continue
        if args.open_to_india:
            location = item["location"].lower()
            locked = bool(REMOTE_LOCKED.search(location))
            india = any(word in location for word in INDIA_WORDS)
            open_remote = (
                any(word in location for word in ("remote", "work from home", "anywhere", "worldwide", "wfh"))
                and not locked
            )
            if not (india or open_remote):
                continue
        if args.min_score is not None and (item["score"] or 0) < args.min_score:
            continue
        if args.max_years is not None and item["years_required"] is not None and item["years_required"] > args.max_years:
            continue
        picked.append(item)

    if priority:
        rank = {name: index for index, name in enumerate(priority)}
        fallback = len(rank)
        picked.sort(
            key=lambda item: (
                rank.get(item["level"], fallback) if item["eligible"] else fallback + 1,
                -(item["score"] or 0),
            )
        )
    return picked[: args.top]


def show(results: list[dict[str, Any]]) -> None:
    if not results:
        print("Nothing matched those filters.")
        return
    print(f"{'SCORE':>5}  {'LEVEL':10} {'YRS':>3}  {'COMPANY':24} {'TITLE':44} {'LOCATION':20} SOURCE")
    print("-" * 132)
    for item in results:
        score = f"{round(item['score'])}" if item["score"] is not None else "-"
        years = item["years_required"] if item["years_required"] is not None else ""
        print(
            f"{score:>5}  {(item['level'] or '-'):10} {str(years):>3}  "
            f"{(item['company'] or '')[:24]:24} {(item['title'] or '')[:44]:44} "
            f"{(item['location'] or '')[:20]:20} {item['source']}"
        )
        print(f"       {item['id']}")


def explain(conn: sqlite3.Connection, job_id: str, profile: dict) -> None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM jobs WHERE id LIKE ? LIMIT 1", (f"%{job_id}%",)).fetchone()
    if row is None:
        print(f"No job matching {job_id!r}.")
        return
    job = job_row(row)
    scored = score_job(job, profile)
    print(f"{job['company']} - {job['title']}")
    print(f"  id      {job['id']}")
    print(f"  url     {job['url']}")
    print(f"  level   {scored['level']} (asks {scored['years_required']} years)")
    print(f"  score   {scored['score']}  eligible={scored['eligible']}")
    print(f"  reason  {scored['reason']}")
    if scored["missing"]:
        print(f"  missing {scored['missing']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--level", help="comma list: internship,fresher,junior,mid,senior,unknown")
    parser.add_argument("--source", help="comma list of sources, for example hn,yc,internshala")
    parser.add_argument(
        "--priority",
        default="internship,fresher,junior",
        help="levels listed first, in this order (default: internship,fresher,junior)",
    )
    parser.add_argument("--no-priority", action="store_true", help="sort purely by score")
    parser.add_argument("--remote-only", action="store_true")
    parser.add_argument("--open-to-india", action="store_true", help="India locations and worldwide-remote only")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--max-years", type=int, default=None, help="hide postings asking for more")
    parser.add_argument("--explain", metavar="JOB_ID")
    parser.add_argument("--no-write", action="store_true", help="score and print, leave the DB alone")
    args = parser.parse_args(argv)

    profile = load_profile(args.profile)
    conn = connect()
    try:
        if args.explain:
            explain(conn, args.explain, profile)
            return 0

        results = rescore(conn, profile, write=not args.no_write)
        levels: dict[str, int] = {}
        for item in results:
            levels[item["level"]] = levels.get(item["level"], 0) + 1
        eligible = sum(1 for item in results if item["eligible"])
        name = profile.get("identity", {}).get("name", "profile")
        print(
            f"Scored {len(results)} jobs against {name}: "
            f"{eligible} eligible for an internship/fresher profile."
        )
        print("Levels: " + ", ".join(f"{key} {value}" for key, value in sorted(levels.items())))
        if args.no_write:
            print("(preview only, nothing written)")
        print()
        show(select(results, args))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
