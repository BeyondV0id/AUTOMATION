"""Render an application email from config, the posting, and nothing else.

No model call, no invented achievements. If a field the template needs is
empty, rendering fails loudly instead of sending a half-written message.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from urllib.parse import urlparse

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Addresses that are never a human who can act on an application.
DEAD = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|unsubscribe|postmaster|"
    r"abuse@|privacy@|legal@|example\.(com|org)|sentry\.io|wixpress|@sentry)",
    re.I,
)
ATS_HOSTS = re.compile(
    r"(greenhouse\.io|lever\.co|ashbyhq\.com|workday|smartrecruiters|"
    r"recruitee|teamtailor|bamboohr|jobvite|icims|taleo|linkedin\.com|"
    r"indeed\.com|naukri\.com|internshala\.com|remotive\.com|arbeitnow\.com|"
    r"himalayas\.app|wellfound\.com)",
    re.I,
)

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "application": {
        "subject": "Application for {title} at {company}",
        "body": """Hi {company} team,

I'm {sender_name}, {headline}. I'd like to apply for the {title} role.

{summary_line}
{skills_line}
{availability_line}

My resume is attached. Thanks for your time.

{sender_name}
{contact_line}
{links_line}
""",
    },
    "followup": {
        "subject": "Following up: {title} at {company}",
        "body": """Hi {company} team,

I applied for the {title} role a little while ago and wanted to check whether
the application is still under review. I'm still very interested.

If it helps, I'm happy to walk through my work on {skills_short} whenever suits you.

Thanks,
{sender_name}
{contact_line}
{links_line}
""",
    },
    "cold": {
        "subject": "{sender_name} - {title} at {company}",
        "body": """Hi {company} team,

I'm {sender_name}, {headline}. I saw the {title} opening and wanted to write to
you directly rather than only through the portal.

{summary_line}
{skills_line}
{availability_line}

My resume is attached. If someone else is better placed to look at it, I'd
appreciate a pointer.

Thanks for your time,
{sender_name}
{contact_line}
{links_line}
""",
    },
}

REQUIRED = ("sender_name", "sender_email", "company", "title")
# A message shorter than this is almost certainly a broken template.
MIN_BODY = 80


@dataclass
class OutreachConfig:
    sender_name: str = ""
    sender_email: str = ""
    headline: str = "a software engineer"
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    availability: str = ""
    summary: str = ""
    skills: list[str] = dc_field(default_factory=list)
    templates: dict = dc_field(default_factory=lambda: {k: dict(v) for k, v in DEFAULT_TEMPLATES.items()})
    daily_limit: int = 20
    per_company_limit: int = 1
    delay_seconds: float = 20.0
    attach_resume: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.sender_name and self.sender_email)


def config_from(mapping: dict, env: dict | None = None) -> OutreachConfig:
    import os

    env = env if env is not None else os.environ
    section = mapping.get("outreach") or {}
    identity = section.get("identity") or {}
    limits = section.get("limits") or {}
    templates = {k: dict(v) for k, v in DEFAULT_TEMPLATES.items()}
    for name, template in (section.get("templates") or {}).items():
        templates.setdefault(name, {})
        templates[name].update({k: str(v) for k, v in template.items()})

    return OutreachConfig(
        sender_name=str(identity.get("name") or env.get("JOB_SENDER_NAME", "")),
        sender_email=str(identity.get("email") or env.get("JOB_GMAIL_ADDRESS", "")),
        headline=str(identity.get("headline") or "a software engineer"),
        phone=str(identity.get("phone") or ""),
        location=str(identity.get("location") or ""),
        linkedin=str(identity.get("linkedin") or ""),
        github=str(identity.get("github") or ""),
        portfolio=str(identity.get("portfolio") or ""),
        availability=str(identity.get("availability") or ""),
        summary=str(identity.get("summary") or ""),
        skills=[str(s) for s in (identity.get("skills") or [])],
        templates=templates,
        daily_limit=int(limits.get("daily_limit", 20)),
        per_company_limit=int(limits.get("per_company_limit", 1)),
        delay_seconds=float(limits.get("delay_seconds", 20)),
        attach_resume=bool(limits.get("attach_resume", True)),
    )


def config_from_profile(profile_path: Path | None) -> dict:
    """Best-effort identity from data/resume/profile.json, used as a fallback."""
    if not profile_path or not profile_path.exists():
        return {}
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    identity = data.get("identity") or {}
    links = data.get("links") or {}
    skills: list[str] = []
    for values in (data.get("skills") or {}).values():
        if isinstance(values, list):
            skills.extend(str(v) for v in values)
    return {
        "outreach": {
            "identity": {
                "name": identity.get("name", ""),
                "email": identity.get("email", ""),
                "headline": identity.get("headline", ""),
                "phone": identity.get("phone", ""),
                "location": identity.get("location", ""),
                "linkedin": links.get("linkedin", ""),
                "github": links.get("github", ""),
                "portfolio": links.get("portfolio", ""),
                "summary": data.get("summary", ""),
                "skills": skills[:20],
            }
        }
    }


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def extract_emails(text: str) -> list[str]:
    seen: list[str] = []
    for match in EMAIL.findall(text or ""):
        address = match.strip(".,;:<>()[]").lower()
        if DEAD.search(address):
            continue
        if address not in seen:
            seen.append(address)
    return seen


def _job_skills(job: dict) -> list[str]:
    raw = job.get("skills")
    if isinstance(raw, list):
        return [str(s) for s in raw]
    if isinstance(raw, str) and raw.strip():
        if raw.strip().startswith("["):
            try:
                return [str(s) for s in json.loads(raw)]
            except ValueError:
                pass
        return [s.strip() for s in re.split(r"[,;/|]", raw) if s.strip()]
    return []


def contact_for(job: dict, contacts: dict | None = None) -> tuple[str | None, str]:
    """Return (address, reason). Never guesses an address from a domain pattern."""
    contacts = contacts or {}

    stored = (job.get("contact_email") or "").strip()
    if stored and EMAIL.fullmatch(stored):
        return stored, "contact_email column"

    found = extract_emails(job.get("description") or "")
    if found:
        return found[0], "found in posting"

    by_company = contacts.get("by_company") or {}
    wanted = _norm(job.get("company") or "")
    for name, address in by_company.items():
        if _norm(name) == wanted and EMAIL.fullmatch(str(address).strip()):
            return str(address).strip(), f"contacts.toml by_company:{name}"

    host = (urlparse(job.get("url") or "").netloc or "").lower()
    host = host.split(":")[0].removeprefix("www.")
    if host and not ATS_HOSTS.search(host):
        by_domain = contacts.get("by_domain") or {}
        address = by_domain.get(host)
        if address and EMAIL.fullmatch(str(address).strip()):
            return str(address).strip(), f"contacts.toml by_domain:{host}"

    return None, "no contact address"


def _sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    return text if text[-1] in ".!?" else text + "."


def build_context(job: dict, cfg: OutreachConfig, kind: str = "application") -> dict:
    posting = _job_skills(job)
    owned = {s.lower(): s for s in cfg.skills}
    matched = [owned[s.lower()] for s in posting if s.lower() in owned][:5]
    if not matched:
        matched = cfg.skills[:4]

    contact_line = " | ".join(part for part in (cfg.sender_email, cfg.phone) if part)
    links_line = " | ".join(part for part in (cfg.linkedin, cfg.github, cfg.portfolio) if part)

    summary_line = _sentence(cfg.summary)

    skills_line = ""
    if matched:
        skills_line = (
            f"The posting's stack lines up with what I work in: {', '.join(matched)}."
        )

    availability_line = ""
    if cfg.availability:
        availability_line = _sentence(cfg.availability)
    elif cfg.location:
        availability_line = f"I'm based in {cfg.location}."

    return {
        "sender_name": cfg.sender_name,
        "sender_email": cfg.sender_email,
        "headline": cfg.headline,
        "company": (job.get("company") or "").strip(),
        "title": (job.get("title") or "").strip(),
        "location": cfg.location,
        "job_url": job.get("url") or "",
        "summary_line": summary_line,
        "skills_line": skills_line,
        "skills_short": ", ".join(matched[:3]) or "the stack in the posting",
        "availability_line": availability_line,
        "contact_line": contact_line,
        "links_line": links_line,
    }


def _clean(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


def render(template: dict, context: dict) -> tuple[str, str]:
    subject = template.get("subject") or ""
    body = template.get("body") or ""
    try:
        subject = subject.format_map(context)
        body = body.format_map(context)
    except KeyError as exc:
        raise ValueError(f"template uses unknown placeholder {exc}") from exc
    return _clean(subject), _clean(body)


def validate(subject: str, body: str, context: dict) -> None:
    missing = [key for key in REQUIRED if not str(context.get(key) or "").strip()]
    if missing:
        raise ValueError("missing required values: " + ", ".join(missing))
    if re.search(r"\{[a-z_]+\}", f"{subject}\n{body}"):
        raise ValueError("unresolved placeholder left in the message")
    if not subject.strip():
        raise ValueError("empty subject")
    if len(body.strip()) < MIN_BODY:
        raise ValueError(f"body too short ({len(body.strip())} chars)")
    if len(subject) > 200:
        raise ValueError("subject too long")


def compose(
    job: dict,
    cfg: OutreachConfig,
    to_addr: str,
    kind: str = "application",
) -> tuple[str, str]:
    template = cfg.templates.get(kind) or DEFAULT_TEMPLATES["application"]
    context = build_context(job, cfg, kind)
    subject, body = render(template, context)
    validate(subject, body, context)
    return subject, body
