"""Hacker News "Who is hiring?" — the startup board with no gatekeeper.

Every month the ``whoishiring`` account posts a thread and founders post one
top-level comment per open role. There is no API key, no browser and no
pagination puzzle: Algolia mirrors the whole thread as JSON.

Each comment is free text, so the parser is deliberately forgiving. It reads
the usual shapes:

    Acme | Backend Engineer | Remote (US) | https://acme.dev/jobs
    Acme is hiring a founding engineer in Bengaluru (on-site)
    **Company:** Acme  **Role:** Full-stack developer  **Location:** Remote

Nothing is inferred: if a field is not in the comment, it stays empty.
"""

from __future__ import annotations

import html as html_module
import re
from datetime import datetime, timezone
from typing import Any

from .net import fetch_json

ALGOLIA = "https://hn.algolia.com/api/v1"
SEARCH_URL = f"{ALGOLIA}/search_by_date?tags=story,author_whoishiring&hitsPerPage=24"
THREAD_TITLE = re.compile(r"who is hiring", re.I)

ROLE_WORDS = re.compile(
    r"\b(engineer|developer|programmer|scientist|architect|sre|devops|intern|"
    r"internship|analyst|designer|manager|founding)\b",
    re.I,
)
LOCATION_HINT = re.compile(
    r"\b(remote|onsite|on-site|hybrid|india|bengaluru|bangalore|hyderabad|pune|"
    r"chennai|mumbai|delhi|noida|gurgaon|gurugram|kolkata|anywhere|worldwide)\b",
    re.I,
)
_LINK = re.compile(r"https?://[^\s<>\"')]+", re.I)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\u00a0]+")


def clean_text(raw: str) -> str:
    """Turn comment HTML into plain lines."""
    text = raw or ""
    text = re.sub(r"<\s*(p|br|div|li)\s*/?>", "\n", text, flags=re.I)
    text = _TAGS.sub(" ", text)
    text = html_module.unescape(text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _segments(first_line: str) -> list[str]:
    return [part.strip(" *:") for part in re.split(r"\s*[|•·]\s*", first_line) if part.strip(" *:")]


def _pick(parts: list[str]) -> tuple[str, str, str]:
    """Company, role and location from the pipe-separated first line."""
    company = parts[0] if parts else ""
    role = ""
    location = ""
    for part in parts[1:]:
        if not role and ROLE_WORDS.search(part) and len(part) <= 90:
            role = part
        elif not location and LOCATION_HINT.search(part) and len(part) <= 70:
            location = part
    if not role:
        for part in parts[1:]:
            if not _LINK.search(part) and len(part) <= 90:
                role = part
                break
    return company, role, location


def _from_sentence(first_line: str) -> tuple[str, str, str]:
    """Read the "Acme is hiring a founding engineer in Bengaluru" shape."""
    match = re.match(
        r"^(?P<company>[^.,|]{2,60}?)\s+(?:is\s+)?(?:hiring|looking for|seeks?|seeking)\s+"
        r"(?:a|an|our)?\s*(?P<title>[^.,|]{3,90})",
        first_line,
        re.I,
    )
    if not match:
        return "", "", ""
    company = match.group("company").strip()
    title = match.group("title").strip()
    location = ""
    where = re.search(r"\b(?:in|at)\s+([A-Z][^.,|]{2,40})$", title)
    if where:
        location = where.group(1).strip()
        title = title[: where.start()].strip()
    return company, title, location


def parse_comment(raw_html: str) -> dict[str, Any] | None:
    """One top-level HN comment -> a job dict, or None when it is not a job."""
    text = clean_text(raw_html)
    if not text:
        return None
    first = text.splitlines()[0]
    parts = _segments(first)
    company, role, location = _pick(parts) if len(parts) > 1 else ("", "", "")
    if not role:
        company, role, location = _from_sentence(first)
    if not role:
        # A comment that never names a role is discussion, not a posting.
        if not ROLE_WORDS.search(first):
            return None
        role = first[:90]

    link = _LINK.search(text)
    return {
        "company": company,
        "title": role,
        "location": location,
        "url": link.group(0).rstrip(".,)") if link else "",
        "description": text[:4000],
    }


def latest_thread() -> dict | None:
    """The newest "Ask HN: Who is hiring?" story."""
    payload = fetch_json(SEARCH_URL, max_age_hours=6)
    for hit in payload.get("hits") or []:
        title = str(hit.get("title") or "")
        if THREAD_TITLE.search(title):
            return hit
    return None


def discover_hn(
    keywords: list[str] | None = None,
    max_comments: int = 400,
    days_old: int = 40,
    remote_only: bool = False,
    log=print,
) -> list[dict]:
    """Read the current hiring thread and keep the comments that look like jobs."""
    keywords = [word.lower() for word in (keywords or []) if word.strip()]
    try:
        thread = latest_thread()
    except Exception as exc:  # network failure: keep other sources running
        log(f"hn: thread unavailable ({exc})")
        return []
    if not thread:
        log("hn: no Who is hiring thread found")
        return []

    thread_id = thread.get("objectID")
    created = str(thread.get("created_at") or "")
    try:
        age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(created.replace("Z", "+00:00"))).days
    except ValueError:
        age_days = 0
    if days_old and age_days > days_old:
        log(f"hn: latest thread is {age_days}d old, older than the {days_old}d limit")
        return []

    try:
        item = fetch_json(f"{ALGOLIA}/items/{thread_id}", max_age_hours=6)
    except Exception as exc:
        log(f"hn: thread {thread_id} unavailable ({exc})")
        return []

    jobs: list[dict] = []
    seen: set[str] = set()
    for child in (item.get("children") or [])[:max_comments]:
        if not isinstance(child, dict) or child.get("deleted"):
            continue
        parsed = parse_comment(str(child.get("text") or ""))
        if not parsed or not parsed["title"]:
            continue
        haystack = f"{parsed['title']} {parsed['company']} {parsed['description']}".lower()
        if keywords and not any(word in haystack for word in keywords):
            continue
        if remote_only and "remote" not in f"{parsed['location']} {parsed['description']}".lower():
            continue
        comment_id = str(child.get("id") or "")
        if not comment_id or comment_id in seen:
            continue
        seen.add(comment_id)
        jobs.append(
            {
                "id": f"hn:{comment_id}",
                "source": "hn",
                "company": parsed["company"],
                "title": parsed["title"],
                "location": parsed["location"],
                "url": parsed["url"] or f"https://news.ycombinator.com/item?id={comment_id}",
                "description": parsed["description"],
                "skills": [],
                "posted_at": child.get("created_at") or None,
                "remote": "remote" in f"{parsed['location']} {parsed['description']}".lower(),
            }
        )

    log(f"hn: {thread.get('title')} -> {len(jobs)} postings from {len(item.get('children') or [])} comments")
    return jobs
