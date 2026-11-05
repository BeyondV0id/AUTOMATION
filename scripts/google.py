"""Check or refresh the Google token the MCP server and push_tracker use.

    python scripts/google.py status
    python scripts/google.py refresh
    python scripts/google.py scopes

The refresh token expires every 7 days while the OAuth app is in Testing, so
`status` is the fastest way to tell "auth lapsed" from "something is broken".
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOKENS = Path.home() / ".config" / "opencode" / "google-credentials"


def token_file(address: str) -> Path:
    path = TOKENS / f"{address}.json"
    if not path.exists():
        raise SystemExit(
            f"No token at {path}.\nAsk the agent to run start_google_auth for {address}."
        )
    return path


def read(address: str) -> dict:
    return json.loads(token_file(address).read_text(encoding="utf-8"))


def status(address: str) -> int:
    path = token_file(address)
    creds = read(address)
    expiry = str(creds.get("expiry") or "")
    left = "unknown"
    try:
        when = datetime.fromisoformat(expiry)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        delta = when - datetime.now(timezone.utc)
        left = (
            f"{int(delta.total_seconds() // 60)}m left"
            if delta.total_seconds() > 0
            else "expired (refreshes automatically)"
        )
    except ValueError:
        pass

    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    )
    days_left = max(0, 7 - age.days)

    print(f"account         {address}")
    print(f"access token    {left}  (expiry {expiry})")
    print(f"refresh window  {days_left}d left of 7  (Testing mode; re-auth when it lapses)")
    print(f"scopes          {len(creds.get('scopes') or [])}")
    print(f"refresh token   {'yes' if creds.get('refresh_token') else 'no'}")
    return 0


def scopes(address: str) -> int:
    for scope in read(address).get("scopes") or []:
        print(scope.replace("https://www.googleapis.com/auth/", ""))
    return 0


def refresh(address: str) -> int:
    path = token_file(address)
    creds = read(address)
    body = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    request = urllib.request.Request(
        creds.get("token_uri") or "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"refresh failed ({exc.code}): {detail}", file=sys.stderr)
        if exc.code == 400 and "invalid_grant" in detail:
            print("The 7-day Testing window lapsed. Ask the agent to run start_google_auth.", file=sys.stderr)
        return 1

    if payload.get("refresh_token"):
        creds["refresh_token"] = payload["refresh_token"]
    if payload.get("access_token"):
        creds["token"] = payload["access_token"]
    if payload.get("expires_in"):
        from datetime import timedelta

        when = datetime.now(timezone.utc) + timedelta(seconds=int(payload["expires_in"]))
        creds["expiry"] = when.isoformat(timespec="seconds")
    path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    print(f"refreshed {address}; new expiry {creds.get('expiry')}")
    return 0


def default_address() -> str:
    """Read the account from config/sync.toml instead of hard-coding one."""
    config = Path(__file__).resolve().parents[1] / "config" / "sync.toml"
    if not config.exists():
        return ""
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    return str((data.get("gmail") or {}).get("address") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "refresh", "scopes"], nargs="?", default="status")
    parser.add_argument("--email", default=default_address())
    args = parser.parse_args(argv)
    return {"status": status, "refresh": refresh, "scopes": scopes}[args.action](args.email)


if __name__ == "__main__":
    raise SystemExit(main())
