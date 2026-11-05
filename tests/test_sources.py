from __future__ import annotations

import unittest

from resume_pipeline.source_engg import _payload, normalize as normalize_engg
from resume_pipeline.source_internshala import parse_cards as parse_internshala
from resume_pipeline.source_linkedin import parse_cards as parse_linkedin
from resume_pipeline.source_naukri import normalize as normalize_naukri

LINKEDIN = """
<li>
  <div class="base-card relative base-search-card job-search-card"
       data-entity-urn="urn:li:jobPosting:4471319619">
    <a class="base-card__full-link absolute top-0"
       href="https://in.linkedin.com/jobs/view/4471319619?trk=public_jobs">
      <span class="sr-only">Backend Engineer</span>
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title"> Backend Engineer </h3>
      <h4 class="base-search-card__subtitle">
        <a class="hidden-nested-link" href="https://in.linkedin.com/company/acme">Acme Labs</a>
      </h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location"> Bengaluru, Karnataka, India </span>
        <time class="job-search-card__listdate" datetime="2026-10-01">1 month ago</time>
      </div>
    </div>
  </div>
</li>
"""

INTERNSHALA = """
<div class="container-fluid individual_internship logged_out_jd_summary "
     id="individual_internship_3318491" internshipId="3318491">
  <h2 class="job-internship-name">
    <a class="job-title-href" href="/internship/detail/software-development-internship-at-acme1792133076">
      Software Development
    </a>
  </h2>
  <p class="company-name"> Acme Technologies </p>
  <div class="row-1-item locations"><i class="ic-16-map-pin"></i><span><a>Bengaluru</a></span></div>
  <span class='stipend'>? 20,000 /month</span>
  <div class="about_job"><div class="text">Build Python services and automation.</div></div>
  <div class="job_skills"><div class='job_skill'>Python</div><div class='job_skill'>Django</div></div>
  <div class="category_name">Software Development</div>
  <div class="category_name">Backend Development</div>
</div>
"""


class LinkedInTests(unittest.TestCase):
    def test_card_fields(self) -> None:
        jobs = parse_linkedin(LINKEDIN)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["id"], "linkedin:4471319619")
        self.assertEqual(job["company"], "Acme Labs")
        self.assertEqual(job["title"], "Backend Engineer")
        self.assertEqual(job["url"], "https://www.linkedin.com/jobs/view/4471319619")
        self.assertEqual(job["posted_at"], "2026-10-01")

    def test_empty_markup(self) -> None:
        self.assertEqual(parse_linkedin("<html></html>"), [])


class InternshalaTests(unittest.TestCase):
    def test_card_fields(self) -> None:
        jobs = parse_internshala(INTERNSHALA)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["id"], "internshala:3318491")
        self.assertEqual(job["company"], "Acme Technologies")
        self.assertEqual(job["title"], "Software Development")
        self.assertEqual(job["location"], "Bengaluru")
        self.assertEqual(job["url"], "https://internshala.com/internship/detail/software-development-internship-at-acme1792133076")
        self.assertIn("Python", job["skills"])
        self.assertIn("Backend Development", job["description"])

    def test_missing_cards(self) -> None:
        self.assertEqual(parse_internshala("<html></html>"), [])


class EnggTests(unittest.TestCase):
    def test_sse_payload(self) -> None:
        message = _payload('event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n')
        self.assertEqual(message["result"], {"ok": True})

    def test_normalize_prefers_apply_url(self) -> None:
        job = normalize_engg(
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Bengaluru, IND",
                "workplace": "hybrid",
                "level": "mid",
                "min_years": 2,
                "salary": "₹20L",
                "posted": "2026-11-04",
                "company_funding": "Series B",
                "url": "https://www.engg.space/companies/acme/backend-engineer-abc123",
                "apply_url": "https://jobs.ashbyhq.com/acme/123",
            }
        )
        self.assertEqual(job["id"], "engg:backend-engineer-abc123")
        self.assertEqual(job["url"], "https://jobs.ashbyhq.com/acme/123")
        self.assertIn("minimum 2 years", job["description"])
        self.assertFalse(job["remote"])


class NaukriTests(unittest.TestCase):
    def test_normalize_card(self) -> None:
        job = normalize_naukri(
            {
                "title": "Software Engineer",
                "company": "Qualcomm",
                "location": "Hyderabad",
                "experience": "0-1 Yrs",
                "salary": "15-25 Lacs PA",
                "description": "Neural network runtimes",
                "skills": ["Python", "C++"],
                "posted": "1 day ago",
                "url": "https://www.naukri.com/job-listings-software-engineer-qualcomm-hyderabad-061026002836",
            }
        )
        self.assertEqual(job["id"], "naukri:job-listings-software-engineer-qualcomm-hyderabad-061026002836")
        self.assertEqual(job["source"], "naukri")
        self.assertIn("Python", job["skills"])
        self.assertFalse(job["remote"])


if __name__ == "__main__":
    unittest.main()
