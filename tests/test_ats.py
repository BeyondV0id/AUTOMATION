from __future__ import annotations

import unittest

from resume_pipeline.ats import (
    COVERED,
    MISSING_GAP,
    MISSING_HAVE,
    SYNONYM,
    candidate_terms,
    contains,
    flatten,
    overlay_text,
    report_markdown,
    score_keywords,
)


PROFILE = {
    "identity": {"name": "Jane Doe"},
    "summary": "Backend engineer working in Python and PostgreSQL.",
    "skills": {
        "languages": ["Python", "SQL"],
        "databases": ["Postgres"],
        "cloud": ["Docker"],
    },
    "experience": [
        {
            "company": "Acme",
            "title": "Backend Engineer",
            "bullets": ["Built Python services on Postgres and Docker."],
        }
    ],
}

RESUME = "Jane Doe. Python services on Postgres. Built APIs with Python."


class AtsTests(unittest.TestCase):
    def test_contains_respects_word_boundaries(self) -> None:
        self.assertTrue(contains("Python and Java", "java"))
        self.assertFalse(contains("JavaScript and TypeScript", "java"))
        self.assertTrue(contains("Node.js services", "node.js"))

    def test_flatten_reads_nested_profile(self) -> None:
        text = flatten(PROFILE)
        self.assertIn("Acme", text)
        self.assertIn("Docker", text)

    def test_overlay_text_includes_bullets_and_skills(self) -> None:
        overlay = {
            "summary": "Backend engineer",
            "skills_order": {"languages": ["Python"]},
            "experience": [{"company": "Acme", "title": "Engineer", "bullets": ["Shipped APIs"]}],
        }
        text = overlay_text(overlay)
        self.assertIn("Shipped APIs", text)
        self.assertIn("Python", text)

    def test_four_statuses_are_separated(self) -> None:
        job = (
            "We need Python, PostgreSQL, Docker, Kubernetes and Rust. "
            "You will write Python services and deploy with Kubernetes."
        )
        result = score_keywords(job, PROFILE, RESUME)
        status = {item["keyword"]: item["status"] for item in result["keywords"]}
        self.assertEqual(status["python"], COVERED)
        self.assertEqual(status["postgresql"], SYNONYM)
        self.assertEqual(status["docker"], MISSING_HAVE)
        self.assertEqual(status["kubernetes"], MISSING_GAP)
        self.assertEqual(status["rust"], MISSING_GAP)

    def test_gap_terms_are_never_actionable(self) -> None:
        job = "Rust and Kubernetes are required."
        result = score_keywords(job, PROFILE, RESUME)
        self.assertIn("rust", result["gaps"])
        self.assertNotIn("rust", result["actionable"])

    def test_have_it_terms_are_actionable(self) -> None:
        job = "Docker and Python are required."
        result = score_keywords(job, PROFILE, RESUME)
        self.assertEqual(result["actionable"], ["docker"])

    def test_synonym_only_uses_alias(self) -> None:
        job = "JavaScript everywhere. JavaScript."
        resume = "Built tools in JS."
        result = score_keywords(job, {"skills": {"languages": ["JavaScript"]}}, resume)
        self.assertEqual(result["keywords"][0]["status"], SYNONYM)

    def test_title_terms_are_weighted(self) -> None:
        job = "Event sourcing. Event sourcing."
        plain = dict(candidate_terms(job, title=""))
        titled = dict(candidate_terms(job, title="Backend Engineer"))
        self.assertIn("event sourcing", plain)
        self.assertGreaterEqual(titled.get("backend", 0), 3)

    def test_report_has_actionable_and_gap_sections(self) -> None:
        job = "Docker and Rust are required."
        result = score_keywords(job, PROFILE, RESUME)
        markdown = report_markdown(result, {"title": "Backend Engineer", "company": "Acme"})
        self.assertIn("Add these", markdown)
        self.assertIn("docker", markdown)
        self.assertIn("Gaps", markdown)
        self.assertIn("rust", markdown)

    def test_empty_job_returns_empty_report(self) -> None:
        result = score_keywords("", PROFILE, RESUME)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["exact_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
