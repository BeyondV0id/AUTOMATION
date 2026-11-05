from __future__ import annotations

import argparse
import json
import sqlite3
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import DATA, REPORTS
from .match import score_job
from .source_ats import discover_ats
from .source_engg import discover_engg
from .source_gmafj import discover_gmafj
from .source_hn import discover_hn
from .source_hiringcafe import discover_hiringcafe
from .source_internshala import discover_internshala
from .source_linkedin import discover_linkedin
from .source_naukri import discover_naukri
from .source_yc import discover_yc

DB = DATA / "applications.db"
PROFILE_CANDIDATES = (Path("config/profile.json"), Path("data/resume/profile.json"))

COLUMNS = {
    "match_score": "REAL",
    "match_reason": "TEXT",
    "missing_skills": "TEXT",
    "posted_at": "TEXT",
    "skills": "TEXT",
    "level": "TEXT",
    "years_required": "INTEGER",
    "eligible": "INTEGER",
}


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, "
        "location TEXT, url TEXT, description TEXT, discovered_at TEXT, status TEXT DEFAULT 'new')"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS applications (job_id TEXT PRIMARY KEY, status TEXT, applied_at TEXT, "
        "notes TEXT, FOREIGN KEY(job_id) REFERENCES jobs(id))"
    )
    existing = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    for name, kind in COLUMNS.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
    return connection


def load_match_profile(path: Path | None, log=print) -> dict | None:
    """Load whatever profile exists for scoring. Missing data is not an error here."""
    candidates = [path] if path else list(PROFILE_CANDIDATES)
    for candidate in candidates:
        if candidate and candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except ValueError as exc:
                log(f"note: {candidate} is not valid JSON ({exc})")
                continue
            if isinstance(data, dict):
                log(f"scoring with {candidate}")
                return data
    log("note: no profile found, jobs are stored without a match score")
    return None


def get_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "job-automation/0.2"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize(source: str, item: dict) -> dict[str, Any]:
    if source == "arbeitnow":
        return {
            "id": f"arbeitnow:{item.get('slug', item.get('url'))}",
            "source": source,
            "company": item.get("company_name", ""),
            "title": item.get("title", ""),
            "location": item.get("location", ""),
            "url": item.get("url", ""),
            "description": item.get("description", ""),
        }
    return {
        "id": f"{source}:{item.get('id', item.get('url', item.get('title', '')))}",
        "source": source,
        "company": item.get("company_name", item.get("company", "")),
        "title": item.get("title", ""),
        "location": item.get("candidate_required_location", item.get("location", "")),
        "url": item.get("url", item.get("application_url", "")),
        "description": item.get("description", item.get("content", "")),
    }


def fetch_feed_sources(enabled: dict) -> list[dict]:
    sources: list[dict] = []
    if enabled.get("arbeitnow"):
        payload = get_json("https://www.arbeitnow.com/api/job-board-api")
        sources += [normalize("arbeitnow", item) for item in payload.get("data", [])]
    if enabled.get("remotive"):
        payload = get_json("https://remotive.com/api/remote-jobs?limit=100")
        sources += [normalize("remotive", item) for item in payload.get("jobs", [])]
    if enabled.get("himalayas"):
        payload = get_json("https://himalayas.app/jobs/api")
        sources += [normalize("himalayas", item) for item in payload.get("jobs", [])]
    return sources


def fetch_sources(
    config: dict,
    log=print,
    use_yc: bool = True,
    use_ats: bool = True,
    use_naukri: bool = True,
    use_linkedin: bool = True,
    use_internshala: bool = True,
    use_engg: bool = True,
    use_gmafj: bool = True,
    use_hiringcafe: bool = True,
    use_hn: bool = True,
) -> list[dict]:
    sources = config.get("sources") or {}
    jobs = fetch_feed_sources(sources)

    if use_linkedin and sources.get("linkedin"):
        section = config.get("linkedin") or {}
        try:
            jobs += discover_linkedin(
                keywords=[str(item) for item in section.get("keywords") or []],
                location=str(section.get("location") or "India"),
                max_pages=int(section.get("max_pages", 2)),
                log=log,
            )
        except Exception as exc:
            log(f"linkedin: failed ({exc})")

    if use_internshala and sources.get("internshala"):
        section = config.get("internshala") or {}
        try:
            jobs += discover_internshala(
                categories=[str(item) for item in section.get("categories") or []],
                max_pages=int(section.get("max_pages", 1)),
                log=log,
            )
        except Exception as exc:
            log(f"internshala: failed ({exc})")

    if use_engg and sources.get("engg"):
        section = config.get("engg") or {}
        try:
            jobs += discover_engg(
                queries=list(section.get("queries") or []),
                log=log,
                limit=int(section.get("limit", 25)),
                level=section.get("level"),
                remote=section.get("remote"),
                location=section.get("location"),
                max_years=section.get("max_years"),
                posted_within_days=section.get("posted_within_days"),
            )
        except Exception as exc:
            log(f"engg: failed ({exc})")

    if use_naukri and sources.get("naukri"):
        section = config.get("naukri") or {}
        try:
            jobs += discover_naukri(
                keywords=[str(item) for item in section.get("keywords") or []],
                location=str(section.get("location") or ""),
                max_pages=int(section.get("max_pages", 1)),
                headful=bool(section.get("headful", True)),
                wait_ms=int(section.get("wait_ms", 6000)),
                log=log,
            )
        except Exception as exc:
            log(f"naukri: failed ({exc})")

    if use_gmafj and sources.get("gmafj"):
        section = config.get("gmafj") or {}
        try:
            jobs += discover_gmafj(
                keywords=[str(item) for item in section.get("keywords") or []],
                countries=[str(item) for item in section.get("countries") or []],
                remote_only=bool(section.get("remote", False)),
                days_old=section.get("days_old", 45),
                max_jobs=int(section.get("max_jobs", 40)),
                log=log,
            )
        except Exception as exc:
            log(f"gmafj: failed ({exc})")

    if use_hiringcafe and sources.get("hiringcafe"):
        section = config.get("hiringcafe") or {}
        try:
            jobs += discover_hiringcafe(
                queries=[str(item) for item in section.get("queries") or []],
                location=section.get("location", "India"),
                days_old=section.get("days_old", 7),
                max_pages=int(section.get("max_pages", 1)),
                headful=bool(section.get("headful", True)),
                wait_ms=int(section.get("wait_ms", 9000)),
                max_jobs=int(section.get("max_jobs", 80)),
                log=log,
            )
        except Exception as exc:
            log(f"hiringcafe: failed ({exc})")

    if use_hn and sources.get("hn"):
        section = config.get("hn") or {}
        try:
            jobs += discover_hn(
                keywords=[str(item) for item in section.get("keywords") or []],
                max_comments=int(section.get("max_comments", 400)),
                days_old=int(section.get("days_old", 40)),
                remote_only=bool(section.get("remote", False)),
                log=log,
            )
        except Exception as exc:
            log(f"hn: failed ({exc})")

    yc_config = config.get("yc") or {}
    if use_yc and yc_config.get("enabled", True):
        jobs += discover_yc(
            max_pages=int(yc_config.get("max_pages", 12)),
            years_back=int(yc_config.get("years_back", 2)),
            regions=list(yc_config.get("regions") or ["India", "Fully Remote"]),
            log=log,
        )

    ats_config = config.get("ats") or {}
    if use_ats and any(ats_config.get(vendor) for vendor in ("greenhouse", "lever", "ashby")):
        jobs += discover_ats({k: list(v) for k, v in ats_config.items()}, log=log)

    return jobs


def upsert(connection: sqlite3.Connection, jobs: list[dict], now: str) -> int:
    accepted = 0
    for job in jobs:
        if not job.get("title") or not job.get("url"):
            continue
        connection.execute(
            "INSERT INTO jobs (id, source, company, title, location, url, description, discovered_at, status, "
            "match_score, match_reason, missing_skills, posted_at, skills, level, years_required, eligible) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT status FROM jobs WHERE id = ?), 'new'), ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "match_score = excluded.match_score, match_reason = excluded.match_reason, "
            "missing_skills = excluded.missing_skills, skills = excluded.skills, "
            "level = excluded.level, years_required = excluded.years_required, "
            "eligible = excluded.eligible, "
            "description = CASE WHEN excluded.description <> '' THEN excluded.description ELSE jobs.description END",
            (
                job["id"], job.get("source", ""), job.get("company", ""), job.get("title", ""),
                job.get("location", ""), job.get("url", ""), job.get("description", ""), now, job["id"],
                job.get("match_score"), job.get("match_reason", ""), job.get("missing", ""),
                job.get("posted_at"), json.dumps(job.get("skills") or []),
                job.get("level", ""), job.get("years_required"), int(bool(job.get("eligible"))),
            ),
        )
        accepted += 1
    return accepted


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover public technical jobs and store them in SQLite")
    parser.add_argument("--config", default="config/jobs.toml", type=Path)
    parser.add_argument("--profile", default=None, type=Path, help="Profile JSON used for match scoring")
    parser.add_argument("--min-score", type=float, default=None, help="Drop scored jobs below this value")
    parser.add_argument("--no-yc", action="store_true", help="Skip the YC directory")
    parser.add_argument("--no-ats", action="store_true", help="Skip Greenhouse/Lever/Ashby boards")
    parser.add_argument("--no-linkedin", action="store_true", help="Skip LinkedIn")
    parser.add_argument("--no-internshala", action="store_true", help="Skip Internshala")
    parser.add_argument("--no-engg", action="store_true", help="Skip the engg.space MCP search")
    parser.add_argument("--no-naukri", action="store_true", help="Skip Naukri (the browser source)")
    parser.add_argument("--no-gmafj", action="store_true", help="Skip GiveMeAFuckingJ*b")
    parser.add_argument("--no-hiringcafe", action="store_true", help="Skip HiringCafe (the browser source)")
    parser.add_argument("--no-hn", action="store_true", help="Skip Hacker News Who is hiring")
    parser.add_argument("--no-browser", action="store_true", help="Skip every source that needs a browser")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    log = (lambda *_: None) if args.quiet else print
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    keywords = [str(x).lower() for x in config.get("keywords", [])]
    locations = [str(x).lower() for x in config.get("locations", [])]

    profile = load_match_profile(args.profile, log=log)

    try:
        jobs = fetch_sources(
            config,
            log=log,
            use_yc=not args.no_yc,
            use_ats=not args.no_ats,
            use_linkedin=not args.no_linkedin,
            use_internshala=not args.no_internshala,
            use_engg=not args.no_engg,
            use_naukri=not (args.no_naukri or args.no_browser),
            use_gmafj=not args.no_gmafj,
            use_hiringcafe=not (args.no_hiringcafe or args.no_browser),
            use_hn=not args.no_hn,
        )
    except Exception as exc:
        REPORTS.mkdir(exist_ok=True)
        (REPORTS / "discovery-error.json").write_text(json.dumps({"error": str(exc)}, indent=2), encoding="utf-8")
        raise

    now = datetime.now(timezone.utc).isoformat()
    connection = db()
    accepted = 0
    for job in jobs:
        haystack = f"{job.get('title', '')} {job.get('description', '')} {job.get('location', '')}".lower()
        if keywords and not any(word in haystack for word in keywords):
            continue
        if locations and not any(place in haystack for place in locations):
            continue
        scored = score_job(job, profile)
        job["match_score"] = scored["score"]
        job["match_reason"] = scored["reason"]
        job["missing"] = scored["missing"]
        job["level"] = scored["level"]
        job["years_required"] = scored["years_required"]
        job["eligible"] = scored["eligible"]
        if args.min_score is not None and job["match_score"] is not None and job["match_score"] < args.min_score:
            continue
        accepted += upsert(connection, [job], now)
    connection.commit()
    connection.close()
    print(json.dumps({"fetched": len(jobs), "accepted": accepted, "database": str(DB)}, indent=2))


if __name__ == "__main__":
    main()
