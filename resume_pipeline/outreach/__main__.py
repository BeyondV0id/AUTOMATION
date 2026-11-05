"""CLI for application and cold email.

    python -m resume_pipeline.outreach collect --limit 50 --fetch-pages
    python -m resume_pipeline.outreach contacts
    python -m resume_pipeline.outreach draft --limit 10
    python -m resume_pipeline.outreach list
    python -m resume_pipeline.outreach show <job-id>

``collect`` fills the contact list while you scrape. ``draft`` renders a message
and saves it to Gmail Drafts; you press send there. ``send`` exists for the
unattended case but is never the default.

Nothing leaves the machine until a draft is sent, and ``send`` only touches rows
already stored as ``draft``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tomllib
from pathlib import Path

from .. import DATA, RESUMES, ROOT
from . import collect, store
from .compose import OutreachConfig, compose, config_from, config_from_profile, contact_for
from .send import MailConfig, append_draft, build_message, config_from as mail_config_from, send_batch

CONFIG = ROOT / "config" / "outreach.toml"
CONTACTS = ROOT / "config" / "contacts.toml"
PROFILE = DATA / "resume" / "profile.json"


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _merge(base: dict, extra: dict) -> dict:
    """Shallow merge where `extra` wins per section key, not per nested leaf."""
    merged = dict(base)
    for section, values in extra.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section] = {**merged[section], **values}
        else:
            merged[section] = values
    return merged


def load_outreach_config(path: Path | None = None) -> OutreachConfig:
    mapping = _merge(config_from_profile(PROFILE), _load_toml(path or CONFIG))
    return config_from(mapping)


def select_jobs(statuses: list[str], limit: int, min_score: float | None, source: str | None) -> list[dict]:
    placeholders = ",".join("?" for _ in statuses)
    query = (
        "SELECT * FROM jobs WHERE status IN (" + placeholders + ") "
        "AND url IS NOT NULL AND url != ''"
    )
    params: list[object] = list(statuses)
    if min_score is not None:
        query += " AND COALESCE(match_score, 0) >= ?"
        params.append(min_score)
    if source:
        query += " AND source = ?"
        params.append(source)
    query += " ORDER BY COALESCE(match_score, -1) DESC, discovered_at DESC LIMIT ?"
    params.append(limit * 4)  # room for jobs without a contact address

    conn = sqlite3.connect(DATA / "applications.db")
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(query, params)]
    finally:
        conn.close()


def _resume_for(job_id: str | None) -> Path | None:
    if job_id:
        folder = RESUMES / "generated" / "job-specific" / job_id
        if folder.exists():
            pdfs = sorted(folder.rglob("*.pdf"))
            if pdfs:
                return pdfs[0]
    for candidate in (RESUMES / "generated" / "master.pdf", RESUMES / "g_harsha_resume.pdf"):
        if candidate.exists():
            return candidate
    fallback = sorted((DATA / "resume").glob("*.pdf"))
    return fallback[0] if fallback else None


def cmd_collect(args: argparse.Namespace) -> int:
    statuses = [s.strip() for s in args.status.split(",") if s.strip()] if args.status else None
    jobs = collect.select_jobs(
        statuses=statuses,
        min_score=args.min_score,
        source=args.source,
        missing_only=not args.all,
        limit=args.limit,
    )
    if not jobs:
        print("No jobs to scan. Run discovery first, or pass --all to rescan jobs that already have an address.")
        return 0

    print(f"Scanning {len(jobs)} job(s)" + (" including their web pages" if args.fetch_pages else ""))
    summary = collect.collect(jobs, fetch_pages=args.fetch_pages, log=print)
    print(
        f"\nContacts: {summary['contacts']} found "
        f"({summary['from_description']} from descriptions, {summary['from_page']} from pages), "
        f"{summary['already_had']} already had one, {summary['none']} none, {summary['errors']} page errors"
    )
    total = len(store.all_contacts())
    print(f"Contact list: {total} addresses in data/applications.db (table: contacts)")
    if summary["none"] and not args.fetch_pages:
        print("Most postings do not name an address. Re-run with --fetch-pages to check employer contact pages.")
    return 0


def cmd_contacts(args: argparse.Namespace) -> int:
    rows = store.all_contacts()
    if not rows:
        print("No contacts yet. Run: python -m resume_pipeline.outreach collect")
        return 0
    print(f"{'EMAIL':40} {'COMPANY':30} {'SOURCE':22} STATUS")
    print("-" * 110)
    for row in rows[: args.limit]:
        print(
            f"{(row['email'] or '')[:40]:40} {(row['company'] or '')[:30]:30} "
            f"{(row['source'] or '')[:22]:22} {row['status'] or ''}"
        )
    print(f"\n{len(rows)} contacts")
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    cfg = load_outreach_config(args.config)
    if not cfg.configured:
        print(
            "Outreach identity is incomplete.\n"
            "Copy config/outreach.example.toml to config/outreach.toml and set\n"
            "  [outreach.identity] name, email, headline, skills.",
            file=sys.stderr,
        )
        return 2

    contacts = _load_toml(args.contacts)
    statuses = [s.strip() for s in args.status.split(",") if s.strip()]
    jobs = select_jobs(statuses, args.limit, args.min_score, args.source)

    created = skipped = no_contact = bad = 0
    rows: list[tuple[dict, str, str]] = []

    for job in jobs:
        if created >= args.limit:
            break
        address, reason = contact_for(job, contacts)
        if not address:
            no_contact += 1
            if args.verbose:
                print(f"  no contact  {job['company'][:28]:28} — {job['title'][:44]}")
            continue
        try:
            subject, body = compose(job, cfg, address, args.kind)
        except ValueError as exc:
            bad += 1
            print(f"  invalid     {job['company'][:28]:28} — {exc}", file=sys.stderr)
            continue
        if store.save_draft(
            job_id=job["id"],
            kind=args.kind,
            to_addr=address,
            subject=subject,
            body=body,
            force=args.force,
        ):
            created += 1
            rows.append((job, address, subject))
        else:
            skipped += 1

    if not args.db_only and rows:
        mail_cfg = mail_config_from(_load_toml(CONFIG))
        if not mail_cfg.configured:
            print(
                "Gmail drafts: skipped, [gmail].address or JOB_GMAIL_APP_PASSWORD is missing.\n"
                "Drafts are still in the local database; use --db-only to silence this.",
                file=sys.stderr,
            )
        else:
            saved = 0
            for job, address, subject in rows:
                draft = store.existing(job["id"], args.kind)
                if not draft:
                    continue
                attachments: list[Path] = []
                if cfg.attach_resume:
                    found = args.resume or _resume_for(job["id"])
                    if found:
                        attachments = [found]
                message = build_message(
                    mail_cfg,
                    to_addr=address,
                    subject=subject,
                    body=draft["body"],
                    attachments=attachments,
                )
                try:
                    append_draft(mail_cfg, message)
                    saved += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  draft append failed for {job['company']}: {exc}", file=sys.stderr)
            print(f"Gmail drafts: {saved} saved to {mail_cfg.drafts_folder}")

    print(
        f"Drafts: {created} created, {skipped} already existed, "
        f"{no_contact} without a contact address, {bad} invalid"
    )
    if created:
        print("Review with:  python -m resume_pipeline.outreach list")
        print("Send with:    python -m resume_pipeline.outreach send")
    if no_contact:
        print(
            "Add addresses to config/contacts.toml ([by_company] / [by_domain]) "
            "or fill the Contact Email column in data/jobs.xlsx."
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    status = None if args.all else "draft"
    rows = store.rows(status=status, limit=args.limit)
    if not rows:
        print("No drafts. Run: python -m resume_pipeline.outreach draft")
        return 0
    print(f"{'STATUS':8} {'TO':32} {'COMPANY':26} {'SUBJECT'}")
    print("-" * 110)
    for row in rows:
        print(
            f"{(row['status'] or '')[:8]:8} {(row['to_addr'] or '')[:32]:32} "
            f"{(row['company'] or '')[:26]:26} {(row['subject'] or '')[:52]}"
        )
    counts = store.counts()
    print("\n" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    match = [r for r in store.rows(status=None) if r["job_id"] == args.job]
    if not match:
        print(f"No draft for {args.job}", file=sys.stderr)
        return 1
    row = match[0]
    print(f"To:      {row['to_addr']}")
    print(f"Subject: {row['subject']}")
    print(f"Status:  {row['status']}")
    print("-" * 70)
    print(row["body"])
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    cfg = load_outreach_config(args.config)
    mail_cfg = mail_config_from(_load_toml(CONFIG))
    drafts = store.rows(status="draft", limit=args.limit)
    if not drafts:
        print("No drafts to send.")
        return 0

    limit = args.limit or cfg.daily_limit
    drafts = drafts[:limit]

    print(f"About to send {len(drafts)} email(s) from {mail_cfg.address or '<unset>'}:\n")
    for row in drafts:
        print(f"  {row['to_addr'][:38]:38} {row['company'][:24]:24} {row['subject'][:50]}")

    if args.dry_run:
        print("\nDry run: nothing sent.")
        return 0

    if not args.yes:
        print()
        answer = input("Type 'yes' to send: ").strip().lower()
        if answer != "yes":
            print("Cancelled. Nothing was sent.")
            return 1

    def attachments_for(draft: dict) -> list[Path]:
        if not cfg.attach_resume:
            return []
        if args.resume:
            return [args.resume]
        found = _resume_for(draft.get("job_id"))
        return [found] if found else []

    print()
    summary = send_batch(
        mail_cfg,
        drafts,
        daily_limit=limit,
        per_company_limit=cfg.per_company_limit,
        delay_seconds=0 if args.no_delay else cfg.delay_seconds,
        attachments_for=attachments_for,
        dry_run=False,
    )
    if summary.get("error"):
        print(summary["error"], file=sys.stderr)
        return 2

    # A sent application is an applied application.
    from ..sync import store as tracker

    for row in drafts:
        current = tracker.get_job(row["job_id"])
        if current and (current.get("status") or "new") in {"new", "review"}:
            tracker.set_status(row["job_id"], "applied")

    print(
        f"\nSent {summary['sent']}, failed {summary['failed']}, skipped {summary['skipped']}"
    )
    for error in summary.get("errors", [])[:5]:
        print(f"  ! {error}")
    return 0 if not summary["failed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m resume_pipeline.outreach",
        description="Draft and send job emails",
    )
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--contacts", type=Path, default=CONTACTS)
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="render drafts and save them to Gmail")
    draft.add_argument("--limit", type=int, default=10)
    draft.add_argument("--status", default="new,review", help="comma-separated tracker statuses")
    draft.add_argument("--kind", default="cold", choices=["cold", "application", "followup"])
    draft.add_argument("--min-score", type=float)
    draft.add_argument("--source")
    draft.add_argument("--resume", type=Path, help="attach this PDF instead of the default")
    draft.add_argument("--force", action="store_true", help="replace existing drafts")
    draft.add_argument("--db-only", action="store_true", help="do not write to Gmail Drafts")
    draft.add_argument("--verbose", action="store_true")
    draft.set_defaults(func=cmd_draft)

    collect_parser = sub.add_parser("collect", help="find company contact addresses")
    collect_parser.add_argument("--limit", type=int)
    collect_parser.add_argument("--status", default="", help="comma-separated tracker statuses (default: all)")
    collect_parser.add_argument("--min-score", type=float)
    collect_parser.add_argument("--source")
    collect_parser.add_argument("--all", action="store_true", help="rescan jobs that already have an address")
    collect_parser.add_argument(
        "--fetch-pages",
        action="store_true",
        help="also read the job page and the employer's contact/careers page",
    )
    collect_parser.set_defaults(func=cmd_collect)

    contacts = sub.add_parser("contacts", help="list collected addresses")
    contacts.add_argument("--limit", type=int, default=80)
    contacts.set_defaults(func=cmd_contacts)

    listing = sub.add_parser("list", help="show drafts and sent mail")
    listing.add_argument("--all", action="store_true", help="include sent and failed")
    listing.add_argument("--limit", type=int, default=40)
    listing.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="print one draft in full")
    show.add_argument("job")
    show.set_defaults(func=cmd_show)

    send = sub.add_parser("send", help="send stored drafts")
    send.add_argument("--limit", type=int, default=None)
    send.add_argument("--resume", type=Path)
    send.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--no-delay", action="store_true")
    send.set_defaults(func=cmd_send)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
