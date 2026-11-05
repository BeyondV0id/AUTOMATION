"""Compile generated LaTeX sources, locally or through LaTeX.to.

Order of preference:

1. A real local engine when one is installed (``latexmk``, ``pdflatex``,
   ``xelatex``, ``lualatex``, ``tectonic``).
2. Otherwise LaTeX.to, which runs TeX Live in WebAssembly inside a Chromium
   page and returns the PDF plus the engine log. No TeX installation, and the
   document never leaves the machine.

``--remote`` forces LaTeX.to, ``--local`` refuses it. A PDF is only reported as
produced when the file exists and starts with the PDF magic bytes.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import REPORTS, RESUMES

LOCAL_ENGINES = ("latexmk", "pdflatex", "xelatex", "lualatex", "tectonic")

ERROR_MARKERS = ("Undefined control sequence", "Emergency stop", "Fatal error")


def find_engine() -> str | None:
    for name in LOCAL_ENGINES:
        if shutil.which(name):
            return name
    return None


def _has_pdf(path: Path) -> bool:
    try:
        return path.read_bytes()[:5] == b"%PDF-"
    except OSError:
        return False


def _inspect(
    source: Path,
    log_text: str,
    returncode: int,
    compiler: str,
    exists: bool,
    message: str = "",
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    for marker in ERROR_MARKERS:
        if marker in log_text:
            errors.append(marker)
    latex_errors = re.findall(r"! LaTeX Error: (.*)", log_text)
    if latex_errors and not errors:
        errors.append(f"LaTeX Error: {latex_errors[0].strip()}")
    if "Overfull \\hbox" in log_text:
        warnings.append("Overfull \\hbox detected")
    if "Underfull \\hbox" in log_text:
        warnings.append("Underfull \\hbox detected")
    if returncode != 0 and not errors:
        errors.append(f"compiler exited with status {returncode}")
    if not exists:
        errors.append("no PDF was produced")

    result = {
        "source": str(source),
        "pdf": str(source.with_suffix(".pdf")),
        "compiler": compiler,
        "returncode": returncode,
        "exists": exists,
        "status": "passed" if returncode == 0 and exists and not errors else "failed",
        "errors": errors,
        "warnings": warnings,
    }
    if message:
        result["message"] = message
    return result


def compile_one(source: Path, engine: str) -> dict:
    """Compile with a locally installed engine."""
    command = (
        [engine, "-pdf", "-interaction=nonstopmode", "-halt-on-error", source.name]
        if engine == "latexmk"
        else [engine, "-interaction=nonstopmode", "-halt-on-error", source.name]
    )
    result = subprocess.run(command, cwd=source.parent, text=True, capture_output=True, timeout=120)
    log = source.with_suffix(".log")
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else result.stdout + result.stderr
    return _inspect(source, log_text, result.returncode, f"local:{engine}", _has_pdf(source.with_suffix(".pdf")))


def compile_remote(source: Path, engine: str = "pdflatex", clean: bool = False) -> dict:
    """Compile with LaTeX.to in a headless Chromium page."""
    from .latexto import LatextoError, compile_project

    pdf = source.with_suffix(".pdf")
    compiler = f"latex.to:{engine}"
    try:
        result = compile_project(source.parent, main=source.name, engine=engine, clean=clean)
    except LatextoError as error:
        return _inspect(source, error.log or str(error), 1, compiler, False, str(error))
    except Exception as error:  # missing browser, no network, closed page
        return _inspect(source, str(error), 1, compiler, False, f"LaTeX.to unavailable: {error}")
    pdf.write_bytes(result["pdf"])
    return _inspect(source, result["log"], 0, compiler, _has_pdf(pdf))


def compile_source(source: Path, prefer: str = "auto", engine: str = "pdflatex", clean: bool = False) -> dict:
    """Compile one source with the best available compiler."""
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"LaTeX source not found: {source}")

    local = find_engine()
    if prefer == "remote":
        return compile_remote(source, engine, clean)
    if prefer == "local":
        if not local:
            return _inspect(source, "", 1, "none", False, "No local LaTeX compiler found; use --remote")
        return compile_one(source, local)
    if local:
        return compile_one(source, local)
    return compile_remote(source, engine, clean)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile and validate LaTeX sources")
    parser.add_argument("--all", action="store_true", help="compile every generated source (default)")
    parser.add_argument("--source", type=Path, help="one resume.tex to compile")
    parser.add_argument("--remote", action="store_true", help="force LaTeX.to instead of a local engine")
    parser.add_argument("--local", action="store_true", help="never use LaTeX.to")
    parser.add_argument("--engine", default="pdflatex", help="LaTeX.to engine id (pdflatex, xelatex, lualatex, ...)")
    parser.add_argument("--fresh", action="store_true", help="discard the LaTeX.to build cache for this project")
    args = parser.parse_args()
    if args.remote and args.local:
        parser.error("choose one of --remote or --local")

    prefer = "remote" if args.remote else "local" if args.local else "auto"
    local = find_engine()
    if prefer == "local":
        compiler = f"local:{local}" if local else "none"
    elif prefer == "remote":
        compiler = f"latex.to:{args.engine}"
    else:
        compiler = f"local:{local}" if local else f"latex.to:{args.engine}"
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": local,
        "compiler": compiler,
        "results": [],
    }

    if args.source:
        sources = [args.source]
    else:
        sources = sorted(RESUMES.glob("generated/**/resume.tex"))

    if not sources:
        report["status"] = "blocked"
        report["message"] = "No generated .tex sources found. Run resume_pipeline.generate first."
    elif prefer == "local" and not local:
        report["status"] = "blocked"
        report["message"] = "No supported LaTeX compiler found and --local was requested."
    else:
        report["results"] = [compile_source(path, prefer, args.engine, args.fresh) for path in sources]
        report["status"] = "passed" if all(x["status"] == "passed" for x in report["results"]) else "failed"
        report["compiler"] = report["results"][0]["compiler"]

    REPORTS.mkdir(exist_ok=True)
    output = REPORTS / "compile-report.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "passed" else 2)


if __name__ == "__main__":
    main()
