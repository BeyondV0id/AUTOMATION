from __future__ import annotations

import unittest

from resume_pipeline.match import score_job
from resume_pipeline.source_yc import parse_company_page

PROFILE = {
    "identity": {"name": "Test"},
    "skills": {"languages": ["Python"], "backend": ["FastAPI", "Postgres"]},
    "experience": [{"role": "engineer", "years": 3}],
    "preferences": {"roles": ["Backend Engineer", "Software Engineer"], "locations": ["India"], "remote": True},
}


class MatchingTests(unittest.TestCase):
    def test_exact_role_beats_off_topic(self) -> None:
        good = score_job({"title": "Backend Engineer", "location": "Remote", "skills": [], "description": ""}, PROFILE)
        bad = score_job({"title": "Account Executive", "location": "Remote", "skills": [], "description": ""}, PROFILE)
        self.assertGreater(good["score"], bad["score"])
        self.assertGreaterEqual(good["score"], 60)

    def test_missing_skills_reported(self) -> None:
        result = score_job(
            {"title": "Software Engineer", "location": "Remote", "skills": ["Python", "Rust"], "description": ""},
            PROFILE,
        )
        self.assertIn("rust", result["missing"])
        self.assertNotIn("python", result["missing"])

    def test_no_profile_scores_nothing(self) -> None:
        self.assertIsNone(score_job({"title": "Backend Engineer"}, None)["score"])


class YcParsingTests(unittest.TestCase):
    def test_missing_page_data_returns_none(self) -> None:
        self.assertIsNone(parse_company_page("<html></html>"))


if __name__ == "__main__":
    unittest.main()
