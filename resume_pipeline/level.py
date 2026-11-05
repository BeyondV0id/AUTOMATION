"""Read the seniority a posting asks for, from its title and description.

Ranking without this puts a 12-year staff role above a paid internship, because
both say "software engineer". Every pattern here is deliberately conservative:
when the posting does not state a level, the answer is ``unknown`` and the
scorer treats it as a maybe rather than inventing a requirement.

    >>> detect_level("Software Engineer", "3+ years of experience with Python")["level"]
    'mid'
    >>> detect_level("Software Development Intern", "")["level"]
    'internship'
"""

from __future__ import annotations

import re
from typing import Any

INTERNSHIP = "internship"
FRESHER = "fresher"
JUNIOR = "junior"
MID = "mid"
SENIOR = "senior"
UNKNOWN = "unknown"

LEVELS = (INTERNSHIP, FRESHER, JUNIOR, MID, SENIOR, UNKNOWN)

# Entry-level signals. "Trainee" and "graduate programme" are how Indian and UK
# employers label the same thing an internship is in the US.
_ENTRY = re.compile(
    r"\b(internship|intern|trainee|co-?op|apprentice|placement (?:year|student)|"
    r"industrial trainee|summer (?:analyst|intern))\b",
    re.I,
)
_FRESHER = re.compile(
    r"\b(fresher|fresh(?:er)? graduate|recent graduate|entry[- ]level|"
    r"graduate (?:engineer|program|programme|scheme|role|trainee|developer|analyst)|"
    r"campus (?:hire|hiring|placement)|no prior experience|"
    r"0\s*(?:-|–|to)\s*[12]\s*(?:years?|yrs?))\b",
    re.I,
)
_JUNIOR = re.compile(r"\b(junior|jr\.?|associate (?:engineer|developer)|engineer i|developer i)\b", re.I)

# "Software Engineer II" is a second-level role; III and above are out of reach.
_ROMAN = re.compile(r"\b(?:engineer|developer|sde|swe|analyst|consultant)\s+(ii|iii|iv)\b", re.I)

# Some boards state the band as its own field: "mid level", "senior level".
_STATED_LEVEL = re.compile(r"\b(senior|mid|junior|entry)[- ]level\b", re.I)

# Seniority that a 2027 graduate cannot claim.
_SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|head|director|vp|architect|distinguished|manager|fellow)\b",
    re.I,
)

# "minimum 12 years", "at least 3 years", "requires 5+ years"
_YEARS_MIN = re.compile(
    r"(?:minimum|min\.?|at least|atleast|requires?|requiring|need(?:ed)?|expect(?:ed)?)\s*"
    r"(?:of\s*)?(\d{1,2})\s*(?:\+)?\s*(?:years?|yrs?)",
    re.I,
)
# "3-5 years of experience", "2+ years experience", "1 year of relevant experience"
_YEARS_EXP = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*(?:\+)?\s*(?:years?|yrs?)\s*"
    r"(?:of\s+)?(?:relevant\s+|professional\s+|industry\s+|hands[- ]on\s+|work\s+|full[- ]time\s+)?"
    r"(?:experience|exp\b)",
    re.I,
)
_YEARS_PLUS = re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)", re.I)
# A title like "Python Developer | Django/Flask | 5-12 Years" states the band
# without the word "experience" after it.
_YEARS_TITLE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*(?:\+)?\s*(?:years?|yrs?)\b",
    re.I,
)


def _numbers(text: str) -> list[int]:
    """Every stated lower bound, lowest first. 0-1 years is a 0."""
    found: list[int] = []
    for pattern in (_YEARS_MIN, _YEARS_EXP, _YEARS_PLUS):
        for match in pattern.finditer(text):
            try:
                found.append(int(match.group(1)))
            except (TypeError, ValueError):
                continue
    return sorted(found)


def years_required(title: str = "", description: str = "") -> int | None:
    """The smallest experience requirement the posting states, if any.

    Title and description are read separately and the larger of the two wins:
    boards disagree with themselves ("Cloud - 4+ Years" over "minimum 0 years")
    and the safer reading is the one that does not understate the requirement.
    The head of the description is preferred, because that is where
    requirements are written.
    """
    from_title = sorted(
        int(match.group(1)) for match in _YEARS_TITLE.finditer(title or "") if match.group(1)
    )
    head = (description or "")[:1500]
    from_body = _numbers(head) or _numbers(description or "")

    candidates = [value for value in (from_title[0] if from_title else None, from_body[0] if from_body else None) if value is not None]
    return max(candidates) if candidates else None


def detect_level(title: str = "", description: str = "") -> dict[str, Any]:
    """Return ``{level, years_required, evidence}`` for one posting."""
    title = title or ""
    description = description or ""
    years = years_required(title, description)
    head = description[:1500]
    title_and_head = f"{title}\n{head}"

    if _ENTRY.search(title_and_head) and (years is None or years <= 1):
        evidence = _ENTRY.search(title_and_head)
        return {"level": INTERNSHIP, "years_required": years, "evidence": evidence.group(0)}

    if _FRESHER.search(title_and_head) and (years is None or years <= 1):
        evidence = _FRESHER.search(title_and_head)
        return {"level": FRESHER, "years_required": years, "evidence": evidence.group(0)}

    stated = _STATED_LEVEL.search(head)
    if stated:
        band = stated.group(1).lower()
        mapped = {"entry": FRESHER, "junior": JUNIOR, "mid": MID, "senior": SENIOR}[band]
        return {"level": mapped, "years_required": years, "evidence": f"{band} level"}

    if years is not None and years >= 5:
        return {"level": SENIOR, "years_required": years, "evidence": f"{years}+ years asked"}
    if _SENIOR_TITLE.search(title):
        evidence = _SENIOR_TITLE.search(title)
        return {"level": SENIOR, "years_required": years, "evidence": f"title says {evidence.group(0)}"}
    roman = _ROMAN.search(title)
    if roman:
        level = MID if roman.group(1).lower() == "ii" else SENIOR
        return {"level": level, "years_required": years, "evidence": f"title says {roman.group(0)}"}
    if years is not None and years >= 2:
        return {"level": MID, "years_required": years, "evidence": f"{years}+ years asked"}
    if _JUNIOR.search(title):
        return {"level": JUNIOR, "years_required": years, "evidence": _JUNIOR.search(title).group(0)}
    if years == 1:
        return {"level": JUNIOR, "years_required": years, "evidence": "1 year asked"}
    if years == 0:
        return {"level": FRESHER, "years_required": years, "evidence": "0 years asked"}

    return {"level": UNKNOWN, "years_required": None, "evidence": "no level stated"}
