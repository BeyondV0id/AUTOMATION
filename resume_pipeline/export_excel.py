"""Export tracked jobs to data/jobs.xlsx.

The sheet is the human interface: read it in Excel, change the Status or Notes
column, and the next run keeps your edits.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from . import DATA

DB = DATA / "applications.db"
OUTPUT = DATA / "jobs.xlsx"

HEADERS = [
    ("Match", 8),
    ("Company", 26),
    ("Title", 44),
    ("Location", 26),
    ("Source", 12),
    ("Status", 14),
    ("Notes", 30),
    ("Apply", 10),
    ("Discovered", 12),
    ("Why it matches", 46),
    ("Missing skills", 28),
    ("Job ID", 40),
]

STATUS_OPTIONS = ["new", "review", "applied", "interview", "offer", "rejected"]


def read_rows(limit: int | None, min_score: float | None) -> list[sqlite3.Row]:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    query = (
        "SELECT j.id, j.company, j.title, j.location, j.source, j.status, j.url, "
        "j.discovered_at, j.match_score, j.match_reason, j.missing_skills, a.notes "
        "FROM jobs j LEFT JOIN applications a ON a.job_id = j.id "
    )
    params: list[object] = []
    if min_score is not None:
        query += "WHERE j.match_score IS NOT NULL AND j.match_score >= ? "
        params.append(min_score)
    query += "ORDER BY COALESCE(j.match_score, -1) DESC, j.discovered_at DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    rows = connection.execute(query, params).fetchall()
    connection.close()
    return rows


def write_workbook(rows: list[sqlite3.Row], output: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Jobs"

    header_fill = PatternFill("solid", fgColor="1F2937")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for column, (name, width) in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=1, column=column, value=name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")
        cell.border = border
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"

    high = PatternFill("solid", fgColor="DCFCE7")
    mid = PatternFill("solid", fgColor="FEF9C3")
    low = PatternFill("solid", fgColor="F3F4F6")

    for index, row in enumerate(rows, start=2):
        score = row["match_score"]
        discovered = (row["discovered_at"] or "")[:10]
        values = [
            round(score) if score is not None else None,
            row["company"],
            row["title"],
            row["location"],
            row["source"],
            row["status"] or "new",
            row["notes"] or "",
            "open",
            discovered,
            row["match_reason"] or "",
            row["missing_skills"] or "",
            row["id"],
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=index, column=column, value=value)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=column in (3, 4, 10, 11))
        link = sheet.cell(row=index, column=8)
        if row["url"]:
            link.hyperlink = row["url"]
            link.font = Font(color="2563EB", underline="single")
        if score is None:
            fill = low
        elif score >= 70:
            fill = high
        elif score >= 45:
            fill = mid
        else:
            fill = low
        sheet.cell(row=index, column=1).fill = fill

    validation = DataValidation(type="list", formula1='"' + ",".join(STATUS_OPTIONS) + '"', allow_blank=True)
    sheet.add_data_validation(validation)
    if rows:
        validation.add(f"F2:F{len(rows) + 1}")

    sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{max(len(rows) + 1, 2)}"
    id_column = get_column_letter(len(HEADERS))
    sheet.column_dimensions[id_column].hidden = True
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Export tracked jobs to an Excel sheet")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=None)
    args = parser.parse_args()

    rows = read_rows(args.limit, args.min_score)
    path = write_workbook(rows, args.output)
    generated = datetime.now().strftime("%H:%M")
    print(f"{len(rows)} rows -> {path} (generated {generated})")


if __name__ == "__main__":
    main()
