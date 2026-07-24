"""SERP 快照持久化：复用现有 ``seo_agent.serp_snapshots`` 表。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.serpapi import fetch_google_serp


async def fetch_and_save_serp_snapshot(
    session: AsyncSession,
    *,
    keyword: str,
    gl: str = "us",
    hl: str = "en",
) -> dict[str, Any]:
    """调用 SerpApi 并把一次成功请求保存为无关键词库依赖的快照。

    ``keyword_id`` 保持为空；需要使用快照的文章通过现有
    ``articles.serp_snapshot_id`` 建立关联。
    """
    query = " ".join(keyword.split())
    data = await fetch_google_serp(query, gl=gl, hl=hl)
    result: dict[str, Any] = {**data, "snapshot_id": None, "source": "serpapi"}

    if not data.get("configured") or data.get("status") == "fetch-failed":
        return result

    saved = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.serp_snapshots
                  (keyword, google_gl, google_hl, top_result_count,
                   organic_results, related_questions, related_searches, raw)
                VALUES
                  (:keyword, :gl, :hl, :count,
                   CAST(:organic AS jsonb), CAST(:paa AS jsonb),
                   CAST(:related AS jsonb), CAST(:raw AS jsonb))
                RETURNING id, requested_at
                """
            ),
            {
                "keyword": query,
                "gl": gl,
                "hl": hl,
                "count": len(data.get("organic_results") or []),
                "organic": json.dumps(data.get("organic_results") or [], ensure_ascii=False),
                "paa": json.dumps(data.get("related_questions") or [], ensure_ascii=False),
                "related": json.dumps(data.get("related_searches") or [], ensure_ascii=False),
                "raw": json.dumps(data, ensure_ascii=False),
            },
        )
    ).mappings().first()
    await session.commit()

    if saved:
        result["snapshot_id"] = str(saved["id"])
        result["requested_at"] = saved["requested_at"].isoformat()
    return result


__all__ = ["fetch_and_save_serp_snapshot"]
