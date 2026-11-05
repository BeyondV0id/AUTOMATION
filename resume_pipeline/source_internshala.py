"""Internshala internship listings.

Internshala renders its search pages on the server, so a category page is a
plain HTML document with one card per internship. No account or browser is
needed. Category slugs come from config, for example
``software-development-internship``.
"""

from __future__ import annotations

import html as html_module
import re

from .net import fetch_text

BASE = "https://internshala.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_CARD = re.compile(
    r'<div class="[^"]*individual_internship[^"]*"[^>]*\bid="individual_internship_(\d+)"',
    re.I,
)
_TITLE = re.compile(r'<a class="job-title-href"[^>]*>([\s\S]*?)</a>', re.I)
_COMPANY = re.compile(r'<p class="company-name">([\s\S]*?)</p>', re.I)
_LOCATION = re.compile(r'<div class="row-1-item locations">([\s\S]*?)</div>', re.I)
_STIPEND = re.compile(r"<span class='stipend'>([\s\S]*?)</span>", re.I)
_DURATION = re.compile(r"ic-16-calendar[\s\S]{0,200}?<span>([\s\S]*?)</span>", re.I)
_ABOUT = re.compile(r'<div class="about_job">[\s\S]*?<div class="text">([\s\S]*?)</div>', re.I)
_SKILL = re.compile(r"<div class='job_skill'>([\s\S]*?)</div>", re.I)
_CATEGORY = re.compile(r'<div class="category_name">([\s\S]*?)</div>', re.I)
_LINK = re.compile(r'href="(/internship/detail/[^"]+)"', re.I)

_STRIP = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _text(value: str) -> str:
    return _WHITESPACE.sub(" ", html_module.unescape(_STRIP.sub(" ", value or ""))).strip()


def parse_cards(markup: str) -> list[dict]:
    """Parse one Internshala category page into normalized job records."""
    markup = markup or ""
    starts = list(_CARD.finditer(markup))
    jobs: list[dict] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(markup)
        block = markup[match.start():end]
        internship_id = match.group(1)
        title_match = _TITLE.search(block)
        title = _text(title_match.group(1)) if title_match else ""
        if not title:
            continue
        company = _text(_COMPANY.search(block).group(1)) if _COMPANY.search(block) else ""
        location = _text(_LOCATION.search(block).group(1)) if _LOCATION.search(block) else ""
        stipend = _text(_STIPEND.search(block).group(1)) if _STIPEND.search(block) else ""
        duration = _text(_DURATION.search(block).group(1)) if _DURATION.search(block) else ""
        about = _text(_ABOUT.search(block).group(1)) if _ABOUT.search(block) else ""
        skills = [_text(item) for item in _SKILL.findall(block) if _text(item)]
        categories = [_text(item) for item in _CATEGORY.findall(block) if _text(item)]
        link_match = _LINK.search(block)
        path = link_match.group(1) if link_match else ""
        remote = bool(
            re.search(r"work from home|wfh|remote", f"{title} {location} {path}", re.I)
        )
        details = [about]
        if categories:
            details.append(" · ".join(dict.fromkeys(categories)))
        if stipend:
            details.append(f"Stipend: {stipend}")
        if duration:
            details.append(f"Duration: {duration}")
        jobs.append(
            {
                "id": f"internshala:{internship_id}",
                "source": "internshala",
                "company": company,
                "title": title,
                "location": location or ("Work from home" if remote else ""),
                "url": f"{BASE}{path}" if path else BASE,
                "description": " | ".join(part for part in details if part),
                "skills": list(dict.fromkeys(skills)),
                "posted_at": None,
                "remote": remote,
            }
        )
    return jobs


def discover_internshala(
    categories: list[str],
    max_pages: int = 1,
    log=print,
) -> list[dict]:
    """Read a handful of category pages, newest first."""
    jobs: list[dict] = []
    seen: set[str] = set()
    for category in categories:
        slug = str(category).strip().strip("/")
        if not slug:
            continue
        for page in range(1, max(1, max_pages) + 1):
            url = f"{BASE}/internships/{slug}" if page == 1 else f"{BASE}/internships/{slug}/page-{page}"
            try:
                markup = fetch_text(url, max_age_hours=12, headers=HEADERS)
            except Exception as exc:  # markup change or block: keep other sources running
                log(f"internshala:{slug} page {page} failed ({exc})")
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
            log(f"internshala:{slug} page {page} -> {added} internships")
            if added == 0:
                break
    return jobs
