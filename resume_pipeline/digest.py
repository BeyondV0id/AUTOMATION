from __future__ import annotations

import argparse
import sqlite3
from datetime import date

from . import DATA, REPORTS


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a daily local Markdown job digest")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--min-score", type=float, default=None)
    args = parser.parse_args()

    connection = sqlite3.connect(DATA / "applications.db")
    connection.row_factory = sqlite3.Row
    query = (
        "SELECT company, title, location, url, source, match_score, missing_skills "
        "FROM jobs WHERE status IN ('new', 'review') "
    )
    params: list[object] = []
    if args.min_score is not None:
        query += "AND match_score IS NOT NULL AND match_score >= ? "
        params.append(args.min_score)
    query += "ORDER BY COALESCE(match_score, -1) DESC, discovered_at DESC LIMIT ?"
    params.append(args.limit)
    rows = connection.execute(query, params).fetchall()
    connection.close()

    lines = [f"# Job Digest - {date.today().isoformat()}", ""]
    for row in rows:
        score = f"{round(row['match_score'])}% match" if row["match_score"] is not None else "unscored"
        lines.append(f"- **{score}** — **{row['company']}** — {row['title']} ({row['location'] or 'Location not listed'})")
        lines.append(f"  {row['url']}")
        if row["missing_skills"]:
            lines.append(f"  Missing: {row['missing_skills']}")
    if not rows:
        lines.append("No new matching jobs.")

    REPORTS.mkdir(exist_ok=True)
    output = REPORTS / f"job-digest-{date.today().isoformat()}.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
