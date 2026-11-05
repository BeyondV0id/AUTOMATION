from __future__ import annotations

import unittest

from resume_pipeline.latex import _period, tex


class LatexTests(unittest.TestCase):
    def test_period_uses_ascii_hyphen(self) -> None:
        self.assertEqual(_period({"start": "2022", "end": "2024"}), "2022 - 2024")
        self.assertNotIn("--", _period({"start": "2022", "end": "2024"}))
        self.assertEqual(_period({"start": "2022"}), "2022")

    def test_period_with_default_end(self) -> None:
        self.assertEqual(_period({"start": "2022"}, "Present"), "2022 - Present")

    def test_tex_escapes_special_characters(self) -> None:
        self.assertEqual(tex("A & B"), r"A \& B")
        self.assertEqual(tex("100%"), r"100\%")
        self.assertEqual(tex("a_b"), r"a\_b")


if __name__ == "__main__":
    unittest.main()
