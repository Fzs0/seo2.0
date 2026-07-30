"""Authoritative lineage checks for persisted Strategy Plans and Actions."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def validate_strategy_action_lineage(
    session: AsyncSession,
    action: dict[str, Any],
    *,
    lock: bool = False,
) -> dict[str, Any]:
    """Require an Action to originate from an executable formal Strategy."""
    required = (
        "run_id",
        "plan_id",
        "source_strategy_task_id",
        "business_id",
        "site_id",
        "action_type",
    )
    missing = [key for key in required if not action.get(key)]
    if missing:
        raise ValueError(f"strategy action lineage is incomplete: {missing}")
    row = (
        await session.execute(
            text(
                """
                SELECT strategy.id::text AS strategy_task_id,
                       strategy.status AS strategy_status,
                       strategy.decision AS strategy_decision,
                       strategy.payload AS strategy_payload,
                       plan.id::text AS plan_id,
                       plan.status AS plan_status,
                       plan.decision AS plan_decision,
                       run.id::text AS run_id,
                       run.decision->>'status' AS run_status
                  FROM seo_agent.tasks strategy
                  JOIN seo_agent.tasks plan
                    ON plan.id=CAST(strategy.payload->>'plan_id' AS uuid)
                   AND plan.task_type='review'
                   AND plan.payload->>'kind'='strategy_plan'
                  JOIN seo_agent.tasks run
                    ON run.id=CAST(:run_id AS uuid)
                   AND run.task_type='review'
                   AND run.payload->>'kind'='strategy_run'
                 WHERE strategy.id=CAST(:strategy_id AS uuid)
                   AND strategy.task_type='review'
                   AND strategy.payload->>'kind'='seo_strategy'
                   AND strategy.status IN ('queued','done')
                   AND strategy.site_id=CAST(:site_id AS uuid)
                   AND strategy.payload->>'business_id'=:business_id
                   AND strategy.payload->>'strategy_run_id'=CAST(:run_id AS text)
                   AND strategy.payload->>'plan_id'=CAST(:plan_id AS text)
                   AND strategy.payload->>'schedule_class'='execute_now'
                   AND plan.id=CAST(:plan_id AS uuid)
                   AND plan.status='queued'
                   AND plan.payload->>'business_id'=:business_id
                   AND plan.payload->>'strategy_run_id'=CAST(:run_id AS text)
                   AND plan.decision->'strategy_task_ids'
                         @> CAST(:selected_strategy AS jsonb)
                   AND plan.decision->'execute_now_strategy_ids'
                         @> CAST(:selected_strategy AS jsonb)
                   AND run.payload->>'business_id'=:business_id
                   AND COALESCE(run.decision->>'status','queued')
                         NOT IN ('completed','partial','blocked','failed','canceled')
                """
                + (" FOR UPDATE OF strategy, plan" if lock else "")
            ),
            {
                "run_id": str(action["run_id"]),
                "plan_id": str(action["plan_id"]),
                "strategy_id": str(action["source_strategy_task_id"]),
                "site_id": str(action["site_id"]),
                "business_id": str(action["business_id"]),
                "selected_strategy": json.dumps(
                    [str(action["source_strategy_task_id"])]
                ),
            },
        )
    ).mappings().first()
    if not row:
        raise ValueError(
            "strategy action is not bound to an active execute_now strategy plan"
        )
    result = dict(row)
    strategy_type = str(
        (result.get("strategy_decision") or {}).get("strategy_type") or ""
    )
    if strategy_type and strategy_type != str(action["action_type"]):
        raise ValueError("strategy action type does not match its formal strategy")
    return result


async def validate_strategy_action_wave(
    session: AsyncSession,
    action: dict[str, Any],
) -> None:
    """Allow at most one in-flight remote write per site and ordered waves."""
    wave_number = max(1, int(action.get("wave_number") or 1))
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:site_id, 7302026))"
        ),
        {"site_id": str(action["site_id"])},
    )
    blocked = (
        await session.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                    FROM seo_agent.tasks prior
                   WHERE prior.task_type='review'
                     AND prior.payload->>'kind'='strategy_action'
                     AND prior.payload->>'site_id'=:site_id
                     AND prior.id<>CAST(:action_id AS uuid)
                     AND (
                       prior.payload->>'status' IN ('executing','verifying','recovering')
                       OR (
                         prior.payload->>'run_id'=:run_id
                         AND
                         COALESCE((prior.payload->>'wave_number')::int, 1) < :wave_number
                         AND prior.payload->>'status'<>'completed'
                       )
                     )
                )
                """
            ),
            {
                "run_id": str(action["run_id"]),
                "site_id": str(action["site_id"]),
                "action_id": str(action["action_id"]),
                "wave_number": wave_number,
            },
        )
    ).scalar_one()
    if blocked:
        raise ValueError(
            "an earlier or in-flight Action for this site must pass readback first"
        )


__all__ = [
    "validate_strategy_action_lineage",
    "validate_strategy_action_wave",
]
