"""Offline tests for contact collection, email rendering, and draft storage."""

from __future__ import annotations

import sqlite3

import pytest

from resume_pipeline.outreach import collect, store
from resume_pipeline.outreach.compose import (
    OutreachConfig,
    compose,
    contact_for,
    extract_emails,
    validate,
)


def cfg(**overrides) -> OutreachConfig:
    values = dict(
        sender_name="Alex Doe",
        sender_email="alex@example.com",
        headline="a backend and ML engineer",
        phone="+1 555 0100",
        location="Hyderabad, India",
        linkedin="https://linkedin.com/in/alex",
        github="https://github.com/alex",
        skills=["Python", "Django", "PostgreSQL"],
        summary="I build backend services in Python",
        availability="I can start within 30 days",
    )
    values.update(overrides)
    return OutreachConfig(**values)


def job(**overrides) -> dict:
    values = {
        "id": "test:1",
        "company": "Acme Corp",
        "title": "Backend Engineer",
        "url": "https://acme.com/jobs/1",
        "description": "",
        "skills": '["Python", "Go", "PostgreSQL"]',
    }
    values.update(overrides)
    return values


# --- address extraction -------------------------------------------------


def test_extract_emails_drops_dead_addresses():
    text = "Write to careers@acme.com, not no-reply@acme.com or privacy@acme.com"
    assert extract_emails(text) == ["careers@acme.com"]


def test_rank_prefers_a_hiring_address_over_support():
    ranked = collect.rank(["support@acme.com", "careers@acme.com", "billing@acme.com"])
    assert ranked[0] == "careers@acme.com"


def test_emails_from_html_reads_mailto_links():
    html = '<a href="mailto:hr@acme.com?subject=Job">Mail us</a>'
    assert collect.emails_from_html(html) == ["hr@acme.com"]


def test_emails_from_html_ignores_ats_domains_when_collecting_pages():
    pages = collect._pages_for(job(url="https://boards.greenhouse.io/acme/jobs/1"))
    assert len(pages) == 1  # job page only, no /contact guessing on an ATS host


# --- contact resolution -------------------------------------------------


def test_contact_prefers_stored_column():
    address, reason = contact_for(job(contact_email="hr@acme.com"))
    assert address == "hr@acme.com"
    assert reason == "contact_email column"


def test_contact_falls_back_to_the_posting():
    address, reason = contact_for(job(description="Send your CV to jobs@acme.com"))
    assert address == "jobs@acme.com"
    assert reason == "found in posting"


def test_contact_uses_contacts_file_by_company():
    contacts = {"by_company": {"Acme Corp": "careers@acme.com"}}
    address, reason = contact_for(job(), contacts)
    assert address == "careers@acme.com"
    assert "by_company" in reason


def test_contact_is_never_guessed():
    address, reason = contact_for(job(url="https://unknown.example/jobs/1"))
    assert address is None
    assert reason == "no contact address"


# --- rendering ----------------------------------------------------------


def test_cold_email_renders_without_placeholders():
    subject, body = compose(job(), cfg(), "careers@acme.com", "cold")
    assert "Backend Engineer" in subject
    assert "Acme Corp" in body
    assert "{" not in body and "}" not in body


def test_application_email_mentions_matched_skills_only():
    _, body = compose(job(), cfg(), "hr@acme.com", "application")
    assert "Python" in body and "PostgreSQL" in body
    assert "Go" not in body  # in the posting, not in the profile


def test_empty_optional_lines_are_dropped():
    _, body = compose(job(), cfg(summary="", availability="", location=""), "hr@acme.com", "cold")
    assert "\n\n\n" not in body


def test_validate_rejects_a_missing_sender():
    with pytest.raises(ValueError):
        compose(job(), cfg(sender_name=""), "hr@acme.com", "cold")


def test_validate_rejects_a_short_body():
    with pytest.raises(ValueError):
        validate("hi", "too short", {"sender_name": "x", "sender_email": "y", "company": "z", "title": "t"})


# --- storage ------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "applications.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, "
        "location TEXT, url TEXT, description TEXT, discovered_at TEXT, status TEXT, "
        "match_score REAL, match_reason TEXT, missing_skills TEXT, posted_at TEXT, skills TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (id, company, title, url, description, status) "
        "VALUES ('test:1', 'Acme Corp', 'Backend Engineer', 'https://acme.com/jobs/1', "
        "'Send your CV to jobs@acme.com', 'new')"
    )
    conn.commit()
    conn.close()
    return path


def test_draft_is_not_overwritten_without_force(db):
    first = store.save_draft(job_id="test:1", kind="cold", to_addr="a@x.com",
                             subject="s", body="b", path=db)
    second = store.save_draft(job_id="test:1", kind="cold", to_addr="a@x.com",
                              subject="s2", body="b2", path=db)
    assert first is True
    assert second is False
    assert store.existing("test:1", "cold", db)["subject"] == "s"


def test_sent_draft_cannot_be_reopened(db):
    store.save_draft(job_id="test:1", kind="cold", to_addr="a@x.com",
                     subject="s", body="b", path=db)
    store.mark_sent("test:1", "cold", "<id@x>", db)
    assert store.save_draft(job_id="test:1", kind="cold", to_addr="a@x.com",
                            subject="s", body="b", force=True, path=db) is False
    assert store.existing("test:1", "cold", db)["status"] == "sent"


def test_collect_stores_the_address_and_updates_the_job(db):
    summary = collect.collect(
        [job(description="Send your CV to jobs@acme.com")], db_path=db
    )
    assert summary["from_description"] == 1
    with sqlite3.connect(db) as conn:
        email = conn.execute("SELECT contact_email FROM jobs WHERE id = 'test:1'").fetchone()[0]
        contact = conn.execute("SELECT email, source FROM contacts").fetchone()
    assert email == "jobs@acme.com"
    assert contact == ("jobs@acme.com", "description")
