"""Repeated browser steps, written once.

Every function here takes a :class:`~resume_pipeline.browser.Session` and does
its work in one round trip. The older code walked up to 40 buttons per frame
with a driver call each; these are a single ``evaluate``, which is what makes a
20-field form cheap on either backend.
"""

from __future__ import annotations

import re
from typing import Any

from . import Session

ATS_PATTERNS: list[tuple[str, str, str]] = [
    ("greenhouse", r"greenhouse\.io|boards\.greenhouse", "embedded iframe; upload field is type=file"),
    ("lever", r"lever\.co", "single page; custom dropdowns for location"),
    ("ashby", r"ashbyhq\.com", "React form; custom comboboxes"),
    ("workday", r"myworkdayjobs\.com|workday", "multi-step wizard; account login may be required"),
    ("smartrecruiters", r"smartrecruiters\.com", "single page; GDPR consent checkbox"),
    ("workable", r"workable\.com", "single page; custom dropdowns"),
    ("breezy", r"breezy\.hr", "single page"),
    ("recruitee", r"recruitee\.com", "single page"),
    ("teamtailor", r"teamtailor\.com", "single page"),
    ("jobvite", r"jobvite\.com", "single page"),
    ("icims", r"icims\.com", "account gate; multi-step"),
    ("taleo", r"taleo\.net|taleo\.com", "legacy frames; slow"),
    ("personio", r"personio\.(de|com)", "single page"),
    ("rippling", r"rippling\.com", "single page"),
    ("jazzhr", r"applytojob\.com", "single page"),
    ("bamboohr", r"bamboohr\.com", "single page"),
]

# The one rule that must never regress: nothing here presses submit.
SUBMIT_WORDS = r"\b(submit|send application|apply now|finish application|complete application)\b"

COOKIE_LABELS = (
    r"^(accept( all| cookies)?|allow all|i agree|agree|got it|ok(ay)?|understood|consent)$"
)

DISMISS_COOKIES_JS = (
    r"""
(labels) => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const re = new RegExp(labels, "i");
  for (const el of document.querySelectorAll("button, [role=button], a, input[type=button]")) {
    const text = norm(el.innerText || el.value || "");
    if (!text || text.length > 40 || !re.test(text) || !visible(el)) continue;
    el.click();
    return text;
  }
  return "";
}
"""
)

CLICK_NEXT_JS = (
    r"""
({ next, submit }) => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const nextRe = new RegExp(next, "i");
  const submitRe = new RegExp(submit, "i");
  for (const el of document.querySelectorAll("button, [role=button], input[type=button], a")) {
    const text = norm(el.innerText || el.value || "");
    if (!text || text.length > 40 || !visible(el)) continue;
    if (submitRe.test(text)) continue;
    if (!nextRe.test(text)) continue;
    el.click();
    return text;
  }
  return "";
}
"""
)

FIND_SUBMIT_JS = (
    r"""
(pattern) => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const re = new RegExp(pattern, "i");
  const out = [];
  for (const el of document.querySelectorAll("button, [role=button], input[type=submit], input[type=button]")) {
    const text = norm(el.innerText || el.value || "");
    if (text && re.test(text)) out.push(text);
  }
  return [...new Set(out)].slice(0, 8);
}
"""
)

NUDGE_SCROLL_JS = (
    r"""
(times) => {
  for (let i = 0; i < times; i += 1) window.scrollBy(0, 1200);
  return Math.round(window.scrollY);
}
"""
)

NEXT_LABELS = r"^(next|continue|save (and|&) continue|continue to application)$"


def dismiss_cookies(session: Session, rounds: int = 2) -> list[str]:
    """Click an obvious cookie/consent button. Returns what was clicked."""
    clicked: list[str] = []
    for _ in range(max(1, rounds)):
        try:
            label = session.evaluate(DISMISS_COOKIES_JS, COOKIE_LABELS, frame=0)
        except Exception:
            break
        if not label:
            break
        clicked.append(str(label))
        session.wait(200)
    return clicked


def click_next(session: Session) -> bool:
    """Advance a multi-step form. Never clicks anything submit-like."""
    try:
        label = session.evaluate(
            CLICK_NEXT_JS, {"next": NEXT_LABELS, "submit": SUBMIT_WORDS}, frame=0
        )
    except Exception:
        return False
    if not label:
        return False
    session.wait(700)
    return True


def find_submit_buttons(session: Session) -> list[str]:
    try:
        labels = session.evaluate(FIND_SUBMIT_JS, SUBMIT_WORDS, frame=0)
    except Exception:
        return []
    seen: list[str] = []
    for label in labels or []:
        text = str(label).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def nudge_scroll(session: Session, times: int = 2) -> None:
    """Pull lazy sections into view so a later scan sees them."""
    try:
        session.evaluate(NUDGE_SCROLL_JS, times, frame=0)
    except Exception:
        return


def detect_ats(url: str, session: Session | None = None) -> tuple[str, str]:
    for name, pattern, note in ATS_PATTERNS:
        if re.search(pattern, url, re.I):
            return name, note
    if session is not None:
        try:
            html = str(session.evaluate("() => document.documentElement.outerHTML") or "").lower()
        except Exception:
            html = ""
        for name, pattern, note in ATS_PATTERNS:
            host = pattern.split("|")[0].replace("\\", "")
            if host and host in html:
                return name, note
    return "generic", "unknown ATS; generic label matching"


def page_facts(session: Session) -> dict[str, Any]:
    """Cheap state used for reports and logs."""
    try:
        return dict(
            session.evaluate(
                """() => ({
                  title: document.title,
                  url: location.href,
                  fields: document.querySelectorAll("input, select, textarea").length,
                  forms: document.forms.length,
                  iframes: document.querySelectorAll("iframe").length,
                })"""
            )
            or {}
        )
    except Exception as exc:  # noqa: BLE001 - a report must still be produced
        return {"error": f"{type(exc).__name__}: {exc}"}


def safe_screenshot(session: Session, path, full_page: bool = True) -> tuple[str, str]:
    """Screenshot without letting a cosmetic failure abort a run.

    ego lite refuses to capture while its window is minimized, which is a
    property of the user's desktop, not of the page.
    """
    try:
        return session.screenshot(path, full_page=full_page), ""
    except Exception as exc:  # noqa: BLE001 - see docstring
        # BrowserError carries the driver's own message on a following line;
        # keep the informative part, not just "ego-browser failed:".
        lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
        useful = next((ln for ln in lines if ln.startswith("Error")), lines[0] if lines else "")
        return "", f"{type(exc).__name__}: {useful}"[:200]
