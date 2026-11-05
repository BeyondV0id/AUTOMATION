"""GiveMeAFuckingJ*b job listings.

The site publishes its whole catalogue as static files, so no browser and no
account are needed:

    /jobs/index.txt      first line is metadata, then one tab-separated row per
                         job: delta-encoded id, title, location, country codes,
                         flags, posting day, salary
    /jobs/<chunk>.json   details for a group of ids: apply URL, years, tasks,
                         perks, skills, roles, education, language

A chunk is named after the nearest job at or above it whose id hashes low
(the same rule the site's own search uses). The index is a few megabytes, so it
is cached for a day and filtered locally before any chunk is fetched.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse

from .net import fetch_json, fetch_text

BASE = "https://www.givemeafuckingjob.com"
INDEX_URL = f"{BASE}/jobs/index.txt"

# ATS hosts whose first label is the employer, not the vendor.
_ATS_SUFFIXES = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "bamboohr.com",
    "smartrecruiters.com", "jobvite.com", "icims.com", "taleo.net",
    "successfactors.com", "isolvedhire.com", "workday.com", "recruitee.com",
    "teamtailor.com", "personio.com", "jazzhr.com", "applytojob.com",
)
# Hosts that resell or aggregate listings rather than name the employer.
_AGGREGATORS = {"foorilla.com"}
_GENERIC_LABELS = {"apply", "jobs", "boards", "careers", "job-boards", "www", "hire"}

_WHITESPACE = re.compile(r"\s+")


def _today() -> int:
    """Days since the Unix epoch, the unit the index uses for posting day."""
    return int(time.time() // 86400)


def chunk_for(job_id: int, k: int, current: str) -> str:
    """Name the chunk a job belongs to, matching the site's hash rule."""
    hashed = (job_id * 0x9E3779B1) & 0xFFFFFFFF
    if hashed < (2 ** 32) / max(1, k):
        return str(job_id)
    return current


def parse_index(text: str) -> tuple[dict, list[dict]]:
    """Parse index.txt into metadata and job rows, rebuilding delta ids."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return {}, []
    meta = json.loads(lines[0])
    k = int(meta.get("k") or 64)
    rows: list[dict] = []
    job_id = 0
    chunk = "top"
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        job_id = job_id - int(fields[0]) if rows else int(fields[0])
        chunk = chunk_for(job_id, k, chunk)
        flags = fields[4]
        rows.append(
            {
                "id": job_id,
                "title": fields[1],
                "location": fields[2],
                "countries": [code for code in fields[3].split(" ") if code],
                "flags": flags,
                "day": int(fields[5]) if fields[5].lstrip("-").isdigit() else 0,
                "pay": fields[6],
                "chunk": chunk,
            }
        )
    return meta, rows


def _title(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").strip().title()


def company_from_url(url: str) -> str:
    """Best-effort employer name from an apply URL.

    The listings carry no company field, so the URL is the only signal. ATS
    hosts either name the employer in the subdomain (acme.greenhouse.io) or in
    the first path segment (apply.workable.com/acme). Aggregator hosts are left
    blank rather than mislabelled.
    """
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.hostname or ""
    if not host or host in _AGGREGATORS:
        return ""
    labels = host.split(".")
    for suffix in _ATS_SUFFIXES:
        parts = suffix.split(".")
        if not host.endswith(suffix):
            continue
        prefix = labels[: -len(parts)]
        named = [label for label in prefix if label not in _GENERIC_LABELS]
        if named:
            return _title(named[0])
        first = next(
            (segment for segment in parsed.path.split("/") if segment),
            "",
        )
        if first:
            return _title(first)
        return ""
    name = labels[-2] if len(labels) >= 2 else labels[0]
    return _title(name)


def _salary(pay: str) -> str:
    match = re.match(r"^([A-Z]{3})([\d.]+)(?:-([\d.]+))?(~?)$", pay or "")
    if not match:
        return ""
    currency, low, high, estimated = match.groups()
    unit = "L" if currency == "INR" else "K"
    text = f"{currency} {low}{unit}"
    if high:
        text += f"-{high}{unit}"
    return text + (" (estimated)" if estimated else "")


def pick(
    rows: list[dict],
    keywords: list[str],
    countries: list[str] | None = None,
    remote_only: bool = False,
    days_old: int | None = None,
    max_jobs: int = 40,
) -> list[dict]:
    """Filter the catalogue locally before any detail file is fetched."""
    wanted_words = [word.lower() for word in keywords if word.strip()]
    wanted_countries = {code.upper() for code in (countries or [])}
    cutoff = _today() - days_old if days_old else None
    chosen: list[dict] = []
    for row in rows:
        title = row["title"].lower()
        if wanted_words and not any(word in title for word in wanted_words):
            continue
        is_remote = "R" in row["flags"][2:3]
        in_country = bool(wanted_countries & set(row["countries"]))
        if wanted_countries and not in_country and not (remote_only and is_remote):
            continue
        if remote_only and not is_remote:
            continue
        if cutoff is not None and row["day"] < cutoff:
            continue
        chosen.append(row)
    chosen.sort(key=lambda row: row["day"], reverse=True)
    return chosen[:max_jobs]


def normalize(row: dict, detail: list | None) -> dict:
    url = ""
    skills: list[str] = []
    tasks: list[str] = []
    if isinstance(detail, list) and detail:
        url = detail[0] if isinstance(detail[0], str) else ""
        if len(detail) > 2 and isinstance(detail[2], list):
            tasks = [str(item) for item in detail[2]]
        if len(detail) > 4 and isinstance(detail[4], list):
            skills = [str(item) for item in detail[4]]
    parts = [f"via GiveMeAFuckingJ*b - {row['title']} in {row['location']}"]
    salary = _salary(row["pay"])
    if salary:
        parts.append(f"Salary: {salary}")
    if tasks:
        parts.append("What you will do: " + "; ".join(tasks[:8]))
    if skills:
        parts.append("Skills: " + ", ".join(skills[:20]))
    return {
        "id": f"gmafj:{row['id']}",
        "source": "gmafj",
        "company": company_from_url(url),
        "title": row["title"],
        "location": row["location"],
        "url": url or f"{BASE}/jobs/{row['chunk']}.json",
        "description": " ".join(parts),
        "skills": skills,
        "posted_at": None,
        "remote": "R" in row["flags"][2:3],
    }


def discover_gmafj(
    keywords: list[str],
    countries: list[str] | None = None,
    remote_only: bool = False,
    days_old: int | None = 45,
    max_jobs: int = 40,
    log=print,
) -> list[dict]:
    """Read the static catalogue, filter it, then fetch details for the picks."""
    text = fetch_text(INDEX_URL, max_age_hours=24)
    meta, rows = parse_index(text)
    if not rows:
        log("gmafj: index was empty or in an unexpected format")
        return []
    chosen = pick(rows, keywords, countries, remote_only, days_old, max_jobs)
    log(f"gmafj: {len(rows)} listings, {len(chosen)} picked")

    chunks: dict[str, dict] = {}
    jobs: list[dict] = []
    for row in chosen:
        chunk = row["chunk"]
        if chunk not in chunks:
            try:
                payload = fetch_json(f"{BASE}/jobs/{chunk}.json", max_age_hours=24)
                chunks[chunk] = payload if isinstance(payload, dict) else {}
            except Exception as exc:
                log(f"gmafj: chunk {chunk} failed ({exc})")
                chunks[chunk] = {}
        jobs.append(normalize(row, chunks[chunk].get(str(row["id"]))))
    log(f"gmafj: {len(jobs)} jobs with details")
    return jobs
