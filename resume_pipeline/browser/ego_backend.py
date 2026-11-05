"""ego lite backend: drives the browser the user is already signed into.

Every call is a separate ``ego-browser nodejs`` process, so state lives in the
task space, not in Python. One task space named after ``ego_space`` is created
on first use and resumed after that, which keeps a single tab alive between
runs and lets the user watch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import DATA
from . import BrowserError, Session, SessionConfig

MARKER = "__EGO_RESULT__"
RUNS = DATA / "browser" / "ego-runs"
DEFAULT_EXE = Path(r"C:\Program Files\Citro Labs\ego lite\Application")


def find_exe(configured: str = "") -> Path:
    if configured:
        path = Path(configured)
        if path.exists():
            return path
        raise BrowserError(f"ego_exe does not exist: {path}")

    from_env = os.environ.get("EGO_BROWSER", "")
    if from_env and Path(from_env).exists():
        return Path(from_env)

    found = shutil.which("ego-browser")
    if found:
        return Path(found)

    if DEFAULT_EXE.exists():
        # Highest installed version wins.
        candidates = sorted(
            (p for p in DEFAULT_EXE.glob("*/ego-browser.exe") if p.is_file()),
            key=lambda p: p.parent.name,
            reverse=True,
        )
        if candidates:
            return candidates[0]

    raise BrowserError(
        "ego-browser.exe not found. Install ego lite, or set ego_exe in config/browser.toml."
    )


def as_function(js: str) -> str:
    """Accept a function source or a bare expression."""
    text = js.strip()
    if text.startswith("(") or text.startswith("async") or text.startswith("function"):
        return text
    return f"() => ({text})"


class EgoSession(Session):
    kind = "ego"
    supports_frames = False
    supports_upload = True

    def __init__(self, config: SessionConfig | None = None):
        self.cfg = config or SessionConfig()
        self.exe = find_exe(self.cfg.ego_exe)
        RUNS.mkdir(parents=True, exist_ok=True)
        self._runs = 0
        self._page_label = "p1"

    # -- process plumbing ------------------------------------------------
    def _run(self, body: str, timeout_s: int = 90) -> Any:
        self._runs += 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        script = RUNS / f"run-{stamp}-{self._runs:03d}.mjs"
        header = [
            f"const task = await taskSpace({json.dumps(self.cfg.ego_space)});",
            f'const page = task.page("{self._page_label}");',
        ]
        script.write_text("\n".join(header) + "\n" + body + "\n", encoding="utf-8")

        entry = f"await import('file:///{script.as_posix()}')"
        try:
            done = subprocess.run(
                [str(self.exe), "nodejs", "-e", entry],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise BrowserError(f"ego-browser timed out after {timeout_s}s") from exc

        output = (done.stdout or "") + (done.stderr or "")
        if done.returncode != 0:
            tail = output.strip().splitlines()[-8:]
            raise BrowserError("ego-browser failed:\n" + "\n".join(tail))

        for line in reversed(output.splitlines()):
            if line.startswith(MARKER):
                return json.loads(line[len(MARKER) :])
        tail = output.strip().splitlines()[-8:]
        raise BrowserError("ego-browser returned no result:\n" + "\n".join(tail))

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        """Leave the tab open: the user may still be looking at it."""
        return None

    # -- navigation ------------------------------------------------------
    def goto(self, url: str, timeout_ms: int | None = None) -> None:
        timeout = timeout_ms or self.cfg.timeout_ms
        self._run(
            f"await page.goto({json.dumps(url)}, {{ timeout: {int(timeout)} }});\n"
            'await page.waitForLoadState("load").catch(() => {});\n'
            f"console.log({json.dumps(MARKER)} + JSON.stringify({{ url: await page.url() }}));",
            timeout_s=max(60, int(timeout / 1000) + 30),
        )

    def wait(self, ms: int) -> None:
        self._run(f"await page.waitForTimeout({int(ms)});\nconsole.log({json.dumps(MARKER)} + 'null');")

    def url(self) -> str:
        return str(self._run(f"console.log({json.dumps(MARKER)} + JSON.stringify(await page.url()));"))

    # -- observation -----------------------------------------------------
    def evaluate(self, js: str, arg: Any = None, frame: int = 0) -> Any:
        if frame:
            self.require_frames("evaluate(frame>0)")
        call = f"await page.evaluate({as_function(js)}"
        call += f", {json.dumps(arg)})" if arg is not None else ")"
        return self._run(
            f"const value = {call};\nconsole.log({json.dumps(MARKER)} + JSON.stringify(value ?? null));"
        )

    def screenshot(self, path: Path, full_page: bool = True) -> str:
        target = Path(path).resolve().as_posix()
        body = (
            f"const shot = await page.screenshot({{ path: {json.dumps(target)} }});\n"
            f"console.log({json.dumps(MARKER)} + JSON.stringify({{ path: shot }}));"
        )
        result = self._run(body)
        return str((result or {}).get("path") or target)

    # -- actions ---------------------------------------------------------
    def fill(self, selector: str, value: str, frame: int = 0) -> None:
        if frame:
            self.require_frames("fill(frame>0)")
        self._run(
            f"await page.fill({json.dumps(selector)}, {json.dumps(value)});\n"
            f"console.log({json.dumps(MARKER)} + 'null');"
        )

    def select_option(self, selector: str, label: str, frame: int = 0) -> None:
        if frame:
            self.require_frames("select_option(frame>0)")
        self._run(
            f"await page.selectOption({json.dumps(selector)}, {{ label: {json.dumps(label)} }});\n"
            f"console.log({json.dumps(MARKER)} + 'null');"
        )

    def click(self, selector: str, frame: int = 0) -> None:
        if frame:
            self.require_frames("click(frame>0)")
        self._run(
            f"await page.click({json.dumps(selector)});\n"
            f"console.log({json.dumps(MARKER)} + 'null');"
        )

    def upload(self, selector: str, paths: list[Path], frame: int = 0) -> None:
        if frame:
            self.require_frames("upload(frame>0)")
        absolute = [Path(p).resolve().as_posix() for p in paths]
        self._run(
            f"await page.setInputFiles({json.dumps(selector)}, {json.dumps(absolute)});\n"
            f"console.log({json.dumps(MARKER)} + 'null');"
        )
