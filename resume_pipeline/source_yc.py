"""Y Combinator hiring companies, from the public yc-oss mirror.

The mirror lists which YC companies are hiring. The individual postings live
on each company's public YC page, so a run reads a bounded number of pages and
caches them, spreading the rest across later runs.
"""

from __future__ import annotations

import html as html_module
import json
import re
from datetime import date

from .net import fetch_json, fetch_text

HIRING_URL = "https://yc-oss.github.io/api/companies/hiring.json"
YC = "https://www.ycombinator.com"

_PAGE_DATA = re.compile(r'data-page="([^"]+)"')


def parse_company_page(markup: str) -> tuple[dict, list[dict]] | None:
    """Return (company, jobPostings) from a YC company page, or None."""
    match = _PAGE_DATA.search(markup)
    if not match:
        return None
    try:
        page = json.loads(html_module.unescape(match.group(1)))
    except ValueError:
        return None
    props = page.get("props") or {}
    return props.get("company") or {}, props.get("jobPostings") or []


def _is_india(company: dict) -> bool:
    regions = company.get("regions") or []
    return "India" in regions or bool(re.search(r"india", company.get("all_locations") or "", re.I))


def _is_remote(company: dict) -> bool:
    return "Fully Remote" in (company.get("regions") or [])


def _batch_year(batch: str) -> int | None:
    match = re.search(r"(20\d{2})", batch or "")
    return int(match.group(1)) if match else None


def _recent(company: dict, years_back: int) -> bool:
    year = _batch_year(company.get("batch") or "")
    return year is not None and year >= date.today().year - years_back


def _normalize(company: dict, posting: dict) -> dict:
    slug = company.get("slug") or company.get("name") or "yc"
    title = (posting.get("title") or "").strip()
    location = (posting.get("location") or "").strip()
    url = posting.get("url") or ""
    if not url.startswith("http"):
        url = f"{YC}{url}"
    skills = [str(s) for s in (posting.get("skills") or [])]
    role = posting.get("prettyRole") or posting.get("roleSpecificType")
    description = " · ".join(part for part in (role, posting.get("type")) if part)
    return {
        "id": f"yc:{slug}:{title}:{location}".lower(),
        "source": "yc",
        "company": company.get("name") or "",
        "title": title,
        "location": location,
        "url": url,
        "description": description,
        "skills": skills,
        "posted_at": posting.get("createdAt") or None,
        "remote": bool(re.search(r"remote", location, re.I)),
    }


def discover_yc(
    max_pages: int = 12,
    years_back: int = 2,
    regions: list[str] | None = None,
    log=print,
) -> list[dict]:
    """Collect job postings from hiring YC companies."""
    regions = regions or ["India", "Fully Remote"]
    try:
        companies = fetch_json(HIRING_URL, max_age_hours=20)
    except Exception as exc:  # network or JSON failure: keep other sources running
        log(f"yc: directory unavailable ({exc})")
        return []
    if not isinstance(companies, list):
        log("yc: unexpected directory payload")
        return []

    active = [
        c
        for c in companies
        if isinstance(c, dict)
        and (c.get("status") or "Active") == "Active"
        and c.get("isHiring")
        and _recent(c, years_back)
    ]
    wanted = [c for c in active if any(
        (region == "India" and _is_india(c)) or (region == "Fully Remote" and _is_remote(c))
        for region in regions
    )]
    if not wanted:
        wanted = active
    wanted.sort(key=lambda c: c.get("launched_at") or 0, reverse=True)
    selected = wanted[:max_pages]

    log(f"yc: {len(active)} hiring companies, reading {len(selected)} pages")
    jobs: list[dict] = []
    for company in selected:
        slug = company.get("slug")
        if not slug:
            continue
        try:
            markup = fetch_text(f"{YC}/companies/{slug}", max_age_hours=20)
        except Exception as exc:
            log(f"yc: skipped {slug} ({exc})")
            continue
        parsed = parse_company_page(markup)
        if not parsed:
            continue
        page_company, postings = parsed
        # Merge so company metadata from the page wins over the directory entry.
        merged = {**company, **{k: v for k, v in page_company.items() if v}}
        for posting in postings:
            if not isinstance(posting, dict):
                continue
            if re.match(r"^\s*\[paused\]", str(posting.get("title") or ""), re.I):
                continue
            jobs.append(_normalize(merged, posting))
    return jobs
