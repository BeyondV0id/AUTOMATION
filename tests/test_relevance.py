from __future__ import annotations

import unittest

from resume_pipeline.relevance import (
    all_bullets,
    bullet_score,
    cut_candidates,
    fit_overlay,
    frequencies,
    lowest_value_bullet,
    remove_bullet,
)
from resume_pipeline.tailor_variants import tokens


def overlay() -> dict:
    return {
        "experience": [
            {
                "company": "Acme",
                "title": "Engineer",
                "bullets": [
                    "Maintained legacy reports for the finance team.",
                    "Cut latency 40% with Python and Kubernetes services.",
                ],
            },
            {
                "company": "Beta",
                "title": "Engineer",
                "bullets": [
                    "Wrote internal documentation.",
                    "Attended weekly planning meetings.",
                ],
            },
        ],
        "projects": [
            {"name": "Tool", "bullets": ["Built a command line tool."]},
        ],
    }


class RelevanceTests(unittest.TestCase):
    def test_all_bullets_reads_both_sections(self) -> None:
        bullets = all_bullets(overlay())
        self.assertEqual(len(bullets), 5)
        self.assertIn("Cut latency 40% with Python and Kubernetes services.", bullets)

    def test_frequencies_counts_documents_not_occurrences(self) -> None:
        counts = frequencies(["python python python", "python and sql"])
        self.assertEqual(counts["python"], 2)
        self.assertEqual(counts["sql"], 1)

    def test_relevant_metric_bullet_outscores_generic(self) -> None:
        job_tokens = tokens("python kubernetes")
        counts = frequencies(all_bullets(overlay()))
        relevant = bullet_score(overlay()["experience"][0]["bullets"][1], job_tokens, counts, 1)
        generic = bullet_score(overlay()["experience"][0]["bullets"][0], job_tokens, counts, 0)
        self.assertGreater(relevant, generic)

    def test_lowest_value_bullet_skips_single_bullet_records(self) -> None:
        candidate = lowest_value_bullet(overlay(), tokens("python kubernetes"))
        self.assertIsNotNone(candidate)
        self.assertNotEqual(candidate["section"], "projects")

    def test_empty_bullets_do_not_count_as_cuttable_content(self) -> None:
        data = {"experience": [{"company": "Acme", "title": "Engineer", "bullets": ["", "Only real bullet."]}]}
        self.assertIsNone(lowest_value_bullet(data, set()))

    def test_remove_bullet_returns_text_and_shrinks_record(self) -> None:
        data = overlay()
        candidate = cut_candidates(data, tokens("python"))[0]
        removed = remove_bullet(data, candidate)
        self.assertIsInstance(removed, str)
        self.assertEqual(len(data[candidate["section"]][candidate["record_index"]]["bullets"]), 1)

    def test_fit_overlay_never_removes_the_last_bullet(self) -> None:
        fitted, cuts = fit_overlay(overlay(), tokens("python kubernetes"), max_cuts=10)
        self.assertLessEqual(len(cuts), 2)
        for section in ("experience", "projects"):
            for record in fitted[section]:
                self.assertGreaterEqual(len(record["bullets"]), 1)

    def test_fit_overlay_keeps_the_relevant_bullet(self) -> None:
        fitted, _cuts = fit_overlay(overlay(), tokens("python kubernetes"), max_cuts=10)
        bullets = all_bullets(fitted)
        self.assertIn("Cut latency 40% with Python and Kubernetes services.", bullets)

    def test_fit_overlay_does_not_mutate_the_input(self) -> None:
        original = overlay()
        fit_overlay(original, tokens("python"), max_cuts=4)
        self.assertEqual(len(all_bullets(original)), 5)

    def test_fit_overlay_with_no_candidates_returns_input_copy(self) -> None:
        data = {"experience": [{"company": "A", "title": "B", "bullets": ["Only one."]}]}
        fitted, cuts = fit_overlay(data, set(), max_cuts=3)
        self.assertEqual(cuts, [])
        self.assertEqual(all_bullets(fitted), ["Only one."])

    def test_cut_order_is_deterministic(self) -> None:
        first = cut_candidates(overlay(), tokens("python kubernetes"))
        second = cut_candidates(overlay(), tokens("python kubernetes"))
        self.assertEqual([item["bullet"] for item in first], [item["bullet"] for item in second])


if __name__ == "__main__":
    unittest.main()
