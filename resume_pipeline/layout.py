"""Measure a compiled PDF's layout instead of eyeballing the rendered page.

A clean compile and a correct page count do not prove the layout is usable. A
Jake's Resume entry is a tabular block: when it does not fit, it jumps to the
next page and leaves a hole behind, or its header lands at the bottom of one
page while its bullets resume overleaf. This module measures those failures
from the PDF's text geometry.

Coordinates come from PyMuPDF (optional: ``pip install pymupdf``). Without it
the check reports ``skipped`` rather than inventing a verdict. Page count is
deliberately left to ``pdfcheck.py``; two implementations of one rule drift.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A gap larger than this between consecutive lines is a hole, not section spacing.
GAP_LIMIT_PT = 100.0
# A non-final page that stops this far short of the bottom reads as a broken break.
BOTTOM_LIMIT_FRACTION = 0.25
# A final page emptier than this reads as an unfinished document.
LAST_PAGE_THIN_FRACTION = 0.35
# Text inside this band is in the bottom margin. Jake's Resume leaves a 0.5in
# margin and an empty footer, so 36pt is the real edge; a wider band would flag
# a page that is merely full.
FOOTER_BAND_PT = 36.0
# A line indented at least this far past the document margin is a bullet.
INDENT_PT = 8.0
# A line this much taller than the body median is a section heading.
HEADING_SIZE_RATIO = 1.25


class LayoutUnavailable(Exception):
    """Raised when no usable PDF geometry extractor is installed."""


@dataclass
class Line:
    top: float
    bottom: float
    left: float
    size: float
    text: str


@dataclass
class Page:
    width: float
    height: float
    lines: list[Line]

    @property
    def body(self) -> list[Line]:
        cutoff = self.height - FOOTER_BAND_PT
        return [line for line in self.lines if line.top < cutoff]

    @property
    def bottom_space(self) -> float:
        return self.height - max(line.bottom for line in self.body) if self.body else self.height

    @property
    def left_edge(self) -> float:
        return min(line.left for line in self.body) if self.body else 0.0

    @property
    def median_size(self) -> float:
        sizes = sorted(line.size for line in self.body)
        return sizes[len(sizes) // 2] if sizes else 0.0

    @property
    def footer_crowded(self) -> bool:
        return any(line.top >= self.height - FOOTER_BAND_PT for line in self.lines)

    def largest_gap(self) -> tuple[float, float]:
        tops = sorted({round(line.top, 1) for line in self.body})
        if len(tops) < 2:
            return (0.0, 0.0)
        return max((tops[index + 1] - tops[index], tops[index]) for index in range(len(tops) - 1))

    def is_heading(self, line: Line) -> bool:
        median = self.median_size
        return bool(median) and line.size > median * HEADING_SIZE_RATIO


def _lines_from_page(page: Any) -> list[Line]:
    lines: list[Line] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            x0, y0, _x1, y1 = line.get("bbox", (0, 0, 0, 0))
            lines.append(
                Line(
                    top=float(y0),
                    bottom=float(y1),
                    left=float(x0),
                    size=max(float(span.get("size", 0)) for span in spans),
                    text=text,
                )
            )
    return sorted(lines, key=lambda line: (line.top, line.left))


def parse_pdf(pdf_path: str | Path) -> list[Page]:
    """Return measured pages, or raise ``LayoutUnavailable``."""
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise LayoutUnavailable(
            "PyMuPDF is not installed; install it with `pip install pymupdf`"
        ) from exc

    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF does not exist: {path}")
    try:
        document = fitz.open(str(path))
    except Exception as exc:
        raise LayoutUnavailable(f"PyMuPDF could not read {path}: {exc}") from exc

    try:
        pages = [
            Page(page.rect.width, page.rect.height, _lines_from_page(page))
            for page in document
        ]
    finally:
        document.close()
    return pages


def find_orphans(pages: list[Page]) -> list[str]:
    """Flag a page that ends on an entry header or section heading."""
    problems: list[str] = []
    body_lines = [line for page in pages for line in page.body]
    if not body_lines:
        return problems
    document_left = min(line.left for line in body_lines)

    def indented(line: Line) -> bool:
        return line.left > document_left + INDENT_PT

    for index in range(len(pages) - 1):
        here, following = pages[index], pages[index + 1]
        if not here.body or not following.body:
            continue
        last, first = here.body[-1], following.body[0]
        if here.is_heading(last):
            problems.append(
                f"p{index + 1} ends on the section heading {last.text[:60]!r} with its "
                f"content on p{index + 2}; keep the heading with its first entry"
            )
        elif not indented(last) and indented(first):
            problems.append(
                f"p{index + 1} ends on {last.text[:60]!r} while p{index + 2} starts "
                f"indented ({first.text[:60]!r}); an entry header is orphaned from its "
                "bullets. Add \\needspace before the entry or shorten it"
            )
    return problems


def analyze(pages: list[Page]) -> dict[str, Any]:
    """Return per-page measurements and every layout problem found."""
    problems: list[str] = []
    page_reports: list[dict[str, Any]] = []

    for index, page in enumerate(pages, 1):
        if not page.body:
            problems.append(f"p{index} contains no text")
            page_reports.append({"page": index, "empty": True})
            continue

        gap, gap_y = page.largest_gap()
        share = page.bottom_space / page.height if page.height else 0.0
        page_reports.append(
            {
                "page": index,
                "text_top": round(page.body[0].top, 1),
                "text_bottom": round(page.body[-1].bottom, 1),
                "bottom_space": round(page.bottom_space, 1),
                "bottom_share": round(share, 3),
                "largest_gap": round(gap, 1),
                "largest_gap_y": round(gap_y, 1),
                "footer_crowded": page.footer_crowded,
            }
        )

        if gap > GAP_LIMIT_PT:
            problems.append(
                f"p{index} has a {gap:.0f}pt hole at y{gap_y:.0f} (~{gap / 14:.0f} blank "
                "lines); shorten the entry after the hole or move a shorter section above it"
            )
        if index < len(pages) and share > BOTTOM_LIMIT_FRACTION:
            problems.append(
                f"p{index} ends {page.bottom_space:.0f}pt ({share * 100:.0f}%) early although "
                "more pages follow"
            )
        if page.footer_crowded:
            problems.append(
                f"p{index} has text inside the bottom margin; cut content instead of "
                "stretching the page"
            )
        if index == len(pages) > 1 and share > LAST_PAGE_THIN_FRACTION:
            problems.append(
                f"p{index} is the last page and {share * 100:.0f}% empty; restore the "
                "highest-relevance content previously cut"
            )

    problems.extend(find_orphans(pages))
    return {
        "ok": not problems,
        "pages": len(pages),
        "page_reports": page_reports,
        "problems": problems,
    }


def check_layout(pdf_path: str | Path) -> dict[str, Any]:
    """Analyze a PDF, returning ``skipped`` when geometry extraction is unavailable."""
    try:
        pages = parse_pdf(pdf_path)
    except LayoutUnavailable as exc:
        return {"ok": True, "skipped": True, "reason": str(exc), "pages": None, "problems": []}
    result = analyze(pages)
    result["pdf"] = str(pdf_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure a PDF's page layout")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"error: {args.pdf} not found", file=sys.stderr)
        raise SystemExit(2)

    result = check_layout(args.pdf)
    if args.json:
        print(json.dumps(result, indent=2))
    elif result.get("skipped"):
        print(f"skipped: {result['reason']}", file=sys.stderr)
    else:
        print(f"{args.pdf}: {result['pages']} page(s)")
        for page in result["page_reports"]:
            if page.get("empty"):
                print(f"  p{page['page']}: EMPTY")
            else:
                print(
                    f"  p{page['page']}: bottom {page['bottom_space']}pt "
                    f"({page['bottom_share'] * 100:.0f}%), largest gap "
                    f"{page['largest_gap']}pt at y{page['largest_gap_y']}"
                )
        for problem in result["problems"]:
            print(f"  - {problem}")
        print("layout: clean" if result["ok"] else "layout: problems found")

    if result.get("skipped"):
        raise SystemExit(2)
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
