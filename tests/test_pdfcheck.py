from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from resume_pipeline.pdfcheck import (
    PdfCheckError,
    check_pdf,
    extract_text,
    find_non_ascii_date_ranges,
    looks_garbled,
    normalize_text,
)


def make_pdf(path: Path, pages: list[list[str]], fontsize: float = 11) -> Path:
    document = fitz.open()
    try:
        for lines in pages:
            page = document.new_page()
            for index, line in enumerate(lines):
                page.insert_text((72, 72 + index * 16), line, fontsize=fontsize)
        document.save(str(path))
    finally:
        document.close()
    return path


class PdfCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_clean_pdf_passes(self) -> None:
        path = make_pdf(
            self.root / "clean.pdf",
            [["Jane Doe", "jane@example.com", "Experience"], ["Education"]],
        )
        result = check_pdf(path, expected_pages=2, contains=["Jane Doe", "jane@example.com"])
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["pages"], 2)
        self.assertGreaterEqual(result["characters"], 1)

    def test_page_count_mismatch_is_reported(self) -> None:
        path = make_pdf(self.root / "one.pdf", [["Jane Doe"]])
        result = check_pdf(path, expected_pages=2)
        self.assertFalse(result["ok"])
        self.assertIn("expected exactly 2 page(s), found 1", result["problems"])

    def test_max_pages_is_enforced(self) -> None:
        path = make_pdf(self.root / "two.pdf", [["A"], ["B"]])
        result = check_pdf(path, max_pages=1)
        self.assertFalse(result["ok"])
        self.assertIn("expected at most 1 page(s), found 2", result["problems"])

    def test_missing_required_text_is_reported(self) -> None:
        path = make_pdf(self.root / "contact.pdf", [["Jane Doe"]])
        result = check_pdf(path, contains=["jane@example.com"])
        self.assertFalse(result["ok"])
        self.assertTrue(any("missing required text" in problem for problem in result["problems"]))

    def test_dump_text_writes_the_layer(self) -> None:
        path = make_pdf(self.root / "dump.pdf", [["Jane Doe"]])
        dump = self.root / "out" / "resume.txt"
        check_pdf(path, dump_text=dump)
        self.assertIn("Jane Doe", dump.read_text(encoding="utf-8"))

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(PdfCheckError):
            check_pdf(self.root / "nope.pdf")

    def test_non_pdf_bytes_raise(self) -> None:
        path = self.root / "fake.pdf"
        path.write_bytes(b"not a pdf")
        with self.assertRaises(PdfCheckError):
            extract_text(path)

    def test_normalize_folds_latex_typography(self) -> None:
        self.assertEqual(normalize_text("2020 \u2013 2024"), "2020 - 2024")
        self.assertEqual(normalize_text("a\u00a0 b"), "a b")

    def test_garbled_text_is_detected(self) -> None:
        self.assertTrue(looks_garbled("email (cid:12)"))
        self.assertTrue(looks_garbled("bad \ufffd glyph"))
        self.assertFalse(looks_garbled("Jane Doe"))

    def test_non_ascii_date_range_is_reported(self) -> None:
        hits = find_non_ascii_date_ranges("Acme 2020 \u2013 2024\nOther 2019\u2014present")
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0][1], "\u2013")
        self.assertEqual(hits[1][1], "\u2014")

    def test_ascii_date_range_is_clean(self) -> None:
        self.assertEqual(find_non_ascii_date_ranges("Acme 2020 - 2024"), [])

    def test_numeric_quantity_is_not_a_date_range(self) -> None:
        self.assertEqual(find_non_ascii_date_ranges("cut latency 40 \u2013 50%"), [])

    def test_pdf_with_non_ascii_date_fails_if_extractor_sees_it(self) -> None:
        # PyMuPDF preserves the en dash when the glyph is embedded. pypdf may
        # hand back a replacement glyph instead, in which case the layer is
        # treated as unreadable rather than silently passing.
        try:
            import matplotlib
        except ImportError:
            self.skipTest("matplotlib font not available")
        font = Path(matplotlib.__file__).parent / "mpl-data/fonts/ttf/DejaVuSans.ttf"
        if not font.exists():
            self.skipTest("DejaVuSans.ttf not available")
        path = self.root / "dash.pdf"
        document = fitz.open()
        try:
            page = document.new_page()
            page.insert_text((72, 72), "Acme 2020 \u2013 2024", fontsize=11, fontname="dejavu", fontfile=str(font))
            document.save(str(path))
        finally:
            document.close()
        result = check_pdf(path)
        self.assertFalse(result["ok"])
        self.assertTrue(any("date range" in problem for problem in result["problems"]))


if __name__ == "__main__":
    unittest.main()
