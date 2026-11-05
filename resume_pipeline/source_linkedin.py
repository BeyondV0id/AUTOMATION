"""LinkedIn public job search.

Uses LinkedIn's logged-out guest endpoint, the same HTML the public job search
page renders. No account, cookie, or automation framework is involved, and the
run is cached and rate-limited like every other source. LinkedIn can throttle
this endpoint, so a failure is reported and the rest of the run continues.
"""

from __future__ import annotations

import html as html_module
import re
import urllib.parse

from .net import fetch_text

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.linkedin.com/jobs/search/",
}

_STRIP = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_CARD = re.compile(r"<li>\s*<div class=\"base-card[\s\S]*?</li>", re.I)
_JOB_ID = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
_URL = re.compile(r'base-card__full-link[\s\S]{0,400}?href="([^"]+)"')
_TITLE = re.compile(r'base-search-card__title">([\s\S]*?)</h3>')
_COMPANY = re.compile(r'base-search-card__subtitle">([\s\S]*?)</h4>')
_LOCATION = re.compile(r'job-search-card__location">([\s\S]*?)</span>')
_DATE = re.compile(r'job-search-card__listdate[^"]*"\s+datetime="([^"]+)"')


def _text(value: str) -> str:
    return _WHITESPACE.sub(" ", html_module.unescape(_STRIP.sub(" ", value or ""))).strip()


def _canonical(url: str, job_id: str) -> str:
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}"
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))


def parse_cards(markup: str) -> list[dict]:
    """Parse the guest search HTML into normalized job records."""
    jobs: list[dict] = []
    seen: set[str] = set()
    for block in _CARD.findall(markup or ""):
        id_match = _JOB_ID.search(block)
        job_id = id_match.group(1) if id_match else ""
        title = _text(_TITLE.search(block).group(1)) if _TITLE.search(block) else ""
        if not title:
            continue
        company = _text(_COMPANY.search(block).group(1)) if _COMPANY.search(block) else ""
        location = _text(_LOCATION.search(block).group(1)) if _LOCATION.search(block) else ""
        raw_url = _URL.search(block).group(1) if _URL.search(block) else ""
        url = _canonical(raw_url, job_id)
        if not job_id:
            job_id = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        posted = _DATE.search(block).group(1) if _DATE.search(block) else None
        jobs.append(
            {
                "id": f"linkedin:{job_id}",
                "source": "linkedin",
                "company": company,
                "title": title,
                "location": location,
                "url": url,
                "description": f"via LinkedIn · {title} at {company} · {location}".strip(" ·"),
                "skills": [],
                "posted_at": posted,
                "remote": bool(re.search(r"remote|work from home|wfh", location, re.I)),
            }
        )
    return jobs


def discover_linkedin(
    keywords: list[str],
    location: str = "India",
    max_pages: int = 2,
    log=print,
) -> list[dict]:
    """Search LinkedIn's public guest listing for each keyword."""
    jobs: list[dict] = []
    seen: set[str] = set()
    for keyword in keywords:
        for page in range(max(1, max_pages)):
            query = urllib.parse.urlencode(
                {"keywords": keyword, "location": location, "start": page * 10}
            )
            try:
                markup = fetch_text(f"{SEARCH}?{query}", max_age_hours=6, headers=HEADERS)
            except Exception as exc:  # throttling or markup change: keep other sources running
                log(f"linkedin:{keyword} page {page + 1} failed ({exc})")
                break
            found = parse_cards(markup)
            if not found:
                break
            added = 0
            for job in found:
                if job["id"] in seen:
                    continue
                seen.add(job["id"])
                jobs.append(job)
                added += 1
            log(f"linkedin:{keyword} page {page + 1} -> {added} jobs")
            if added == 0:
                break
    return jobs
