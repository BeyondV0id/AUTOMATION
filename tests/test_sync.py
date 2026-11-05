from __future__ import annotations

import email.policy
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

from resume_pipeline.sync import excel, store
from resume_pipeline.sync.gmail import parse_message
from resume_pipeline.sync.intent import classify, next_status
from resume_pipeline.sync.match_email import best_job, company_tokens, normalize_company, score_job


def raw_message(sender: str, subject: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 02 Nov 2026 10:00:00 +0000"
    msg["Message-ID"] = f"<{abs(hash(subject))}@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


JOB = {
    "id": "greenhouse:123",
    "company": "Stripe",
    "title": "Backend Engineer",
    "status": "applied",
    "url": "https://boards.greenhouse.io/stripe/jobs/123",
}


@contextmanager
def db(path):
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class IntentTests(unittest.TestCase):
    def test_rejection(self) -> None:
        result = classify("Update on your application", "Unfortunately we have decided to move forward with other candidates.")
        self.assertEqual(result.name, "rejected")
        self.assertGreaterEqual(result.confidence, 0.6)

    def test_unable_to_offer_is_rejection(self) -> None:
        result = classify("Your application", "We are unable to offer you a position at this time.")
        self.assertEqual(result.name, "rejected")

    def test_offer(self) -> None:
        result = classify("Your offer", "We are pleased to offer you the Backend Engineer role. Your offer letter is attached.")
        self.assertEqual(result.name, "offer")

    def test_interview(self) -> None:
        result = classify("Next steps", "We would like to invite you to an interview with the hiring manager.")
        self.assertEqual(result.name, "interview")

    def test_application_received(self) -> None:
        result = classify("Thanks", "Thank you for applying. Your application has been received.")
        self.assertEqual(result.name, "applied")

    def test_newsletter_is_unknown(self) -> None:
        result = classify("Weekly digest", "Here are this week's trending articles. Unsubscribe anytime.")
        self.assertFalse(result.known)

    def test_transitions_are_forward_only(self) -> None:
        self.assertEqual(next_status("applied", "interview"), "interview")
        self.assertEqual(next_status("interview", "rejected"), "rejected")
        self.assertEqual(next_status("offer", "rejected"), "offer")
        self.assertEqual(next_status("rejected", "applied"), "rejected")
        self.assertEqual(next_status("new", "applied"), "applied")
        self.assertEqual(next_status("new", "interview"), "interview")


class MatchingTests(unittest.TestCase):
    def test_company_normalization(self) -> None:
        self.assertEqual(normalize_company("Acme Technologies, Inc."), "acme")
        self.assertEqual(company_tokens("Acme Technologies, Inc."), ["acme"])

    def test_domain_match_wins(self) -> None:
        score, reasons = score_job(
            "stripe recruiting stripe backend engineer", "Update", "no-reply@stripe.com", JOB
        )
        self.assertGreaterEqual(score, 0.45)
        self.assertIn("sender domain", reasons)

    def test_best_job_threshold(self) -> None:
        match = best_job("Weekly newsletter", "nothing relevant", "news@example.org", [JOB])
        self.assertIsNone(match.job)

    def test_email_parsing(self) -> None:
        parsed = parse_message(
            raw_message("Stripe <no-reply@stripe.com>", "Your Stripe application", "Unfortunately, not this time.")
        )
        self.assertIn("stripe", parsed["sender"].lower())
        self.assertEqual(parsed["subject"], "Your Stripe application")
        self.assertIn("Unfortunately", parsed["body"])
        self.assertTrue(parsed["message_id"].startswith("<"))


class ExcelRoundTripTests(unittest.TestCase):
    def test_status_and_notes_are_imported(self) -> None:
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "test.db"
            with db(db_path) as conn:
                conn.execute(
                    "CREATE TABLE jobs (id TEXT PRIMARY KEY, company TEXT, title TEXT, status TEXT)"
                )
                conn.execute("INSERT INTO jobs VALUES ('j1', 'Acme', 'Backend Engineer', 'new')")
                conn.execute(
                    "CREATE TABLE applications (job_id TEXT PRIMARY KEY, status TEXT, applied_at TEXT, notes TEXT)"
                )

            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["Company", "Title", "Status", "Notes", "Job ID"])
            sheet.append(["Acme", "Backend Engineer", "interview", "Phone screen booked", "j1"])
            workbook.save(root / "jobs.xlsx")

            result = excel.import_edits(root / "jobs.xlsx", db_path)
            self.assertEqual(result["status_changes"], 1)
            self.assertEqual(result["note_changes"], 1)

            with db(db_path) as conn:
                status = conn.execute("SELECT status FROM jobs WHERE id = 'j1'").fetchone()[0]
                notes = conn.execute("SELECT notes FROM applications WHERE job_id = 'j1'").fetchone()[0]
            self.assertEqual(status, "interview")
            self.assertEqual(notes, "Phone screen booked")

    def test_unknown_status_is_rejected(self) -> None:
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "test.db"
            with db(db_path) as conn:
                conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, company TEXT, title TEXT, status TEXT)")
                conn.execute("INSERT INTO jobs VALUES ('j1', 'Acme', 'Engineer', 'new')")
                conn.execute(
                    "CREATE TABLE applications (job_id TEXT PRIMARY KEY, status TEXT, applied_at TEXT, notes TEXT)"
                )
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["Company", "Title", "Status", "Notes", "Job ID"])
            sheet.append(["Acme", "Engineer", "maybe", "", "j1"])
            workbook.save(root / "jobs.xlsx")

            result = excel.import_edits(root / "jobs.xlsx", db_path)
            self.assertEqual(result["status_changes"], 0)
            self.assertEqual(len(result["invalid"]), 1)


if __name__ == "__main__":
    unittest.main()
