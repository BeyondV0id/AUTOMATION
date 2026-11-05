"""Match an email to one tracked job using the company and title.

Deliberately conservative: if two jobs look equally likely, the email is left
unmatched and shows up in the audit table for you to review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "plc", "gmbh", "ag", "sa", "sas", "bv", "nv", "pvt", "private", "technologies", "technology",
    "tech", "labs", "lab", "solutions", "systems", "software", "services", "group", "global",
    "holdings", "ventures", "the",
}

STOPWORDS = {
    "and", "for", "with", "from", "your", "you", "our", "the", "this", "that", "have", "has",
    "will", "would", "are", "was", "were", "been", "being", "about", "into", "over", "under",
}

GENERIC_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
    "protonmail.com", "proton.me", "aol.com", "mail.com", "live.com", "msn.com",
}


def normalize_company(name: str) -> str:
    text = (name or "").lower()
    text = re.sub(r"[\u00ae\u2122]", "", text)
    text = re.sub(r"[^a-z0-9& ]+", " ", text)
    tokens = [t for t in text.split() if t and t not in LEGAL_SUFFIXES]
    return " ".join(tokens).strip()


def company_tokens(name: str) -> list[str]:
    return [t for t in normalize_company(name).split() if len(t) >= 3 and t not in STOPWORDS]


def sender_domain(sender: str) -> str:
    match = re.search(r"@([a-z0-9.-]+\.[a-z]{2,})", (sender or "").lower())
    if not match:
        return ""
    domain = match.group(1).strip(".")
    if domain in GENERIC_DOMAINS:
        return ""
    return domain


@dataclass
class JobMatch:
    job: dict | None
    score: float = 0.0
    reason: str = ""


def score_job(text: str, subject: str, sender: str, job: dict) -> tuple[float, list[str]]:
    company = normalize_company(job.get("company", ""))
    tokens = company_tokens(job.get("company", ""))
    if not company and not tokens:
        return 0.0, []

    reasons: list[str] = []
    score = 0.0

    if company and re.search(rf"(?<![a-z0-9]){re.escape(company)}(?![a-z0-9])", text):
        score += 0.45
        reasons.append("company name")

    if company and company in subject.lower():
        score += 0.1
        reasons.append("company in subject")

    domain = sender_domain(sender)
    if domain:
        base = domain.split(".")[0]
        if company and (base in company or company.split()[0] in base):
            score += 0.45
            reasons.append("sender domain")
        elif tokens and any(t in base for t in tokens):
            score += 0.25
            reasons.append("sender domain token")

    if tokens:
        present = sum(1 for t in tokens if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", text))
        coverage = present / len(tokens)
        if coverage:
            score += 0.3 * coverage
            reasons.append(f"company tokens {present}/{len(tokens)}")

    title_tokens = [
        t for t in re.findall(r"[a-z0-9]+", (job.get("title") or "").lower())
        if len(t) >= 4 and t not in STOPWORDS
    ]
    if title_tokens:
        in_subject = sum(1 for t in title_tokens[:6] if t in subject.lower())
        if in_subject:
            score += min(0.2, 0.05 * in_subject)
            reasons.append(f"title words {in_subject}")

    return round(min(score, 1.0), 2), reasons


def best_job(subject: str, body: str, sender: str, jobs: list[dict], min_score: float = 0.45) -> JobMatch:
    text = "\n".join([sender or "", subject or "", (body or "")[:3000]]).lower()
    best: JobMatch = JobMatch(None)
    for job in jobs:
        score, reasons = score_job(text, subject or "", sender or "", job)
        if score > best.score:
            best = JobMatch(job, score, ", ".join(reasons))
    if best.job is not None and best.score < min_score:
        return JobMatch(None, best.score, best.reason)
    return best
