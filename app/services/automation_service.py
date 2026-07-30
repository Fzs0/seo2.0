"""Read-only execution history and scheduled effect observation.

Strategy execution no longer lives here. All external SEO writes must pass
through ``Strategy Run -> Formal Plan -> Strategy Action``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_effect_service import process_due_effects


async def run_effect_checks_once(
    session: AsyncSession, batch_size: int = 20
) -> dict[str, Any]:
    """Process due local effect records without claiming or writing SEO actions."""
    return await process_due_effects(session, limit=max(1, min(batch_size, 100)))


async def get_execution_status(
    session: AsyncSession, business_id: str | None = None
) -> dict[str, Any]:
    """Return historical article execution ledger data; this never starts work."""
    counts = {
        row["status"]: int(row["count"])
        for row in (
            await session.execute(
                text(
                    "SELECT t.status, count(*) AS count FROM seo_agent.tasks t "
                    "LEFT JOIN seo_agent.sites s ON s.id = t.site_id "
                    "WHERE t.task_type IN ('new_article', 'update_article') "
                    "AND (CAST(:business_id AS text) IS NULL OR s.business_id = :business_id) "
                    "GROUP BY t.status"
                ),
                {"business_id": business_id},
            )
        ).mappings().all()
    }
    recent = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT t.id::text, t.task_type, t.title, t.status, "
                    "t.error_message, t.finished_at "
                    "FROM seo_agent.tasks t LEFT JOIN seo_agent.sites s ON s.id = t.site_id "
                    "WHERE t.task_type IN ('new_article', 'update_article') "
                    "AND t.status IN ('done', 'failed', 'blocked', 'canceled') "
                    "AND (CAST(:business_id AS text) IS NULL OR s.business_id = :business_id) "
                    "ORDER BY t.finished_at DESC NULLS LAST LIMIT 10"
                ),
                {"business_id": business_id},
            )
        ).mappings().all()
    ]
    return {
        "executor": "strategy_actions",
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0) + counts.get("blocked", 0),
        "recent": recent,
    }


__all__ = ["get_execution_status", "run_effect_checks_once"]
