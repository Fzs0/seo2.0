"""Copy Claim-only records from the standalone knowledge DB into SEO Workbench.

The source database is read-only from this script's perspective. The target
table must already exist via db/migrations/016_knowledge_claims.sql. Re-running
the script is safe: rows are upserted by their original Claim UUID.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg


CLAIM_COLUMNS = (
    "id",
    "document_id",
    "channel",
    "topic",
    "knowledge_type",
    "statement",
    "conditions",
    "exceptions",
    "recommended_action",
    "confidence",
    "review_status",
    "review_note",
    "reviewed_by",
    "reviewed_at",
    "metadata",
    "quality_status",
    "quality_utility_score",
    "quality_reviewer_confidence",
    "quality_reasons",
    "quality_note",
    "quality_model",
    "quality_prompt_version",
    "quality_reviewed_at",
    "created_at",
    "updated_at",
)


def normalize_database_url(value: str) -> str:
    """Make SQLAlchemy-style URLs acceptable to asyncpg."""

    parts = urlsplit(value)
    scheme = parts.scheme.removesuffix("+asyncpg")
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def json_value(value: Any) -> str:
    """Encode JSONB values explicitly for asyncpg."""

    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def target_values(row: asyncpg.Record) -> tuple[Any, ...]:
    values = [row[column] for column in CLAIM_COLUMNS]
    # The target intentionally keeps only an opaque origin document ID.
    values[1] = row["document_id"]
    for index in (6, 7, 14, 18):
        values[index] = json_value(values[index])
    return tuple(values)


async def migrate(source_url: str, target_url: str) -> dict[str, Any]:
    source = await asyncpg.connect(normalize_database_url(source_url))
    target = await asyncpg.connect(normalize_database_url(target_url))
    try:
        source_rows = await source.fetch(
            """
            SELECT id, document_id, channel, topic, knowledge_type, statement,
                   conditions, exceptions, recommended_action, confidence,
                   review_status, review_note, reviewed_by, reviewed_at,
                   metadata, quality_status, quality_utility_score,
                   quality_reviewer_confidence, quality_reasons, quality_note,
                   quality_model, quality_prompt_version, quality_reviewed_at,
                   created_at, updated_at
              FROM knowledge.claims
             ORDER BY created_at, id
            """
        )

        upsert_sql = """
        INSERT INTO seo_agent.knowledge_claims (
          id, origin_document_id, channel, topic, knowledge_type, statement,
          conditions, exceptions, recommended_action, confidence, review_status,
          review_note, reviewed_by, reviewed_at, metadata, quality_status,
          quality_utility_score, quality_reviewer_confidence, quality_reasons,
          quality_note, quality_model, quality_prompt_version,
          quality_reviewed_at, created_at, updated_at
        ) VALUES (
          $1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11,
          $12, $13, $14, $15::jsonb, $16, $17, $18, $19::jsonb, $20,
          $21, $22, $23, $24, $25
        )
        ON CONFLICT (id) DO UPDATE SET
          origin_document_id = EXCLUDED.origin_document_id,
          channel = EXCLUDED.channel,
          topic = EXCLUDED.topic,
          knowledge_type = EXCLUDED.knowledge_type,
          statement = EXCLUDED.statement,
          conditions = EXCLUDED.conditions,
          exceptions = EXCLUDED.exceptions,
          recommended_action = EXCLUDED.recommended_action,
          confidence = EXCLUDED.confidence,
          review_status = EXCLUDED.review_status,
          review_note = EXCLUDED.review_note,
          reviewed_by = EXCLUDED.reviewed_by,
          reviewed_at = EXCLUDED.reviewed_at,
          metadata = EXCLUDED.metadata,
          quality_status = EXCLUDED.quality_status,
          quality_utility_score = EXCLUDED.quality_utility_score,
          quality_reviewer_confidence = EXCLUDED.quality_reviewer_confidence,
          quality_reasons = EXCLUDED.quality_reasons,
          quality_note = EXCLUDED.quality_note,
          quality_model = EXCLUDED.quality_model,
          quality_prompt_version = EXCLUDED.quality_prompt_version,
          quality_reviewed_at = EXCLUDED.quality_reviewed_at,
          created_at = EXCLUDED.created_at,
          updated_at = EXCLUDED.updated_at
        """

        async with target.transaction():
            await target.executemany(upsert_sql, [target_values(row) for row in source_rows])

        target_counts = await target.fetch(
            """
            SELECT review_status, count(*)::int AS count
              FROM seo_agent.knowledge_claims
             GROUP BY review_status
             ORDER BY review_status
            """
        )
        source_statuses = Counter(row["review_status"] for row in source_rows)
        target_statuses = {row["review_status"]: row["count"] for row in target_counts}
        target_total = await target.fetchval("SELECT count(*)::int FROM seo_agent.knowledge_claims")
        return {
            "source_total": len(source_rows),
            "target_total": target_total,
            "source_by_review_status": dict(sorted(source_statuses.items())),
            "target_by_review_status": dict(sorted(target_statuses.items())),
        }
    finally:
        await source.close()
        await target.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=os.environ.get("KNOWLEDGE_DATABASE_URL"))
    parser.add_argument("--target-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.source_url or not args.target_url:
        parser.error("source and target database URLs are required")
    return args


def main() -> None:
    args = parse_args()
    result = asyncio.run(migrate(args.source_url, args.target_url))
    if result["source_total"] != result["target_total"]:
        raise SystemExit(f"count mismatch after migration: {result}")
    if result["source_by_review_status"] != result["target_by_review_status"]:
        raise SystemExit(f"review status mismatch after migration: {result}")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
