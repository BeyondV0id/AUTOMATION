from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from resume_pipeline.jobfile import from_db, load


class JobFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "jobs.db"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, "
                "location TEXT, url TEXT, description TEXT, discovered_at TEXT, status TEXT, "
                "match_score REAL, match_reason TEXT, missing_skills TEXT, posted_at TEXT, skills TEXT)"
            )
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "linkedin:4471319619",
                    "linkedin",
                    "Acme Labs",
                    "Backend Engineer",
                    "Bengaluru",
                    "https://www.linkedin.com/jobs/view/4471319619",
                    "Python and Postgres services",
                    "2026-11-04T08:00:00+00:00",
                    "new",
                    None,
                    None,
                    None,
                    "2026-10-01",
                    json.dumps(["Python", "Postgres"]),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_from_db_decodes_skills(self) -> None:
        job = from_db("linkedin:4471319619", self.db_path)
        self.assertEqual(job["company"], "Acme Labs")
        self.assertEqual(job["skills"], ["Python", "Postgres"])
        self.assertEqual(job["status"], "new")

    def test_from_db_missing_id(self) -> None:
        with self.assertRaises(KeyError):
            from_db("does-not-exist", self.db_path)

    def test_load_prefers_json_file(self) -> None:
        path = Path(self.directory.name) / "job.json"
        path.write_text(json.dumps({"id": "custom:1", "title": "Platform Engineer"}), encoding="utf-8")
        job = load(path, None, self.db_path)
        self.assertEqual(job["id"], "custom:1")

    def test_load_without_source_fails(self) -> None:
        with self.assertRaises(ValueError):
            load(None, None, self.db_path)

    def test_load_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load(Path(self.directory.name) / "missing.json")


if __name__ == "__main__":
    unittest.main()
