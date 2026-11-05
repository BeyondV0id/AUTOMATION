from __future__ import annotations

import unittest

from resume_pipeline.level import (
    FRESHER,
    INTERNSHIP,
    JUNIOR,
    MID,
    SENIOR,
    UNKNOWN,
    detect_level,
    years_required,
)


class InternshipTest(unittest.TestCase):
    def test_intern_in_title(self) -> None:
        self.assertEqual(detect_level("Software Engineer Intern")["level"], INTERNSHIP)

    def test_graduate_scheme_counts_as_entry(self) -> None:
        self.assertEqual(detect_level("Graduate Engineering Summer Internship 2027")["level"], INTERNSHIP)

    def test_trainee_counts_as_entry(self) -> None:
        self.assertEqual(detect_level("Graduate Engineer Trainee")["level"], INTERNSHIP)

    def test_intern_with_long_requirement_is_not_entry(self) -> None:
        self.assertNotEqual(
            detect_level("Engineering Intern", "3+ years of experience required")["level"],
            INTERNSHIP,
        )


class FresherTest(unittest.TestCase):
    def test_zero_to_one_year(self) -> None:
        info = detect_level("Full Stack Developer", "0-1 years of experience; freshers welcome")
        self.assertEqual(info["level"], FRESHER)
        self.assertEqual(info["years_required"], 0)

    def test_entry_level_field(self) -> None:
        self.assertEqual(detect_level("Software Engineer", "entry level | minimum 1 year")["level"], FRESHER)


class SeniorityTest(unittest.TestCase):
    def test_three_plus_years_is_mid(self) -> None:
        info = detect_level("Backend Engineer", "You have 3+ years of experience with Python.")
        self.assertEqual(info["level"], MID)
        self.assertEqual(info["years_required"], 3)

    def test_range_uses_the_lower_bound(self) -> None:
        self.assertEqual(years_required("Software Engineer", "2-4 years of experience"), 2)

    def test_years_in_title_are_read(self) -> None:
        info = detect_level("Python Developer | Django/Flask | 5-12 Years", "")
        self.assertEqual(info["level"], SENIOR)
        self.assertEqual(info["years_required"], 5)

    def test_title_and_body_disagreement_uses_the_larger(self) -> None:
        info = detect_level("Cloud Backend Engineer - 4+ Years", "mid level | minimum 0 years")
        self.assertEqual(info["years_required"], 4)
        self.assertEqual(info["level"], MID)

    def test_stated_band_is_trusted(self) -> None:
        self.assertEqual(detect_level("Backend Engineer", "senior level | minimum 12 years")["level"], SENIOR)

    def test_senior_title(self) -> None:
        self.assertEqual(detect_level("Staff Engineer - Cloud Backend")["level"], SENIOR)

    def test_roman_numeral_title(self) -> None:
        self.assertEqual(detect_level("Software Engineer II - C++")["level"], MID)
        self.assertEqual(detect_level("Software Engineer III")["level"], SENIOR)

    def test_junior_title(self) -> None:
        self.assertEqual(detect_level("Junior Software Developer")["level"], JUNIOR)


class UnknownTest(unittest.TestCase):
    def test_no_signal_is_unknown(self) -> None:
        info = detect_level("Software Engineer", "We build developer tools.")
        self.assertEqual(info["level"], UNKNOWN)
        self.assertIsNone(info["years_required"])

    def test_company_age_is_not_a_requirement(self) -> None:
        self.assertIsNone(years_required("Software Engineer", "We have built products for 10 years."))


if __name__ == "__main__":
    unittest.main()
