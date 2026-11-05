from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from resume_pipeline.layout import (
    FOOTER_BAND_PT,
    check_layout,
    find_orphans,
    parse_pdf,
)

PAGE_HEIGHT = 792.0


def make_pdf(path: Path, pages: list[list[tuple[float, float, str]]]) -> Path:
    """Build a PDF from ``(x, baseline_y, text)`` triples per page."""
    document = fitz.open()
    try:
        for lines in pages:
            page = document.new_page(width=612, height=PAGE_HEIGHT)
            for x, y, text, *rest in lines:
                size = rest[0] if rest else 11
                page.insert_text((x, y), text, fontsize=size)
        document.save(str(path))
    finally:
        document.close()
    return path


def filled(start: float = 72, stop: float = 700, step: float = 20, x: float = 72):
    return [(x, y, f"line {int(y)}") for y in range(int(start), int(stop) + 1, int(step))]


class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_clean_two_page_layout(self) -> None:
        path = make_pdf(self.root / "clean.pdf", [filled(), filled()])
        result = check_layout(path)
        self.assertFalse(result.get("skipped"))
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["pages"], 2)

    def test_parse_pdf_measures_pages(self) -> None:
        path = make_pdf(self.root / "measure.pdf", [filled()])
        pages = parse_pdf(path)
        self.assertEqual(len(pages), 1)
        self.assertAlmostEqual(pages[0].width, 612, places=0)
        self.assertAlmostEqual(pages[0].height, PAGE_HEIGHT, places=0)
        self.assertGreater(len(pages[0].body), 10)

    def test_internal_hole_is_reported(self) -> None:
        page = [(72, 72, "top"), (72, 100, "second"), (72, 400, "after the hole")]
        page += filled(430)
        path = make_pdf(self.root / "hole.pdf", [page, filled()])
        result = check_layout(path)
        self.assertFalse(result["ok"])
        self.assertTrue(any("hole" in problem for problem in result["problems"]))

    def test_early_page_ending_is_reported(self) -> None:
        path = make_pdf(self.root / "early.pdf", [filled(stop=140), filled()])
        result = check_layout(path)
        self.assertTrue(any("ends" in problem and "early" in problem for problem in result["problems"]))

    def test_thin_last_page_is_reported(self) -> None:
        path = make_pdf(self.root / "thin.pdf", [filled(), filled(stop=180)])
        result = check_layout(path)
        self.assertTrue(any("last page" in problem for problem in result["problems"]))

    def test_orphaned_entry_header_is_reported(self) -> None:
        first = filled(stop=700, x=72)
        second = filled(stop=700, x=90)
        path = make_pdf(self.root / "orphan.pdf", [first, second])
        result = check_layout(path)
        self.assertTrue(any("orphaned" in problem for problem in result["problems"]))

    def test_heading_at_page_bottom_is_reported(self) -> None:
        first = filled(stop=680, x=72) + [(72, 700, "EXPERIENCE", 16)]
        second = filled(stop=700, x=90)
        path = make_pdf(self.root / "heading.pdf", [first, second])
        result = check_layout(path)
        self.assertTrue(any("section heading" in problem for problem in result["problems"]))

    def test_footer_crowding_is_reported(self) -> None:
        page = filled(stop=700) + [(72, 785, "page 1 of 2")]
        path = make_pdf(self.root / "footer.pdf", [page])
        result = check_layout(path)
        self.assertTrue(any("bottom margin" in problem for problem in result["problems"]))

    def test_footer_band_boundary(self) -> None:
        page = filled(stop=700) + [(72, PAGE_HEIGHT - FOOTER_BAND_PT + 14, "footer")]
        path = make_pdf(self.root / "band.pdf", [page])
        result = check_layout(path)
        self.assertTrue(any("bottom margin" in problem for problem in result["problems"]))

    def test_empty_page_is_reported(self) -> None:
        path = make_pdf(self.root / "empty.pdf", [[], filled()])
        result = check_layout(path)
        self.assertTrue(any("no text" in problem for problem in result["problems"]))

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            parse_pdf(self.root / "missing.pdf")

    def test_find_orphans_accepts_empty_input(self) -> None:
        self.assertEqual(find_orphans([]), [])


if __name__ == "__main__":
    unittest.main()
