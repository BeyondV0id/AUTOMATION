"""Browser sessions behind one small interface.

Two backends, because the two jobs are different:

``playwright``
    Launches its own Chromium with a persistent profile under
    ``data/browser-profile``. It is the only backend that can read inside
    cross-origin iframes, which Greenhouse and Lever forms are built out of,
    and it can upload files. Unattended form filling runs here.

``ego``
    Drives the ego lite browser the user is already signed into, through the
    ``ego-browser`` CLI. The user can watch every click. It sees one document
    at a time, so it is for single-document pages, logged-in sources, and
    anything a human wants to supervise.

Pick one in ``config/browser.toml`` or per call with ``open_session(backend=...)``.
"""

from __future__ import annotations

import importlib.util
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import DATA, ROOT

CONFIG_PATH = ROOT / "config" / "browser.toml"


class BrowserError(RuntimeError):
    """Raised when a backend cannot do what was asked of it."""


@dataclass
class SessionConfig:
    backend: str = "playwright"
    headless: bool = True
    slow_mo: int = 0
    timeout_ms: int = 45000
    profile_dir: Path = field(default_factory=lambda: DATA / "browser-profile")
    ego_space: str = "job-automation"
    ego_profile: str = ""
    ego_exe: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "headless": self.headless,
            "slow_mo": self.slow_mo,
            "timeout_ms": self.timeout_ms,
            "profile_dir": str(self.profile_dir),
            "ego_space": self.ego_space,
            "ego_profile": self.ego_profile,
            "ego_exe": self.ego_exe,
        }


class Session:
    """The interface both backends implement.

    Anything that only one backend can do raises :class:`BrowserError` with a
    sentence saying which backend to switch to, instead of an AttributeError
    from deep inside a driver.
    """

    kind = "base"
    supports_frames = False
    supports_upload = False

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        raise NotImplementedError

    # -- navigation ------------------------------------------------------
    def goto(self, url: str, timeout_ms: int | None = None) -> None:
        raise NotImplementedError

    def wait(self, ms: int) -> None:
        raise NotImplementedError

    def url(self) -> str:
        raise NotImplementedError

    # -- observation -----------------------------------------------------
    def evaluate(self, js: str, arg: Any = None, frame: int = 0) -> Any:
        """Run a function source in the page and return its JSON-able result."""
        raise NotImplementedError

    def screenshot(self, path: Path, full_page: bool = True) -> str:
        raise NotImplementedError

    # -- actions ---------------------------------------------------------
    def fill(self, selector: str, value: str, frame: int = 0) -> None:
        raise NotImplementedError

    def select_option(self, selector: str, label: str, frame: int = 0) -> None:
        raise NotImplementedError

    def click(self, selector: str, frame: int = 0) -> None:
        raise NotImplementedError

    def upload(self, selector: str, paths: list[Path], frame: int = 0) -> None:
        raise BrowserError(
            f"{self.kind} cannot upload files; run this with backend='playwright'"
        )

    # -- helpers ---------------------------------------------------------
    def require_frames(self, what: str) -> None:
        if not self.supports_frames:
            raise BrowserError(
                f"{what} needs frame access. ego lite sees one document at a time, "
                "so cross-origin iframes (Greenhouse, Lever) are invisible to it. "
                "Use backend='playwright'."
            )


def playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def load_config(path: Path | None = None) -> SessionConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return SessionConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = data.get("browser") or {}
    profile = section.get("profile_dir") or ""
    return SessionConfig(
        backend=str(section.get("backend") or "playwright").lower(),
        headless=bool(section.get("headless", True)),
        slow_mo=int(section.get("slow_mo", 0)),
        timeout_ms=int(section.get("timeout_ms", 45000)),
        profile_dir=Path(profile) if profile else DATA / "browser-profile",
        ego_space=str(section.get("ego_space") or "job-automation"),
        ego_profile=str(section.get("ego_profile") or ""),
        ego_exe=str(section.get("ego_exe") or ""),
    )


def open_session(config: SessionConfig | None = None, **overrides: Any) -> Session:
    cfg = config or load_config()
    for key, value in overrides.items():
        if not hasattr(cfg, key):
            raise BrowserError(f"unknown session setting: {key}")
        setattr(cfg, key, value)

    if cfg.backend == "auto":
        cfg.backend = "playwright" if playwright_available() else "ego"

    if cfg.backend == "ego":
        from .ego_backend import EgoSession

        return EgoSession(cfg)
    if cfg.backend == "playwright":
        from .playwright_backend import PlaywrightSession

        return PlaywrightSession(cfg)
    raise BrowserError(f"unknown backend: {cfg.backend} (use playwright or ego)")
