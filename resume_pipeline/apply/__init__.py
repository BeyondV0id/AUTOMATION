"""Application-form autofill built on Playwright.

Deterministic and fast: the page is read in one DOM pass, fields are matched
against a fixed answer map, and each control is filled with a single Playwright
action. No per-field model call. Unknown required fields are reported as
``needs_human`` instead of guessed.

Guardrail: this package never presses a submit button.
"""

from .answers import AnswerBook, load_answers
from .fields import Field, classify, scan
from .fill import FillResult, fill_form
from .runner import detect_ats, run_application

__all__ = [
    "AnswerBook",
    "Field",
    "FillResult",
    "classify",
    "detect_ats",
    "fill_form",
    "load_answers",
    "run_application",
    "scan",
]
