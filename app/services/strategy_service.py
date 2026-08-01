"""Read-only access to strategy history and the retired candidate archive.

New editorial decisions are created only through
``autonomous_strategy_orchestrator``.  This module intentionally contains no
candidate ranking, candidate-to-Strategy conversion, planning or Action
creation logic.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_strategy_candidates(
    session: AsyncSession,
    *,
    business_id: str,
    page: int = 1,
    limit: int = 50,
    status: str | None = None,
) -> dict[str, Any]:
    """Return historical candidate rows without changing or executing them."""
    page = max(1, page)
    limit = max(1, min(limit, 200))
    status_filter = (
        status
        if status in {"available", "selected", "hold", "executed"}
        else None
    )
    rows = await session.execute(
        text(
            """
            WITH latest AS (
                SELECT id::text AS id
                  FROM seo_agent.tasks
                 WHERE task_type='review'
                   AND payload->>'kind'='strategy_analysis_batch'
                   AND payload->>'business_id'=:business_id
                   AND status='done'
                 ORDER BY created_at DESC
                 LIMIT 1
            ), candidates AS (
                SELECT t.id, t.status, t.priority, t.score, t.site_id,
                       s.name AS site_name, t.keyword_id, t.post_id,
                       t.article_id, t.title, t.decision,
                       t.payload->>'analysis_batch_id' AS analysis_batch_id,
                       t.created_at, t.updated_at,
                       CASE
                         WHEN EXISTS (
                           SELECT 1
                             FROM seo_agent.tasks strategy
                            WHERE strategy.task_type='review'
                              AND strategy.payload->>'kind'='seo_strategy'
                              AND strategy.payload->>'candidate_id'=t.id::text
                              AND (
                                  strategy.status='done'
                                  OR strategy.decision ? 'execution_task_id'
                              )
                         ) THEN 'executed'
                         WHEN t.decision->>'strategy_type'='hold'
                              OR t.priority='Hold' THEN 'hold'
                         WHEN EXISTS (
                           SELECT 1
                             FROM seo_agent.tasks strategy
                            WHERE strategy.task_type='review'
                              AND strategy.status='queued'
                              AND strategy.payload->>'kind'='seo_strategy'
                              AND strategy.payload->>'candidate_id'=t.id::text
                         ) THEN 'selected'
                         ELSE 'available'
                       END AS candidate_status
                  FROM seo_agent.tasks t
                  JOIN seo_agent.sites s ON s.id=t.site_id
                 WHERE t.task_type='review'
                   AND t.payload->>'kind'='strategy_candidate'
                   AND t.payload->>'business_id'=:business_id
                   AND t.payload->>'analysis_batch_id'=(SELECT id FROM latest)
            )
            SELECT *, count(*) OVER() AS total
              FROM candidates
             WHERE CAST(:candidate_status AS text) IS NULL
                OR candidate_status=CAST(:candidate_status AS text)
             ORDER BY score DESC NULLS LAST, created_at DESC
             LIMIT :limit OFFSET :offset
            """
        ),
        {
            "business_id": business_id,
            "candidate_status": status_filter,
            "limit": limit,
            "offset": (page - 1) * limit,
        },
    )
    mapped = [dict(row) for row in rows.mappings().all()]
    total = int(mapped[0].pop("total")) if mapped else 0
    items = []
    for row in mapped:
        candidate_status = row["candidate_status"]
        item = _strategy_row(row)
        item["candidate_status"] = candidate_status
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "analysis_batch_id": (
            items[0].get("analysis_batch_id") if items else None
        ),
        "read_only": True,
        "retired_source": True,
    }


async def list_strategies(
    session: AsyncSession,
    *,
    status: str | None = "pending",
    limit: int = 50,
    site_id: str | None = None,
    search: str | None = None,
    strategy_type: str | None = None,
    priority: str | None = None,
    evidence_level: str | None = None,
    business_id: str | None = None,
) -> list[dict[str, Any]]:
    status_sql = {
        "pending": "queued",
        "approved": "done",
        "rejected": "canceled",
    }.get(status or "")
    where = (
        "WHERE t.task_type='review' "
        "AND t.payload->>'kind'='seo_strategy'"
    )
    params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
    if status_sql:
        where += " AND t.status=:status"
        params["status"] = status_sql
    if business_id:
        where += (
            " AND t.payload->>'business_id'=:business_id "
            "AND s.business_id=:business_id"
        )
        params["business_id"] = business_id
    if site_id:
        where += " AND t.site_id=CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    if search:
        where += (
            " AND (t.title ILIKE :search "
            "OR t.decision->>'query' ILIKE :search)"
        )
        params["search"] = f"%{search.strip()}%"
    if strategy_type:
        where += " AND t.decision->>'strategy_type'=:strategy_type"
        params["strategy_type"] = strategy_type
    if priority:
        where += " AND t.priority=:priority"
        params["priority"] = priority
    if evidence_level:
        where += " AND t.decision->>'evidence_level'=:evidence_level"
        params["evidence_level"] = evidence_level
    rows = await session.execute(
        text(
            f"""
            SELECT t.id, t.status, t.priority, t.score, t.site_id,
                   s.name AS site_name, t.keyword_id, t.post_id, t.article_id,
                   t.title, t.decision, t.created_at, t.updated_at,
                   t.payload->>'candidate_id' AS candidate_id,
                   t.payload->>'option_id' AS option_id,
                   t.payload->>'option_origin' AS option_origin,
                   t.payload->>'plan_id' AS plan_id,
                   e.status AS execution_status,
                   e.decision->>'current_stage' AS execution_stage,
                   e.logs AS execution_logs,
                   e.error_message AS execution_error,
                   e.started_at AS execution_started_at,
                   e.finished_at AS execution_finished_at,
                   e.run_after AS execution_run_after,
                   (e.payload->>'auto_publish')::boolean AS auto_publish
              FROM seo_agent.tasks t
              LEFT JOIN seo_agent.sites s ON s.id=t.site_id
              LEFT JOIN seo_agent.tasks e
                ON e.id=CAST(t.decision->>'execution_task_id' AS uuid)
               AND e.task_type IN ('new_article', 'update_article')
              {where}
             ORDER BY t.score DESC NULLS LAST, t.created_at DESC
             LIMIT :limit
            """
        ),
        params,
    )
    return [_strategy_row(dict(row)) for row in rows.mappings().all()]


def _strategy_row(row: dict[str, Any]) -> dict[str, Any]:
    decision = row.pop("decision") or {}
    return {
        **row,
        **decision,
        "status": {
            "queued": "pending",
            "done": "approved",
            "canceled": "rejected",
        }.get(row.get("status"), row.get("status")),
    }
