"""Gmail sync over IMAP.

An app password keeps this local: no OAuth client, no Google Cloud project,
no token files. The password is read from JOB_GMAIL_APP_PASSWORD, never from
the tracked config.

Every message is classified and matched, then recorded. A status only moves
when the intent is clear and confidence clears the threshold.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import html
import imaplib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import store
from .intent import classify
from .match_email import best_job


@dataclass
class GmailConfig:
    address: str = ""
    password: str = ""
    folder: str = "[Gmail]/All Mail"
    days: int = 21
    max_messages: int = 200
    threshold: float = 0.6
    match_threshold: float = 0.45

    @property
    def configured(self) -> bool:
        return bool(self.address and self.password)


def config_from(mapping: dict, env: dict | None = None) -> GmailConfig:
    import os

    env = env if env is not None else os.environ
    section = mapping.get("gmail") or {}
    return GmailConfig(
        address=str(section.get("address") or env.get("JOB_GMAIL_ADDRESS", "")),
        password=str(env.get("JOB_GMAIL_APP_PASSWORD", "")),
        folder=str(section.get("folder") or "[Gmail]/All Mail"),
        days=int(section.get("days", 21)),
        max_messages=int(section.get("max_messages", 200)),
        threshold=float(section.get("auto_apply_threshold", 0.6)),
        match_threshold=float(section.get("match_threshold", 0.45)),
    )


def _decode(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def message_id(msg) -> str:
    raw = _decode(msg.get("Message-ID")).strip()
    if raw:
        return raw
    digest = hashlib.sha256(
        f"{msg.get('Date')}|{msg.get('From')}|{msg.get('Subject')}".encode("utf-8", "ignore")
    ).hexdigest()[:24]
    return f"generated:{digest}"


def body_text(msg) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    parts.append(part.get_content())
                except Exception:
                    continue
            elif ctype == "text/html" and not parts:
                try:
                    parts.append(_html_to_text(part.get_content()))
                except Exception:
                    continue
    else:
        try:
            content = msg.get_content()
            parts.append(content if msg.get_content_type() == "text/plain" else _html_to_text(content))
        except Exception:
            parts.append("")
    text = "\n".join(p for p in parts if p)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:12000]


def _html_to_text(source: str) -> str:
    source = re.sub(r"(?is)<(script|style).*?</\1>", " ", source)
    source = re.sub(r"(?s)<[^>]+>", " ", source)
    return re.sub(r"[ \t]{2,}", " ", html.unescape(source)).strip()


def parse_message(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))).isoformat(timespec="seconds")
    except Exception:
        received = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "message_id": message_id(msg),
        "received_at": received,
        "sender": str(msg.get("From") or ""),
        "subject": str(msg.get("Subject") or ""),
        "body": body_text(msg),
    }


def fetch_imap(cfg: GmailConfig) -> list[dict]:
    if not cfg.configured:
        raise RuntimeError(
            "Gmail is not configured. Set [gmail].address in config/sync.toml and "
            "JOB_GMAIL_APP_PASSWORD in the environment."
        )
    since = (date.today() - timedelta(days=cfg.days)).strftime("%d-%b-%Y")
    client = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        client.login(cfg.address, cfg.password)
        client.select(f'"{cfg.folder}"', readonly=True)
        typ, data = client.search(None, f"(SINCE {since})")
        if typ != "OK":
            raise RuntimeError(f"Gmail search failed: {typ}")
        ids = (data[0] or b"").split()[-cfg.max_messages:]
        messages: list[dict] = []
        for number in ids:
            typ, payload = client.fetch(number, "(RFC822)")
            if typ != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            messages.append(parse_message(payload[0][1]))
        return messages
    finally:
        try:
            client.logout()
        except Exception:
            pass


def load_eml_dir(directory: Path) -> list[dict]:
    messages: list[dict] = []
    for path in sorted(directory.glob("*.eml")):
        messages.append(parse_message(path.read_bytes()))
    return messages


def sync(
    cfg: GmailConfig,
    *,
    dry_run: bool = False,
    db_path: Path | None = None,
    eml_dir: Path | None = None,
) -> dict:
    messages = load_eml_dir(eml_dir) if eml_dir else fetch_imap(cfg)
    jobs = store.all_jobs(db_path)

    summary = {
        "messages": len(messages),
        "already_seen": 0,
        "unknown": 0,
        "unmatched": 0,
        "unchanged": 0,
        "changed": 0,
        "suggested": 0,
    }
    events: list[dict] = []

    for msg in messages:
        if store.has_event(msg["message_id"], db_path):
            summary["already_seen"] += 1
            continue

        intent = classify(msg["subject"], msg["body"], msg["sender"])
        if not intent.known:
            summary["unknown"] += 1
            continue

        match = best_job(msg["subject"], msg["body"], msg["sender"], jobs, cfg.match_threshold)
        previous = match.job.get("status", "new") if match.job else "new"

        if match.job is None:
            summary["unmatched"] += 1
            store.record_event(
                message_id=msg["message_id"],
                received_at=msg["received_at"],
                sender=msg["sender"],
                subject=msg["subject"],
                job_id=None,
                intent=intent.name,
                confidence=intent.confidence,
                previous_status="",
                proposed_status="",
                applied=False,
                reason=f"no job matched (best {match.score:.2f}: {match.reason})",
                path=db_path,
            )
            continue

        proposed, changed, reason = store.apply_intent(
            match.job,
            intent.name,
            intent.confidence,
            threshold=cfg.threshold,
            dry_run=dry_run,
            path=db_path,
        )

        store.record_event(
            message_id=msg["message_id"],
            received_at=msg["received_at"],
            sender=msg["sender"],
            subject=msg["subject"],
            job_id=match.job["id"],
            intent=intent.name,
            confidence=intent.confidence,
            previous_status=previous,
            proposed_status=proposed,
            applied=changed,
            reason=f"{reason}; match {match.score:.2f} ({match.reason})",
            path=db_path,
        )

        if changed:
            summary["changed"] += 1
        elif proposed != previous:
            summary["suggested"] += 1
        else:
            summary["unchanged"] += 1

        events.append(
            {
                "job": match.job,
                "intent": intent,
                "previous": previous,
                "proposed": proposed,
                "changed": changed,
                "reason": reason,
                "subject": msg["subject"],
                "sender": msg["sender"],
            }
        )

    summary["events"] = events
    return summary
