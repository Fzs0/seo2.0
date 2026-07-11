"""关键词服务：列出 + 筛选 + 按 id 取。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_keywords(
    session: AsyncSession,
    *,
    status: str | None = None,
    priority: str | None = None,
    assigned_site_id: str | None = None,
    min_score: float | None = None,
    market: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """返回 { items: [...], total: int, limit, offset }。"""
    where = "WHERE 1=1"
    params: dict[str, Any] = {"limit": max(1, min(limit, 500)), "offset": max(0, offset)}
    if status:
        where += " AND status = :status"
        params["status"] = status
    if priority:
        where += " AND priority = :priority"
        params["priority"] = priority
    if assigned_site_id:
        where += " AND assigned_site_id = :assigned_site_id"
        params["assigned_site_id"] = assigned_site_id
    if min_score is not None:
        where += " AND score >= :min_score"
        params["min_score"] = float(min_score)
    if market:
        where += " AND market = :market"
        params["market"] = market
    if search:
        where += " AND keyword ILIKE :search"
        params["search"] = f"%{search}%"

    count_sql = text(f"SELECT count(*) AS n FROM seo_agent.keywords {where}")
    total = (await session.execute(count_sql, params)).scalar_one()

    list_sql = text(
        f"""
        SELECT id, keyword, normalized_keyword, source, semrush_database, market,
               language_code, google_gl, google_hl, volume, kd, cpc, intent,
               topic_cluster, seed_keyword, page_group,
               assigned_site_id, assigned_site_label, page_type, page_role,
               target_asset_url, asset_status, content_action, priority, score,
               status, reason, ai_review, imported_at, created_at, updated_at
          FROM seo_agent.keywords
          {where}
         ORDER BY score DESC NULLS LAST, volume DESC NULLS LAST
         LIMIT :limit OFFSET :offset
        """
    )
    result = await session.execute(list_sql, params)
    items = [dict(r) for r in result.mappings().all()]
    return {"items": items, "total": int(total or 0), "limit": params["limit"], "offset": params["offset"]}


async def get_keyword(session: AsyncSession, keyword_id: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, keyword, normalized_keyword, source, semrush_database, market, "
            "language_code, google_gl, google_hl, volume, kd, cpc, intent, topic_cluster, seed_keyword, page_group, "
            "assigned_site_id, assigned_site_label, page_type, page_role, "
            "target_asset_url, asset_status, content_action, priority, score, "
            "status, reason, ai_review, imported_at, created_at, updated_at "
            "FROM seo_agent.keywords WHERE id = :id"
        ),
        {"id": keyword_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None
