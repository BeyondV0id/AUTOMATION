"""Tracking sync: Gmail, Notion, Excel.

    python -m resume_pipeline.sync --all
    python -m resume_pipeline.sync --gmail --excel
    python -m resume_pipeline.sync --notion --dry-run
    python -m resume_pipeline.sync --gmail --eml-dir samples/emails
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import ROOT
from . import excel, gmail, notion, store

CONFIG = ROOT / "config" / "sync.toml"


def load_config() -> dict:
    if not CONFIG.exists():
        return {}
    import tomllib

    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def _gmail_section(result: dict) -> None:
    if result.get("error"):
        print(f"Gmail: {result['error']}")
        return
    print(
        f"Gmail: {result['messages']} messages, {result['already_seen']} already seen, "
        f"{result['changed']} status changes, {result['suggested']} suggestions, "
        f"{result['unmatched']} unmatched, {result['unknown']} not application mail"
    )
    for event in result.get("events", []):
        if not event["changed"]:
            continue
        job = event["job"]
        print(
            f"  {job['company']} — {job['title'][:50]}: "
            f"{event['previous']} -> {event['proposed']} "
            f"({event['intent'].name} {event['intent'].confidence:.2f})"
        )


def _notion_section(result: dict) -> None:
    if result.get("error"):
        print(f"Notion: {result['error']}")
        return
    print(
        f"Notion: {result['created']} created, {result['updated']} updated, "
        f"{result['skipped']} unchanged, {result['errors']} errors"
    )
    for message in result.get("messages", [])[:5]:
        print(f"  ! {message}")
    if result.get("sample"):
        print(f"  dry run sample: {result['sample']['job']}")
        import json

        print("  " + json.dumps(result["sample"]["properties"], indent=2)[:1200].replace("\n", "\n  "))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m resume_pipeline.sync", description=__doc__)
    parser.add_argument("--gmail", action="store_true", help="read application emails and update statuses")
    parser.add_argument("--notion", action="store_true", help="publish tracked jobs to Notion")
    parser.add_argument("--excel", action="store_true", help="import Excel edits, then rewrite the sheet")
    parser.add_argument("--excel-import", action="store_true", help="read Status/Notes edits back into SQLite")
    parser.add_argument("--excel-export", action="store_true", help="rewrite data/jobs.xlsx from SQLite")
    parser.add_argument("--all", action="store_true", help="excel import, gmail, notion, excel export")
    parser.add_argument("--dry-run", action="store_true", help="classify and plan, change nothing")
    parser.add_argument("--apply", action="store_true", help="allow writes when --eml-dir is used")
    parser.add_argument("--eml-dir", type=Path, help="read .eml files instead of connecting to Gmail")
    parser.add_argument("--limit", type=int, help="max jobs to publish to Notion")
    args = parser.parse_args(argv)

    any_flag = any([args.gmail, args.notion, args.excel, args.excel_import, args.excel_export, args.all])
    do_all = args.all or not any_flag
    do_gmail = do_all or args.gmail
    do_notion = do_all or args.notion
    do_import = do_all or args.excel or args.excel_import
    do_export = do_all or args.excel or args.excel_export

    dry_run = args.dry_run or (bool(args.eml_dir) and not args.apply)
    config = load_config()
    failures = 0

    if do_import:
        result = excel.import_edits()
        if result.get("error"):
            print(f"Excel import: {result['error']}")
            failures += 1
        else:
            print(
                f"Excel import: {result['status_changes']} status edits, "
                f"{result['note_changes']} note edits"
                + (f", {result.get('contact_changes', 0)} contact edits" if result.get("contact_changes") else "")
                + (f", {len(result['missing'])} rows not matched" if result.get("missing") else "")
            )

    if do_gmail:
        cfg = gmail.config_from(config)
        if not cfg.configured:
            print(
                "Gmail: not configured. Add [gmail].address to config/sync.toml "
                "and set JOB_GMAIL_APP_PASSWORD."
            )
            failures += 1
        elif dry_run and not args.eml_dir:
            messages = gmail.fetch_imap(cfg)
            from .intent import classify
            from .match_email import best_job

            jobs = store.all_jobs()
            print(f"Gmail dry run: {len(messages)} messages")
            for msg in messages:
                intent = classify(msg["subject"], msg["body"], msg["sender"])
                if not intent.known:
                    continue
                match = best_job(msg["subject"], msg["body"], msg["sender"], jobs, cfg.match_threshold)
                if match.job:
                    print(
                        f"  {msg['subject'][:60]} -> {match.job['company']} "
                        f"({intent.name} {intent.confidence:.2f}, match {match.score:.2f})"
                    )
        else:
            try:
                result = gmail.sync(cfg, dry_run=dry_run, eml_dir=args.eml_dir)
                _gmail_section(result)
            except Exception as exc:  # noqa: BLE001 - login/network errors must be readable
                print(f"Gmail: {type(exc).__name__}: {exc}")
                failures += 1

    if do_notion:
        cfg = notion.config_from(config)
        try:
            result = notion.publish(cfg, dry_run=dry_run, limit=args.limit)
            _notion_section(result)
            if result.get("error"):
                failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"Notion: {type(exc).__name__}: {exc}")
            failures += 1

    if do_export:
        from ..export_excel import OUTPUT, read_rows, write_workbook

        rows = read_rows(None, None)
        write_workbook(rows, OUTPUT)
        print(f"Excel export: {len(rows)} rows -> {OUTPUT}")

    counts = store.status_counts()
    print("Tracker: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
