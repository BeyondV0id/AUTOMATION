"""Verify a compiled PDF the way an ATS parser sees it.

The compiler reports success as soon as a file starts with ``%PDF-``. That is
not enough: a resume can compile cleanly and still have a text layer an ATS
cannot read (icon glyphs instead of an email, ``(cid:NNN)`` runs, an en dash
joining a date range). This module extracts the embedded text, checks the page
count and required contact strings, and flags date ranges an ATS would drop.

Extraction tries pypdf first (BSD, optional), then PyMuPDF, then Poppler's
``pdftotext``. A missing extractor is reported, never guessed around.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

PDF_MAGIC = b"%PDF-"
# Characters that mean the extractor handed back a broken text layer rather than
# the document: U+FFFD is a failed glyph, and (cid:NNN) is an unmapped glyph.
GARBLED = ("\ufffd", "(cid:")

# Typographic substitutions LaTeX makes from plain source text. Folding is for
# comparison only; the extracted text is never rewritten.
TYPOGRAPHIC_FOLDS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)

NON_ASCII_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_YEAR = r"(?<!\d)(?:19|20)\d{2}(?!\d)"
NON_ASCII_DATE_RANGE = re.compile(
    rf"{_YEAR}[^\S\n]*[{NON_ASCII_DASHES}]|[{NON_ASCII_DASHES}][^\S\n]*{_YEAR}"
)
_TOKEN_BEFORE = re.compile(r"(\S+)[^\S\n]*$")
_TOKEN_AFTER = re.compile(r"^[^\S\n]*(\S+)")
_BARE_NUMBER = re.compile(r"\d+(?:[.,]\d+)*[kKmM%]?")
_BARE_YEAR = re.compile(r"(?:19|20)\d{2}")
_TWO_DIGIT_YEAR = re.compile(r"\d{2}")
_OUTER_PUNCTUATION = ".,;:!?()[]{}\"'"


class PdfCheckError(Exception):
    """Raised when a PDF cannot be read or fails verification."""


def normalize_text(text: str) -> str:
    """Fold whitespace, Unicode form and LaTeX typography for comparison."""
    text = unicodedata.normalize("NFC", text or "")
    return " ".join(text.translate(TYPOGRAPHIC_FOLDS).split())


def looks_garbled(text: str) -> bool:
    """True when a text extractor returned replacement or unmapped glyphs."""
    return any(marker in (text or "") for marker in GARBLED)


def _bare_number(token: str) -> str | None:
    core = token.strip(_OUTER_PUNCTUATION)
    if not _BARE_NUMBER.fullmatch(core):
        return None
    groups = re.split(r"[.,]", core.rstrip("kKmM%"))
    if len(groups) > 1 and any(_BARE_YEAR.fullmatch(group) for group in groups):
        return None
    return core


def _is_quantity(line: str, dash_index: int) -> bool:
    """True when both sides of the dash are plain numbers that are not a date pair."""
    before = _TOKEN_BEFORE.search(line[:dash_index])
    after = _TOKEN_AFTER.search(line[dash_index + 1 :])
    if not before or not after:
        return False
    left = _bare_number(before.group(1))
    right = _bare_number(after.group(1))
    if left is None or right is None:
        return False
    if _BARE_YEAR.fullmatch(left) and (_BARE_YEAR.fullmatch(right) or _TWO_DIGIT_YEAR.fullmatch(right)):
        return False
    return True


def find_non_ascii_date_ranges(text: str) -> list[tuple[str, str]]:
    """Return ``(line, dash)`` for every year joined to a non-ASCII dash."""
    hits: list[tuple[str, str]] = []
    for match in NON_ASCII_DATE_RANGE.finditer(text or ""):
        dash = next(char for char in match.group(0) if char in NON_ASCII_DASHES)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line_end = line_end if line_end != -1 else len(text)
        line = text[line_start:line_end]
        offset = match.start() + match.group(0).index(dash) - line_start
        if _is_quantity(line, offset):
            continue
        hits.append((" ".join(line.split()), dash))
    return hits


def _extract_pypdf(path: Path) -> tuple[str, int] | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    logger = logging.getLogger("pypdf")
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return None
    finally:
        logger.setLevel(previous)
    if not normalize_text(text) or looks_garbled(text):
        return None
    return text, pages


def _extract_fitz(path: Path) -> tuple[str, int] | None:
    try:
        import fitz  # type: ignore
    except ImportError:
        return None
    try:
        document = fitz.open(str(path))
        try:
            pages = document.page_count
            text = "\n".join(page.get_text("text") for page in document)
        finally:
            document.close()
    except Exception:
        return None
    if not normalize_text(text) or looks_garbled(text):
        return None
    return text, pages


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
    except FileNotFoundError as exc:
        raise PdfCheckError(f"{command[0]} is not installed") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or "command failed"
        raise PdfCheckError(f"{command[0]} could not read the PDF: {detail}") from exc


def _extract_pdftotext(path: Path) -> tuple[str, int] | None:
    if not shutil.which("pdftotext") or not shutil.which("pdfinfo"):
        return None
    text = _run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"])
    info = _run(["pdfinfo", str(path)])
    match = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
    if not match:
        raise PdfCheckError("pdfinfo did not report a page count")
    return text, int(match.group(1))


def extract_text(pdf_path: str | Path) -> tuple[str, int, str]:
    """Return ``(text, pages, extractor)`` for a PDF."""
    path = Path(pdf_path)
    if not path.is_file():
        raise PdfCheckError(f"PDF does not exist: {path}")
    if path.read_bytes()[:5] != PDF_MAGIC:
        raise PdfCheckError(f"not a PDF: {path}")

    for extractor, reader in (
        ("pypdf", _extract_pypdf),
        ("fitz", _extract_fitz),
        ("pdftotext", _extract_pdftotext),
    ):
        result = reader(path)
        if result is not None:
            return result[0], result[1], extractor
    raise PdfCheckError(
        "no working PDF text extractor. Install pypdf (`pip install pypdf`), "
        "PyMuPDF (`pip install pymupdf`), or Poppler"
    )


def check_pdf(
    pdf_path: str | Path,
    expected_pages: int | None = None,
    max_pages: int | None = None,
    min_chars: int = 1,
    contains: tuple[str, ...] | list[str] = (),
    ascii_dates: bool = True,
    dump_text: str | Path | None = None,
) -> dict[str, Any]:
    """Check page count, extractable text, required strings and date ranges."""
    path = Path(pdf_path)
    text, pages, extractor = extract_text(path)
    problems: list[str] = []

    if dump_text is not None:
        dump = Path(dump_text)
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")

    if expected_pages is not None and pages != expected_pages:
        problems.append(f"expected exactly {expected_pages} page(s), found {pages}")
    if max_pages is not None and pages > max_pages:
        problems.append(f"expected at most {max_pages} page(s), found {pages}")

    normalized = normalize_text(text)
    if len(normalized) < min_chars:
        problems.append(
            f"text layer has {len(normalized)} character(s); expected at least {min_chars}"
        )

    for required in contains:
        if normalize_text(str(required)) not in normalized:
            problems.append(f"text layer is missing required text: {required!r}")

    if ascii_dates:
        for line, dash in find_non_ascii_date_ranges(text):
            problems.append(
                f"date range joined by U+{ord(dash):04X} in {line[:80]!r}; "
                "write the range with an ASCII hyphen so an ATS keeps both years"
            )

    return {
        "pdf": str(path),
        "ok": not problems,
        "pages": pages,
        "extractor": extractor,
        "characters": len(normalized),
        "problems": problems,
    }


def contact_strings(profile: dict[str, Any]) -> list[str]:
    """Strings an ATS must find as literal text in a resume."""
    identity = profile.get("identity") or {}
    required = [str(identity.get(key) or "").strip() for key in ("name", "email", "phone")]
    return [value for value in required if value]


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a PDF's page count and ATS text layer")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", type=int, help="require exactly this many pages")
    parser.add_argument("--max-pages", type=int, help="require at most this many pages")
    parser.add_argument("--min-chars", type=int, default=1)
    parser.add_argument("--contains", action="append", default=[], help="required text; repeatable")
    parser.add_argument("--no-ascii-dates", action="store_true", help="skip the date-range check")
    parser.add_argument("--dump-text", type=Path, help="write the extracted text layer here")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_pdf(
            args.pdf,
            expected_pages=args.pages,
            max_pages=args.max_pages,
            min_chars=args.min_chars,
            contains=args.contains,
            ascii_dates=not args.no_ascii_dates,
            dump_text=args.dump_text,
        )
    except PdfCheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"{result['pdf']}: {result['pages']} page(s), "
            f"{result['characters']} chars, extractor {result['extractor']}"
        )
        for problem in result["problems"]:
            print(f"  - {problem}")
        print("pdf: clean" if result["ok"] else "pdf: problems found")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
