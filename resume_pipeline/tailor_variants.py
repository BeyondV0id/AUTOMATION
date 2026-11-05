"""Build job-specific resume variations from verified profile data.

Three angles are produced for the same posting:

- ``ats-match``   mirror the posting's vocabulary, skills ordered by frequency
- ``impact-led``  measurable bullets first, tighter bullet count
- ``role-fit``    most relevant roles and projects first, role-framed summary

Everything here only reorders, reselects and rewrites text that already exists
in ``profile.json``. It never invents a skill, employer, date or metric. An
optional overlay written by a model can replace the deterministic text; the
overlay is checked by ``verify.py`` before it is rendered.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .latex import VARIANTS
from .posting import plain_text
from .profile import record_matches

STRATEGIES = ("ats-match", "impact-led", "role-fit")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the",
    "their", "this", "to", "was", "were", "will", "with", "you", "your", "we",
    "our", "using", "used", "use", "across", "also", "via", "per", "than",
    "then", "they", "them", "these", "those", "while", "where", "which", "who",
    "work", "working", "role", "team", "teams", "including", "etc",
}

TOKEN = re.compile(r"[a-z0-9+#.]+")
NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\b")


def tokens(text: str) -> set[str]:
    return {token for token in TOKEN.findall((text or "").lower()) if len(token) > 2 and token not in STOPWORDS}


def choose_variant(job: dict[str, Any]) -> str:
    text = f"{job.get('title', '')} {plain_text(str(job.get('description', '')))}".lower()
    if any(word in text for word in ("machine learning", "ml engineer", "artificial intelligence", "ai engineer", "deep learning")):
        return "ai-ml"
    if any(word in text for word in ("backend", "api", "server", "platform")):
        return "backend"
    return "software-engineer"


def flat_skills(profile: dict[str, Any]) -> list[str]:
    skills = profile.get("skills") or {}
    values: list[str] = []
    for group in skills.values() if isinstance(skills, dict) else []:
        if isinstance(group, list):
            values.extend(str(item) for item in group if str(item).strip())
    return values


def keyword_hits(text: str, skills: Iterable[str]) -> dict[str, int]:
    """Count how often each profile skill appears in the posting."""
    lowered = (text or "").lower()
    hits: dict[str, int] = {}
    for skill in skills:
        pattern = rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])"
        count = len(re.findall(pattern, lowered))
        if count:
            hits[skill] = count
    return hits


def rank_skills(skills: dict[str, list], hits: dict[str, int]) -> dict[str, list[str]]:
    """Keep every skill, move the ones the posting asks for to the front."""
    ordered = dict(skills)
    for group, values in skills.items():
        if not isinstance(values, list):
            continue
        indexed = list(enumerate(str(value) for value in values))
        indexed.sort(key=lambda pair: (-hits.get(pair[1], 0), pair[0]))
        ordered[group] = [value for _, value in indexed]
    return ordered


def bullet_score(bullet: str, job_tokens: set[str], strategy: str) -> tuple[float, float]:
    overlap = len(tokens(bullet) & job_tokens)
    has_metric = 1.0 if NUMBER.search(bullet) else 0.0
    if strategy == "impact-led":
        return (has_metric * 2.0 + overlap, overlap)
    return (overlap + has_metric * 0.25, has_metric)


def rank_bullets(bullets: list, job_tokens: set[str], strategy: str, limit: int) -> list[str]:
    indexed = [(index, str(bullet)) for index, bullet in enumerate(bullets) if str(bullet).strip()]
    indexed.sort(key=lambda pair: (-bullet_score(pair[1], job_tokens, strategy)[0], pair[0]))
    kept = [bullet for _, bullet in indexed[:limit]]
    if strategy == "impact-led":
        kept.sort(key=lambda bullet: (0 if NUMBER.search(bullet) else 1))
    return kept


def record_text(record: dict[str, Any]) -> str:
    parts = [str(record.get(key, "")) for key in ("company", "title", "name", "technology", "stack", "location")]
    parts += [str(item) for item in record.get("bullets", record.get("highlights", [])) or []]
    return " ".join(parts)


def rank_records(records: list[dict[str, Any]], job_tokens: set[str], strategy: str) -> list[dict[str, Any]]:
    if strategy != "role-fit" or not records:
        return records
    indexed = list(enumerate(records))
    indexed.sort(key=lambda pair: (-len(tokens(record_text(pair[1])) & job_tokens), pair[0]))
    return [record for _, record in indexed]


def build_summary(profile: dict[str, Any], job: dict[str, Any], strategy: str, hits: dict[str, int]) -> str:
    base = str(profile.get("summary", "")).strip()
    matched = [skill for skill, _ in sorted(hits.items(), key=lambda pair: (-pair[1], pair[0]))]
    if not matched:
        return base
    title = str(job.get("title", "")).strip() or "this role"
    if strategy == "ats-match":
        tail = f"Core stack for this role: {', '.join(matched[:6])}."
    elif strategy == "impact-led":
        tail = f"Delivered measurable results with {', '.join(matched[:4])}."
    else:
        tail = f"Targeting {title} with hands-on {', '.join(matched[:4])}."
    if tail.lower() in base.lower():
        return base
    return f"{base} {tail}".strip()


def build_overlay(profile: dict[str, Any], job: dict[str, Any], strategy: str) -> dict[str, Any]:
    """Deterministic overlay: no model, no new claims."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy}; choose from {', '.join(STRATEGIES)}")

    variant = choose_variant(job)
    tags = VARIANTS[variant]
    skills = flat_skills(profile)
    job_text = f"{job.get('title', '')} {plain_text(str(job.get('description', '')))}"
    hits = keyword_hits(job_text, skills)
    job_tokens = tokens(job_text)

    experience = [item for item in profile.get("experience", []) if record_matches(item, tags)]
    projects = [item for item in profile.get("projects", []) if record_matches(item, tags)]

    limits = {"ats-match": 5, "impact-led": 4, "role-fit": 5}
    limit = limits[strategy]

    experience = rank_records(experience, job_tokens, strategy)
    projects = rank_records(projects, job_tokens, strategy)

    overlay: dict[str, Any] = {
        "strategy": strategy,
        "variant": variant,
        "headline": str(job.get("title", "")).strip(),
        "summary": build_summary(profile, job, strategy, hits),
        "skills_order": rank_skills(profile.get("skills") or {}, hits),
        "experience": [
            {
                "company": item.get("company", ""),
                "title": item.get("title", ""),
                "bullets": rank_bullets(item.get("bullets", item.get("highlights", [])), job_tokens, strategy, limit),
            }
            for item in experience
        ],
        "projects": [
            {
                "name": item.get("name", ""),
                "bullets": rank_bullets(item.get("bullets", item.get("highlights", [])), job_tokens, strategy, limit),
            }
            for item in projects
        ],
        "keywords": sorted(hits, key=lambda skill: (-hits[skill], skill))[:12],
    }
    return overlay


def prompt_packet(profile: dict[str, Any], job: dict[str, Any], strategy: str) -> str:
    """Instructions for a model that rewrites the overlay, fact-locked."""
    angle = {
        "ats-match": "Mirror the posting's exact terminology. Reorder skills by relevance. Do not add a skill the profile lacks.",
        "impact-led": "Lead every bullet with the measurable outcome. Keep only the strongest 3-4 bullets per role.",
        "role-fit": "Foreground the roles and projects closest to this posting. Frame the summary around this role.",
    }[strategy]
    return "\n".join([
        f"# Tailoring brief: {strategy}",
        "",
        angle,
        "",
        "## Hard rules",
        "- Rewrite only facts already present in the profile JSON below.",
        "- Never add an employer, tool, date, metric, degree or certification.",
        "- Keep every number that appears in a bullet; do not invent new ones.",
        "- Keep bullets under 320 characters, one sentence each.",
        "- Return JSON only, shaped like:",
        "",
        "```json",
        '{"strategy": "...", "headline": "...", "summary": "...",',
        ' "skills_order": {"languages": ["..."]},',
        ' "experience": [{"company": "...", "title": "...", "bullets": ["..."]}],',
        ' "projects": [{"name": "...", "bullets": ["..."]}]}',
        "```",
        "",
        "## Job",
        f"Title: {job.get('title', '')}",
        f"Company: {job.get('company', '')}",
        f"Description:\n{plain_text(str(job.get('description', '')))[:6000]}",
        "",
        "## Verified profile",
        "```json",
        __import__("json").dumps(profile, indent=2, ensure_ascii=False),
        "```",
        "",
    ])
