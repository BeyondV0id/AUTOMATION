"""engg.space job search over the public MCP endpoint.

https://www.engg.space/mcp is a read-only, keyless MCP server. It searches
engineering roles that are read from company career boards and returns the
employer's own apply URL. This module speaks the small part of MCP it needs
(initialize + tools/call) over plain HTTP, with the same disk cache as every
other source.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from .net import fetch_text_post

MCP_URL = "https://www.engg.space/mcp"
HEADERS = {"Accept": "application/json, text/event-stream"}

_WHITESPACE = re.compile(r"\s+")


def _payload(text: str) -> dict:
    """Return the JSON-RPC message from a JSON or server-sent-events body."""
    messages: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                message = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(message, dict):
                messages.append(message)
    for message in messages:
        if "result" in message or "error" in message:
            return message
    if messages:
        return messages[0]
    return json.loads(text)


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Initialize a session and call one tool, returning its result object."""
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "job-automation", "version": "0.3"},
        },
    }
    fetch_text_post(MCP_URL, initialize, max_age_hours=24, headers=HEADERS)
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    message = _payload(fetch_text_post(MCP_URL, request, max_age_hours=6, headers=HEADERS))
    if "error" in message:
        raise RuntimeError(f"MCP error: {message['error']}")
    return message.get("result") or {}


def _slug(url: str) -> str:
    path = urllib.parse.urlparse(url or "").path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "job"


def normalize(item: dict[str, Any]) -> dict:
    """Convert one engg.space result into the pipeline's job shape."""
    page_url = str(item.get("url") or "")
    apply_url = str(item.get("apply_url") or "")
    url = apply_url or page_url
    title = str(item.get("title") or "").strip()
    company = str(item.get("company") or "").strip()
    location = str(item.get("location") or "").strip()
    workplace = str(item.get("workplace") or "").strip()
    level = str(item.get("level") or "").strip()
    salary = str(item.get("salary") or "").strip()
    funding = str(item.get("company_funding") or "").strip()
    years = item.get("min_years")
    remote = "remote" in f"{workplace} {location}".lower()

    details = []
    if level:
        details.append(f"{level} level")
    if years is not None:
        details.append(f"minimum {years} years")
    if workplace:
        details.append(workplace)
    if salary:
        details.append(salary)
    if funding:
        details.append(funding)

    return {
        "id": f"engg:{_slug(page_url or url)}",
        "source": "engg",
        "company": company,
        "title": title,
        "location": location or workplace,
        "url": url or page_url,
        "description": " | ".join(details),
        "skills": [],
        "posted_at": item.get("posted") or None,
        "remote": remote,
    }


def search_jobs(
    query: str,
    limit: int = 25,
    level: str | None = None,
    remote: bool | None = None,
    location: str | None = None,
    max_years: int | None = None,
    posted_within_days: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """Run one search and normalize its results."""
    arguments: dict[str, Any] = {"query": query, "limit": max(1, min(int(limit), 25)), "offset": offset}
    if level:
        arguments["level"] = level
    if remote is not None:
        arguments["remote"] = remote
    if location:
        arguments["location"] = location
    if max_years is not None:
        arguments["max_years"] = max_years
    if posted_within_days is not None:
        arguments["posted_within_days"] = posted_within_days

    result = call_tool("search_jobs", arguments)
    structured = result.get("structuredContent") or {}
    return [normalize(item) for item in structured.get("results") or [] if item.get("title")]


def discover_engg(queries: list[Any], log=print, **defaults: Any) -> list[dict]:
    """Run every configured query and de-duplicate by posting URL."""
    jobs: list[dict] = []
    seen: set[str] = set()
    for entry in queries or []:
        options = dict(defaults)
        if isinstance(entry, str):
            options["query"] = entry
        elif isinstance(entry, dict):
            options.update(entry)
        else:
            continue
        query = str(options.pop("query", "") or "").strip()
        if not query:
            continue
        try:
            found = search_jobs(query, **options)
        except Exception as exc:  # network or schema failure: keep other sources running
            log(f"engg:{query} failed ({exc})")
            continue
        added = 0
        for job in found:
            if job["id"] in seen:
                continue
            seen.add(job["id"])
            jobs.append(job)
            added += 1
        log(f"engg:{query} -> {added} jobs")
    return jobs
