"""自动执行策略任务并生成/发布文章。"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.services.strategy_effect_service import process_due_effects
from app.services.strategy_service import execute_strategy


# ponytail: 单进程锁足够当前本地部署；多 worker 时再换成 Redis/数据库锁。
_RUN_LOCK = asyncio.Lock()
_AUTOMATION_RUNS: dict[str, asyncio.Task[None]] = {}
MAX_PARALLEL_EXECUTIONS = 3


def _execution_slots(running: int) -> int:
    return max(0, MAX_PARALLEL_EXECUTIONS - running)


async def run_effect_checks_once(session: AsyncSession, batch_size: int = 20) -> dict[str, Any]:
    """只处理本地到期效果记录；不领取执行任务，也不触发外站写入。"""
    return await process_due_effects(session, limit=max(1, min(batch_size, 100)))


async def recover_interrupted_executions(session: AsyncSession) -> int:
    result = await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET status = 'failed',
                   error_message = '系统恢复：服务已重启，进程内执行已中断；可重新执行。',
                   finished_at = now(),
                   updated_at = now()
             WHERE status = 'running'
               AND task_type IN ('new_article', 'update_article')
               AND article_id IS NULL
            """
        )
    )
    await session.commit()
    return result.rowcount or 0


async def run_automation_once(
    session: AsyncSession,
    *,
    batch_size: int = MAX_PARALLEL_EXECUTIONS,
    min_impressions: int = 20,
) -> dict[str, Any]:
    del min_impressions  # 策略生成阶段已完成数据门槛筛选。
    if _RUN_LOCK.locked():
        return {"status": "busy", "processed": 0}

    async with _RUN_LOCK:
        await _recover_stale_executions(session)
        running = int(
            (
                await session.execute(
                    text("SELECT count(*) FROM seo_agent.tasks WHERE task_type IN ('new_article', 'update_article') AND status = 'running'")
                )
            ).scalar_one()
            or 0
        )
        results: list[dict[str, Any]] = []
        for _ in range(min(max(1, batch_size), _execution_slots(running))):
            execution = await _next_execution(session)
            if not execution:
                break
            if not await _claim_execution(session, execution["execution_id"]):
                continue
            task = asyncio.create_task(
                _run_claimed_execution(
                    execution["review_id"],
                    execution["execution_id"],
                )
            )
            _AUTOMATION_RUNS[execution["execution_id"]] = task
            results.append({"ok": True, "status": "running", "execution_task_id": execution["execution_id"]})

        return {
            "status": "started" if results else "idle",
            "processed": len(results),
            **({"results": results} if results else {}),
        }


async def start_execution(session: AsyncSession, strategy_task_id: str) -> dict[str, Any]:
    """立即启动指定策略；并发已满时保留在队列中。"""
    async with _RUN_LOCK:
        row = (
            await session.execute(
                text(
                    "SELECT e.id::text AS execution_id, e.status "
                    "FROM seo_agent.tasks r JOIN seo_agent.tasks e "
                    "ON e.id = CAST(r.decision->>'execution_task_id' AS uuid) "
                    "WHERE r.id = CAST(:id AS uuid) AND r.task_type = 'review' "
                    "AND r.status = 'done' AND r.decision->>'review_status' = 'approved'"
                ),
                {"id": strategy_task_id},
            )
        ).mappings().first()
        if not row:
            raise ValueError("策略没有已批准的执行任务")
        if row["status"] == "running":
            return {"ok": True, "status": "running", "execution_task_id": row["execution_id"]}
        if row["status"] != "queued":
            raise ValueError(f"执行任务已经是 {row['status']} 状态")

        running = int(
            (
                await session.execute(
                    text("SELECT count(*) FROM seo_agent.tasks WHERE task_type IN ('new_article', 'update_article') AND status = 'running'")
                )
            ).scalar_one()
            or 0
        )
        if not _execution_slots(running):
            return {"ok": True, "status": "queued", "execution_task_id": row["execution_id"]}
        if not await _claim_execution(session, row["execution_id"]):
            raise ValueError("执行任务已被其他进程领取")
        task = asyncio.create_task(_run_claimed_execution(strategy_task_id, row["execution_id"]))
        _AUTOMATION_RUNS[row["execution_id"]] = task
        return {"ok": True, "status": "running", "execution_task_id": row["execution_id"]}


async def stop_execution(session: AsyncSession, strategy_task_id: str) -> dict[str, Any]:
    execution = (
        await session.execute(
            text(
                "SELECT e.id::text AS execution_id, e.status, e.decision->>'current_stage' AS current_stage "
                "FROM seo_agent.tasks r JOIN seo_agent.tasks e "
                "ON e.id = CAST(r.decision->>'execution_task_id' AS uuid) "
                "WHERE r.id = CAST(:id AS uuid) AND r.task_type = 'review' AND r.status = 'done'"
            ),
            {"id": strategy_task_id},
        )
    ).mappings().first()
    if not execution:
        raise ValueError("策略没有可停止的执行任务")
    execution_id = execution["execution_id"]
    if execution["current_stage"] == "publishing":
        raise ValueError("文章已进入外站发布阶段，不能安全停止")
    task = _AUTOMATION_RUNS.get(execution_id)
    if not task or task.done():
        raise ValueError("运行中的执行任务不存在")

    task.cancel("用户主动停止")
    with contextlib.suppress(asyncio.CancelledError):
        await task
    if _AUTOMATION_RUNS.get(execution_id) is task:
        _AUTOMATION_RUNS.pop(execution_id, None)

    updated = await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', error_message = NULL, "
            "logs = COALESCE(logs, '[]'::jsonb) || jsonb_build_array(jsonb_build_object('stage', 'canceled', 'message', '用户主动停止', 'at', now())), "
            "finished_at = now(), updated_at = now() "
            "WHERE id = CAST(:id AS uuid) AND task_type IN ('new_article', 'update_article') "
            "AND status IN ('running', 'failed') RETURNING id"
        ),
        {"id": execution_id},
    )
    if updated.first() is None:
        raise ValueError("执行任务已经结束")
    await session.commit()
    return {"ok": True, "status": "canceled", "execution_task_id": execution_id}


async def _run_claimed_execution(review_id: str, execution_id: str) -> None:
    try:
        async with SessionLocal() as session:
            await execute_strategy(session, task_id=review_id, allow_running=True, execution_task_id=execution_id)
    except Exception as error:  # noqa: BLE001
        async with SessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE seo_agent.tasks SET status = 'failed', error_message = :error, "
                    "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid) AND status = 'running'"
                ),
                {"id": execution_id, "error": str(error)},
            )
            await session.commit()
    finally:
        _AUTOMATION_RUNS.pop(execution_id, None)
        async with SessionLocal() as session:
            await run_automation_once(session)


async def _recover_stale_executions(session: AsyncSession, timeout_minutes: int = 30) -> None:
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET status = 'failed',
                   error_message = '系统恢复：任务超过 30 分钟未完成，疑似服务重启或请求中断；可重新执行。',
                   finished_at = now(),
                   updated_at = now()
             WHERE status = 'running'
               AND task_type IN ('new_article', 'update_article')
               AND article_id IS NULL
               AND updated_at < now() - (:timeout_minutes * interval '1 minute')
            """
        ),
        {"timeout_minutes": timeout_minutes},
    )
    await session.commit()


async def _next_execution(session: AsyncSession) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT e.id::text AS execution_id,
                   e.payload->>'strategy_task_id' AS review_id,
                   e.title
              FROM seo_agent.tasks e
              JOIN seo_agent.tasks r ON r.id = CAST(e.payload->>'strategy_task_id' AS uuid)
             WHERE e.task_type IN ('new_article', 'update_article')
               AND e.status = 'queued'
               AND (e.run_after IS NULL OR e.run_after <= now())
               AND r.task_type = 'review'
               AND r.status = 'done'
               AND r.payload->>'kind' = 'seo_strategy'
               AND r.decision->>'review_status' = 'approved'
               AND r.decision->>'execution_task_id' = e.id::text
             ORDER BY e.priority, e.score DESC NULLS LAST, e.created_at
             LIMIT 1
            """
        )
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def _claim_execution(session: AsyncSession, execution_id: str) -> bool:
    result = await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'running', started_at = now(), "
            "logs = COALESCE(logs, '[]'::jsonb) || jsonb_build_array(jsonb_build_object('stage', 'running', 'message', '执行队列已启动任务', 'at', now())), updated_at = now() "
            "WHERE id = CAST(:id AS uuid) AND status = 'queued' RETURNING id"
        ),
        {"id": execution_id},
    )
    await session.commit()
    return result.first() is not None


async def get_execution_status(session: AsyncSession, business_id: str | None = None) -> dict[str, Any]:
    counts = {
        row["status"]: int(row["count"])
        for row in (
            await session.execute(
                text(
                    "SELECT t.status, count(*) AS count FROM seo_agent.tasks t "
                    "LEFT JOIN seo_agent.sites s ON s.id = t.site_id "
                    "WHERE t.task_type IN ('new_article', 'update_article') "
                    "AND (CAST(:business_id AS text) IS NULL OR s.business_id = :business_id) GROUP BY t.status"
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
                    "SELECT t.id::text, t.task_type, t.title, t.status, t.error_message, t.finished_at "
                    "FROM seo_agent.tasks t LEFT JOIN seo_agent.sites s ON s.id = t.site_id "
                    "WHERE t.task_type IN ('keyword_review', 'new_article', 'update_article') "
                    "AND t.status IN ('done', 'failed', 'blocked') "
                    "AND (CAST(:business_id AS text) IS NULL OR s.business_id = :business_id) "
                    "ORDER BY t.finished_at DESC NULLS LAST LIMIT 10"
                ),
                {"business_id": business_id},
            )
        ).mappings().all()
    ]
    return {
        "parallel_limit": MAX_PARALLEL_EXECUTIONS,
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0) + counts.get("blocked", 0),
        "recent": recent,
    }


__all__ = [
    "get_execution_status",
    "recover_interrupted_executions",
    "run_automation_once",
    "run_effect_checks_once",
    "start_execution",
    "stop_execution",
]
