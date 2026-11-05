"""SMTP send and IMAP draft append.

Both use the same Gmail app password as ``resume_pipeline.sync``. No OAuth
client, no token file: the password is read from ``JOB_GMAIL_APP_PASSWORD``.

Sending is capped twice: per run (``daily_limit``) and per company
(``per_company_limit``). A bounce costs sender reputation, so the caps are not
decoration.
"""

from __future__ import annotations

import imaplib
import mimetypes
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from . import store


@dataclass
class MailConfig:
    address: str = ""
    password: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    drafts_folder: str = "[Gmail]/Drafts"
    display_name: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.address and self.password)


def config_from(mapping: dict, env: dict | None = None) -> MailConfig:
    import os

    env = env if env is not None else os.environ
    section = mapping.get("smtp") or {}
    gmail = mapping.get("gmail") or {}
    return MailConfig(
        address=str(gmail.get("address") or env.get("JOB_GMAIL_ADDRESS", "")),
        password=str(env.get("JOB_GMAIL_APP_PASSWORD", "")),
        smtp_host=str(section.get("host") or "smtp.gmail.com"),
        smtp_port=int(section.get("port", 465)),
        imap_host=str(section.get("imap_host") or "imap.gmail.com"),
        imap_port=int(section.get("imap_port", 993)),
        drafts_folder=str(section.get("drafts_folder") or "[Gmail]/Drafts"),
        display_name=str(section.get("display_name") or ""),
    )


def build_message(
    cfg: MailConfig,
    *,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
    reply_to: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    sender = cfg.address
    message["From"] = f"{cfg.display_name} <{sender}>" if cfg.display_name else sender
    message["To"] = to_addr
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=sender.split("@")[-1] or "localhost")
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)

    for path in attachments or []:
        path = Path(path)
        if not path.exists():
            continue
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        message.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
    return message


def send_one(cfg: MailConfig, message: EmailMessage) -> str:
    """Send one message. Returns its Message-ID, raises on failure."""
    context = ssl.create_default_context()
    if cfg.smtp_port == 465:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=30) as client:
            client.login(cfg.address, cfg.password)
            client.send_message(message)
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as client:
            client.ehlo()
            client.starttls(context=context)
            client.login(cfg.address, cfg.password)
            client.send_message(message)
    return str(message["Message-ID"] or "")


def append_draft(cfg: MailConfig, message: EmailMessage) -> str:
    """Save the message to Gmail's Drafts folder so it can be reviewed in the web UI."""
    client = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        client.login(cfg.address, cfg.password)
        internal = imaplib.Time2Internaldate(time.time())
        typ, data = client.append(
            f'"{cfg.drafts_folder}"', "\\Draft", internal, message.as_bytes()
        )
        if typ != "OK":
            raise RuntimeError(f"draft append failed: {typ} {data}")
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return str(message["Message-ID"] or "")


def send_batch(
    cfg: MailConfig,
    drafts: list[dict],
    *,
    daily_limit: int = 20,
    per_company_limit: int = 1,
    delay_seconds: float = 20.0,
    attachments_for=None,
    dry_run: bool = False,
    log=print,
) -> dict:
    """Send stored drafts. Returns a summary with per-job outcomes."""
    summary = {"sent": 0, "failed": 0, "skipped": 0, "errors": []}
    if not cfg.configured and not dry_run:
        return {"error": "SMTP is not configured. Set [gmail].address and JOB_GMAIL_APP_PASSWORD.",
                "sent": 0, "failed": 0, "skipped": 0, "errors": []}

    already = store.sent_today()
    if already >= daily_limit:
        return {"error": f"daily limit reached ({already}/{daily_limit})",
                "sent": 0, "failed": 0, "skipped": 0, "errors": []}

    budget = daily_limit - already
    sent_companies: dict[str, int] = {}

    for draft in drafts:
        if budget <= 0:
            summary["skipped"] += 1
            log("  daily limit reached, remaining drafts left alone")
            break

        company = (draft.get("company") or "").strip()
        if company:
            used = sent_companies.get(company.lower(), 0)
            if used >= per_company_limit or store.company_sent(company) >= per_company_limit:
                summary["skipped"] += 1
                log(f"  skip {company}: per-company limit")
                continue

        attachments = attachments_for(draft) if attachments_for else []
        message = build_message(
            cfg,
            to_addr=draft["to_addr"],
            subject=draft["subject"],
            body=draft["body"],
            attachments=attachments,
        )

        if dry_run:
            summary["skipped"] += 1
            log(f"  would send to {draft['to_addr']} ({draft['subject'][:60]})")
            continue

        try:
            message_id = send_one(cfg, message)
            store.mark_sent(draft["job_id"], draft["kind"], message_id)
            summary["sent"] += 1
            budget -= 1
            if company:
                sent_companies[company.lower()] = sent_companies.get(company.lower(), 0) + 1
            log(f"  sent to {draft['to_addr']} ({draft['subject'][:60]})")
        except Exception as exc:  # noqa: BLE001 - one bad address must not stop the batch
            store.mark_failed(draft["job_id"], draft["kind"], f"{type(exc).__name__}: {exc}")
            summary["failed"] += 1
            summary["errors"].append(f"{draft['job_id']}: {exc}")
            log(f"  FAILED {draft['to_addr']}: {type(exc).__name__}: {exc}")

        if delay_seconds and budget > 0:
            time.sleep(delay_seconds)

    return summary
