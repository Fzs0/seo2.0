"""Backfill stable pillar/cluster metadata for existing imported keywords.

Preview:  python scripts/backfill_keyword_clusters.py
Execute:  python scripts/backfill_keyword_clusters.py --execute --confirm BACKFILL_KEYWORD_CLUSTERS
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal
from app.engine.topic_cluster import assign_topic_clusters

CONFIRMATION = "BACKFILL_KEYWORD_CLUSTERS"


async def main(execute: bool) -> None:
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT id, keyword, topic_cluster, seed_keyword, page_group,
                       priority, volume, kd, intent, keyword_type
                  FROM seo_agent.keywords
                """
            )
        )
        rows = [dict(row) for row in result.mappings().all()]
        clustered = assign_topic_clusters(rows)
        sizes: dict[str, int] = {}
        for row in clustered:
            sizes[str(row.get("topicClusterId"))] = int(row.get("clusterSize") or 0)
        print(f"keywords={len(rows)} clusters={len(sizes)}")
        if not execute:
            print("preview only; use --execute --confirm BACKFILL_KEYWORD_CLUSTERS to write")
            return
        await session.execute(
            text(
                """
                UPDATE seo_agent.keywords
                   SET topic_cluster = :topic_cluster,
                       topic_cluster_id = :topic_cluster_id,
                       cluster_role = :cluster_role,
                       cluster_size = :cluster_size,
                       pillar_keyword = :pillar_keyword,
                       updated_at = now()
                 WHERE id = :id
                """
            ),
            [
                {
                    "id": row["id"],
                    "topic_cluster": row.get("topicCluster"),
                    "topic_cluster_id": row.get("topicClusterId"),
                    "cluster_role": row.get("clusterRole"),
                    "cluster_size": row.get("clusterSize"),
                    "pillar_keyword": row.get("pillarKeyword"),
                }
                for row in clustered
            ],
        )
        await session.commit()
        print("updated", len(clustered))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.execute and args.confirm != CONFIRMATION:
        raise SystemExit(f"执行写入需要 --confirm {CONFIRMATION}")
    asyncio.run(main(args.execute))
