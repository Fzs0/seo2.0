"""SEO strategy execution lifecycle.

This deep module owns validation, claiming, heartbeat, generation, publishing,
compensation, cancellation, and terminal task persistence.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.article_urls import resolve_article_public_url
from app.core.database import SessionLocal
from app.services.strategy_effect_service import (
    cancel_unpublished_effect,
    ensure_effect,
    mark_effect_published,
)
from app.core.remote_outcomes import policy_for_remote_outcome
from app.services.strategy_exception_service import record_exception
from app.services.strategy_decision_service import build_keywordless_new_article_context
from app.core.config import get_settings


class _ArticleGeneratorAdapter(Protocol):
    async def generate(
        self,
        session: AsyncSession,
        keyword_id: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


class _ArticlePublisherAdapter(Protocol):
    async def publish(
        self,
        session: AsyncSession,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


class _PipelineArticleGenerator:
    async def generate(
        self,
        session: AsyncSession,
        keyword_id: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from app.services.article_generation_service import generate_article_pipeline

        return await generate_article_pipeline(session, keyword_id, **kwargs)


class _PublishArticleAdapter:
    async def publish(self, session: AsyncSession, **kwargs: Any) -> dict[str, Any]:
        from app.services.publish_service import publish_article

        return await publish_article(session, **kwargs)


_HeartbeatSessionFactory = Callable[[], Any]


@dataclass(frozen=True)
class _ExecutionAdapters:
    article_generator: _ArticleGeneratorAdapter
    publisher: _ArticlePublisherAdapter
    heartbeat_session_factory: _HeartbeatSessionFactory


_PRODUCTION_ADAPTERS = _ExecutionAdapters(
    article_generator=_PipelineArticleGenerator(),
    publisher=_PublishArticleAdapter(),
    heartbeat_session_factory=SessionLocal,
)


async def _validate_current_strategy(
    session: AsyncSession,
    *,
    row: dict[str, Any],
    decision: dict[str, Any],
    current_execution_id: str | None = None,
) -> None:
    if (
        row["site_status"] != "active"
        or not row["strategy_enabled"]
        or not row["business_id"]
        or row["task_business_id"] != row["business_id"]
    ):
        raise ValueError("目标站点已退出当前业务策略范围，请重新扫描并生成策略")
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:business_id))"), {"business_id": row["business_id"]})
    post_analysis_id = (((decision.get("evidence") or {}).get("content_audit") or {}).get("post_analysis") or {}).get("id")
    context = (
        await session.execute(
            text(
                """
                SELECT
                  EXISTS (
                    SELECT 1 FROM seo_agent.tasks candidate
                     WHERE candidate.id = CAST(:candidate_id AS uuid)
                       AND candidate.task_type = 'review' AND candidate.status = 'done'
                       AND candidate.payload->>'kind' = 'strategy_candidate'
                       AND candidate.payload->>'business_id' = :business_id
                       AND candidate.payload->>'analysis_batch_id' = CAST(:analysis_batch_id AS text)
                       AND candidate.payload->>'strategy_fingerprint' = :strategy_fingerprint
                       AND candidate.payload->>'evidence_fingerprint' = :evidence_fingerprint
                  ) AS candidate_ok,
                  EXISTS (
                    SELECT 1 FROM seo_agent.tasks plan
                     WHERE plan.id = CAST(:plan_id AS uuid) AND plan.task_type = 'review' AND plan.status = 'queued'
                       AND plan.payload->>'kind' = 'strategy_plan' AND plan.payload->>'business_id' = :business_id
                       AND plan.payload->>'analysis_batch_id' = CAST(:analysis_batch_id AS text)
                       AND plan.decision->>'plan_date' = (now() AT TIME ZONE 'Asia/Shanghai')::date::text
                       AND plan.decision->'selected_candidate_ids' @> CAST(:selected_candidate AS jsonb)
                       AND plan.id = (
                         SELECT current_plan.id FROM seo_agent.tasks current_plan
                          WHERE current_plan.task_type = 'review' AND current_plan.status = 'queued'
                            AND current_plan.payload->>'kind' = 'strategy_plan'
                            AND current_plan.payload->>'business_id' = :business_id
                            AND current_plan.decision->>'plan_date' = (now() AT TIME ZONE 'Asia/Shanghai')::date::text
                          ORDER BY current_plan.created_at DESC LIMIT 1
                       )
                  ) AS plan_ok,
                  EXISTS (
                    SELECT 1 FROM seo_agent.tasks analysis
                      WHERE analysis.id = CAST(:analysis_batch_id AS uuid)
                        AND analysis.task_type = 'review' AND analysis.status = 'done'
                        AND analysis.payload->>'kind' = 'strategy_analysis_batch'
                       AND analysis.payload->>'business_id' = :business_id
                       AND analysis.id = (
                         SELECT latest_analysis.id FROM seo_agent.tasks latest_analysis
                           WHERE latest_analysis.task_type = 'review' AND latest_analysis.status = 'done'
                             AND latest_analysis.payload->>'kind' = 'strategy_analysis_batch'
                             AND latest_analysis.payload->>'business_id' = :business_id
                          ORDER BY latest_analysis.created_at DESC LIMIT 1
                       )
                       AND analysis.payload->>'source_audit_batch_id' = (
                         SELECT latest_audit.id::text FROM seo_agent.tasks latest_audit
                          WHERE latest_audit.task_type = 'review'
                            AND latest_audit.payload->>'kind' = 'content_audit_batch'
                            AND latest_audit.payload->>'business_id' = :business_id
                          ORDER BY (latest_audit.payload->>'scanned_at')::timestamptz DESC NULLS LAST,
                                   latest_audit.created_at DESC LIMIT 1
                       )
                       AND (analysis.payload->>'source_audit_scanned_at')::timestamptz = (
                         SELECT (latest_audit.payload->>'scanned_at')::timestamptz
                           FROM seo_agent.tasks latest_audit
                          WHERE latest_audit.task_type = 'review'
                            AND latest_audit.payload->>'kind' = 'content_audit_batch'
                            AND latest_audit.payload->>'business_id' = :business_id
                          ORDER BY (latest_audit.payload->>'scanned_at')::timestamptz DESC NULLS LAST,
                                   latest_audit.created_at DESC LIMIT 1
                       )
                  ) AS analysis_ok,
                   CASE WHEN :strategy_type = 'update_article' OR CAST(:keyword_id AS uuid) IS NULL THEN true ELSE EXISTS (
                    SELECT 1 FROM seo_agent.keywords keyword
                     WHERE keyword.id = CAST(:keyword_id AS uuid) AND keyword.business_id = :business_id
                       AND keyword.assigned_site_id = CAST(:site_id AS uuid)
                  ) END AS keyword_ok,
                  CASE WHEN :strategy_type <> 'update_article' THEN true ELSE EXISTS (
                    SELECT 1 FROM seo_agent.posts post
                     WHERE post.id = CAST(:post_id AS uuid) AND post.site_id = CAST(:site_id AS uuid)
                       AND CAST(:post_analysis_id AS uuid) = (
                         SELECT analysis.id FROM seo_agent.post_analyses analysis
                          WHERE analysis.post_id = post.id ORDER BY analysis.analyzed_at DESC LIMIT 1
                       )
                  ) END AS target_ok,
                  NOT EXISTS (
                    SELECT 1 FROM seo_agent.tasks conflict
                     WHERE conflict.task_type IN ('new_article', 'update_article')
                       AND conflict.status IN ('queued', 'running')
                       AND (CAST(:current_execution_id AS uuid) IS NULL OR conflict.id <> CAST(:current_execution_id AS uuid))
                       AND (
                         (NULLIF(:scope_key, '') IS NOT NULL AND conflict.payload->>'scope_key' = :scope_key)
                         OR (CAST(:post_id AS uuid) IS NOT NULL AND conflict.post_id = CAST(:post_id AS uuid))
                         OR (CAST(:keyword_id AS uuid) IS NOT NULL AND conflict.keyword_id = CAST(:keyword_id AS uuid))
                       )
                  ) AS conflict_ok,
                  NOT EXISTS (
                    SELECT 1 FROM seo_agent.tasks effect
                     WHERE effect.task_type = 'review' AND effect.payload->>'kind' = 'strategy_effect'
                       AND effect.payload->>'scope_key' = :scope_key
                       AND (CAST(:current_execution_id AS uuid) IS NULL
                            OR effect.payload->>'execution_task_id' <> CAST(:current_execution_id AS text))
                       AND (
                         (effect.status IN ('queued', 'running') AND NULLIF(effect.payload->>'target_url', '') IS NULL)
                         OR (effect.payload->>'action' = 'update_article'
                             AND (effect.payload->>'cooldown_until')::timestamptz > now())
                         OR (effect.payload->>'action' = 'new_article'
                             AND NULLIF(effect.payload->>'target_url', '') IS NOT NULL)
                       )
                  ) AS effect_ok
                """
            ),
            {
                "candidate_id": row["candidate_id"],
                "plan_id": row["plan_id"],
                "analysis_batch_id": row["analysis_batch_id"],
                "business_id": row["business_id"],
                "selected_candidate": json.dumps([row["candidate_id"]]),
                "keyword_id": row["keyword_id"],
                "site_id": row["site_id"],
                "strategy_type": decision.get("strategy_type"),
                "post_id": row["post_id"],
                "post_analysis_id": post_analysis_id,
                "scope_key": decision.get("scope_key") or "",
                "strategy_fingerprint": decision.get("strategy_fingerprint") or "",
                "evidence_fingerprint": decision.get("evidence_fingerprint") or "",
                "current_execution_id": current_execution_id,
            },
        )
    ).mappings().one()
    if not all(context[key] for key in ("candidate_ok", "plan_ok", "analysis_ok", "keyword_ok", "target_ok", "conflict_ok", "effect_ok")):
        raise ValueError("策略证据或业务范围已变化，请重新运行全站内容扫描并生成策略")


async def execute_strategy(
    session: AsyncSession,
    *,
    task_id: str,
    allow_running: bool = False,
    execution_task_id: str | None = None,
) -> dict[str, Any]:
    """Execute an approved strategy through the production adapters."""
    return await _execute_strategy(
        session,
        task_id=task_id,
        allow_running=allow_running,
        execution_task_id=execution_task_id,
        adapters=_PRODUCTION_ADAPTERS,
    )


async def _execute_strategy(
    session: AsyncSession,
    *,
    task_id: str,
    allow_running: bool = False,
    execution_task_id: str | None = None,
    adapters: _ExecutionAdapters,
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT t.decision, t.site_id, t.keyword_id, t.post_id, "
                "       s.status AS site_status, s.business_id, s.strategy_enabled, "
                "       t.payload->>'business_id' AS task_business_id, "
                "       t.payload->>'candidate_id' AS candidate_id, t.payload->>'plan_id' AS plan_id, "
                "       t.payload->>'analysis_batch_id' AS analysis_batch_id "
                "FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id "
                "WHERE t.id = CAST(:id AS uuid) AND t.task_type = 'review' AND t.status = 'done' "
                "AND t.payload->>'kind' = 'seo_strategy'"
            ),
            {"id": task_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("approved strategy task not found")
    decision = dict(row["decision"] or {})
    execution_id = execution_task_id or decision.get("execution_task_id")
    if not execution_id:
        raise ValueError("strategy has no execution task")
    execution = (
        await session.execute(
            text(
                "SELECT id, task_type, status, site_id, keyword_id, post_id, payload "
                "FROM seo_agent.tasks WHERE id = CAST(:id AS uuid)"
            ),
            {"id": execution_id},
        )
    ).mappings().first()
    if not execution:
        raise ValueError("execution task not found")
    approved_decision = dict((execution.get("payload") or {}).get("strategy") or decision)
    if execution["status"] == "failed" and not allow_running:
        await session.execute(
            text(
                "UPDATE seo_agent.tasks SET status = 'queued', error_message = NULL, "
                "started_at = NULL, finished_at = NULL, updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status = 'failed'"
            ),
            {"id": execution_id},
        )
        await session.commit()
        execution = {**execution, "status": "queued"}
    if execution["status"] != "queued" and not (allow_running and execution["status"] == "running"):
        raise ValueError(f"execution task is already {execution['status']}")
    try:
        await _validate_current_strategy(
            session,
            row=dict(row),
            decision=approved_decision,
            current_execution_id=str(execution_id),
        )
    except ValueError as error:
        await _finish_execution_task(session, execution_id, status="blocked", error_message=str(error))
        await session.commit()
        return {"ok": False, "status": "blocked", "execution_task_id": str(execution_id), "error": str(error)}
    site_scope = (
        await session.execute(
            text(
                "SELECT name, status, business_id, strategy_enabled, market, language_code, "
                "site_type, domain, base_url, api_base_url, api_config "
                "FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
            ),
            {"id": execution["site_id"]},
        )
    ).mappings().first()
    if (
        not site_scope
        or site_scope["status"] != "active"
        or not site_scope["strategy_enabled"]
        or not site_scope["business_id"]
        or approved_decision.get("business_id") != site_scope["business_id"]
    ):
        message = "目标站点已退出当前业务策略范围，请重新扫描并生成策略"
        await _finish_execution_task(session, execution_id, status="blocked", error_message=message)
        await session.commit()
        return {"ok": False, "status": "blocked", "execution_task_id": str(execution_id), "error": message}
    if not execution["site_id"]:
        message = "文章策略缺少目标站点"
        await _finish_execution_task(session, execution_id, status="failed", error_message=message)
        await session.commit()
        return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": message}
    update_post_id = None
    update_post_url = None
    if execution["task_type"] == "update_article":
        post = (
            await session.execute(
                text(
                    "SELECT external_id, url, slug FROM seo_agent.posts "
                    "WHERE id = CAST(:id AS uuid) AND site_id = CAST(:site_id AS uuid)"
                ),
                {"id": execution["post_id"], "site_id": execution["site_id"]},
            )
        ).mappings().first()
        update_post_id = str(post["external_id"]) if post and post["external_id"] else None
        if not update_post_id:
            await _finish_execution_task(session, execution_id, status="failed", error_message="旧文缺少远端文章 ID")
            await session.commit()
            return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": "旧文缺少远端文章 ID"}
        update_post_url = resolve_article_public_url(
            dict(site_scope),
            slug=post["slug"] if post else None,
            article_id=update_post_id,
            remote_url=post["url"] if post else None,
        )
        if not update_post_url:
            message = "旧文缺少正式公开地址，请先同步目标站点文章"
            await _finish_execution_task(session, execution_id, status="blocked", error_message=message)
            await session.commit()
            return {"ok": False, "status": "blocked", "execution_task_id": str(execution_id), "error": message}

    keyword_context = None
    if not execution["keyword_id"]:
        if execution["task_type"] == "new_article":
            try:
                keyword_context = build_keywordless_new_article_context(
                    approved_decision,
                    site={**dict(site_scope), "id": str(execution["site_id"])},
                )
            except ValueError as error:
                message = str(error)
                await _finish_execution_task(session, execution_id, status="blocked", error_message=message)
                await session.commit()
                return {"ok": False, "status": "blocked", "execution_task_id": str(execution_id), "error": message}
        else:
            query = str(approved_decision.get("query") or "").strip()
            if not query:
                message = "更新策略缺少查询词"
                await _finish_execution_task(session, execution_id, status="failed", error_message=message)
                await session.commit()
                return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": message}
            keyword_context = {
                "id": None,
                "keyword": query,
                "business_id": site_scope["business_id"],
                "assigned_site_id": str(execution["site_id"]),
                "assigned_site_label": site_scope["name"],
                "market": site_scope["market"],
                "language_code": site_scope["language_code"],
            }

    if execution["status"] == "queued":
        claimed = await session.execute(
            text(
                "UPDATE seo_agent.tasks SET status = 'running', started_at = now(), "
                "logs = COALESCE(logs, '[]'::jsonb) || jsonb_build_array(jsonb_build_object('stage', 'running', 'message', '开始执行文章任务', 'at', now())), updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status = 'queued' RETURNING id"
            ),
            {"id": execution_id},
        )
        if claimed.first() is None:
            raise ValueError("execution task was claimed by another worker")
        await session.commit()
    try:
        effect_strategy = dict(approved_decision)
        if update_post_url:
            effect_strategy["target_url"] = update_post_url
        await ensure_effect(
            session,
            execution_task_id=str(execution_id),
            strategy_task_id=str(task_id),
            site_id=str(execution["site_id"]),
            article_id=None,
            strategy=effect_strategy,
        )
        await session.commit()
    except Exception as error:  # noqa: BLE001
        await session.rollback()
        await _finish_execution_task(session, execution_id, status="failed", error_message=f"效果基线创建失败：{error}")
        await session.commit()
        return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": str(error)}

    heartbeat = asyncio.create_task(
        _execution_heartbeat(
            str(execution_id),
            asyncio.current_task(),
            session_factory=adapters.heartbeat_session_factory,
        )
    )
    try:
        result = await adapters.article_generator.generate(
            session,
            str(execution["keyword_id"]) if execution["keyword_id"] else None,
            forced_site_id=str(execution["site_id"]),
            approved_strategy=approved_decision,
            keyword_context=keyword_context,
            task_id=str(execution["id"]),
        )
    except asyncio.CancelledError as error:
        message = str(error) or "服务或请求中断，任务已停止，可重新执行"
        await session.rollback()
        await cancel_unpublished_effect(session, execution_task_id=str(execution_id), reason=message)
        await _finish_execution_task(session, execution_id, status="failed", error_message=message)
        await session.commit()
        return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": message}
    except Exception as error:  # noqa: BLE001
        await session.rollback()
        await cancel_unpublished_effect(session, execution_task_id=str(execution_id), reason=str(error))
        await _finish_execution_task(session, execution_id, status="failed", error_message=str(error))
        await session.commit()
        return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": str(error)}
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
    if result.get("status") == "done":
        article_id = (result.get("article") or {}).get("id")
        published_url: str | None = None
        scheduled_payload = dict(execution.get("payload") or {})
        if (scheduled_payload.get("auto_publish") or execution["task_type"] == "update_article") and article_id:
            await session.execute(
                text(
                    "UPDATE seo_agent.tasks SET decision = jsonb_set(decision, '{current_stage}', to_jsonb(CAST(:stage AS text)), true), "
                    "updated_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND status = 'running'"
                ),
                {"id": execution_id, "stage": "publishing"},
            )
            await session.commit()
            try:
                published = await adapters.publisher.publish(
                    session,
                    article_id=str(article_id),
                    site_id=str(execution["site_id"]),
                    dry_run=False,
                    actor="strategy_automation",
                    update_post_id=update_post_id,
                )
            except Exception as error:  # noqa: BLE001
                await session.rollback()
                await cancel_unpublished_effect(session, execution_task_id=str(execution_id), reason=str(error))
                await _finish_execution_task(session, execution_id, status="failed", error_message=str(error))
                await session.commit()
                return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "result": result, "error": str(error)}
            if not published.get("ok"):
                remote_outcome = published.get("remote_outcome")
                outcome_policy = policy_for_remote_outcome(remote_outcome)
                await cancel_unpublished_effect(
                    session,
                    execution_task_id=str(execution_id),
                    reason=published.get("error") or "自动发布失败",
                )
                final_status = outcome_policy.task_status if outcome_policy else "failed"
                await _finish_execution_task(
                    session,
                    execution_id,
                    status=final_status,
                    error_message=published.get("error") or "自动发布失败",
                )
                if outcome_policy and outcome_policy.task_status == "blocked":
                    exception_id = published.get("exception_id")
                    if not exception_id:
                        exception = await record_exception(
                            session,
                            {
                                "run_id": approved_decision.get("strategy_run_id"),
                                "business_id": approved_decision.get("business_id"),
                                "site_id": str(execution["site_id"]),
                                "publish_task_id": published.get("task_id"),
                                "execution_task_id": str(execution_id),
                                "target_url": published.get("url"),
                                "type": "remote_write_uncertain",
                                "error_code": outcome_policy.error_code,
                                "stage": "publishing",
                                "severity": outcome_policy.severity,
                                "summary": "Remote publish outcome blocks automatic execution retry.",
                                "remote_write_occurred": outcome_policy.remote_write_occurred,
                                "retryable": outcome_policy.auto_retry_allowed,
                                "responsibility_type": "human_operator",
                                "unlock_condition": "Complete a fresh remote readback and resolve the exception.",
                            },
                        )
                        exception_id = exception["exception_id"]
                    await session.execute(
                        text(
                            "UPDATE seo_agent.tasks SET payload = payload || "
                            "jsonb_build_object('remote_outcome', CAST(:remote_outcome AS text), "
                            "'publish_task_id', CAST(:publish_task_id AS text), "
                            "'exception_id', CAST(:exception_id AS text)), updated_at=now() "
                            "WHERE id=CAST(:id AS uuid)"
                        ),
                        {
                            "id": execution_id,
                            "remote_outcome": remote_outcome,
                            "publish_task_id": published.get("task_id"),
                            "exception_id": exception_id,
                        },
                    )
                await session.commit()
                return {"ok": False, "status": final_status, "execution_task_id": str(execution_id), "result": result, "publish": published}
            published_url = str(published.get("url") or "").strip() or None
            try:
                await mark_effect_published(
                    session,
                    execution_task_id=str(execution_id),
                    article_id=str(article_id),
                    target_url=published.get("url"),
                    action=approved_decision.get("strategy_type"),
                    topic_cooldown_days=get_settings().strategy_topic_cooldown_days,
                )
            except Exception as error:  # noqa: BLE001
                await session.rollback()
                message = f"远端发布成功，但效果观察初始化失败：{error}；可安全重试以完成本地关联"
                await _finish_execution_task(session, execution_id, status="failed", error_message=message)
                await session.commit()
                return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "result": result, "publish": published, "error": message}
        await session.execute(
            text(
                "UPDATE seo_agent.tasks SET status = 'done', article_id = CAST(:article_id AS uuid), "
                "target_url = COALESCE(:target_url, target_url), "
                "logs = COALESCE(logs, '[]'::jsonb) || jsonb_build_array(jsonb_build_object('stage', 'done', 'message', '文章生成并保存完成', 'at', now())), "
                "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": execution_id, "article_id": article_id, "target_url": published_url},
        )
    else:
        failed = next((step for step in result.get("steps", []) if step.get("status") == "failed"), {})
        message = failed.get("message") or "文章生成失败"
        await cancel_unpublished_effect(session, execution_task_id=str(execution_id), reason=message)
        await _finish_execution_task(session, execution_id, status="failed", error_message=message)
    await session.commit()
    return {"ok": result.get("status") == "done", "status": result.get("status"), "execution_task_id": str(execution_id), "result": result}


async def cancel_strategy(session: AsyncSession, *, task_id: str) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT decision FROM seo_agent.tasks "
                "WHERE id = CAST(:id AS uuid) AND task_type = 'review' AND status = 'done' "
                "AND payload->>'kind' = 'seo_strategy'"
            ),
            {"id": task_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("approved strategy task not found")
    decision = dict(row["decision"] or {})
    execution_id = decision.get("execution_task_id")
    if not execution_id:
        raise ValueError("strategy has no execution task")
    execution = (
        await session.execute(
            text("SELECT status, keyword_id FROM seo_agent.tasks WHERE id = CAST(:id AS uuid)"),
            {"id": execution_id},
        )
    ).mappings().first()
    if not execution:
        raise ValueError("execution task not found")
    if execution["status"] in {"running", "done"}:
        raise ValueError(f"execution task is already {execution['status']}")

    canceled = await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'failed', 'blocked') RETURNING id"
        ),
        {"id": execution_id},
    )
    if canceled.first() is None:
        await session.rollback()
        raise ValueError("execution task is already running or done")
    decision.update({"review_status": "canceled", "execution_status": "canceled"})
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', decision = CAST(:decision AS jsonb), "
            "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"
        ),
        {"id": task_id, "decision": json.dumps(decision, ensure_ascii=False)},
    )
    if execution["keyword_id"]:
        await session.execute(
            text(
                "UPDATE seo_agent.keywords SET status = 'analyzed', updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status = 'queued'"
            ),
            {"id": execution["keyword_id"]},
        )
    await session.commit()
    return {"ok": True, "status": "canceled", "execution_task_id": str(execution_id)}


async def _finish_execution_task(session: AsyncSession, task_id: str, *, status: str, error_message: str) -> None:
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = CAST(:status AS text), error_message = CAST(:error_message AS text), "
            "logs = COALESCE(logs, '[]'::jsonb) || jsonb_build_array(jsonb_build_object('stage', CAST(:status AS text), 'message', CAST(:error_message AS text), 'at', now())), "
            "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"
        ),
        {"id": task_id, "status": status, "error_message": error_message},
    )


async def _execution_heartbeat(
    task_id: str,
    owner: asyncio.Task[Any] | None,
    interval_seconds: int = 60,
    session_factory: _HeartbeatSessionFactory = SessionLocal,
) -> None:
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            async with session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE seo_agent.tasks SET updated_at = now() "
                        "WHERE id = CAST(:id AS uuid) AND status = 'running'"
                    ),
                    {"id": task_id},
                )
                await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        if owner:
            owner.cancel(f"心跳异常：{error}")
