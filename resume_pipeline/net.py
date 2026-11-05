"""Cached, rate-limited HTTP helpers for public job sources.

Every response is cached on disk so a second run on the same day costs no
network. Requests to the same host are spaced out to stay polite.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import DATA

CACHE = DATA / "cache"
UA = "job-automation/0.2 (local terminal job search)"
MIN_INTERVAL_SECONDS = 1.5

_last_request: dict[str, float] = {}


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return CACHE / f"{digest}.json"


def _throttle(host: str) -> None:
    now = time.monotonic()
    last = _last_request.get(host)
    if last is not None:
        wait = MIN_INTERVAL_SECONDS - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_request[host] = time.monotonic()


def fetch_text(
    url: str,
    max_age_hours: float = 12.0,
    timeout: int = 25,
    headers: dict[str, str] | None = None,
) -> str:
    """Return the body for `url`, from disk cache when it is fresh enough."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)
    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours <= max_age_hours:
            try:
                return json.loads(path.read_text(encoding="utf-8"))["body"]
            except (ValueError, KeyError):
                path.unlink(missing_ok=True)

    host = urllib.parse.urlparse(url).netloc
    _throttle(host)
    request = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    path.write_text(json.dumps({"url": url, "body": body}), encoding="utf-8")
    return body


def fetch_json(
    url: str,
    max_age_hours: float = 12.0,
    timeout: int = 25,
    headers: dict[str, str] | None = None,
) -> Any:
    return json.loads(fetch_text(url, max_age_hours, timeout, headers))


def fetch_text_post(
    url: str,
    payload: Any,
    max_age_hours: float = 12.0,
    timeout: int = 25,
    headers: dict[str, str] | None = None,
) -> str:
    """POST JSON and return the body, cached by URL plus request body.

    Some public endpoints (MCP servers) only answer POST. Caching the exact
    request keeps a repeat run offline while still allowing different queries.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, sort_keys=True)
    path = _cache_path(f"{url}\n{body}")
    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours <= max_age_hours:
            try:
                return json.loads(path.read_text(encoding="utf-8"))["body"]
            except (ValueError, KeyError):
                path.unlink(missing_ok=True)

    host = urllib.parse.urlparse(url).netloc
    _throttle(host)
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={"User-Agent": UA, "Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
    path.write_text(json.dumps({"url": url, "body": text}), encoding="utf-8")
    return text
