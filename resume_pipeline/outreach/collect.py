"""Find a contact address for a posting and keep it in one list.

Three passes, cheapest first:

1. the description we already scraped (free, no network)
2. the job page itself (one cached request)
3. the employer's /contact and /careers pages, only when the job is hosted on
   the employer's own domain

Addresses are ranked, not taken in document order: ``careers@`` beats
``support@`` even if support appears first. A guess is never invented from a
name pattern, because a bounced address costs sender reputation.
"""

from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from .. import DATA, net
from . import store
from .compose import ATS_HOSTS, EMAIL, extract_emails

# Local parts that suggest someone who can act on an application.
ROLE_HINTS = (
    "career", "job", "hr", "hiring", "talent", "recruit", "apply",
    "resume", "cv", "people", "hello", "contact", "info", "team",
)
# Never worth a send.
BAD_LOCAL = (
    "support", "billing", "sales", "press", "abuse", "privacy", "legal",
    "security", "webmaster", "admin", "marketing", "newsletter",
)
MAILTO = re.compile(r"""mailto:([^"'?>\s]+)""", re.I)
CONTACT_PATHS = ("/contact", "/contact-us", "/careers", "/about")
MAX_PAGES_PER_JOB = 3


def domain_of(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    return host.removeprefix("www.")


def _local(address: str) -> str:
    return address.split("@", 1)[0].lower()


def rank(addresses: list[str]) -> list[str]:
    """Order addresses by how likely a human reads them."""
    scored: list[tuple[int, str]] = []
    for address in addresses:
        local = _local(address)
        score = 100
        if any(local.startswith(hint) or local == hint for hint in ROLE_HINTS):
            score = 0
        elif any(hint in local for hint in ROLE_HINTS):
            score = 10
        if any(bad in local for bad in BAD_LOCAL):
            score += 50
        # A person's name (first.last) is a decent second choice.
        if re.fullmatch(r"[a-z]+[._-][a-z]+", local):
            score = min(score, 20)
        scored.append((score, address))
    return [address for _, address in sorted(scored)]


def emails_from_html(source: str) -> list[str]:
    """Pull mailto links and visible addresses out of a page."""
    found: list[str] = []
    for raw in MAILTO.findall(source or ""):
        address = html.unescape(raw).strip().lower()
        if EMAIL.fullmatch(address):
            found.append(address)
    found.extend(extract_emails(html.unescape(source or "")))
    seen: list[str] = []
    for address in found:
        if address not in seen:
            seen.append(address)
    return rank(seen)


def _pages_for(job: dict) -> list[str]:
    """The job page, plus contact pages when the employer owns the domain."""
    pages: list[str] = []
    url = job.get("url") or ""
    if not url:
        return pages
    pages.append(url)
    host = domain_of(url)
    if host and not ATS_HOSTS.search(host):
        pages.extend(f"https://{host}{path}" for path in CONTACT_PATHS)
    return pages


def collect(
    jobs: list[dict],
    *,
    fetch_pages: bool = False,
    limit: int | None = None,
    db_path: Path | None = None,
    log=print,
) -> dict:
    summary = {
        "scanned": 0,
        "from_description": 0,
        "from_page": 0,
        "already_had": 0,
        "none": 0,
        "errors": 0,
        "contacts": 0,
    }
    if limit:
        jobs = jobs[:limit]

    for job in jobs:
        summary["scanned"] += 1
        if (job.get("contact_email") or "").strip():
            summary["already_had"] += 1
            continue

        address = ""
        source = ""

        found = rank(extract_emails(job.get("description") or ""))
        if found:
            address, source = found[0], "description"

        if not address and fetch_pages:
            for page in _pages_for(job)[:MAX_PAGES_PER_JOB]:
                try:
                    body = net.fetch_text(page, max_age_hours=24 * 7, timeout=20)
                except Exception:  # noqa: BLE001 - one dead page must not stop the run
                    summary["errors"] += 1
                    continue
                found = emails_from_html(body)
                if found:
                    address, source = found[0], f"page:{domain_of(page) or page}"
                    break

        if not address:
            summary["none"] += 1
            if fetch_pages:
                log(f"  no contact  {(job.get('company') or '')[:30]:30} {(job.get('title') or '')[:40]}")
            continue

        store.set_job_contact(job["id"], address, db_path)
        store.save_contact(
            email=address,
            company=job.get("company") or "",
            domain=address.split("@", 1)[-1],
            job_id=job["id"],
            source=source,
            path=db_path,
        )
        summary["from_description" if source == "description" else "from_page"] += 1
        summary["contacts"] += 1
        log(f"  {address:38} {source:22} {(job.get('company') or '')[:28]}")

    return summary


def select_jobs(
    *,
    statuses: list[str] | None = None,
    min_score: float | None = None,
    source: str | None = None,
    missing_only: bool = True,
    limit: int | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    query = "SELECT * FROM jobs WHERE url IS NOT NULL AND url != ''"
    params: list[object] = []
    if statuses:
        query += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
        params.extend(statuses)
    if min_score is not None:
        query += " AND COALESCE(match_score, 0) >= ?"
        params.append(min_score)
    if source:
        query += " AND source = ?"
        params.append(source)
    if missing_only:
        query += " AND (contact_email IS NULL OR contact_email = '')"
    query += " ORDER BY COALESCE(match_score, -1) DESC, discovered_at DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    conn = sqlite3.connect(db_path or DATA / "applications.db")
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(query, params)]
    finally:
        conn.close()
