from __future__ import annotations

import unittest

from resume_pipeline.source_hn import clean_text, parse_comment

PIPES = "<p>Acme Robotics | Backend Engineer | Remote (worldwide) | https://acme.dev/jobs</p><p>Python, Postgres, 0-2 years.</p>"
SENTENCE = "<p>Acme is hiring a founding full-stack engineer in Bengaluru.</p><p>Node.js and React.</p>"
FIELDS = "<p>**Company:** Acme</p><p>**Role:** Software Engineer Intern</p><p>**Location:** Remote</p>"


class CleanTextTests(unittest.TestCase):
    def test_tags_become_lines(self) -> None:
        self.assertEqual(clean_text("<p>one</p><p>two</p>"), "one\ntwo")

    def test_entities_are_decoded(self) -> None:
        self.assertIn("R&D", clean_text("R&amp;D team"))


class ParseTests(unittest.TestCase):
    def test_pipe_format(self) -> None:
        job = parse_comment(PIPES)
        self.assertEqual(job["company"], "Acme Robotics")
        self.assertEqual(job["title"], "Backend Engineer")
        self.assertEqual(job["location"], "Remote (worldwide)")
        self.assertEqual(job["url"], "https://acme.dev/jobs")

    def test_sentence_format(self) -> None:
        job = parse_comment(SENTENCE)
        self.assertEqual(job["company"], "Acme")
        self.assertIn("engineer", job["title"].lower())
        self.assertIn("Bengaluru", job["location"])

    def test_field_format(self) -> None:
        job = parse_comment(FIELDS)
        self.assertTrue(job)
        self.assertIn("Intern", job["title"])

    def test_discussion_is_skipped(self) -> None:
        self.assertIsNone(parse_comment("<p>Does anyone know if this thread is moderated?</p>"))

    def test_empty_comment_is_skipped(self) -> None:
        self.assertIsNone(parse_comment(""))


if __name__ == "__main__":
    unittest.main()
