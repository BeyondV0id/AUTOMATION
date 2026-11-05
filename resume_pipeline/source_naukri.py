"""Naukri job search.

Naukri serves search results from JavaScript and its JSON API answers with a
reCAPTCHA challenge, so this source drives a real Chromium window through
Playwright and reads the rendered cards. It is the only source that needs a
visible browser: leave ``headful = true`` (the default) or Naukri will answer
with "Access Denied".

The rest of the pipeline keeps working when Playwright is missing or Naukri
blocks the request; the failure is logged and the source returns no jobs.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from . import DATA

CACHE = DATA / "cache"
_WHITESPACE = re.compile(r"\s+")

EXTRACT = """() => {
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  return Array.from(document.querySelectorAll('.srp-jobtuple-wrapper')).map((card) => {
    const title = card.querySelector('a.title');
    return {
      url: title ? title.href : '',
      title: clean(title ? title.textContent : ''),
      company: clean((card.querySelector('.comp-name') || {}).textContent),
      location: clean((card.querySelector('.locWdth') || {}).textContent),
      experience: clean((card.querySelector('.expwdth') || {}).textContent),
      salary: clean((card.querySelector('.sal-wrap span') || {}).textContent),
      description: clean((card.querySelector('.job-desc') || {}).textContent),
      skills: Array.from(card.querySelectorAll('.tags-gt li')).map((item) => clean(item.textContent)),
      posted: clean((card.querySelector('.job-post-day') || {}).textContent)
    };
  });
}"""


def normalize(item: dict[str, Any]) -> dict:
    """Convert one rendered Naukri card into the pipeline's job shape."""
    url = str(item.get("url") or "")
    title = str(item.get("title") or "").strip()
    company = str(item.get("company") or "").strip()
    location = str(item.get("location") or "").strip()
    experience = str(item.get("experience") or "").strip()
    salary = str(item.get("salary") or "").strip()
    posted = str(item.get("posted") or "").strip()
    description = str(item.get("description") or "").strip()
    skills = [str(skill).strip() for skill in item.get("skills") or [] if str(skill).strip()]

    path = urllib.parse.urlparse(url).path.rstrip("/")
    job_id = path.rsplit("/", 1)[-1] if path else hashlib.sha1(f"{title}{company}".encode()).hexdigest()[:16]

    details = [part for part in (experience, salary, description) if part]
    details += skills
    return {
        "id": f"naukri:{job_id}",
        "source": "naukri",
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "description": " | ".join(dict.fromkeys(details)),
        "skills": list(dict.fromkeys(skills)),
        "posted_at": posted or None,
        "remote": bool(re.search(r"remote|work from home|wfh", location, re.I)),
    }


def _search_url(keyword: str, location: str, page: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-") or "jobs"
    params = {"k": keyword}
    if location:
        params["l"] = location
    if page > 1:
        params["pageNo"] = page
    return f"https://www.naukri.com/{slug}-jobs?{urllib.parse.urlencode(params)}"


def discover_naukri(
    keywords: list[str],
    location: str = "",
    max_pages: int = 1,
    headful: bool = True,
    wait_ms: int = 6000,
    timeout_ms: int = 60000,
    profile_dir: str | Path | None = None,
    log=print,
) -> list[dict]:
    """Search Naukri with a rendered browser and return its cards."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("naukri: playwright is not installed; skipping")
        return []

    CACHE.mkdir(parents=True, exist_ok=True)
    profile = Path(profile_dir) if profile_dir else Path(tempfile.mkdtemp(prefix="naukri-", dir=CACHE))
    temporary = profile_dir is None

    jobs: list[dict] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=not headful,
                viewport={"width": 1400, "height": 950},
                locale="en-IN",
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                for keyword in keywords:
                    for page_number in range(1, max(1, max_pages) + 1):
                        url = _search_url(keyword, location, page_number)
                        try:
                            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                            page.wait_for_selector(".srp-jobtuple-wrapper", timeout=15000)
                            page.wait_for_timeout(wait_ms)
                            cards = page.evaluate(EXTRACT)
                        except Exception as exc:
                            log(f"naukri:{keyword} page {page_number} failed ({exc})")
                            break
                        found = [normalize(card) for card in cards if card.get("title")]
                        added = 0
                        for job in found:
                            if job["id"] in seen:
                                continue
                            seen.add(job["id"])
                            jobs.append(job)
                            added += 1
                        log(f"naukri:{keyword} page {page_number} -> {added} jobs")
                        if added == 0:
                            break
            finally:
                context.close()
    except Exception as exc:  # browser launch or block: do not fail discovery
        log(f"naukri: unavailable ({exc})")
    finally:
        if temporary:
            shutil.rmtree(profile, ignore_errors=True)
    return jobs
