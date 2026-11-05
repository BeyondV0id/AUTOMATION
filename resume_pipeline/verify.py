"""Fact-check a tailoring overlay against the verified profile.

The generator refuses to render an overlay that introduces a number, tool or
skill the profile does not contain. This is the guardrail that makes model
rewriting safe: text may change, facts may not.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .tailor_variants import NUMBER, TOKEN, tokens

# Tools and platforms that must exist in the profile before a rewrite may name them.
TECH_VOCAB = {
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c++", "c#",
    "ruby", "php", "scala", "kotlin", "swift", "sql", "bash", "html", "css",
    "react", "next.js", "vue", "angular", "svelte", "node", "node.js", "express",
    "django", "flask", "fastapi", "spring", "rails", "laravel", ".net",
    "postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis", "elasticsearch",
    "kafka", "rabbitmq", "spark", "hadoop", "airflow", "dbt", "snowflake", "bigquery",
    "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "ansible", "jenkins",
    "github actions", "gitlab", "ci/cd", "linux", "nginx", "graphql", "rest", "grpc",
    "pytorch", "tensorflow", "keras", "scikit-learn", "pandas", "numpy", "opencv",
    "langchain", "llamaindex", "hugging face", "transformers", "openai", "llm",
    "rag", "vector database", "pinecone", "weaviate", "faiss", "mlflow", "wandb",
    "prometheus", "grafana", "datadog", "sentry", "jest", "pytest", "cypress",
    "playwright", "selenium", "figma", "jira", "git", "github", "gitlab ci",
    "microservices", "serverless", "websockets", "oauth", "jwt", "saml",
}

MAX_BULLET = 320
MAX_SUMMARY = 800


def _profile_text(profile: dict[str, Any]) -> str:
    return json.dumps(profile, ensure_ascii=False).lower()


def _record_key(name: str, title: str) -> tuple[str, str]:
    return (name.strip().lower(), title.strip().lower())


def _source_bullets(profile: dict[str, Any]) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    experience: dict[tuple[str, str], str] = {}
    for item in profile.get("experience", []):
        key = _record_key(str(item.get("company", "")), str(item.get("title", "")))
        experience[key] = " ".join(str(x) for x in item.get("bullets", item.get("highlights", [])) or [])
    projects: dict[str, str] = {}
    for item in profile.get("projects", []):
        projects[str(item.get("name", "")).strip().lower()] = " ".join(
            str(x) for x in item.get("bullets", item.get("highlights", [])) or []
        )
    return experience, projects


def verify_overlay(profile: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"ok": bool, "problems": [...]}`` for one overlay."""
    problems: list[str] = []
    text = _profile_text(profile)
    profile_words = tokens(text)

    def check_text(label: str, value: str, limit: int | None = None) -> None:
        if not isinstance(value, str):
            problems.append(f"{label}: expected text")
            return
        if limit is not None and len(value) > limit:
            problems.append(f"{label}: {len(value)} chars, limit {limit}")
        for number in NUMBER.findall(value):
            if number.lower() not in text:
                problems.append(f"{label}: number '{number}' is not in the profile")
        lowered = value.lower()
        for term in TECH_VOCAB:
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered) and term not in text:
                problems.append(f"{label}: mentions '{term}', which the profile does not contain")

    check_text("headline", overlay.get("headline", ""))
    check_text("summary", overlay.get("summary", ""), MAX_SUMMARY)

    skills = profile.get("skills") or {}
    order = overlay.get("skills_order") or {}
    if not isinstance(order, dict):
        problems.append("skills_order: expected an object")
        order = {}
    for group, values in order.items():
        allowed = {str(value).lower() for value in skills.get(group, [])} if isinstance(skills.get(group), list) else set()
        for value in values if isinstance(values, list) else []:
            if str(value).lower() not in allowed:
                problems.append(f"skills_order[{group}]: '{value}' is not a profile skill")

    experience_sources, project_sources = _source_bullets(profile)
    for index, record in enumerate(overlay.get("experience", []) or []):
        if not isinstance(record, dict):
            problems.append(f"experience[{index}]: expected an object")
            continue
        key = _record_key(str(record.get("company", "")), str(record.get("title", "")))
        if key not in experience_sources:
            problems.append(f"experience[{index}]: '{key[0]} / {key[1]}' is not in the profile")
            source = ""
        else:
            source = experience_sources[key]
        for bullet in record.get("bullets", []) or []:
            check_text(f"experience[{index}] bullet", bullet, MAX_BULLET)
            if source and not (tokens(str(bullet)) & tokens(source)):
                problems.append(f"experience[{index}] bullet: shares no fact with the source bullet")

    for index, record in enumerate(overlay.get("projects", []) or []):
        if not isinstance(record, dict):
            problems.append(f"projects[{index}]: expected an object")
            continue
        key = str(record.get("name", "")).strip().lower()
        source = project_sources.get(key, "")
        for bullet in record.get("bullets", []) or []:
            check_text(f"projects[{index}] bullet", bullet, MAX_BULLET)
            if source and not (tokens(str(bullet)) & tokens(source)):
                problems.append(f"projects[{index}] bullet: shares no fact with the source bullet")

    return {"ok": not problems, "problems": problems}
