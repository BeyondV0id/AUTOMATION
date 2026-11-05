"""Compile a LaTeX project with LaTeX.to, no TeX installation required.

LaTeX.to runs a real TeX Live inside WebAssembly in a Chromium page and hands
back PDF bytes plus the engine log (https://latex.to/agents/). The document is
compiled on this machine: nothing is uploaded, the browser only downloads the
site and the TeX Live blocks it needs.

The first compile on a cold cache downloads packages and can take minutes.
Later compiles reuse ``.latexto-profile`` (override with ``LATEXTO_PROFILE``),
so keep that directory between runs.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path
from typing import Any

from . import ROOT

SITE = os.environ.get("LATEXTO_URL", "https://latex.to")
PROFILE = Path(os.environ.get("LATEXTO_PROFILE", ROOT / ".latexto-profile"))

TEXT_SUFFIXES = {".tex", ".sty", ".cls", ".bib", ".bst", ".cfg", ".def"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".otf", ".ttf"}

# Runs inside the page. A Uint8Array cannot cross the bridge, so the PDF is
# base64 encoded there and decoded here.
_COMPILE_JS = r"""
async ({ files, main, engine, project, clean }) => {
  try {
    const { pdf, log } = await window.latexto.compile({ files, main, engine, project, clean });
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < pdf.length; i += chunk) {
      binary += String.fromCharCode.apply(null, pdf.subarray(i, i + chunk));
    }
    return { ok: true, pdf: btoa(binary), log: log || '' };
  } catch (error) {
    return {
      ok: false,
      error: String((error && error.message) || error),
      log: (error && error.log) || '',
    };
  }
}
"""


class LatextoError(RuntimeError):
    """A compile LaTeX.to refused or could not finish. ``log`` carries TeX's output."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return cleaned[:64] or "resume"


def _collect(root: Path) -> dict[str, Any]:
    """Map every project file to its contents, keyed by a forward-slash relative path."""
    files: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            files[relative] = path.read_text(encoding="utf-8")
        elif suffix in BINARY_SUFFIXES:
            files[relative] = {"base64": base64.b64encode(path.read_bytes()).decode("ascii")}
    return files


def _main_file(root: Path, files: dict[str, Any], main: str | None) -> str:
    if main:
        if main not in files:
            raise LatextoError(f"Main file '{main}' is not in {root}")
        return main
    candidates = sorted(name for name in files if name.endswith(".tex") and "/" not in name)
    if "main.tex" in candidates:
        return "main.tex"
    if "resume.tex" in candidates:
        return "resume.tex"
    if len(candidates) == 1:
        return candidates[0]
    raise LatextoError(f"Cannot pick a main file in {root}; pass --main. Candidates: {', '.join(candidates) or 'none'}")


def compile_project(
    source: Path | str,
    *,
    main: str | None = None,
    engine: str = "pdflatex",
    project: str | None = None,
    clean: bool = False,
) -> dict[str, Any]:
    """Compile a ``.tex`` file or a project directory. Returns ``{"pdf", "log", ...}``."""
    source = Path(source)
    root = source if source.is_dir() else source.parent
    if not root.exists():
        raise LatextoError(f"Project directory does not exist: {root}")

    files = _collect(root)
    if not files:
        raise LatextoError(f"No LaTeX sources found in {root}")
    main = _main_file(root, files, main or (None if source.is_dir() else source.name))
    files.pop(Path(main).with_suffix(".pdf").as_posix(), None)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:  # pragma: no cover - dependency guard
        raise LatextoError(
            "Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
        ) from error

    payload = {
        "files": files,
        "main": main,
        "engine": engine,
        "project": _slug(project or f"{root.name}-{hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]}"),
        "clean": clean,
    }

    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(PROFILE), headless=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(SITE, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_function(
                "() => window.latexto && window.latexto.apiVersion === 1", timeout=180_000
            )
            result = page.evaluate(_COMPILE_JS, payload)
        finally:
            context.close()

    if not result.get("ok"):
        raise LatextoError(result.get("error") or "LaTeX.to compile failed", result.get("log", ""))
    return {
        "pdf": base64.b64decode(result["pdf"]),
        "log": result.get("log", ""),
        "engine": engine,
        "main": main,
        "url": SITE,
    }
