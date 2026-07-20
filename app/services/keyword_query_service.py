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
    ai_analyzed: bool | None = None,
    intent: str | None = None,
    serp_feature: str | None = None,
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
    if ai_analyzed is True:
        where += " AND COALESCE(ai_review, '{}'::jsonb) ? 'strategy'"
    elif ai_analyzed is False:
        where += " AND NOT (COALESCE(ai_review, '{}'::jsonb) ? 'strategy')"
    if intent:
        where += " AND intent = :intent"
        params["intent"] = intent
    if serp_feature:
        where += " AND COALESCE(serp_features, '[]'::jsonb) ? CAST(:serp_feature AS text)"
        params["serp_feature"] = serp_feature

    count_sql = text(f"SELECT count(*) AS n FROM seo_agent.keywords {where}")
    total = (await session.execute(count_sql, params)).scalar_one()

    list_sql = text(
        f"""
        SELECT id, keyword, normalized_keyword, source, semrush_database, market,
               language_code, google_gl, google_hl, volume, kd, cpc, intent, serp_features,
               trend, trend_data, pkd, potential_traffic, competitive_density, serp_results,
               keyword_type, preflight_status, preflight_reason, business_id, source_batch_id,
               topic_cluster, topic_cluster_id, cluster_role, cluster_size, pillar_keyword,
               seed_keyword, page_group,
               raw #>> '{{_strategy_builder,cluster_validation,status}}' AS cluster_validation_status,
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


async def build_analysis_queue(session: AsyncSession, *, limit: int = 100) -> dict[str, Any]:
    """Pick a small, explainable first batch: one representative per topic cluster."""
    limit = max(1, min(limit, 200))
    base = (
        "status IN ('imported', 'analyzed', 'planned') "
        "AND COALESCE(preflight_status, 'ready') = 'ready' "
        "AND COALESCE(priority, '') <> 'Hold' "
        "AND NOT (COALESCE(ai_review, '{}'::jsonb) ?| ARRAY['strategy', 'reservation'])"
    )
    total = int(
        (
            await session.execute(
                text(
                    "SELECT count(DISTINCT COALESCE(topic_cluster_id, topic_cluster, id::text)) "
                    f"FROM seo_agent.keywords WHERE {base} AND cluster_role IN ('pillar', 'standalone') AND volume > 0"
                )
            )
        ).scalar_one()
        or 0
    )
    columns = """
        SELECT id, keyword, volume, kd, intent, keyword_type, priority, score,
               topic_cluster, topic_cluster_id, cluster_role, cluster_size, pillar_keyword,
               market, language_code, assigned_site_label
          FROM seo_agent.keywords
         WHERE {where}
         ORDER BY score DESC NULLS LAST, volume DESC NULLS LAST, kd ASC NULLS LAST
         LIMIT :fetch_limit
    """
    fetch_limit = max(limit * 4, 200)
    representative = "AND cluster_role IN ('pillar', 'standalone')"
    primary_where = f"{base} {representative} AND priority IN ('P0', 'P1') AND kd <= 40 AND volume BETWEEN 100 AND 5000 AND intent IN ('commercial', 'transactional')"
    question_where = f"{base} {representative} AND priority IN ('P0', 'P1') AND kd <= 40 AND volume BETWEEN 50 AND 3000 AND keyword_type = 'question'"
    fallback_where = f"{base} {representative} AND volume > 0"

    async def fetch(where: str) -> list[dict[str, Any]]:
        result = await session.execute(text(columns.format(where=where)), {"fetch_limit": fetch_limit})
        return [dict(row) for row in result.mappings().all()]

    primary = await fetch(primary_where)
    questions = await fetch(question_where)
    fallback = await fetch(fallback_where)
    selected: list[dict[str, Any]] = []
    seen_clusters: set[str] = set()
    seen_ids: set[str] = set()

    def add(rows: list[dict[str, Any]], quota: int) -> None:
        added = 0
        for row in rows:
            if len(selected) >= limit:
                break
            cluster = str(row.get("topic_cluster_id") or row.get("topic_cluster") or row["id"])
            if cluster in seen_clusters or str(row["id"]) in seen_ids:
                continue
            selected.append(row)
            seen_clusters.add(cluster)
            seen_ids.add(str(row["id"]))
            added += 1
            if len(selected) >= limit or added >= quota:
                break

    commercial_quota = max(1, round(limit * 0.7))
    add(primary, commercial_quota)
    add(questions, limit - min(len(selected), commercial_quota))
    add(fallback, limit)
    mix = {
        "commercial": sum(1 for row in selected if str(row.get("intent") or "") in {"commercial", "transactional"}),
        "question": sum(1 for row in selected if row.get("keyword_type") == "question"),
        "clusters": len({str(row.get("topic_cluster_id") or row.get("topic_cluster") or row["id"]) for row in selected}),
    }
    return {
        "items": selected[:limit],
        "totalCandidates": total,
        "selected": min(len(selected), limit),
        "mix": mix,
        "message": "每个主题先选 1 个代表词；优先商业/交易意图，再补充问题型长尾词。",
    }


async def get_keyword(session: AsyncSession, keyword_id: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, keyword, normalized_keyword, source, semrush_database, market, "
            "language_code, google_gl, google_hl, volume, kd, cpc, intent, serp_features, trend, trend_data, pkd, "
            "potential_traffic, competitive_density, serp_results, keyword_type, preflight_status, preflight_reason, "
            "business_id, source_batch_id, topic_cluster, topic_cluster_id, cluster_role, cluster_size, pillar_keyword, seed_keyword, page_group, "
            "assigned_site_id, assigned_site_label, page_type, page_role, "
            "target_asset_url, asset_status, content_action, priority, score, "
            "status, reason, ai_review, imported_at, created_at, updated_at "
            "FROM seo_agent.keywords WHERE id = :id"
        ),
        {"id": keyword_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None
