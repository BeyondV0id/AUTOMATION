"""Score a posting's keywords the way an ATS reads a resume.

An ATS does not understand that "Postgres" answers "PostgreSQL", and it cannot
see a skill that only exists in your profile. This module separates the four
states that matter when tailoring:

``covered``
    The resume text contains the posting's exact term.
``synonym-only``
    The resume uses an accepted alias (``JS`` for ``JavaScript``). A human
    reads it as covered; a strict keyword matcher may not.
``missing (have it)``
    The verified profile supports the term but the resume does not name it.
    This is the only status that should change the resume.
``missing (gap)``
    Neither the profile nor the resume supports the term. It stays missing and
    is reported as a gap; stuffing it in would be a fabrication.

Only terms that appear in the posting are scored. The profile is the source of
truth, so a gap can never be closed by editing the resume alone.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .pdfcheck import PdfCheckError, extract_text
from .posting import plain_text
from .tailor_variants import STOPWORDS

COVERED = "covered"
SYNONYM = "synonym-only"
MISSING_HAVE = "missing (have it)"
MISSING_GAP = "missing (gap)"

STATUSES = (COVERED, SYNONYM, MISSING_HAVE, MISSING_GAP)

# Tight alias groups only. A loose synonym ("docker" ~ "containers") would let
# the report claim coverage the profile cannot defend in an interview.
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("javascript", "js", "ecmascript"),
    ("typescript", "ts"),
    ("postgresql", "postgres", "psql"),
    ("kubernetes", "k8s"),
    ("node.js", "nodejs", "node"),
    ("react", "react.js", "reactjs"),
    ("vue", "vue.js", "vuejs"),
    ("angular", "angularjs"),
    ("next.js", "nextjs"),
    ("golang",),
    ("c#", "csharp"),
    ("c++", "cpp"),
    (".net", "dotnet", "asp.net"),
    ("machine learning", "ml"),
    ("artificial intelligence", "ai"),
    ("deep learning", "dl"),
    ("natural language processing", "nlp"),
    ("large language model", "large language models", "llm", "llms"),
    ("generative ai", "genai", "gen ai"),
    ("continuous integration", "ci"),
    ("continuous delivery", "cd", "continuous deployment"),
    ("ci/cd", "cicd", "ci-cd"),
    ("rest api", "rest apis", "restful"),
    ("bigquery", "big query"),
    ("power bi", "powerbi"),
    ("scikit-learn", "sklearn"),
    ("github actions", "gh actions"),
    ("gitlab ci", "gitlab-ci"),
    ("amazon web services", "aws"),
    ("google cloud platform", "gcp", "google cloud"),
    ("microsoft azure", "azure"),
)

VOCABULARY: tuple[str, ...] = (
    # Languages
    "python", "java", "javascript", "typescript", "golang", "rust", "c++", "c#",
    "ruby", "php", "scala", "kotlin", "swift", "sql", "bash", "matlab", "r",
    # Web and APIs
    "react", "vue", "angular", "next.js", "svelte", "node.js", "express",
    "django", "flask", "fastapi", "spring", "rails", "laravel", "graphql",
    "rest api", "html", "css", "microservices", "api",
    "backend", "frontend", "full-stack", "devops", "site reliability",
    "data engineering", "platform", "streaming", "data streaming",
    "event streaming", "event sourcing", "message queue", "message broker",
    "distributed tracing", "scalability", "reliability", "performance",
    "security", "testing", "debugging", "code review", "mentoring",
    "leadership", "stakeholder management", "communication", "collaboration",
    "problem solving", "architecture", "design patterns", "oop", "tdd", "bdd",
    "code quality", "root cause analysis",
    # Data
    "postgresql", "mysql", "sqlite", "mongodb", "redis", "elasticsearch",
    "cassandra", "dynamodb", "snowflake", "bigquery", "redshift", "spark",
    "hadoop", "airflow", "dbt", "kafka", "rabbitmq", "etl", "data pipeline",
    "data warehouse", "data modeling",
    # Cloud and operations
    "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "ansible",
    "jenkins", "github actions", "gitlab ci", "ci/cd", "linux", "nginx",
    "observability", "monitoring", "serverless", "helm", "prometheus",
    "grafana", "datadog", "opentelemetry", "ec2", "s3", "lambda",
    # ML
    "machine learning", "deep learning", "pytorch", "tensorflow",
    "scikit-learn", "pandas", "numpy", "nlp", "computer vision",
    "large language model", "generative ai", "mlops", "hugging face",
    "transformers", "recommendation system", "recommendation", "ranking",
    "embeddings", "vector database", "rag", "fine-tuning", "feature engineering",
    "data lake", "data mesh", "feature store", "data quality", "analytics",
    # Practices and tools
    "unit testing", "test automation", "agile", "scrum", "system design",
    "distributed systems", "git", "jira", "figma", "tableau", "power bi",
)

# Words that describe the posting rather than the job. They are dropped from
# dynamic keyword extraction even when they repeat.
JD_FILLER = {
    "about", "across", "ability", "apply", "applicant", "benefit", "benefits",
    "best", "build", "candidate", "candidates", "career", "challenge",
    "company", "compensation", "culture", "customer", "customers", "degree",
    "description", "design", "develop", "development", "duties", "employee",
    "employer", "engineer", "engineering", "ensure", "environment", "equal",
    "excellent", "experience", "experiences", "familiarity", "fast", "global",
    "join", "job", "just", "knowledge", "live", "looking", "minimum", "not",
    "opportunity", "part", "passion", "please", "position", "preferred",
    "process", "product", "proven", "qualification", "qualifications",
    "required", "requirement", "requirements", "responsibilities",
    "responsibility", "role", "salary", "seeking", "skill", "skills", "strong",
    "successful", "support", "team", "teams", "technology", "understanding",
    "well", "work", "working", "world", "year", "years", "you", "your",
}
KEYWORD_STOPWORDS = STOPWORDS | JD_FILLER

BIGRAM = re.compile(r"(?<![a-z0-9])([a-z][a-z0-9+#.-]*) ([a-z][a-z0-9+#.-]*)(?![a-z0-9])")

ALIASES: dict[str, tuple[str, ...]] = {}
for group in SYNONYM_GROUPS:
    for alias in group:
        ALIASES[alias] = group

VOCAB_SET = set(VOCABULARY)


def _normalized(text: str) -> str:
    return " ".join((text or "").lower().split())


def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")


def contains(text: str, term: str) -> bool:
    """Whole-term match, so ``java`` does not match ``javascript``."""
    return bool(_pattern(term).search(_normalized(text)))


def count(text: str, term: str) -> int:
    return len(_pattern(term).findall(_normalized(text)))


def flatten(value: Any) -> str:
    """Join every string in a nested profile into one searchable document."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(flatten(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(flatten(item) for item in value)
    return ""


def profile_text(profile: dict[str, Any]) -> str:
    return flatten(profile)


def overlay_text(overlay: dict[str, Any]) -> str:
    """Text a rendered overlay contributes, without reading the LaTeX source."""
    parts: list[str] = [str(overlay.get("headline") or ""), str(overlay.get("summary") or "")]
    for group in (overlay.get("skills_order") or {}).values():
        if isinstance(group, list):
            parts.extend(str(item) for item in group)
    for section in ("experience", "projects"):
        for record in overlay.get(section) or []:
            if not isinstance(record, dict):
                continue
            parts.extend(str(record.get(key) or "") for key in ("company", "title", "name"))
            parts.extend(str(bullet) for bullet in record.get("bullets") or [])
    return " ".join(parts)


def _dynamic_terms(text: str) -> dict[str, int]:
    """Repeated bigrams that the vocabulary does not name.

    Single repeated words are deliberately ignored: postings repeat generic
    nouns ("team", "process", "product") far more often than they repeat a
    skill, so unigrams produce noise that drowns the real keywords.
    """
    counts: dict[str, int] = {}
    for left, right in BIGRAM.findall(text):
        left, right = left.rstrip(".,"), right.rstrip(".,")
        if len(left) < 3 or len(right) < 3:
            continue
        if left in KEYWORD_STOPWORDS or right in KEYWORD_STOPWORDS:
            continue
        phrase = f"{left} {right}"
        if phrase in ALIASES or phrase in VOCAB_SET:
            continue
        counts[phrase] = counts.get(phrase, 0) + 1
    return {term: total for term, total in counts.items() if total >= 2 and len(term) > 3}


def candidate_terms(job_text: str, title: str = "", limit: int = 24) -> list[tuple[str, int]]:
    """Return ``(canonical term, weighted count)`` for a posting."""
    text = _normalized(plain_text(job_text))
    title_text = _normalized(plain_text(title))
    counts: dict[str, int] = {}

    for canonical in VOCABULARY:
        aliases = ALIASES.get(canonical, (canonical,))
        total = sum(count(text, alias) for alias in aliases)
        if not total and title_text:
            total = sum(count(title_text, alias) for alias in aliases)
        if total:
            counts[canonical] = total

    for term, total in _dynamic_terms(text).items():
        counts.setdefault(term, total)

    weighted = {
        term: total + (3 if title_text and contains(title_text, term) else 0)
        for term, total in counts.items()
    }
    ordered = sorted(weighted.items(), key=lambda pair: (-pair[1], pair[0]))
    return ordered[:limit]


def _status(term: str, profile: str, resume: str) -> tuple[str, bool, bool]:
    aliases = ALIASES.get(term, (term,))
    resume_exact = contains(resume, term)
    resume_alias = any(contains(resume, alias) for alias in aliases)
    profile_any = any(contains(profile, alias) for alias in aliases)
    if resume_exact:
        return COVERED, profile_any, True
    if resume_alias:
        return SYNONYM, profile_any, True
    if profile_any:
        return MISSING_HAVE, True, False
    return MISSING_GAP, False, False


def score_keywords(
    job_text: str,
    profile: str | dict[str, Any],
    resume: str,
    title: str = "",
    limit: int = 24,
) -> dict[str, Any]:
    """Score posting keywords against the profile and the resume text."""
    if isinstance(profile, dict):
        profile = profile_text(profile)
    terms = candidate_terms(job_text, title, limit)

    keywords: list[dict[str, Any]] = []
    for term, weight in terms:
        status, supported, visible = _status(term, profile, resume)
        keywords.append(
            {
                "keyword": term,
                "status": status,
                "weight": weight,
                "supported_by_profile": supported,
                "visible_in_resume": visible,
            }
        )

    counts = {status: 0 for status in STATUSES}
    for item in keywords:
        counts[item["status"]] += 1
    total = len(keywords)

    return {
        "keywords": keywords,
        "counts": counts,
        "total": total,
        "exact_ratio": round(counts[COVERED] / total, 3) if total else 0.0,
        "covered_ratio": round((counts[COVERED] + counts[SYNONYM]) / total, 3) if total else 0.0,
        "actionable": [item["keyword"] for item in keywords if item["status"] == MISSING_HAVE],
        "gaps": [item["keyword"] for item in keywords if item["status"] == MISSING_GAP],
    }


def report_markdown(result: dict[str, Any], job: dict[str, Any] | None = None) -> str:
    lines: list[str] = ["# ATS keyword report", ""]
    if job:
        lines += [f"**Role:** {job.get('title', '?')} at {job.get('company', '?')}", ""]
    lines += [
        f"- Exact coverage: {result['exact_ratio'] * 100:.0f}% "
        f"({result['counts'][COVERED]}/{result['total']})",
        f"- Covered or synonym: {result['covered_ratio'] * 100:.0f}% "
        f"({result['counts'][COVERED] + result['counts'][SYNONYM]}/{result['total']})",
        "",
        "| Keyword | Status | Weight | In profile |",
        "| --- | --- | --- | --- |",
    ]
    for item in result["keywords"]:
        lines.append(
            f"| {item['keyword']} | {item['status']} | {item['weight']} | "
            f"{'yes' if item['supported_by_profile'] else 'no'} |"
        )
    lines += [""]
    headline_only = [
        item["keyword"]
        for item in result["keywords"]
        if item["visible_in_resume"] and not item["supported_by_profile"]
    ]
    if headline_only:
        lines += [
            "## Visible but not profile-backed",
            "",
            ", ".join(headline_only),
            "",
            "These words are in the resume only because the headline mirrors the job",
            "title. They are not evidence of experience and should not be presented as",
            "such in an interview.",
            "",
        ]
    if result["actionable"]:
        lines += [
            "## Add these (the profile supports them)",
            "",
            ", ".join(result["actionable"]),
            "",
        ]
    if result["gaps"]:
        lines += [
            "## Gaps (do not add)",
            "",
            ", ".join(result["gaps"]),
            "",
            "These are honest gaps. Leave them out of the resume; mention them in an",
            "interview or cover letter only if you can defend them.",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    from .jobfile import load as load_job
    from .profile import load_profile

    parser = argparse.ArgumentParser(description="Score a posting's keywords against a resume")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job", type=Path, help="JSON job record")
    source.add_argument("--job-id", help="Job id in data/applications.db")
    parser.add_argument("--db", type=Path, help="Job database (default: data/applications.db)")
    parser.add_argument("--profile", default="data/resume/profile.json", type=Path)
    text = parser.add_mutually_exclusive_group(required=True)
    text.add_argument("--pdf", type=Path, help="compiled resume PDF")
    text.add_argument("--resume-text", type=Path, help="extracted resume text file")
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path, help="write the Markdown report here")
    args = parser.parse_args()

    profile = load_profile(args.profile)
    job = load_job(args.job, args.job_id, args.db)
    if args.pdf:
        try:
            resume, _pages, _extractor = extract_text(args.pdf)
        except PdfCheckError as exc:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(2)
    else:
        resume = args.resume_text.read_text(encoding="utf-8")

    result = score_keywords(
        f"{job.get('title', '')} {job.get('description', '')}",
        profile,
        resume,
        title=str(job.get("title", "")),
        limit=args.limit,
    )
    result["job"] = {"id": job.get("id"), "company": job.get("company"), "title": job.get("title")}

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        markdown = report_markdown(result, job)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(markdown, encoding="utf-8")
            print(f"Report: {args.out}")
        else:
            print(markdown)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
