"""Turn an email into an application status signal.

Pure functions, no network. A wrong guess only becomes a suggestion: the
caller decides whether confidence is high enough to change the tracker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

# Ordered by authority: a rejection outranks an interview mention, an offer
# outranks the "interview" word that offer emails usually contain.
PATTERNS: dict[str, list[str]] = {
    "rejected": [
        r"unfortunately",
        r"not (?:been )?(?:selected|successful|shortlisted)",
        r"(?:not|won'?t) (?:be )?moving forward",
        r"will not be moving forward",
        r"decided to (?:move forward|proceed) with (?:other|another)",
        r"other candidates",
        r"regret to inform",
        r"unable to (?:offer|move forward|progress)",
        r"cannot offer",
        r"not a (?:good )?fit",
        r"application (?:was |is )?unsuccessful",
        r"no longer under consideration",
        r"position has been filled",
        r"keep your (?:resume|cv) on file",
        r"not (?:to )?proceed with your application",
        r"we have decided not to",
    ],
    "offer": [
        r"pleased to offer",
        r"delighted to offer",
        r"happy to offer",
        r"offer letter",
        r"job offer",
        r"offer of employment",
        r"would like to offer you",
        r"formal offer",
        r"congratulations[^.]{0,60}(?:offer|joining)",
    ],
    "interview": [
        r"invite you (?:to|for) (?:an? )?(?:interview|conversation|chat)",
        r"schedule (?:a|an|your) (?:call|interview|chat|conversation)",
        r"\binterview\b",
        r"phone screen",
        r"technical screen",
        r"hiring manager",
        r"next (?:round|step)",
        r"your availability",
        r"book a (?:time|slot)",
        r"set up a (?:call|chat|conversation)",
        r"meet with",
        r"recruiter (?:call|chat)",
    ],
    "assessment": [
        r"coding challenge",
        r"take[- ]home",
        r"online assessment",
        r"hackerrank",
        r"codility",
        r"test task",
        r"technical assessment",
        r"coding exercise",
    ],
    "applied": [
        r"thank you for (?:applying|your application)",
        r"thanks for applying",
        r"application (?:has been |was )?received",
        r"we(?:'ve| have) received your application",
        r"received your application",
        r"application (?:is )?(?:under|being) review",
        r"successfully applied",
        r"application submitted",
        r"your application to",
    ],
}

# First match wins when scores tie.
PRIORITY = ["rejected", "offer", "interview", "assessment", "applied"]

_COMPILED = {name: [re.compile(p, re.I) for p in pats] for name, pats in PATTERNS.items()}

AGGREGATORS = re.compile(
    r"(linkedin|indeed|glassdoor|ziprecruiter|naukri|wellfound|angel\.co|monster|himalayas|remotive|arbeitnow)",
    re.I,
)


@dataclass
class Intent:
    name: str = "unknown"
    confidence: float = 0.0
    signals: list[str] = dc_field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.name != "unknown"


def _hits(text: str, patterns: list[re.Pattern]) -> list[str]:
    return [p.pattern for p in patterns if p.search(text)]


def classify(subject: str, body: str, sender: str = "") -> Intent:
    subject = subject or ""
    body = body or ""
    text = f"{subject}\n{body}"

    scores: dict[str, list[str]] = {}
    for name in PRIORITY:
        hits = _hits(text, _COMPILED[name])
        if hits:
            scores[name] = hits

    if not scores:
        return Intent()

    # Highest specificity wins: more distinct phrases, then priority order.
    name = max(
        (n for n in PRIORITY if n in scores),
        key=lambda n: (min(len(scores[n]), 4), -PRIORITY.index(n)),
    )
    hits = scores[name]
    subject_hits = _hits(subject, _COMPILED[name])

    confidence = 0.55 + 0.12 * min(len(hits), 4)
    if subject_hits:
        confidence += 0.1
    if name == "interview" and len(hits) == 1 and hits[0] == r"\binterview\b":
        confidence -= 0.1  # the word alone is weak evidence
    if AGGREGATORS.search(sender or ""):
        confidence -= 0.05

    return Intent(name=name, confidence=round(min(0.95, max(0.0, confidence)), 2), signals=hits[:4])


RANK = {"new": 0, "review": 0, "applied": 1, "interview": 2, "offer": 3, "rejected": 4}


def next_status(current: str, intent: str) -> str:
    """Forward-only transitions. A rejection never erases an offer."""
    current = current if current in RANK else "new"
    if intent == "rejected":
        return current if current == "offer" else "rejected"
    if intent == "offer":
        return "offer"
    if intent == "interview":
        return "interview" if RANK[current] < 2 else current
    if intent == "applied":
        return "applied" if current in {"new", "review"} else current
    return current
