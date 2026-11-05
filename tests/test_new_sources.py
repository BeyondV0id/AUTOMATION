from __future__ import annotations

import unittest

from resume_pipeline.source_gmafj import (
    chunk_for,
    company_from_url,
    normalize as normalize_gmafj,
    parse_index,
    pick,
)
from resume_pipeline.source_hiringcafe import (
    location_state,
    normalize as normalize_hiringcafe,
    search_state,
)

INDEX = "\n".join(
    [
        '{"v":1,"build":"x","updated":"2026-10-05","k":64,"count":2,"countries":{"IN":"India"}}',
        "100\tBackend Engineer\tBengaluru\tIN\tFMR\t20730\tINR120-180",
        "5\tMachine Learning Engineer\tRemote\tIN\tFS-\t20729\tINR150-200~",
    ]
)


class GmafjIndexTests(unittest.TestCase):
    def test_delta_ids_are_rebuilt(self) -> None:
        meta, rows = parse_index(INDEX)
        self.assertEqual(meta["count"], 2)
        self.assertEqual([row["id"] for row in rows], [100, 95])
        self.assertEqual(rows[0]["countries"], ["IN"])
        self.assertTrue(rows[0]["flags"].endswith("R"))

    def test_chunk_hash_matches_the_site_rule(self) -> None:
        limit = (2 ** 32) / 64
        low = next(i for i in range(1, 5000) if ((i * 0x9E3779B1) & 0xFFFFFFFF) < limit)
        high = next(i for i in range(1, 5000) if ((i * 0x9E3779B1) & 0xFFFFFFFF) >= limit)
        self.assertEqual(chunk_for(low, 64, "top"), str(low))
        self.assertEqual(chunk_for(high, 64, "top"), "top")

    def test_pick_filters_by_keyword_country_and_remote(self) -> None:
        _, rows = parse_index(INDEX)
        self.assertEqual([row["id"] for row in pick(rows, ["backend"], ["IN"], False, None, 10)], [100])
        self.assertEqual([row["id"] for row in pick(rows, ["engineer"], ["IN"], False, None, 10)], [100, 95])
        self.assertEqual(pick(rows, ["backend"], ["US"], False, None, 10), [])
        # remote-only jobs survive a country filter they do not match
        self.assertEqual([row["id"] for row in pick(rows, ["engineer"], ["US"], True, None, 10)], [100])

    def test_pick_respects_days_old(self) -> None:
        _, rows = parse_index(INDEX)
        self.assertEqual(pick(rows, ["engineer"], [], False, 1, 10), [])
        self.assertEqual(len(pick(rows, ["engineer"], [], False, 40000, 10)), 2)


class GmafjCompanyTests(unittest.TestCase):
    def test_ats_subdomain_names_the_employer(self) -> None:
        self.assertEqual(company_from_url("https://acme.greenhouse.io/jobs/1"), "Acme")

    def test_ats_path_names_the_employer_when_the_host_is_generic(self) -> None:
        self.assertEqual(company_from_url("https://apply.workable.com/acme/j/ABC/"), "Acme")
        self.assertEqual(company_from_url("https://jobs.lever.co/acme/123"), "Acme")

    def test_aggregator_is_left_blank(self) -> None:
        self.assertEqual(company_from_url("https://foorilla.com/hiring/jobs/x"), "")

    def test_plain_careers_host_uses_the_domain(self) -> None:
        self.assertEqual(company_from_url("https://careers.example.com/x"), "Example")


class GmafjNormalizeTests(unittest.TestCase):
    def test_detail_fields_are_used(self) -> None:
        _, rows = parse_index(INDEX)
        detail = [
            "https://acme.greenhouse.io/jobs/1",
            2,
            ["Build services"],
            [],
            ["Python", "SQL"],
            [],
            [],
            "en",
        ]
        job = normalize_gmafj(rows[0], detail)
        self.assertEqual(job["id"], "gmafj:100")
        self.assertEqual(job["company"], "Acme")
        self.assertEqual(job["url"], "https://acme.greenhouse.io/jobs/1")
        self.assertEqual(job["skills"], ["Python", "SQL"])
        self.assertIn("Build services", job["description"])
        self.assertTrue(job["remote"])


class HiringCafeTests(unittest.TestCase):
    def test_country_location_shape(self) -> None:
        place = location_state("India")
        self.assertIsNotNone(place)
        self.assertEqual(place["address_components"][0]["short_name"], "IN")
        self.assertIsNone(location_state("Atlantis"))

    def test_search_state_carries_filters(self) -> None:
        state = search_state("backend engineer", "India", 7)
        self.assertEqual(state["searchQuery"], "backend engineer")
        self.assertEqual(state["dateFetchedPastNDays"], 7)
        self.assertEqual(state["locations"][0]["types"], ["country"])

    def test_hit_is_normalized(self) -> None:
        hit = {
            "objectID": "abc123",
            "apply_url": "https://acme.example/jobs/1",
            "job_information": {"title": "Backend Engineer"},
            "v5_processed_job_data": {
                "company_name": "Acme",
                "formatted_workplace_location": "Bengaluru, Karnataka, India",
                "workplace_type": "Hybrid",
                "commitment": ["Full Time"],
                "seniority_level": "Mid Level",
                "technical_tools": ["Python", "Postgres"],
                "role_activities": ["build APIs"],
                "requirements_summary": "3+ years with Python",
                "listed_compensation_currency": "INR",
                "yearly_min_compensation": 1200000,
                "yearly_max_compensation": 1800000,
                "estimated_publish_date": "2026-11-04T09:13:53.703Z",
            },
        }
        job = normalize_hiringcafe(hit)
        self.assertEqual(job["id"], "hiringcafe:abc123")
        self.assertEqual(job["company"], "Acme")
        self.assertEqual(job["location"], "Bengaluru, Karnataka, India")
        self.assertEqual(job["skills"], ["Python", "Postgres"])
        self.assertEqual(job["posted_at"], "2026-11-04")
        self.assertIn("3+ years with Python", job["description"])
        self.assertIn("INR 1200K-1800K", job["description"])
        self.assertFalse(job["remote"])


if __name__ == "__main__":
    unittest.main()
