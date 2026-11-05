"""Render verified profile data into Jake's Resume layout.

The generator writes a self-contained folder: ``resume.tex`` carries the
``\\documentclass`` line, inputs a copy of ``resumes/jakes-resume.tex`` sitting
beside it, and never reaches outside its own directory. The same folder
compiles locally, in Overleaf, or through LaTeX.to.

Only reordering, reselection and escaping happen here. No fact is invented;
overlays are checked by ``verify.py`` before they reach this module.
"""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from . import RESUMES
from .profile import record_matches

TEMPLATE = RESUMES / "jakes-resume.tex"

VARIANTS = {
    "master": {"backend", "software-engineer", "ai-ml", "data", "full"},
    "backend": {"backend", "software-engineer"},
    "software-engineer": {"software-engineer", "backend", "frontend"},
    "ai-ml": {"ai-ml", "machine-learning", "data", "software-engineer"},
}

# Jake's canonical order. Reorder this tuple to change the resume, for example
# ("experience", "education", "projects", "skills") for an experience-first page.
SECTION_ORDER = ("education", "experience", "projects", "skills", "certifications")

SKILL_LABELS = {
    "languages": "Languages",
    "backend": "Backend",
    "frontend": "Frontend",
    "data_ml": "AI / ML",
    "cloud_devops": "Cloud / DevOps",
    "databases": "Databases",
    "tools": "Tools",
}


def tex(value: Any) -> str:
    text = "" if value is None else str(value)
    replacements = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def url(value: str) -> str:
    return value.replace("%", r"\%").replace("#", r"\#")


def _display(value: str) -> str:
    return re.sub(r"^https?://", "", value.strip()).rstrip("/")


def _period(item: dict[str, Any], default_end: str = "") -> str:
    start = str(item.get("start") or "")
    end = str(item.get("end") or "")
    if not end and start and default_end:
        end = default_end
    if start and end:
        return f"{tex(start)} - {tex(end)}"
    return tex(start or end)


def _bullets(items: list[Any]) -> list[str]:
    if isinstance(items, str):
        items = [items]
    return [rf"        \resumeItem{{{tex(item)}}}" for item in items if str(item).strip()]


def _href(label: str, value: str, show: str | None = None) -> str:
    return rf"\href{{{url(value)}}}{{\underline{{{tex(show or label)}}}}}"


def _ordered_skills(skills: dict[str, Any], order: dict[str, Any] | None) -> dict[str, list[str]]:
    """Keep every skill, move the ones the overlay names to the front."""
    ordered: dict[str, list[str]] = {}
    for key, values in skills.items():
        if not isinstance(values, list):
            continue
        original = [str(value) for value in values]
        preferred: list[str] = []
        if isinstance((order or {}).get(key), list):
            preferred = [str(value) for value in order[key] if str(value) in original]
        ordered[key] = preferred + [value for value in original if value not in preferred]
    return ordered


def _overlay_records(
    records: list[dict[str, Any]], overrides: list[dict[str, Any]] | None, kind: str
) -> tuple[list[dict[str, Any]], dict[int, list[str]]]:
    """Apply overlay order and bullet rewrites; unmatched records keep their place at the end."""
    if not overrides:
        return records, {}
    keyed: dict[str, dict[str, Any]] = {}
    for record in records:
        if kind == "experience":
            key = f"{record.get('company', '')}|{record.get('title', '')}".lower()
        else:
            key = str(record.get("name", "")).lower()
        keyed[key] = record

    ordered: list[dict[str, Any]] = []
    bullets: dict[int, list[str]] = {}
    for entry in overrides:
        if kind == "experience":
            key = f"{entry.get('company', '')}|{entry.get('title', '')}".lower()
        else:
            key = str(entry.get("name", "")).lower()
        record = keyed.get(key)
        if record is None:
            continue
        ordered.append(record)
        if isinstance(entry.get("bullets"), list):
            bullets[id(record)] = [str(item) for item in entry["bullets"]]
    ordered += [record for record in records if record not in ordered]
    return ordered, bullets


def _header(profile: dict[str, Any], title: str) -> list[str]:
    identity = profile["identity"]
    links = profile.get("links", {})
    contact: list[str] = []
    if identity.get("location"):
        contact.append(tex(identity["location"]))
    if identity.get("phone"):
        contact.append(tex(identity["phone"]))
    if identity.get("email"):
        contact.append(_href(identity["email"], f"mailto:{identity['email']}"))
    for key in ("linkedin", "github", "portfolio"):
        value = str(links.get(key) or "").strip()
        if value:
            contact.append(_href(value, value, _display(value)))

    lines = [r"\begin{center}", rf"    \textbf{{\Huge \scshape {tex(identity.get('name', ''))}}} \\ \vspace{{1pt}}"]
    if title:
        lines.append(rf"    \small {tex(title)} \\ \vspace{{1pt}}")
    if contact:
        lines.append("    \\small " + " $|$ ".join(contact))
    lines.append(r"\end{center}")
    return lines


def _summary_block(summary: str) -> list[str]:
    if not summary:
        return []
    return ["", r"\section{Summary}", rf"  \small {tex(summary)}"]


def _education_block(education: list[dict[str, Any]]) -> list[str]:
    if not education:
        return []
    lines = ["", r"\section{Education}", r"  \resumeSubHeadingListStart"]
    for item in education:
        lines += [
            "",
            r"    \resumeSubheading",
            rf"      {{{tex(item.get('institution', ''))}}}{{{tex(item.get('location', ''))}}}",
            rf"      {{{tex(item.get('degree', item.get('program', '')))}}}{{{_period(item)}}}",
        ]
        details = item.get("details")
        detail_items = details if isinstance(details, list) else ([details] if details else [])
        bullets = _bullets(detail_items)
        if bullets:
            lines += [r"      \resumeItemListStart", *bullets, r"      \resumeItemListEnd"]
    lines += ["", r"  \resumeSubHeadingListEnd"]
    return lines


def _experience_block(
    experience: list[dict[str, Any]], overrides: dict[int, list[str]]
) -> list[str]:
    if not experience:
        return []
    lines = ["", r"\section{Experience}", r"  \resumeSubHeadingListStart"]
    for item in experience:
        lines += [
            "",
            r"    \resumeSubheading",
            rf"      {{{tex(item.get('company', ''))}}}{{{tex(item.get('location', ''))}}}",
            rf"      {{{tex(item.get('title', ''))}}}{{{_period(item, 'Present')}}}",
        ]
        source = overrides.get(id(item), item.get("bullets", item.get("highlights", [])))
        bullets = _bullets(source or [])
        if bullets:
            lines += [r"      \resumeItemListStart", *bullets, r"      \resumeItemListEnd"]
    lines += ["", r"  \resumeSubHeadingListEnd"]
    return lines


def _projects_block(projects: list[dict[str, Any]], overrides: dict[int, list[str]]) -> list[str]:
    if not projects:
        return []
    lines = ["", r"\section{Projects}", r"  \resumeSubHeadingListStart"]
    for item in projects:
        name = rf"\textbf{{{tex(item.get('name', ''))}}}"
        technology = tex(item.get("technology", item.get("stack", "")))
        if technology:
            name += rf" $|$ \emph{{{technology}}}"
        if item.get("url"):
            name = rf"\href{{{url(item['url'])}}}{{{name}}}"
        lines += [
            "",
            r"    \resumeProjectHeading",
            rf"      {{{name}}}{{{_period(item)}}}",
        ]
        source = overrides.get(id(item), item.get("bullets", item.get("highlights", [])))
        bullets = _bullets(source or [])
        if bullets:
            lines += [r"      \resumeItemListStart", *bullets, r"      \resumeItemListEnd"]
    lines += ["", r"  \resumeSubHeadingListEnd"]
    return lines


def _skills_block(skills: dict[str, list[str]]) -> list[str]:
    entries = [
        rf"     \textbf{{{label}}}{{: {tex(', '.join(map(str, skills[key])))}}}"
        for key, label in SKILL_LABELS.items()
        if skills.get(key)
    ]
    if not entries:
        return []
    return [
        "",
        r"\section{Technical Skills}",
        r" \begin{itemize}[leftmargin=0.15in, label={}]",
        r"    \small{\item{",
        " \\\\\n".join(entries),
        r"    }}",
        r" \end{itemize}",
    ]


def _certifications_block(certifications: list[Any]) -> list[str]:
    bullets = _bullets(certifications)
    if not bullets:
        return []
    return ["", r"\section{Certifications}", r"  \resumeSubHeadingListStart", *bullets, r"  \resumeSubHeadingListEnd"]


def render(
    profile: dict[str, Any],
    variant: str,
    destination: Path,
    job: dict[str, Any] | None = None,
    overlay: dict[str, Any] | None = None,
) -> Path:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant {variant}; choose from {', '.join(VARIANTS)}")
    if not TEMPLATE.exists():
        raise FileNotFoundError(f"Resume preamble not found: {TEMPLATE}")

    destination.mkdir(parents=True, exist_ok=True)
    overlay = overlay or {}
    identity = profile["identity"]
    tags = VARIANTS[variant]
    title = overlay.get("headline") or (job.get("title", identity.get("headline", "")) if job else identity.get("headline", ""))

    experience = [x for x in profile.get("experience", []) if record_matches(x, tags)]
    experience, experience_overrides = _overlay_records(experience, overlay.get("experience"), "experience")
    projects = [x for x in profile.get("projects", []) if record_matches(x, tags)]
    projects, project_overrides = _overlay_records(projects, overlay.get("projects"), "projects")
    skills = _ordered_skills(profile.get("skills", {}), overlay.get("skills_order"))

    blocks = {
        "education": _education_block(profile.get("education", [])),
        "experience": _experience_block(experience, experience_overrides),
        "projects": _projects_block(projects, project_overrides),
        "skills": _skills_block(skills),
        "certifications": _certifications_block(profile.get("certifications", [])),
    }

    lines: list[str] = [
        r"\documentclass[letterpaper,11pt]{article}",
        rf"\input{{{TEMPLATE.name}}}",
        "",
        r"\begin{document}",
        "",
    ]
    lines += _header(profile, title)
    lines += _summary_block(overlay.get("summary", profile.get("summary", "")))
    for key in SECTION_ORDER:
        lines += blocks[key]
    lines += ["", r"\end{document}", ""]

    path = destination / "resume.tex"
    path.write_text("\n".join(lines), encoding="utf-8")
    shutil.copyfile(TEMPLATE, destination / TEMPLATE.name)
    return path


def generate_all(profile: dict[str, Any], variants: list[str] | None = None) -> list[Path]:
    chosen = variants or list(VARIANTS)
    stamp = date.today().isoformat()
    paths = []
    for variant in chosen:
        paths.append(render(profile, variant, RESUMES / "generated" / variant / stamp))
    return paths
