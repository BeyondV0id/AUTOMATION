"""Choose which bullet to cut when a resume overflows its page budget.

Mechanical cutting (drop the last bullet, or the oldest role) removes the line
that happens to sit at the bottom of the page rather than the line that matters
least for this posting. This module scores every cuttable bullet on:

* relevance to the posting,
* whether it carries a measurable result,
* how much of its vocabulary is unique in the document,
* how prominently it sits in its record.

The lowest total is cut first. A recent but generic bullet can go before an
older bullet that names the posting's stack, which is the point. The last
bullet of a record is never removed, and a model-written overlay is copied
before fitting so the original stays intact.
"""

from __future__ import annotations

import copy
from typing import Any

from .tailor_variants import NUMBER, tokens


def all_bullets(overlay: dict[str, Any]) -> list[str]:
    bullets: list[str] = []
    for section in ("experience", "projects"):
        for record in overlay.get(section) or []:
            if isinstance(record, dict):
                bullets.extend(str(item) for item in record.get("bullets") or [] if str(item).strip())
    return bullets


def frequencies(bullets: list[str]) -> dict[str, int]:
    """How many bullets each token appears in."""
    counts: dict[str, int] = {}
    for bullet in bullets:
        for token in tokens(bullet):
            counts[token] = counts.get(token, 0) + 1
    return counts


def bullet_score(
    bullet: str,
    job_tokens: set[str],
    counts: dict[str, int],
    position: int,
) -> float:
    """Higher means keep. Position 0 is the first bullet in a record."""
    terms = tokens(bullet)
    relevance = len(terms & job_tokens) * 3.0
    metric = 1.5 if NUMBER.search(bullet) else 0.0
    uniqueness = (
        len([token for token in terms if counts.get(token, 0) == 1]) / len(terms)
        if terms
        else 0.0
    )
    prominence = 0.75 if position == 0 else 0.25 if position == 1 else 0.0
    return relevance + metric + uniqueness + prominence


def cut_candidates(overlay: dict[str, Any], job_tokens: set[str]) -> list[dict[str, Any]]:
    """Every bullet that may be removed, lowest value first."""
    counts = frequencies(all_bullets(overlay))
    candidates: list[dict[str, Any]] = []
    for section_index, section in enumerate(("experience", "projects")):
        for record_index, record in enumerate(overlay.get(section) or []):
            if not isinstance(record, dict):
                continue
            bullets = [str(item) for item in record.get("bullets") or []]
            indexed = [(index, bullet) for index, bullet in enumerate(bullets) if bullet.strip()]
            if len(indexed) <= 1:
                continue
            for bullet_index, bullet in indexed:
                candidates.append(
                    {
                        "section": section,
                        "record_index": record_index,
                        "bullet_index": bullet_index,
                        "bullet": bullet,
                        "score": round(bullet_score(bullet, job_tokens, counts, bullet_index), 3),
                        "section_order": section_index,
                    }
                )
    candidates.sort(
        key=lambda item: (
            item["score"],
            -len(overlay[item["section"]][item["record_index"]]["bullets"]),
            item["section_order"],
            item["record_index"],
            -item["bullet_index"],
        )
    )
    return candidates


def lowest_value_bullet(overlay: dict[str, Any], job_tokens: set[str]) -> dict[str, Any] | None:
    candidates = cut_candidates(overlay, job_tokens)
    return candidates[0] if candidates else None


def remove_bullet(overlay: dict[str, Any], candidate: dict[str, Any]) -> str:
    """Remove the chosen bullet in place and return the text removed."""
    bullets = overlay[candidate["section"]][candidate["record_index"]]["bullets"]
    return str(bullets.pop(candidate["bullet_index"]))


def fit_overlay(
    overlay: dict[str, Any],
    job_tokens: set[str],
    max_cuts: int = 8,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a fitted copy and the ordered record of every cut."""
    fitted = copy.deepcopy(overlay)
    cuts: list[dict[str, Any]] = []
    for _ in range(max(0, max_cuts)):
        candidate = lowest_value_bullet(fitted, job_tokens)
        if candidate is None:
            break
        removed = remove_bullet(fitted, candidate)
        cuts.append(
            {
                "section": candidate["section"],
                "record_index": candidate["record_index"],
                "bullet_index": candidate["bullet_index"],
                "bullet": removed,
                "score": candidate["score"],
            }
        )
    return fitted, cuts
