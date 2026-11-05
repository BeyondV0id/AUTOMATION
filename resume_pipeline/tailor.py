from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import DATA, RESUMES
from .jobfile import load as load_job
from .latex import render
from .profile import load_profile


def choose_variant(job: dict) -> str:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    if any(word in text for word in ("machine learning", "ml engineer", "artificial intelligence", "ai engineer", "deep learning")):
        return "ai-ml"
    if any(word in text for word in ("backend", "api", "server", "platform")):
        return "backend"
    return "software-engineer"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a job-specific LaTeX source from verified profile data")
    parser.add_argument("--profile", default="data/resume/profile.json")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job", type=Path, help="JSON job record")
    source.add_argument("--job-id", help="Job id in data/applications.db")
    parser.add_argument("--db", type=Path, help="Job database (default: data/applications.db)")
    parser.add_argument("--variant", choices=["auto", "master", "backend", "software-engineer", "ai-ml"], default="auto")
    args = parser.parse_args()
    profile = load_profile(args.profile)
    job = load_job(args.job, args.job_id, args.db)
    variant = choose_variant(job) if args.variant == "auto" else args.variant
    raw_id = str(job.get("id") or f"{job.get('company', 'job')}-{job.get('title', 'role')}")
    job_id = re.sub(r"[^a-z0-9-]+", "-", raw_id.lower()).strip("-")[:80]
    destination = RESUMES / "generated" / "job-specific" / job_id
    path = render(profile, variant, destination, job=job)
    record = {**job, "selected_variant": variant, "resume_source": str(path), "status": "draft"}
    (DATA / "jobs").mkdir(parents=True, exist_ok=True)
    (DATA / "jobs" / f"{job_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
