from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_profile(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Reviewed profile not found: {source}")
    profile = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a JSON object")
    name = profile.get("identity", {}).get("name", "").strip()
    if not name:
        raise ValueError("Profile identity.name is empty; do not generate from unverified data")
    return profile


def record_matches(item: dict[str, Any], tags: set[str]) -> bool:
    """Keep untagged verified records; filter only explicitly tagged records."""
    raw = item.get("tags", item.get("roles", []))
    if not raw:
        return True
    values = {str(value).lower().replace(" ", "-") for value in raw}
    return bool(values & tags)
