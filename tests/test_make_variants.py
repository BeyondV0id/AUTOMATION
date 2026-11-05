from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from resume_pipeline import make_variants
from resume_pipeline.relevance import all_bullets


def profile() -> dict:
    return {"identity": {"name": "Jane Doe"}}


def job() -> dict:
    return {"title": "Backend Engineer", "description": "Python and Kubernetes services."}


def overlay() -> dict:
    return {
        "experience": [
            {"company": "Acme", "title": "Engineer", "bullets": ["Maintained reports.", "Built Python APIs."]},
            {"company": "Beta", "title": "Engineer", "bullets": ["Wrote docs.", "Shipped Kubernetes jobs."]},
        ]
    }


class FitPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.destination = Path(self.directory.name)

    def run_fit(self, pages: list[int], max_cuts: int = 4, layouts: list[dict] | None = None) -> tuple:
        sequence = iter(pages)
        layout_sequence = iter(layouts or [])

        def fake_render(*_args, **_kwargs):
            return self.destination / "resume.tex"

        def fake_compile(*_args, **_kwargs):
            return {"status": "passed", "pdf": str(self.destination / "resume.pdf")}

        def fake_check(*_args, **_kwargs):
            return {"pages": next(sequence), "ok": True}

        def fake_layout(*_args, **_kwargs):
            return next(layout_sequence, {"ok": True, "problems": []})

        with mock.patch.object(make_variants, "render", fake_render), \
             mock.patch.object(make_variants, "compile_source", fake_compile), \
             mock.patch.object(make_variants, "check_pdf", fake_check), \
             mock.patch.object(make_variants, "check_layout", fake_layout):
            return make_variants.fit_to_pages(
                profile(), "backend", self.destination, job(), overlay(), "auto", 1, max_cuts
            )

    def test_cuts_until_the_pdf_fits(self) -> None:
        fitted, cuts, compiled, attempts, tex_path, check = self.run_fit([2, 2, 1])
        self.assertEqual(attempts, 3)
        self.assertEqual(len(cuts), 2)
        self.assertEqual(check["pages"], 1)
        self.assertEqual(compiled["status"], "passed")
        self.assertTrue(str(tex_path).endswith("resume.tex"))
        self.assertEqual(cuts[0]["pages_before"], 2)
        self.assertLess(len(all_bullets(fitted)), len(all_bullets(overlay())))

    def test_cut_limit_stops_the_loop(self) -> None:
        _fitted, cuts, _compiled, attempts, _tex, check = self.run_fit([2, 2, 2], max_cuts=1)
        self.assertEqual(attempts, 2)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(check["pages"], 2)

    def test_overfull_layout_triggers_another_cut(self) -> None:
        _fitted, cuts, _compiled, attempts, _tex, check = self.run_fit(
            [1, 1],
            layouts=[
                {"ok": False, "problems": ["p1 has text inside the bottom margin"]},
                {"ok": True, "problems": []},
            ],
        )
        self.assertEqual(attempts, 2)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(check["pages"], 1)
        self.assertEqual(check["blocking_layout"], [])

    def test_thin_last_page_does_not_trigger_another_cut(self) -> None:
        _fitted, cuts, _compiled, attempts, _tex, check = self.run_fit(
            [1],
            layouts=[{"ok": False, "problems": ["p1 is the last page and 80% empty"]}],
        )
        self.assertEqual(attempts, 1)
        self.assertEqual(cuts, [])
        self.assertEqual(check["pages"], 1)

    def test_compile_failure_returns_without_checking_pages(self) -> None:
        def fake_render(*_args, **_kwargs):
            return self.destination / "resume.tex"

        def fake_compile(*_args, **_kwargs):
            return {"status": "failed", "pdf": str(self.destination / "resume.pdf")}

        with mock.patch.object(make_variants, "render", fake_render), \
             mock.patch.object(make_variants, "compile_source", fake_compile), \
             mock.patch.object(make_variants, "check_pdf") as check, \
             mock.patch.object(make_variants, "check_layout", lambda *_a, **_k: {"ok": True, "problems": []}):
            _fitted, cuts, compiled, attempts, _tex, result = make_variants.fit_to_pages(
                profile(), "backend", self.destination, job(), overlay(), "auto", 1, 4
            )
        check.assert_not_called()
        self.assertEqual(compiled["status"], "failed")
        self.assertEqual(cuts, [])
        self.assertEqual(attempts, 1)
        self.assertIsNone(result)

    def test_no_candidates_stops_without_cutting(self) -> None:
        single = {"experience": [{"company": "Acme", "title": "Engineer", "bullets": ["Only bullet."]}]}
        sequence = iter([2])

        def fake_render(*_args, **_kwargs):
            return self.destination / "resume.tex"

        def fake_compile(*_args, **_kwargs):
            return {"status": "passed", "pdf": str(self.destination / "resume.pdf")}

        def fake_check(*_args, **_kwargs):
            return {"pages": next(sequence), "ok": True}

        with mock.patch.object(make_variants, "render", fake_render), \
             mock.patch.object(make_variants, "compile_source", fake_compile), \
             mock.patch.object(make_variants, "check_pdf", fake_check), \
             mock.patch.object(make_variants, "check_layout", lambda *_a, **_k: {"ok": True, "problems": []}):
            _fitted, cuts, _compiled, attempts, _tex, result = make_variants.fit_to_pages(
                profile(), "backend", self.destination, job(), single, "auto", 1, 4
            )
        self.assertEqual(cuts, [])
        self.assertEqual(attempts, 1)
        self.assertEqual(result["pages"], 2)


if __name__ == "__main__":
    unittest.main()
