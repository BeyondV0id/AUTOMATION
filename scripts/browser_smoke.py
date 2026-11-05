"""Prove a browser backend works before you trust it with a real form.

    python scripts/browser_smoke.py --url https://example.com
    python scripts/browser_smoke.py --url https://boards.greenhouse.io/... --backend playwright

Opens the page, reports what it can see, and saves a screenshot. Nothing is
clicked except an obvious cookie button.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_pipeline.browser import BrowserError, load_config, open_session  # noqa: E402
from resume_pipeline.browser.flows import (  # noqa: E402
    detect_ats,
    dismiss_cookies,
    page_facts,
    safe_screenshot,
)

SHOTS = ROOT / "reports" / "browser-smoke"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://example.com")
    parser.add_argument("--backend", choices=["playwright", "ego", "auto"], default=None)
    parser.add_argument("--headed", action="store_true", help="show the window (playwright only)")
    args = parser.parse_args(argv)

    cfg = load_config()
    backend = args.backend or cfg.backend
    out: dict[str, object] = {"backend": backend, "url": args.url}

    session = None
    try:
        session = open_session(backend=backend, headless=not args.headed)
        session.goto(args.url, cfg.timeout_ms)
        out["frames"] = session.supports_frames
        out["upload"] = session.supports_upload
        out["facts"] = page_facts(session)
        out["ats"] = detect_ats(session.url() or args.url, session)
        out["cookies_clicked"] = dismiss_cookies(session)

        SHOTS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shot, shot_error = safe_screenshot(session, SHOTS / f"smoke-{backend}-{stamp}.png")
        out["screenshot"] = shot
        if shot_error:
            out["screenshot_error"] = shot_error
        out["ok"] = True
    except BrowserError as exc:
        out["ok"] = False
        out["error"] = str(exc)
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
