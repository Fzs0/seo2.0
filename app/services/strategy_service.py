"""Read-only access to strategy history.

New editorial decisions are created only through
``autonomous_strategy_orchestrator``.  This module intentionally contains no
candidate ranking, candidate-to-Strategy conversion, planning or Action
creation logic. Historical candidate lineage remains readable on strategy rows,
but the retired candidate archive is no longer exposed as an application API.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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
