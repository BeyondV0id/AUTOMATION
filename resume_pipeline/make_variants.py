"""Produce several tailored resume versions for one job posting.

    python -m resume_pipeline.make_variants --job-id "greenhouse:stripe:123"
    python -m resume_pipeline.make_variants --job data/jobs/acme-swe.json
    python -m resume_pipeline.make_variants --job-id "linkedin:4471319619" --compile
    python -m resume_pipeline.make_variants --job-id "linkedin:4471319619" --fit-pages 1 --inspect

Without an overlay directory this builds three deterministic angles. Drop a
model-written ``<strategy>.json`` into ``data/tailoring/<job>/`` (or pass
``--overlays``) to use rewritten text instead; every overlay is fact-checked
against ``profile.json`` before it is rendered.

``--fit-pages`` compiles the PDF and cuts the lowest-value bullet until it fits,
using relevance to the posting rather than document order. ``--inspect`` then
checks the compiled PDF's text layer, contact strings, date ranges and page
layout. ``--ats-report`` writes a keyword coverage report with the posting's
gaps kept visible.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import DATA, REPORTS, RESUMES
from .ats import overlay_text, report_markdown, score_keywords
from .compile import compile_source
from .jobfile import load as load_job
from .latex import render
from .layout import check_layout
from .pdfcheck import PdfCheckError, check_pdf, contact_strings, extract_text
from .posting import guard_posting, plain_text
from .profile import load_profile
from .relevance import lowest_value_bullet, remove_bullet
from .tailor_variants import STRATEGIES, build_overlay, choose_variant, prompt_packet, tokens
from .verify import verify_overlay

# Layout problems caused by an overfull page. More cutting can fix these; an
# early page break or a thin last page means the opposite and should not drive
# another cut.
BLOCKING_LAYOUT = ("bottom margin", "hole", "orphaned", "section heading")


def job_slug(job: dict) -> str:
    raw = str(job.get("id") or f"{job.get('company', 'job')}-{job.get('title', 'role')}")
    return re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")[:80]


def job_tokens(job: dict[str, Any]) -> set[str]:
    return tokens(f"{job.get('title', '')} {plain_text(str(job.get('description', '')))}")


def fit_to_pages(
    profile: dict[str, Any],
    variant: str,
    destination: Path,
    job: dict[str, Any],
    overlay: dict[str, Any],
    prefer: str,
    max_pages: int,
    max_cuts: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], int, Path, dict[str, Any] | None]:
    """Render and compile, cutting the lowest-value bullet until the PDF fits."""
    fitted = copy.deepcopy(overlay)
    wanted = job_tokens(job)
    cuts: list[dict[str, Any]] = []
    attempts = 0

    while True:
        tex_path = render(profile, variant, destination, job=job, overlay=fitted)
        compile_result = compile_source(tex_path, prefer)
        attempts += 1
        if compile_result["status"] != "passed":
            return fitted, cuts, compile_result, attempts, tex_path, None

        try:
            check = check_pdf(compile_result["pdf"])
        except PdfCheckError as error:
            return fitted, cuts, compile_result, attempts, tex_path, {"ok": False, "problems": [str(error)]}

        layout = check_layout(compile_result["pdf"])
        blocking = [
            problem
            for problem in layout.get("problems", [])
            if any(marker in problem for marker in BLOCKING_LAYOUT)
        ]
        check["layout_problems"] = layout.get("problems", [])
        check["blocking_layout"] = blocking
        check["layout_skipped"] = bool(layout.get("skipped"))

        if len(cuts) >= max_cuts:
            return fitted, cuts, compile_result, attempts, tex_path, check
        if check["pages"] <= max_pages and not blocking:
            return fitted, cuts, compile_result, attempts, tex_path, check

        candidate = lowest_value_bullet(fitted, wanted)
        if candidate is None:
            return fitted, cuts, compile_result, attempts, tex_path, check

        removed = remove_bullet(fitted, candidate)
        cuts.append(
            {
                "section": candidate["section"],
                "record_index": candidate["record_index"],
                "bullet_index": candidate["bullet_index"],
                "bullet": removed,
                "score": candidate["score"],
                "pages_before": check["pages"],
            }
        )


def inspect_pdf(
    pdf_path: str | Path,
    profile: dict[str, Any],
    job: dict[str, Any],
    overlay: dict[str, Any],
    ats_report: bool,
    ats_path: Path | None = None,
) -> dict[str, Any]:
    """Check the text layer and layout; optionally score keyword coverage."""
    result: dict[str, Any] = {}
    text = ""
    try:
        text, _pages, extractor = extract_text(pdf_path)
    except PdfCheckError as error:
        result["text"] = {"ok": False, "extractor": None, "problems": [str(error)]}
    else:
        result["text"] = check_pdf(pdf_path, contains=contact_strings(profile))
        result["text"]["extractor"] = extractor

    result["layout"] = check_layout(pdf_path)
    result["ok"] = bool(result["text"].get("ok")) and bool(result["layout"].get("ok", True))
    result["problems"] = list(result["text"].get("problems", [])) + list(result["layout"].get("problems", []))

    if ats_report:
        scored = score_keywords(
            f"{job.get('title', '')} {job.get('description', '')}",
            profile,
            text or overlay_text(overlay),
            title=str(job.get("title", "")),
        )
        result["ats"] = scored
        if ats_path is not None:
            ats_path.parent.mkdir(parents=True, exist_ok=True)
            ats_path.write_text(report_markdown(scored, job), encoding="utf-8")
            result["ats_report"] = str(ats_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build job-specific resume variations")
    parser.add_argument("--profile", default="data/resume/profile.json", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job", type=Path, help="JSON job record")
    source.add_argument("--job-id", help="Job id in data/applications.db")
    parser.add_argument("--db", type=Path, help="Job database (default: data/applications.db)")
    parser.add_argument("--overlays", type=Path, help="Directory of <strategy>.json rewritten overlays")
    parser.add_argument("--strategies", default=",".join(STRATEGIES), help="Comma-separated strategies")
    parser.add_argument("--variant", choices=["auto", "master", "backend", "software-engineer", "ai-ml"], default="auto")
    parser.add_argument("--compile", action="store_true", help="Compile each rendered source when an engine exists")
    parser.add_argument("--remote", action="store_true", help="Compile through LaTeX.to instead of a local engine")
    parser.add_argument(
        "--fit-pages",
        type=int,
        metavar="N",
        help="compile and cut the lowest-value bullets until the resume fits N pages",
    )
    parser.add_argument("--max-cuts", type=int, default=8, help="cut at most this many bullets while fitting")
    parser.add_argument("--inspect", action="store_true", help="check the compiled PDF's text layer and layout")
    parser.add_argument("--ats-report", action="store_true", help="score posting keywords against the compiled PDF")
    args = parser.parse_args()

    profile = load_profile(args.profile)
    job = load_job(args.job, args.job_id, args.db)
    slug = job_slug(job)
    print(f"Job: {job.get('title', '?')} at {job.get('company', '?')} ({job.get('id', slug)})")
    strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    unknown = [item for item in strategies if item not in STRATEGIES]
    if unknown:
        raise SystemExit(f"Unknown strategies: {', '.join(unknown)}")
    if args.fit_pages is not None and args.fit_pages < 1:
        raise SystemExit("--fit-pages must be at least 1")

    prefer = "remote" if args.remote else "auto"
    compile_requested = args.compile or args.fit_pages is not None or args.inspect or args.ats_report
    out_root = RESUMES / "generated" / "job-specific" / slug
    work_root = DATA / "tailoring" / slug
    work_root.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "job": {"id": job.get("id", slug), "company": job.get("company", ""), "title": job.get("title", "")},
        "profile": str(args.profile),
        "posting": guard_posting(str(job.get("description", ""))).as_dict(),
        "engine": None,
        "variants": [],
    }
    posting = report["posting"]
    if posting["hidden_blocks"]:
        print(f"  warning: removed {len(posting['hidden_blocks'])} hidden block(s) from the posting")
    if posting["flags"]:
        print(f"  warning: posting contains {len(posting['flags'])} instruction-shaped phrase(s)")
        for flag in posting["flags"]:
            print(f"    - {flag}")
    if posting["invisible_characters"]:
        print(f"  note: removed {posting['invisible_characters']} invisible character(s)")

    for strategy in strategies:
        overlay_path = (args.overlays / f"{strategy}.json") if args.overlays else (work_root / f"{strategy}.json")
        if overlay_path.exists():
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            source = str(overlay_path)
        else:
            overlay = build_overlay(profile, job, strategy)
            overlay_path.write_text(json.dumps(overlay, indent=2, ensure_ascii=False), encoding="utf-8")
            source = "deterministic"

        (work_root / f"{strategy}.prompt.md").write_text(prompt_packet(profile, job, strategy), encoding="utf-8")

        verification = verify_overlay(profile, overlay)
        entry: dict = {
            "strategy": strategy,
            "overlay": str(overlay_path),
            "source": source,
            "verified": verification["ok"],
            "problems": verification["problems"],
            "keywords": overlay.get("keywords", []),
        }

        if verification["ok"]:
            variant = args.variant if args.variant != "auto" else overlay.get("variant") or choose_variant(job)
            destination = out_root / strategy
            active_overlay = overlay
            entry["variant"] = variant

            if args.fit_pages is not None:
                active_overlay, cuts, compiled, attempts, tex_path, page_check = fit_to_pages(
                    profile,
                    variant,
                    destination,
                    job,
                    overlay,
                    prefer,
                    args.fit_pages,
                    args.max_cuts,
                )
                entry["compile"] = compiled
                report["engine"] = compiled["compiler"]
                entry["source_tex"] = str(tex_path)
                entry["fit"] = {
                    "max_pages": args.fit_pages,
                    "attempts": attempts,
                    "cuts": cuts,
                    "pages": (page_check or {}).get("pages"),
                    "layout_problems": (page_check or {}).get("layout_problems", []),
                    "ok": bool(
                        page_check
                        and page_check.get("pages") is not None
                        and page_check["pages"] <= args.fit_pages
                        and not page_check.get("blocking_layout")
                    ),
                }
                if cuts:
                    fitted_path = work_root / f"{strategy}.fitted.json"
                    fitted_path.write_text(
                        json.dumps(active_overlay, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    entry["fitted_overlay"] = str(fitted_path)
            else:
                tex_path = render(profile, variant, destination, job=job, overlay=overlay)
                entry["source_tex"] = str(tex_path)
                if compile_requested:
                    entry["compile"] = compile_source(tex_path, prefer)
                    report["engine"] = entry["compile"]["compiler"]

            if (args.inspect or args.ats_report) and entry.get("compile"):
                compiled = entry["compile"]
                pdf_path = compiled.get("pdf") if compiled.get("status") == "passed" else None
                if pdf_path:
                    ats_path = REPORTS / f"ats-{slug}-{strategy}.md" if args.ats_report else None
                    entry["inspect"] = inspect_pdf(
                        pdf_path, profile, job, active_overlay, args.ats_report, ats_path
                    )
                else:
                    entry["inspect"] = {"ok": False, "problems": ["no compiled PDF to inspect"]}

        report["variants"].append(entry)
        status = "ok" if entry["verified"] else f"rejected ({len(entry['problems'])} problems)"
        print(f"{strategy:12} {status}")
        for problem in entry["problems"]:
            print(f"  - {problem}", file=sys.stderr)
        fit = entry.get("fit")
        if fit:
            pages = fit["pages"] if fit["pages"] is not None else "?"
            print(f"  fit        {pages}/{fit['max_pages']} page(s), {len(fit['cuts'])} cut(s)")
        inspection = entry.get("inspect")
        if inspection:
            print("  inspect    " + ("clean" if inspection["ok"] else "problems found"))
            for problem in inspection.get("problems", []):
                print(f"    - {problem}", file=sys.stderr)

    REPORTS.mkdir(exist_ok=True)
    report_path = REPORTS / f"tailoring-{slug}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport: {report_path}")

    rendered = [entry for entry in report["variants"] if entry["verified"]]
    if not rendered:
        raise SystemExit(2)
    unfit = [entry["strategy"] for entry in rendered if entry.get("fit") and not entry["fit"]["ok"]]
    uninspected = [entry["strategy"] for entry in rendered if entry.get("inspect") and not entry["inspect"]["ok"]]
    if unfit:
        print(f"fit: not reached for {', '.join(unfit)}", file=sys.stderr)
    if uninspected:
        print(f"inspect: problems remain for {', '.join(uninspected)}", file=sys.stderr)
    if unfit or uninspected:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
