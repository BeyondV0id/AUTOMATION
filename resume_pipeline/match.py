"""Deterministic job matching: role, skills, location and seniority.

Score out of 100, computed locally with plain rules. Nothing here calls a model,
so ranking a thousand jobs costs nothing.

Seniority carries a quarter of the score on purpose. Without it a 12-year staff
role outranks a paid internship, because both titles say "software engineer".
A posting that asks for more years than the candidate has is capped, so it can
still be read but never reaches the top of the list.
"""

from __future__ import annotations

import re
from typing import Any

from .level import FRESHER, INTERNSHIP, JUNIOR, MID, SENIOR, UNKNOWN, detect_level

WEIGHTS = {"role": 30, "skills": 30, "location": 15, "level": 25}

# A posting that wants more experience than the candidate has can still score
# well on role and skills, so it is capped below every eligible job.
INELIGIBLE_CAP = 35.0

# Titles that are never a match for an engineering profile.
OFF_TOPIC = {
    "sales", "account executive", "recruiter", "talent", "marketing",
    "content", "social media", "customer success", "support", "finance",
    "accountant", "legal", "designer", "product manager", "office manager",
}

REMOTE_WORDS = ("remote", "work from home", "wfh", "anywhere", "worldwide", "distributed")

# "REMOTE (US)" is not remote for someone in India. Only an unqualified remote,
# or one that names India or the world, is open to this candidate.
REMOTE_LOCKED = re.compile(
    r"remote\s*\(\s*(?:us|usa|u\.s\.|united states|canada|uk|united kingdom|eu|europe|"
    r"emea|latam|apac|australia|germany|netherlands|singapore|japan)\b",
    re.I,
)

# Where the candidate can actually take a job without a visa. Used only to
# demote, never to hide: a great role abroad stays visible, just lower.
INDIA_WORDS = (
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "chennai", "mumbai",
    "delhi", "noida", "gurgaon", "gurugram", "kolkata", "ahmedabad", "kochi",
    "cochin", "trivandrum", "thiruvananthapuram", "visakhapatnam", "vizag",
    "coimbatore", "indore", "jaipur", "chandigarh", "mysore", "mysuru",
)

_TOKEN = re.compile(r"[a-z0-9+#.]+")


def _tokens(value: str) -> set[str]:
    return set(_TOKEN.findall((value or "").lower()))


def _mentions(skill: str, text: str) -> bool:
    """Word-boundary match that still works for C++, Node.js and C#."""
    pattern = rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def profile_skills(profile: dict[str, Any]) -> set[str]:
    skills = profile.get("skills") or {}
    values: set[str] = set()
    for group in skills.values() if isinstance(skills, dict) else []:
        if isinstance(group, list):
            values.update(str(item).lower().strip() for item in group if str(item).strip())
    return {value for value in values if len(value) > 1}


def profile_roles(profile: dict[str, Any]) -> list[str]:
    preferences = profile.get("preferences") or {}
    roles = preferences.get("roles") or []
    return [str(role).lower() for role in roles if str(role).strip()]


def candidate_years(profile: dict[str, Any]) -> int:
    """Years of experience the profile can honestly claim.

    ``preferences.max_years_required`` is an explicit override; otherwise the
    sum of the ``years`` fields on the experience records is used, and a profile
    with only internships counts as zero.
    """
    preferences = profile.get("preferences") or {}
    override = preferences.get("max_years_required")
    if isinstance(override, (int, float)):
        return int(override)
    total = 0
    for record in profile.get("experience") or []:
        if isinstance(record, dict):
            try:
                total += int(record.get("years") or 0)
            except (TypeError, ValueError):
                continue
    return total


def _role_score(title: str, roles: list[str]) -> tuple[float, str]:
    lowered = (title or "").lower()
    if any(word in lowered for word in OFF_TOPIC):
        return 0.0, "title is off-topic for this profile"
    if not roles:
        return WEIGHTS["role"] * 0.5, "no target roles configured"
    best = 0.0
    matched = ""
    title_tokens = _tokens(lowered)
    for role in roles:
        role_tokens = _tokens(role)
        if role_tokens and role_tokens <= title_tokens:
            return WEIGHTS["role"], f"exact role match: {matched or role}"
        overlap = len(role_tokens & title_tokens) / max(len(role_tokens), 1)
        if overlap > best:
            best, matched = overlap, role
    if best >= 0.99:
        return WEIGHTS["role"], f"exact role match: {matched}"
    if best > 0:
        return WEIGHTS["role"] * best, f"partial role match: {matched}"
    return 0.0, "no role match"


def _skill_score(job: dict[str, Any], skills: set[str]) -> tuple[float, str, str]:
    job_skills = {str(s).lower() for s in (job.get("skills") or []) if str(s).strip()}
    haystack = f"{job.get('title', '')} {job.get('description', '')}".lower()
    mentioned = {skill for skill in skills if _mentions(skill, haystack)}
    relevant = job_skills | mentioned
    if not relevant:
        return WEIGHTS["skills"] * 0.4, "no skills stated in the posting", ""
    have = relevant & skills
    missing = relevant - skills
    ratio = len(have) / len(relevant)
    reason = f"{len(have)}/{len(relevant)} listed skills matched"
    return WEIGHTS["skills"] * ratio, reason, ", ".join(sorted(missing)[:8])


def _location_score(job: dict[str, Any], profile: dict[str, Any]) -> tuple[float, str]:
    preferences = profile.get("preferences") or {}
    wanted = [str(x).lower() for x in (preferences.get("locations") or [])]
    remote_ok = bool(preferences.get("remote", True))
    location = (job.get("location") or "").lower()
    locked = bool(REMOTE_LOCKED.search(location))
    is_remote = (bool(job.get("remote")) or any(word in location for word in REMOTE_WORDS)) and not locked
    if is_remote and remote_ok:
        return WEIGHTS["location"], "remote"
    if locked:
        return WEIGHTS["location"] * 0.15, "remote, but locked to another region"
    if wanted and any(place in location for place in wanted):
        return WEIGHTS["location"], "target location"
    if any(word in location for word in INDIA_WORDS):
        return WEIGHTS["location"] * 0.9, "India"
    if not location:
        return WEIGHTS["location"] * 0.5, "location not listed"
    return WEIGHTS["location"] * 0.15, "outside India, no remote option"


def level_of(job: dict[str, Any]) -> dict[str, Any]:
    """Cached level if the caller stored it, otherwise read the posting."""
    cached = job.get("level_info")
    if isinstance(cached, dict) and cached.get("level"):
        return cached
    info = detect_level(str(job.get("title") or ""), str(job.get("description") or ""))
    # Every Internshala listing is an internship, even when the title is just
    # the category name ("Software Development") and the body has no signal.
    if str(job.get("source") or "") == "internshala" and info["level"] == UNKNOWN:
        info = {**info, "level": INTERNSHIP, "evidence": "internshala listing"}
    return info


def _level_score(
    info: dict[str, Any], profile: dict[str, Any]
) -> tuple[float, str, bool]:
    preferences = profile.get("preferences") or {}
    max_years = candidate_years(profile)
    level = info.get("level") or UNKNOWN
    years = info.get("years_required")

    if level == INTERNSHIP:
        return WEIGHTS["level"], "internship", True
    if level == FRESHER:
        return WEIGHTS["level"], "fresher / entry level", True
    if level == JUNIOR:
        return WEIGHTS["level"] * 0.85, "junior role", True
    if years is not None and years > max_years:
        return 0.0, f"asks {years}+ years; this profile has {max_years}", False
    if level == SENIOR:
        return WEIGHTS["level"] * 0.12, "senior role", False
    if level == MID:
        return WEIGHTS["level"] * 0.32, "mid-level role", False
    return WEIGHTS["level"] * 0.56, "level not stated", True


def score_job(job: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    """Return score, a human reason, missing skills, and the seniority read."""
    if not profile:
        return {"score": None, "reason": "", "missing": "", "level": "", "years_required": None, "eligible": False}

    skills = profile_skills(profile)
    roles = profile_roles(profile)
    info = level_of(job)

    role, role_reason = _role_score(job.get("title", ""), roles)
    skill, skill_reason, missing = _skill_score(job, skills)
    location, location_reason = _location_score(job, profile)
    level, level_reason, eligible = _level_score(info, profile)

    total = round(role + skill + location + level, 1)
    if role <= 0:
        eligible = False
    if not eligible:
        total = min(total, INELIGIBLE_CAP)

    reasons = [role_reason, skill_reason, location_reason, level_reason]
    return {
        "score": total,
        "reason": "; ".join(r for r in reasons if r),
        "missing": missing,
        "level": info.get("level") or UNKNOWN,
        "years_required": info.get("years_required"),
        "eligible": eligible,
    }
