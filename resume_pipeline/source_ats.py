"""Public applicant-tracking-system job boards.

Greenhouse, Lever and Ashby expose unauthenticated JSON endpoints, which are
far cheaper and more reliable than scraping a careers page.
"""

from __future__ import annotations

import re

from .net import fetch_json

STRIP_TAGS = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")


def strip_html(value: str, limit: int = 1500) -> str:
    text = WHITESPACE.sub(" ", STRIP_TAGS.sub(" ", value or "")).strip()
    return text[:limit]


def _record(source: str, board: str, title: str, location: str, url: str, description: str, posted: str | None) -> dict:
    return {
        "id": f"{source}:{board}:{title}:{location}".lower(),
        "source": source,
        "company": board.replace("-", " ").title(),
        "title": title,
        "location": location,
        "url": url,
        "description": description,
        "skills": [],
        "posted_at": posted,
        "remote": bool(re.search(r"remote", location, re.I)),
    }


def fetch_greenhouse(board: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    payload = fetch_json(url, max_age_hours=6)
    jobs = []
    for item in payload.get("jobs", []) if isinstance(payload, dict) else []:
        jobs.append(
            _record(
                "greenhouse",
                board,
                item.get("title") or "",
                (item.get("location") or {}).get("name") or "",
                item.get("absolute_url") or "",
                strip_html(item.get("content") or ""),
                (item.get("updated_at") or "")[:10] or None,
            )
        )
    return jobs


def fetch_lever(board: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    payload = fetch_json(url, max_age_hours=6)
    jobs = []
    for item in payload if isinstance(payload, list) else []:
        categories = item.get("categories") or {}
        locations = [categories.get("location"), *(categories.get("allLocations") or [])]
        jobs.append(
            _record(
                "lever",
                board,
                item.get("text") or "",
                " / ".join(dict.fromkeys(x for x in locations if x)),
                item.get("hostedUrl") or item.get("applyUrl") or "",
                (item.get("descriptionPlain") or "")[:1500],
                (item.get("createdAt") or "")[:10] or None,
            )
        )
    return jobs


def fetch_ashby(board: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    payload = fetch_json(url, max_age_hours=6)
    jobs = []
    for item in payload.get("jobs", []) if isinstance(payload, dict) else []:
        locations = [item.get("location"), *[(x or {}).get("location") for x in (item.get("secondaryLocations") or [])]]
        jobs.append(
            _record(
                "ashby",
                board,
                item.get("title") or "",
                " / ".join(dict.fromkeys(x for x in locations if x)),
                item.get("jobUrl") or item.get("applyUrl") or "",
                (item.get("descriptionPlain") or "")[:1500],
                (item.get("publishedAt") or "")[:10] or None,
            )
        )
    return jobs


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


def discover_ats(boards: dict[str, list[str]], log=print) -> list[dict]:
    jobs: list[dict] = []
    for vendor, names in boards.items():
        fetcher = FETCHERS.get(vendor)
        if not fetcher:
            continue
        for board in names or []:
            try:
                found = fetcher(board)
            except Exception as exc:
                log(f"{vendor}:{board} failed ({exc})")
                continue
            if found:
                log(f"{vendor}:{board} -> {len(found)} jobs")
            jobs.extend(found)
    return jobs
