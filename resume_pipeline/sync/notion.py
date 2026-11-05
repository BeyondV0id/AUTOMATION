"""Publish the tracker to a Notion database, one page per job.

One-way on purpose: Notion is a read-only view of `jobs.status`. Status edits
belong in Excel or Gmail sync, so there is exactly one place a change can come
from.

Needs an internal integration token (JOB_NOTION_TOKEN) and a database shared
with that integration.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field as dc_field
from pathlib import Path

from . import store

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
DEFAULT_PROPERTIES = {
    "title": "Name",
    "company": "Company",
    "role": "Role",
    "status": "Status",
    "score": "Score",
    "location": "Location",
    "url": "URL",
    "applied": "Applied",
}
PUBLISHED_STATUSES = {"applied", "interview", "offer", "rejected"}


@dataclass
class NotionConfig:
    token: str = ""
    database_id: str = ""
    include_all: bool = False
    properties: dict = dc_field(default_factory=lambda: dict(DEFAULT_PROPERTIES))

    @property
    def configured(self) -> bool:
        return bool(self.token and self.database_id)


def config_from(mapping: dict, env: dict | None = None) -> NotionConfig:
    import os

    env = env if env is not None else os.environ
    section = mapping.get("notion") or {}
    props = dict(DEFAULT_PROPERTIES)
    props.update({k: str(v) for k, v in (section.get("properties") or {}).items() if v})
    return NotionConfig(
        token=str(env.get("JOB_NOTION_TOKEN", "")),
        database_id=str(section.get("database_id") or env.get("JOB_NOTION_DATABASE_ID", "")),
        include_all=bool(section.get("include_all", False)),
        properties=props,
    )


def _request(cfg: NotionConfig, method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{API}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {cfg.token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"Notion {method} {path} failed ({exc.code}): {detail}") from exc


def database_schema(cfg: NotionConfig) -> dict[str, str]:
    data = _request(cfg, "GET", f"/databases/{cfg.database_id}")
    return {name: prop.get("type", "") for name, prop in (data.get("properties") or {}).items()}


def _text(value: str) -> list[dict]:
    return [{"type": "text", "text": {"content": value[:1900]}}] if value else []


def build_properties(job: dict, cfg: NotionConfig, schema: dict[str, str], title_name: str) -> dict:
    props = cfg.properties
    status = job.get("status") or "new"
    score = job.get("match_score")
    applied = (job.get("applied_at") or "")[:10] or None

    def allowed(key: str) -> bool:
        return props.get(key) in schema

    payload: dict = {}

    name = f"{job.get('company') or 'Unknown'} — {job.get('title') or 'Role'}"
    payload[title_name] = {"title": [{"type": "text", "text": {"content": name[:1900]}}]}

    if allowed("company"):
        payload[props["company"]] = {"rich_text": _text(job.get("company") or "")}
    if allowed("role"):
        payload[props["role"]] = {"rich_text": _text(job.get("title") or "")}
    if allowed("status"):
        payload[props["status"]] = {"select": {"name": status}}
    if allowed("score") and score is not None:
        payload[props["score"]] = {"number": round(float(score), 1)}
    if allowed("location"):
        payload[props["location"]] = {"rich_text": _text(job.get("location") or "")}
    if allowed("url") and job.get("url"):
        payload[props["url"]] = {"url": job["url"]}
    if allowed("applied") and applied:
        payload[props["applied"]] = {"date": {"start": applied}}
    return payload


def publish(
    cfg: NotionConfig,
    *,
    db_path: Path | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    if not cfg.configured:
        return {
            "error": "Notion is not configured. Set [notion].database_id and JOB_NOTION_TOKEN.",
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }

    schema = database_schema(cfg)
    titles = [name for name, kind in schema.items() if kind == "title"]
    if not titles:
        return {"error": "target database has no title property", "created": 0, "updated": 0, "skipped": 0, "errors": 0}
    title_name = cfg.properties["title"] if cfg.properties["title"] in schema else titles[0]

    jobs = store.all_jobs(db_path)
    selected = [
        job for job in jobs
        if cfg.include_all or (job.get("status") or "new") in PUBLISHED_STATUSES
    ]
    selected.sort(key=lambda j: (j.get("match_score") is not None, j.get("match_score") or 0), reverse=True)
    if limit:
        selected = selected[:limit]

    summary = {"created": 0, "updated": 0, "skipped": 0, "errors": 0, "sample": None}
    for job in selected:
        properties = build_properties(job, cfg, schema, title_name)
        page_id = store.notion_page(job["id"], db_path)
        try:
            if dry_run:
                summary["sample"] = {"job": job["id"], "properties": properties}
                summary["skipped" if page_id else "created"] += 1
                continue
            if page_id:
                _request(cfg, "PATCH", f"/pages/{page_id}", {"properties": properties})
                summary["updated"] += 1
            else:
                page = _request(
                    cfg,
                    "POST",
                    "/pages",
                    {"parent": {"database_id": cfg.database_id}, "properties": properties},
                )
                store.save_notion_page(job["id"], page["id"], db_path)
                summary["created"] += 1
            time.sleep(0.35)  # Notion allows ~3 requests/second
        except RuntimeError as exc:
            summary["errors"] += 1
            summary.setdefault("messages", []).append(f"{job['id']}: {exc}")
    return summary
