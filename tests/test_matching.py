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

FRESHER = {
    "identity": {"name": "Test"},
    "skills": {"languages": ["Python", "TypeScript"], "backend": ["Node.js"]},
    "experience": [{"role": "Frontend Developer Intern", "years": 0}],
    "preferences": {
        "roles": ["Software Engineer", "Backend Developer"],
        "locations": ["India"],
        "remote": True,
        "level": "fresher",
        "max_years_required": 1,
    },
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


class FresherRankingTests(unittest.TestCase):
    def test_internship_beats_staff_role(self) -> None:
        intern = score_job(
            {"title": "Software Engineer Intern", "location": "Remote", "description": "Python, Node.js"},
            FRESHER,
        )
        staff = score_job(
            {
                "title": "Staff Software Engineer",
                "location": "Remote",
                "description": "Python, Node.js. 12+ years of experience required.",
            },
            FRESHER,
        )
        self.assertEqual(intern["level"], "internship")
        self.assertEqual(staff["level"], "senior")
        self.assertGreater(intern["score"], staff["score"])

    def test_experienced_role_is_capped(self) -> None:
        result = score_job(
            {
                "title": "Backend Developer",
                "location": "Bengaluru, India",
                "description": "Python, Node.js. 4+ years of experience building APIs.",
            },
            FRESHER,
        )
        self.assertFalse(result["eligible"])
        self.assertLessEqual(result["score"], 35)

    def test_off_topic_never_eligible(self) -> None:
        result = score_job({"title": "Corporate Recruiter", "location": "Remote"}, FRESHER)
        self.assertFalse(result["eligible"])

    def test_foreign_onsite_role_ranks_below_india(self) -> None:
        india = score_job({"title": "Backend Developer", "location": "Pune, India"}, FRESHER)
        abroad = score_job({"title": "Backend Developer", "location": "Paris, France"}, FRESHER)
        self.assertGreater(india["score"], abroad["score"])

    def test_region_locked_remote_is_not_remote(self) -> None:
        world = score_job({"title": "Backend Developer", "location": "Remote (worldwide)"}, FRESHER)
        us_only = score_job({"title": "Backend Developer", "location": "REMOTE (US)"}, FRESHER)
        self.assertGreater(world["score"], us_only["score"])


class YcParsingTests(unittest.TestCase):
    def test_missing_page_data_returns_none(self) -> None:
        self.assertIsNone(parse_company_page("<html></html>"))


if __name__ == "__main__":
    unittest.main()
