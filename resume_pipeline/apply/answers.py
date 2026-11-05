"""Merge verified profile data and explicit answers into one flat answer map."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .. import ROOT

DEFAULT_ANSWER_FILE = ROOT / "config" / "answers.toml"
FALLBACK_ANSWER_FILE = ROOT / "config" / "answers.json"

# Dial codes used to fill the phone-country combobox that ATS forms add next to
# the phone field. Unknown countries are left blank rather than guessed.
COUNTRY_DIAL = {
    "india": "+91",
    "united states": "+1",
    "usa": "+1",
    "us": "+1",
    "united kingdom": "+44",
    "uk": "+44",
    "canada": "+1",
    "australia": "+61",
    "germany": "+49",
    "france": "+33",
    "netherlands": "+31",
    "ireland": "+353",
    "singapore": "+65",
    "united arab emirates": "+971",
    "uae": "+971",
    "poland": "+48",
    "spain": "+34",
    "italy": "+39",
    "portugal": "+351",
    "sweden": "+46",
    "norway": "+47",
    "denmark": "+45",
    "finland": "+358",
    "switzerland": "+41",
    "austria": "+43",
    "belgium": "+32",
    "czech republic": "+420",
    "czechia": "+420",
    "romania": "+40",
    "hungary": "+36",
    "japan": "+81",
    "south korea": "+82",
    "china": "+86",
    "hong kong": "+852",
    "taiwan": "+886",
    "indonesia": "+62",
    "malaysia": "+60",
    "philippines": "+63",
    "thailand": "+66",
    "vietnam": "+84",
    "pakistan": "+92",
    "bangladesh": "+880",
    "sri lanka": "+94",
    "nepal": "+977",
    "new zealand": "+64",
    "south africa": "+27",
    "brazil": "+55",
    "mexico": "+52",
    "argentina": "+54",
    "israel": "+972",
    "turkey": "+90",
    "egypt": "+20",
    "nigeria": "+234",
    "kenya": "+254",
}


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        return tomllib.loads(text)
    return json.loads(text)


def _split_name(full: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", full.strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def load_profile(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class AnswerBook:
    """Flat canonical answers plus free-form label questions.

    Values are ``str | bool``. Booleans render as Yes/No for text inputs and
    drive radios/checkboxes.
    """

    def __init__(self, answers: dict[str, Any] | None = None, questions: dict[str, str] | None = None):
        self.answers = {str(k): v for k, v in (answers or {}).items() if v not in (None, "")}
        self.questions = {str(k).lower(): str(v) for k, v in (questions or {}).items() if v not in (None, "")}

    def get(self, key: str) -> Any:
        return self.answers.get(key)

    def has(self, key: str) -> bool:
        return key in self.answers

    def as_text(self, key: str) -> str:
        value = self.answers.get(key)
        if value is None:
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)

    def custom_for(self, label: str) -> str | None:
        """Match a free-form question by substring, longest key first."""
        text = re.sub(r"\s+", " ", label.lower()).strip()
        if len(text) < 4:
            return None
        for question in sorted(self.questions, key=len, reverse=True):
            if question in text:
                return self.questions[question]
        return None

    def __contains__(self, key: object) -> bool:
        return key in self.answers

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AnswerBook({len(self.answers)} answers, {len(self.questions)} questions)"


def build_answers(profile: dict[str, Any], overrides: dict[str, Any] | None = None) -> AnswerBook:
    identity = profile.get("identity") or {}
    links = profile.get("links") or {}
    preferences = profile.get("preferences") or {}

    first, last = _split_name(identity.get("name") or "")
    location = identity.get("location") or ""
    city = location.split(",")[0].strip() if location else ""
    country = location.split(",")[-1].strip() if "," in location else ""

    base: dict[str, Any] = {
        "first_name": first,
        "last_name": last,
        "full_name": identity.get("name") or "",
        "email": identity.get("email") or "",
        "phone": identity.get("phone") or "",
        "location": location,
        "city": city,
        "country": country,
        "headline": identity.get("headline") or "",
        "linkedin": links.get("linkedin") or "",
        "github": links.get("github") or "",
        "portfolio": links.get("portfolio") or "",
        "work_authorization": preferences.get("work_authorization") or "",
        "remote": preferences.get("remote"),
        "preferred_role": (preferences.get("roles") or [""])[0],
    }

    dial = COUNTRY_DIAL.get(country.lower())
    if dial:
        base["phone_country_code"] = dial

    # Drop computed-but-empty values so explicit overrides win cleanly.
    base = {k: v for k, v in base.items() if v not in (None, "")}

    merged = dict(base)
    override_answers = (overrides or {}).get("answers") or {}
    merged.update({k: v for k, v in override_answers.items() if v not in (None, "")})

    questions = (overrides or {}).get("questions") or {}
    return AnswerBook(merged, questions)


def load_answers(profile_path: Path | None = None, answer_path: Path | None = None) -> AnswerBook:
    """Profile is the source of truth; answers.toml supplies what a resume cannot."""
    profile = load_profile(profile_path)
    if answer_path is not None:
        config = _read_config(Path(answer_path))
    elif DEFAULT_ANSWER_FILE.exists():
        config = _read_config(DEFAULT_ANSWER_FILE)
    elif FALLBACK_ANSWER_FILE.exists():
        config = _read_config(FALLBACK_ANSWER_FILE)
    else:
        config = {}
    return build_answers(profile, config)
