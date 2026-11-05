"""Playwright backend: its own Chromium, persistent profile, full frame access."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import BrowserError, Session, SessionConfig

VIEWPORT = {"width": 1400, "height": 950}


class PlaywrightSession(Session):
    kind = "playwright"
    supports_frames = True
    supports_upload = True

    def __init__(self, config: SessionConfig | None = None):
        config = config or SessionConfig()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BrowserError(
                "playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
            ) from exc

        self.cfg = config
        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(config.profile_dir),
            headless=config.headless,
            slow_mo=config.slow_mo,
            viewport=VIEWPORT,
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        try:
            self._context.close()
        finally:
            self._pw.stop()

    # -- navigation ------------------------------------------------------
    def goto(self, url: str, timeout_ms: int | None = None) -> None:
        timeout = timeout_ms or self.cfg.timeout_ms
        self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        try:
            self.page.wait_for_load_state("networkidle", timeout=min(6000, timeout))
        except Exception:
            pass

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    def url(self) -> str:
        return self.page.url or ""

    # -- observation -----------------------------------------------------
    def _frame(self, index: int):
        frames = self.page.frames
        if index < 0 or index >= len(frames):
            raise BrowserError(f"frame {index} does not exist (page has {len(frames)})")
        return frames[index]

    def evaluate(self, js: str, arg: Any = None, frame: int = 0) -> Any:
        return self._frame(frame).evaluate(js, arg)

    def screenshot(self, path: Path, full_page: bool = True) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=full_page)
        return str(path)

    # -- actions ---------------------------------------------------------
    def fill(self, selector: str, value: str, frame: int = 0) -> None:
        self._frame(frame).locator(selector).fill(value, timeout=3000)

    def select_option(self, selector: str, label: str, frame: int = 0) -> None:
        self._frame(frame).locator(selector).select_option(label=label, timeout=3000)

    def click(self, selector: str, frame: int = 0) -> None:
        self._frame(frame).locator(selector).click(timeout=3000)

    def upload(self, selector: str, paths: list[Path], frame: int = 0) -> None:
        self._frame(frame).locator(selector).set_input_files([str(p) for p in paths])
