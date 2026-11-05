"""HiringCafe job search.

HiringCafe is a Next.js app behind Cloudflare. The rendered page carries a
``buildId`` in ``__NEXT_DATA__``, and search results come from a same-origin
data route:

    GET /_next/data/<buildId>/classic.json?searchState=<json>&page=N

Bare HTTP clients get a 403 challenge, so this source drives Chromium through
Playwright, asks the page to fetch the data route, and reads the JSON. Headful
is the default for the same reason Naukri needs it; the challenge clears in a
few seconds when a real window is visible.
"""

from __future__ import annotations

import json
import urllib.parse

BASE = "https://hiring.cafe"
HOMEPAGE = f"{BASE}/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# HiringCafe's backend wants Google-Places-shaped components with ISO codes.
COUNTRIES = {
    "india": ("India", "IN"),
    "united states": ("United States", "US"),
    "usa": ("United States", "US"),
    "united kingdom": ("United Kingdom", "GB"),
    "uk": ("United Kingdom", "GB"),
    "canada": ("Canada", "CA"),
    "australia": ("Australia", "AU"),
    "singapore": ("Singapore", "SG"),
    "germany": ("Germany", "DE"),
    "netherlands": ("Netherlands", "NL"),
    "united arab emirates": ("United Arab Emirates", "AE"),
    "uae": ("United Arab Emirates", "AE"),
}


def location_state(name: str) -> dict | None:
    """Build a country-level location object, or None when unknown."""
    pair = COUNTRIES.get((name or "").strip().lower())
    if not pair:
        return None
    long_name, code = pair
    return {
        "formatted_address": long_name,
        "types": ["country"],
        "id": "user_defined",
        "address_components": [
            {"long_name": long_name, "short_name": code, "types": ["country"]}
        ],
        "options": {},
    }


def search_state(
    query: str,
    location: str | None = None,
    days_old: int | None = 7,
) -> dict:
    state: dict = {}
    if query:
        state["searchQuery"] = query
    state["sortBy"] = "date"
    if days_old is not None:
        state["dateFetchedPastNDays"] = days_old
    place = location_state(location) if location else None
    if place:
        state["locations"] = [place]
    return state


def _text(value: object, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _salary(v5: dict) -> str:
    currency = v5.get("listed_compensation_currency") or ""
    low = v5.get("yearly_min_compensation")
    high = v5.get("yearly_max_compensation")
    if not currency or (low is None and high is None):
        return ""
    def amount(value: object) -> str:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return ""
        return f"{number / 1000:.0f}K" if number >= 1000 else f"{number:.0f}"
    parts = [amount(low), amount(high)]
    span = "-".join(part for part in parts if part)
    return f"{currency} {span}".strip()


def normalize(hit: dict) -> dict:
    """Turn one search hit into the pipeline's job shape."""
    v5 = hit.get("v5_processed_job_data") or {}
    info = hit.get("job_information") or {}
    title = _text(info.get("title") or info.get("job_title_raw"), 200)
    company = _text(v5.get("company_name"), 120)
    location = _text(v5.get("formatted_workplace_location"), 160)
    url = hit.get("apply_url") or f"{BASE}/job/{hit.get('objectID') or hit.get('id')}"

    tools = [str(item) for item in (v5.get("technical_tools") or [])][:25]
    activities = [str(item) for item in (v5.get("role_activities") or [])][:8]
    parts = [f"{title} at {company}".strip(" at")]
    if location:
        parts.append(location)
    meta = [v5.get("workplace_type"), *(v5.get("commitment") or []), v5.get("seniority_level")]
    meta = [str(item) for item in meta if item]
    if meta:
        parts.append(" - ".join(dict.fromkeys(meta)))
    salary = _salary(v5)
    if salary:
        parts.append(f"Salary: {salary}")
    if v5.get("requirements_summary"):
        parts.append("Requirements: " + _text(v5["requirements_summary"], 900))
    if activities:
        parts.append("What you will do: " + "; ".join(activities))
    if tools:
        parts.append("Tools: " + ", ".join(tools))

    posted = v5.get("estimated_publish_date")
    return {
        "id": f"hiringcafe:{hit.get('objectID') or hit.get('id')}",
        "source": "hiringcafe",
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "description": " ".join(parts),
        "skills": tools,
        "posted_at": str(posted)[:10] if posted else None,
        "remote": str(v5.get("workplace_type") or "").lower() == "remote",
    }


def _read_build_id(page) -> str | None:
    return page.evaluate(
        """() => {
            const node = document.getElementById('__NEXT_DATA__');
            if (!node) return null;
            try { return JSON.parse(node.textContent).buildId || null; }
            catch (e) { return null; }
        }"""
    )


def _wait_past_challenge(page, wait_ms: int) -> None:
    """Cloudflare serves 'Just a moment...' until the challenge clears."""
    for _ in range(max(1, wait_ms // 3000)):
        if "just a moment" not in (page.title() or "").lower():
            return
        page.wait_for_timeout(3000)


def _fetch_page(page, build_id: str, state: dict, page_number: int) -> dict:
    encoded = urllib.parse.quote(json.dumps(state, separators=(",", ":")))
    url = f"/_next/data/{build_id}/classic.json?searchState={encoded}&page={page_number}"
    body = page.evaluate(
        """async (url) => {
            const response = await fetch(url, { headers: { accept: 'application/json' } });
            return await response.text();
        }""",
        url,
    )
    return json.loads(body)


def discover_hiringcafe(
    queries: list[str],
    location: str | None = "India",
    days_old: int | None = 7,
    max_pages: int = 1,
    headful: bool = True,
    wait_ms: int = 9000,
    max_jobs: int = 80,
    log=print,
) -> list[dict]:
    """Search HiringCafe and return normalized jobs."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("playwright is required for the HiringCafe source") from exc

    jobs: list[dict] = []
    seen: set[str] = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not headful,
            args=["--disable-blink-features=AutomationControlled"] if headful else [],
        )
        context = browser.new_context(user_agent=USER_AGENT, locale="en-US")
        page = context.new_page()
        try:
            page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=60000)
            _wait_past_challenge(page, wait_ms)
            build_id = _read_build_id(page)
            if not build_id:
                log("hiringcafe: could not read buildId (challenge not cleared?)")
                return []

            for query in queries:
                state = search_state(query, location, days_old)
                for page_number in range(max(1, max_pages)):
                    try:
                        payload = _fetch_page(page, build_id, state, page_number)
                    except Exception as exc:
                        log(f"hiringcafe:{query} page {page_number + 1} failed ({exc})")
                        break
                    props = payload.get("pageProps") or {}
                    if props.get("__N_REDIRECT"):
                        log(f"hiringcafe:{query} redirected; skipping")
                        break
                    hits = props.get("ssrHits") or []
                    added = 0
                    for hit in hits:
                        job = normalize(hit)
                        if not job["title"] or job["id"] in seen:
                            continue
                        seen.add(job["id"])
                        jobs.append(job)
                        added += 1
                    log(
                        f"hiringcafe:{query} page {page_number + 1} -> {added} jobs "
                        f"(of {props.get('ssrTotalCount') or '?'})"
                    )
                    if added == 0 or props.get("ssrIsLastPage") or len(jobs) >= max_jobs:
                        break
                if len(jobs) >= max_jobs:
                    break
        finally:
            browser.close()
    return jobs[:max_jobs]
