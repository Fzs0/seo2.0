"""Export every column from seo_agent.knowledge_claims to JSON and CSV."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import asyncpg


def normalize_database_url(value: str) -> str:
    parts = urlsplit(value)
    scheme = parts.scheme.removesuffix("+asyncpg")
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return value


def csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(json_safe(value), ensure_ascii=False, separators=(",", ":"))
    return str(json_safe(value))


async def export(database_url: str, output_dir: Path) -> dict[str, Any]:
    connection = await asyncpg.connect(normalize_database_url(database_url))
    try:
        rows = await connection.fetch(
            """
            SELECT *
              FROM seo_agent.knowledge_claims
             ORDER BY created_at, id
            """
        )
    finally:
        await connection.close()

    columns = list(rows[0].keys()) if rows else []
    records = [{column: json_safe(row[column]) for column in columns} for row in rows]
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "knowledge_claims.json"
    csv_path = output_dir / "knowledge_claims.csv"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(records, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        for record in records:
            writer.writerow({column: csv_value(record[column]) for column in columns})

    by_status: dict[str, int] = {}
    status_index = columns.index("review_status") if "review_status" in columns else None
    if status_index is not None:
        for row in rows:
            status = row["review_status"]
            by_status[status] = by_status.get(status, 0) + 1
    return {
        "rows": len(records),
        "columns": columns,
        "review_status": dict(sorted(by_status.items())),
        "json": str(json_path),
        "csv": str(csv_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--output-dir", default="exports/knowledge_claims_2026-07-18")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")
    result = asyncio.run(export(args.database_url, Path(args.output_dir)))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
