from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from . import DATA


def extract_text(source: Path) -> str:
    suffix = source.suffix.lower()
    if suffix in {".txt", ".md", ".tex"}:
        return source.read_text(encoding="utf-8", errors="replace")
    if suffix == ".docx":
        with zipfile.ZipFile(source) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        return "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise RuntimeError("PDF extraction needs pypdf or pdftotext. Install/approve a local extractor, then rerun.") from exc
        reader = PdfReader(str(source))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    raise ValueError(f"Unsupported resume type: {source.suffix}")


def make_draft(text: str) -> dict:
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)))
    urls = sorted(set(re.findall(r"https?://[^\s)]+", text)))
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return {
        "identity": {"name": first_line, "headline": "", "email": emails[0] if emails else "", "phone": "", "location": ""},
        "links": {"linkedin": next((u for u in urls if "linkedin.com" in u), ""), "github": next((u for u in urls if "github.com" in u), ""), "portfolio": ""},
        "summary": "",
        "skills": {"languages": [], "backend": [], "frontend": [], "data_ml": [], "cloud_devops": [], "databases": [], "tools": []},
        "experience": [], "projects": [], "education": [], "certifications": [], "preferences": {"roles": [], "locations": [], "remote": True, "work_authorization": ""},
        "source_notes": "Draft only. Review every field against raw_text.txt before saving as profile.json.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy and extract a resume without inventing structured facts")
    parser.add_argument("--resume", required=True, type=Path)
    args = parser.parse_args()
    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")
    target = DATA / "resume"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.resume, target / args.resume.name)
    text = extract_text(args.resume)
    (target / "raw_text.txt").write_text(text, encoding="utf-8")
    (target / "profile.draft.json").write_text(json.dumps(make_draft(text), indent=2), encoding="utf-8")
    print(f"Wrote {target / 'raw_text.txt'} and {target / 'profile.draft.json'}")


if __name__ == "__main__":
    main()
