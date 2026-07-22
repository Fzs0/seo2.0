"""业务范围内的关键词分配、站点证据分析和策略审核。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.engine.classifier import keyword_scope
from app.services.strategy_effect_service import (
    cancel_unpublished_effect,
    ensure_effect,
    load_scope_locks,
    mark_effect_published,
    resolve_strategy_target_url,
    strategy_identity,
)


async def generate_strategies(
    session: AsyncSession,
    *,
    business_id: str,
    site_id: str | None = None,
    limit: int = 4,
    action_budget: int | None = None,
    site_quotas: dict[str, int] | None = None,
    min_impressions: int = 20,
) -> dict[str, Any]:
    budget = max(0, min(action_budget if action_budget is not None else limit, 200))
    scope = await session.execute(
        text(
            "SELECT id, name, site_type FROM seo_agent.sites "
            "WHERE business_id = :business_id AND strategy_enabled = true AND status = 'active' "
            "AND (CAST(:site_id AS uuid) IS NULL OR id = CAST(:site_id AS uuid)) ORDER BY name"
        ),
        {"business_id": business_id, "site_id": site_id},
    )
    site_scope = [dict(row) for row in scope.mappings().all()]
    if not site_scope:
        raise ValueError(f"业务 {business_id} 没有符合条件的已启用策略站点")
    source_audit = (
        await session.execute(
            text(
                "SELECT id, payload->>'scanned_at' AS scanned_at FROM seo_agent.tasks "
                "WHERE task_type = 'review' AND payload->>'kind' = 'content_audit_batch' "
                "AND payload->>'business_id' = :business_id "
                "ORDER BY (payload->>'scanned_at')::timestamptz DESC NULLS LAST, created_at DESC LIMIT 1"
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    if not source_audit:
        raise ValueError("请先运行当前业务的全站内容扫描")
    quarantined = await _quarantine_invalid_assignments(session, business_id)

    rows = await session.execute(
        text(
            """
            WITH latest_audit_scan AS (
                SELECT max((payload->>'scanned_at')::timestamptz) AS scanned_at
                 FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = 'content_audit_batch'
                   AND payload->>'business_id' = :business_id
            )
            SELECT s.id AS site_id, s.name AS site_name, s.site_type, s.business_id, s.market, s.language_code,
                   s.content_role, s.content_scope,
                   COALESCE(s.knowledge_profile, '{}'::jsonb) AS knowledge_profile,
                   k.id AS keyword_id, k.topic_cluster_id,
                   COALESCE(k.keyword, NULLIF(p.primary_keyword, ''), NULLIF(audit.payload#>>'{item,query}', ''), p.title) AS query,
                   k.volume, k.kd, k.intent,
                   k.priority AS keyword_priority, k.score AS keyword_score,
                   q.clicks, q.impressions, q.ctr, q.avg_position, q.last_seen,
                   serp.id AS serp_snapshot_id,
                   jsonb_array_length(COALESCE(serp.organic_results, '[]'::jsonb)) AS serp_result_count,
                   a.id AS article_id, a.title AS article_title,
                   a.status AS article_status, a.published_url AS article_published_url,
                   a.published_post_id AS article_published_post_id,
                   p.id AS post_id, p.title AS post_title, p.url AS post_url,
                   CASE WHEN p.id IS NOT NULL THEN 'exact' END AS post_match_type,
                   audit.id AS audit_task_id, audit.score AS audit_score, audit.decision AS audit_decision,
                   audit.payload->>'scanned_at' AS audit_scanned_at,
                   pa.id AS post_analysis_id, pa.analyzed_at AS post_analysis_analyzed_at
              FROM seo_agent.tasks audit
              JOIN seo_agent.sites s ON s.id = audit.site_id
              LEFT JOIN seo_agent.posts p
                ON p.id = audit.post_id AND p.site_id = audit.site_id
               AND COALESCE(p.status, '') <> 'remote_missing'
              LEFT JOIN LATERAL (
                SELECT candidate.*
                  FROM seo_agent.keywords candidate
                 WHERE candidate.business_id = :business_id
                   AND candidate.assigned_site_id = audit.site_id
                   AND (candidate.id = audit.keyword_id
                        OR (audit.keyword_id IS NULL
                            AND p.primary_keyword IS NOT NULL
                            AND lower(candidate.keyword) = lower(p.primary_keyword)))
                 ORDER BY (candidate.id = audit.keyword_id) DESC, candidate.updated_at DESC
                 LIMIT 1
              ) k ON TRUE
              LEFT JOIN seo_agent.v_gsc_query_28d q
                ON q.site_id = audit.site_id
               AND lower(q.query) = lower(COALESCE(k.keyword, NULLIF(p.primary_keyword, ''), NULLIF(audit.payload#>>'{item,query}', ''), p.title))
              LEFT JOIN LATERAL (
                SELECT id, title, status, published_url, published_post_id
                  FROM seo_agent.articles
                 WHERE site_id = audit.site_id
                   AND ((k.id IS NOT NULL AND lower(primary_keyword) = lower(k.keyword))
                        OR (p.external_id IS NOT NULL AND published_post_id = p.external_id)
                        OR (p.url IS NOT NULL AND published_url = p.url))
                 ORDER BY created_at DESC
                 LIMIT 1
              ) a ON TRUE
              LEFT JOIN LATERAL (
                SELECT id, analyzed_at
                  FROM seo_agent.post_analyses
                 WHERE post_id = p.id
                 ORDER BY analyzed_at DESC
                 LIMIT 1
              ) pa ON TRUE
              LEFT JOIN LATERAL (
                SELECT id, organic_results
                  FROM seo_agent.serp_snapshots
                 WHERE id = audit.serp_snapshot_id OR (k.id IS NOT NULL AND keyword_id = k.id)
                 ORDER BY (id = audit.serp_snapshot_id) DESC, requested_at DESC
                 LIMIT 1
              ) serp ON TRUE
             WHERE audit.task_type = 'review'
               AND (audit.post_id IS NULL OR p.id IS NOT NULL)
               AND audit.payload->>'kind' = 'content_audit'
               AND audit.payload->>'business_id' = :business_id
               AND (audit.payload->>'scanned_at')::timestamptz = (SELECT scanned_at FROM latest_audit_scan)
               AND (p.id IS NOT NULL OR k.id IS NOT NULL)
               AND audit.decision->>'action' IN ('new_article', 'update_article', 'hold')
               AND s.status = 'active'
               AND s.business_id = :business_id
               AND s.strategy_enabled = true
               AND (CAST(:site_id AS uuid) IS NULL OR s.id = CAST(:site_id AS uuid))
             ORDER BY CASE audit.decision->>'priority'
                        WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4
                      END,
                      audit.score DESC NULLS LAST,
                      COALESCE(q.impressions, 0) DESC,
                      k.score DESC NULLS LAST,
                      audit.created_at DESC
            """
        ),
        {
            "site_id": site_id,
            "business_id": business_id,
        },
    )
    candidate_rows = [dict(row) for row in rows.mappings().all()]
    ga4_rows = await session.execute(
        text(
            "SELECT g.site_id, g.sessions, g.conversions FROM seo_agent.v_ga4_site_28d g "
            "JOIN seo_agent.sites s ON s.id = g.site_id "
            "WHERE s.business_id = :business_id AND s.strategy_enabled = true AND s.status = 'active'"
        ),
        {"business_id": business_id},
    )
    ga4_by_site = {str(row["site_id"]): dict(row) for row in ga4_rows.mappings().all()}
    site_content = await _load_site_content(session)
    scope_locks = await load_scope_locks(session, business_id=business_id)

    analysis = await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks (task_type, status, priority, title, payload, decision)
            VALUES ('review', 'done', 'P2', :title, CAST(:payload AS jsonb), '{}'::jsonb)
            RETURNING id
            """
        ),
        {
            "title": f"业务策略分析：{business_id}",
            "payload": json.dumps(
                {
                    "kind": "strategy_analysis_batch",
                    "business_id": business_id,
                    "site_scope": site_scope,
                    "source_audit_batch_id": str(source_audit["id"]),
                    "source_audit_scanned_at": source_audit["scanned_at"],
                    "min_impressions": max(1, min_impressions),
                },
                ensure_ascii=False,
                default=str,
            ),
        },
    )
    analysis_batch_id = str(analysis.scalar_one())
    candidates: list[dict[str, Any]] = []
    seen_candidate_keys: set[str] = set()
    for row in candidate_rows:
        links = _build_internal_link_plan(row["query"], site_content.get(str(row["site_id"]), []))
        strategy = _build_strategy(row, ga4_by_site.get(str(row["site_id"])), links)
        if not strategy:
            continue
        identity = strategy_identity(
            business_id,
            site_id=row.get("site_id"),
            market=row.get("market"),
            language_code=row.get("language_code"),
            topic_cluster_id=row.get("topic_cluster_id"),
            post_id=row.get("post_id"),
            article_id=row.get("article_id"),
            query=row.get("query"),
            action=strategy.get("strategy_type"),
            objective=strategy.get("recommended_action"),
            evidence=strategy.get("evidence"),
        )
        candidate_key, evidence_key = identity["strategy_fingerprint"], identity["evidence_fingerprint"]
        if candidate_key in seen_candidate_keys:
            continue
        seen_candidate_keys.add(candidate_key)
        strategy.update({"candidate_key": candidate_key, "evidence_key": evidence_key, **identity})
        if identity["scope_key"] in scope_locks:
            strategy.update({
                "strategy_type": "hold",
                "priority": "Hold",
                "score": 0,
                "confidence": min(float(strategy.get("confidence") or 0.35), 0.35),
                "evidence_level": "insufficient",
                "reason": f"{scope_locks[identity['scope_key']]}；{strategy.get('reason') or ''}",
            })
        inserted = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, keyword_id, post_id, article_id,
                   title, payload, required_data, decision)
                VALUES
                   ('review', 'done', :priority, :score, :site_id, :keyword_id, :post_id, :article_id,
                    :title, CAST(:payload AS jsonb), :required_data, CAST(:decision AS jsonb))
                RETURNING id
                """
            ),
            {
                "priority": strategy["priority"],
                "score": strategy["score"],
                "site_id": row["site_id"],
                "keyword_id": row["keyword_id"],
                "post_id": row.get("post_id"),
                "article_id": row.get("article_id"),
                "title": strategy["title"],
                "payload": json.dumps({
                    "kind": "strategy_candidate",
                    "business_id": business_id,
                    "analysis_batch_id": analysis_batch_id,
                    "topic_cluster_id": row.get("topic_cluster_id"),
                    "candidate_key": candidate_key,
                    "evidence_key": evidence_key,
                    **identity,
                    "evidence": strategy["evidence"],
                }, ensure_ascii=False),
                "required_data": ["gsc_28d", "ga4_28d", "site_content", "site_knowledge", "content_audit"],
                "decision": json.dumps({**strategy, "candidate_status": "hold" if strategy["strategy_type"] == "hold" else "available"}, ensure_ascii=False),
            },
        )
        candidate_id = inserted.scalar_one()
        candidates.append({
            "id": str(candidate_id),
            "site_id": str(row["site_id"]),
            "site_name": row["site_name"],
            "keyword_id": str(row["keyword_id"]) if row.get("keyword_id") else None,
            "post_id": str(row["post_id"]) if row.get("post_id") else None,
            "article_id": str(row["article_id"]) if row.get("article_id") else None,
            **strategy,
        })

    selected = _select_daily_candidates(candidates, budget, site_quotas)
    plan = await _replace_strategy_plan(
        session,
        business_id=business_id,
        analysis_batch_id=analysis_batch_id,
        action_budget=budget,
        site_quotas=site_quotas or {},
        candidates=selected,
    )
    await session.execute(
        text("UPDATE seo_agent.tasks SET decision = CAST(:decision AS jsonb), updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {
            "id": analysis_batch_id,
            "decision": json.dumps({"total_candidates": len(candidates), "planned_actions": len(selected)}, ensure_ascii=False),
        },
    )
    await session.commit()
    return {
        "business_id": business_id,
        "site_scope": site_scope,
        "analysis_batch_id": analysis_batch_id,
        "plan": plan,
        "items": plan["items"],
        "created": len(plan["items"]),
        "candidates": len(candidates),
        "total_candidates": len(candidates),
        "planned_actions": len(plan["items"]),
        "unassigned": 0,
        "quarantined": quarantined,
    }


def _candidate_keys(business_id: str, row: dict[str, Any], strategy: dict[str, Any]) -> tuple[str, str]:
    identity = strategy_identity(
        business_id,
        site_id=row.get("site_id"),
        market=row.get("market"),
        language_code=row.get("language_code"),
        topic_cluster_id=row.get("topic_cluster_id") or row.get("keyword_id"),
        post_id=row.get("post_id"),
        article_id=row.get("article_id"),
        query=row.get("query"),
        action=strategy.get("strategy_type"),
        objective=strategy.get("recommended_action"),
        evidence={key: row.get(key) for key in ("audit_task_id", "serp_snapshot_id", "post_analysis_id")},
    )
    return identity["strategy_fingerprint"], identity["evidence_fingerprint"]


def _select_daily_candidates(
    rows: list[dict[str, Any]],
    limit: int,
    site_quotas: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """按预算与站点配额组装今日计划；先覆盖站点再补位。"""
    wanted = max(0, min(limit, 200))
    if not wanted:
        return []
    quotas = {str(key): max(0, int(value)) for key, value in (site_quotas or {}).items()}
    selected: list[dict[str, Any]] = []
    seen_sites: set[str] = set()
    site_counts: dict[str, int] = {}

    def allowed(row: dict[str, Any]) -> bool:
        site_id = str(row.get("site_id") or "")
        return (
            row.get("strategy_type") != "hold"
            and row.get("priority") != "Hold"
            and row.get("candidate_status") != "executed"
            and not row.get("executed")
            and site_counts.get(site_id, 0) < quotas.get(site_id, wanted)
        )

    def add(row: dict[str, Any]) -> None:
        selected.append(row)
        site_id = str(row.get("site_id") or "")
        site_counts[site_id] = site_counts.get(site_id, 0) + 1

    for row in rows:
        site_id = str(row.get("site_id") or "")
        if site_id not in seen_sites and allowed(row):
            add(row)
            seen_sites.add(site_id)
        if len(selected) >= wanted:
            return selected
    for row in rows:
        if row not in selected and allowed(row):
            add(row)
        if len(selected) >= wanted:
            break
    return selected[:wanted]


async def _replace_strategy_plan(
    session: AsyncSession,
    *,
    business_id: str,
    analysis_batch_id: str,
    action_budget: int,
    site_quotas: dict[str, int],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:business_id))"), {"business_id": business_id})
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE task_type = 'review' AND status = 'queued' AND payload->>'business_id' = :business_id "
            "AND payload->>'kind' IN ('strategy_plan', 'seo_strategy')"
        ),
        {"business_id": business_id},
    )
    selected_ids = [str(candidate["id"]) for candidate in candidates]
    plan_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    inserted = await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks (task_type, status, priority, title, payload, decision)
            VALUES ('review', 'queued', 'P2', :title, CAST(:payload AS jsonb), CAST(:decision AS jsonb))
            RETURNING id, created_at
            """
        ),
        {
            "title": f"今日策略计划：{business_id}",
            "payload": json.dumps({
                "kind": "strategy_plan",
                "business_id": business_id,
                "analysis_batch_id": analysis_batch_id,
            }, ensure_ascii=False),
            "decision": json.dumps({
                "action_budget": action_budget,
                "site_quotas": site_quotas,
                "selected_candidate_ids": selected_ids,
                "plan_date": plan_date,
            }, ensure_ascii=False),
        },
    )
    plan_row = inserted.mappings().one()
    plan_id = str(plan_row["id"])
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        strategy = {key: value for key, value in candidate.items() if key not in {"id", "site_id", "site_name", "keyword_id", "post_id", "article_id", "status", "created_at", "updated_at", "candidate_status", "executed"}}
        strategy["business_id"] = business_id
        task = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, keyword_id, post_id, article_id,
                   title, payload, required_data, decision)
                VALUES
                  ('review', 'queued', :priority, :score, CAST(:site_id AS uuid), CAST(:keyword_id AS uuid),
                   CAST(:post_id AS uuid), CAST(:article_id AS uuid), :title, CAST(:payload AS jsonb),
                   :required_data, CAST(:decision AS jsonb))
                RETURNING id
                """
            ),
            {
                "priority": strategy.get("priority") or "P2",
                "score": strategy.get("score") or 0,
                "site_id": candidate["site_id"],
                "keyword_id": candidate.get("keyword_id"),
                "post_id": candidate.get("post_id"),
                "article_id": candidate.get("article_id"),
                "title": strategy.get("title"),
                "payload": json.dumps({
                    "kind": "seo_strategy",
                    "business_id": business_id,
                    "analysis_batch_id": analysis_batch_id,
                    "candidate_id": str(candidate["id"]),
                    "plan_id": plan_id,
                    "scope_key": strategy.get("scope_key"),
                    "strategy_fingerprint": strategy.get("strategy_fingerprint"),
                    "evidence_fingerprint": strategy.get("evidence_fingerprint"),
                    "policy_version": strategy.get("policy_version"),
                    "evidence": strategy.get("evidence") or {},
                }, ensure_ascii=False),
                "required_data": ["gsc_28d", "ga4_28d", "site_content", "site_knowledge", "content_audit"],
                "decision": json.dumps(strategy, ensure_ascii=False),
            },
        )
        task_id = str(task.scalar_one())
        items.append({
            **candidate,
            **strategy,
            "id": task_id,
            "candidate_id": str(candidate["id"]),
            "plan_id": plan_id,
            "status": "pending",
        })
    return {
        "id": plan_id,
        "business_id": business_id,
        "analysis_batch_id": analysis_batch_id,
        "action_budget": action_budget,
        "site_quotas": site_quotas,
        "selected_candidate_ids": selected_ids,
        "plan_date": plan_date,
        "planned_actions": len(items),
        "status": "active",
        "created_at": plan_row["created_at"],
        "items": items,
    }


async def list_strategy_candidates(
    session: AsyncSession,
    *,
    business_id: str,
    page: int = 1,
    limit: int = 50,
    status: str | None = None,
) -> dict[str, Any]:
    page = max(1, page)
    limit = max(1, min(limit, 200))
    status_filter = status if status in {"available", "selected", "hold", "executed"} else None
    rows = await session.execute(
        text(
            """
            WITH latest AS (
                SELECT id::text AS id FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = 'strategy_analysis_batch'
                   AND payload->>'business_id' = :business_id
                   AND status = 'done'
                 ORDER BY created_at DESC LIMIT 1
            ), candidates AS (
                SELECT t.id, t.status, t.priority, t.score, t.site_id, s.name AS site_name,
                       t.keyword_id, t.post_id, t.article_id, t.title, t.decision,
                       t.payload->>'analysis_batch_id' AS analysis_batch_id,
                       t.created_at, t.updated_at,
                       CASE
                         WHEN EXISTS (
                           SELECT 1 FROM seo_agent.tasks strategy
                            WHERE strategy.task_type = 'review'
                              AND strategy.payload->>'kind' = 'seo_strategy'
                              AND strategy.payload->>'candidate_id' = t.id::text
                              AND (strategy.status = 'done' OR strategy.decision ? 'execution_task_id')
                         ) THEN 'executed'
                         WHEN t.decision->>'strategy_type' = 'hold' OR t.priority = 'Hold' THEN 'hold'
                         WHEN EXISTS (
                           SELECT 1 FROM seo_agent.tasks strategy
                            WHERE strategy.task_type = 'review' AND strategy.status = 'queued'
                              AND strategy.payload->>'kind' = 'seo_strategy'
                              AND strategy.payload->>'candidate_id' = t.id::text
                         ) THEN 'selected'
                         ELSE 'available'
                       END AS candidate_status
                  FROM seo_agent.tasks t
                  JOIN seo_agent.sites s ON s.id = t.site_id
                 WHERE t.task_type = 'review' AND t.payload->>'kind' = 'strategy_candidate'
                   AND t.payload->>'business_id' = :business_id
                   AND t.payload->>'analysis_batch_id' = (SELECT id FROM latest)
            )
            SELECT *, count(*) OVER() AS total FROM candidates
             WHERE CAST(:candidate_status AS text) IS NULL OR candidate_status = CAST(:candidate_status AS text)
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
        "analysis_batch_id": items[0].get("analysis_batch_id") if items else None,
    }


async def get_strategy_plan(session: AsyncSession, *, business_id: str) -> dict[str, Any] | None:
    plan = (
        await session.execute(
            text(
                """
                SELECT id, status, payload, decision, created_at, updated_at
                  FROM seo_agent.tasks
                 WHERE task_type = 'review' AND status = 'queued'
                   AND payload->>'kind' = 'strategy_plan' AND payload->>'business_id' = :business_id
                   AND decision->>'plan_date' = (now() AT TIME ZONE 'Asia/Shanghai')::date::text
                 ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    if not plan:
        return None
    payload = dict(plan["payload"] or {})
    decision = dict(plan["decision"] or {})
    strategies = await list_strategies(session, status="pending", limit=200, business_id=business_id)
    plan_id = str(plan["id"])
    items = [item for item in strategies if item.get("plan_id") == plan_id]
    return {
        "id": plan_id,
        "business_id": business_id,
        "analysis_batch_id": payload.get("analysis_batch_id"),
        "action_budget": int(decision.get("action_budget") or 0),
        "site_quotas": decision.get("site_quotas") or {},
        "selected_candidate_ids": decision.get("selected_candidate_ids") or [],
        "plan_date": decision.get("plan_date"),
        "planned_actions": len(items),
        "status": "active",
        "created_at": plan["created_at"],
        "updated_at": plan["updated_at"],
        "items": items,
    }


async def save_strategy_plan(
    session: AsyncSession,
    *,
    business_id: str,
    action_budget: int,
    site_quotas: dict[str, int] | None = None,
    selected_candidate_ids: list[str] | None = None,
) -> dict[str, Any]:
    budget = max(0, min(action_budget, 200))
    quotas = {str(key): max(0, int(value)) for key, value in (site_quotas or {}).items()}
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:business_id))"), {"business_id": business_id})
    analysis_batch_id = (
        await session.execute(
            text(
                "SELECT id::text FROM seo_agent.tasks WHERE task_type = 'review' "
                "AND status = 'done' AND payload->>'kind' = 'strategy_analysis_batch' "
                "AND payload->>'business_id' = :business_id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"business_id": business_id},
        )
    ).scalar_one_or_none()
    if not analysis_batch_id:
        raise ValueError("请先生成策略候选池")
    rows = await session.execute(
        text(
            """
            SELECT t.id, t.priority, t.score, t.site_id, s.name AS site_name, t.keyword_id,
                   t.post_id, t.article_id, t.title, t.decision, t.created_at, t.updated_at,
                   EXISTS (
                     SELECT 1 FROM seo_agent.tasks strategy
                      WHERE strategy.task_type = 'review'
                        AND strategy.payload->>'kind' = 'seo_strategy'
                        AND strategy.payload->>'candidate_id' = t.id::text
                        AND (strategy.status = 'done' OR strategy.decision ? 'execution_task_id')
                   ) AS executed
              FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id
             WHERE t.task_type = 'review' AND t.payload->>'kind' = 'strategy_candidate'
               AND t.status = 'done'
               AND t.payload->>'business_id' = :business_id
               AND t.payload->>'analysis_batch_id' = :analysis_batch_id
             ORDER BY t.score DESC NULLS LAST, t.created_at DESC
            """
        ),
        {"business_id": business_id, "analysis_batch_id": analysis_batch_id},
    )
    candidates = [_strategy_row(dict(row)) for row in rows.mappings().all()]
    if selected_candidate_ids is None:
        selected = _select_daily_candidates(candidates, budget, quotas)
    else:
        selected_candidate_ids = list(dict.fromkeys(selected_candidate_ids))
        by_id = {str(candidate["id"]): candidate for candidate in candidates}
        missing = [candidate_id for candidate_id in selected_candidate_ids if candidate_id not in by_id]
        if missing:
            raise ValueError("所选策略候选不属于当前业务分析批次")
        selected = [by_id[candidate_id] for candidate_id in selected_candidate_ids]
        if len(selected) > budget:
            raise ValueError("所选策略数量超过今日动作预算")
        counts: dict[str, int] = {}
        for candidate in selected:
            if candidate.get("executed"):
                raise ValueError("已批准或执行的策略候选不能再次加入计划")
            if candidate.get("strategy_type") == "hold" or candidate.get("priority") == "Hold":
                raise ValueError("Hold 候选不能进入今日计划")
            candidate_site = str(candidate["site_id"])
            counts[candidate_site] = counts.get(candidate_site, 0) + 1
            if candidate_site in quotas and counts[candidate_site] > quotas[candidate_site]:
                raise ValueError("所选策略数量超过站点配额")
    plan = await _replace_strategy_plan(
        session,
        business_id=business_id,
        analysis_batch_id=str(analysis_batch_id),
        action_budget=budget,
        site_quotas=quotas,
        candidates=selected,
    )
    await session.commit()
    return plan


async def _quarantine_invalid_assignments(session: AsyncSession, business_id: str) -> int:
    rows = await session.execute(
        text(
            "SELECT k.id, k.keyword, k.market, k.language_code, k.ai_review, "
            "       s.market AS site_market, s.language_code AS site_language "
            "FROM seo_agent.keywords k "
            "LEFT JOIN seo_agent.sites s ON s.id = k.assigned_site_id "
            "WHERE k.assigned_site_id IS NOT NULL AND k.status NOT IN ('dropped', 'published') "
            "AND s.business_id = :business_id AND s.strategy_enabled = true AND s.status = 'active'"
        ),
        {"business_id": business_id},
    )
    invalid = [
        str(row["id"])
        for row in rows.mappings().all()
        if (
            keyword_scope(row["keyword"])["status"] != "relevant"
            and ((row["ai_review"] or {}).get("strategy") or {}).get("relevance") != "relevant"
            or not row["market"]
            or not row["language_code"]
            or not row["site_market"]
            or not row["site_language"]
            or _locale_token(row["market"]) != _locale_token(row["site_market"])
            or _locale_token(row["language_code"]) != _locale_token(row["site_language"])
        )
    ]
    if not invalid:
        return 0
    await session.execute(
        text(
            """
            UPDATE seo_agent.keywords
               SET assigned_site_id = NULL, assigned_site_label = NULL,
                   status = 'hold', priority = 'Hold', content_action = 'needs_scope_review',
                   reason = '已通过复核撤回：关键词缺少业务/市场语种约束，等待重新导入或人工确认。', updated_at = now()
             WHERE id::text = ANY(:ids)
            """
        ),
        {"ids": invalid},
    )
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE keyword_id::text = ANY(:ids) AND status = 'queued' "
            "AND ((task_type = 'review' AND payload->>'kind' IN ('seo_strategy', 'content_audit')) "
            "OR task_type IN ('new_article', 'update_article'))"
        ),
        {"ids": invalid},
    )
    return len(invalid)


def _locale_token(value: Any) -> str:
    return str(value or "").strip().lower().split("/", 1)[0].strip()


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
    status_sql = {"pending": "queued", "approved": "done", "rejected": "canceled"}.get(status or "")
    where = "WHERE t.task_type = 'review' AND t.payload->>'kind' = 'seo_strategy'"
    params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
    if status_sql:
        where += " AND t.status = :status"
        params["status"] = status_sql
    if business_id:
        where += " AND t.payload->>'business_id' = :business_id AND s.business_id = :business_id"
        params["business_id"] = business_id
    if site_id:
        where += " AND t.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    if search:
        where += " AND (t.title ILIKE :search OR t.decision->>'query' ILIKE :search)"
        params["search"] = f"%{search.strip()}%"
    if strategy_type:
        where += " AND t.decision->>'strategy_type' = :strategy_type"
        params["strategy_type"] = strategy_type
    if priority:
        where += " AND t.priority = :priority"
        params["priority"] = priority
    if evidence_level:
        where += " AND t.decision->>'evidence_level' = :evidence_level"
        params["evidence_level"] = evidence_level
    rows = await session.execute(
        text(
            f"""
            SELECT t.id, t.status, t.priority, t.score, t.site_id, s.name AS site_name,
                   t.keyword_id, t.post_id, t.article_id, t.title, t.decision, t.created_at, t.updated_at,
                   t.payload->>'candidate_id' AS candidate_id, t.payload->>'plan_id' AS plan_id,
                   e.status AS execution_status, e.decision->>'current_stage' AS execution_stage,
                   e.logs AS execution_logs,
                   e.error_message AS execution_error,
                   e.started_at AS execution_started_at, e.finished_at AS execution_finished_at,
                   e.run_after AS execution_run_after,
                   (e.payload->>'auto_publish')::boolean AS auto_publish
              FROM seo_agent.tasks t
              LEFT JOIN seo_agent.sites s ON s.id = t.site_id
              LEFT JOIN seo_agent.tasks e
                ON e.id = CAST(t.decision->>'execution_task_id' AS uuid)
               AND e.task_type IN ('new_article', 'update_article')
              {where}
             ORDER BY t.score DESC NULLS LAST, t.created_at DESC
             LIMIT :limit
            """
        ),
        params,
    )
    return [_strategy_row(dict(row)) for row in rows.mappings().all()]


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
                  CASE WHEN :strategy_type = 'update_article' THEN true ELSE EXISTS (
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


async def review_strategy(session: AsyncSession, *, task_id: str, approved: bool) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT t.id, t.site_id, t.keyword_id, t.post_id, t.article_id, t.title, t.decision, "
                "       s.site_type, s.status AS site_status, s.business_id, s.strategy_enabled, "
                "       t.payload->>'business_id' AS task_business_id, "
                "       t.payload->>'candidate_id' AS candidate_id, t.payload->>'plan_id' AS plan_id, "
                "       t.payload->>'analysis_batch_id' AS analysis_batch_id "
                "FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id "
                "WHERE t.id = CAST(:id AS uuid) AND t.task_type = 'review' AND t.status = 'queued' "
                "AND t.payload->>'kind' = 'seo_strategy' "
                "FOR UPDATE OF t"
            ),
            {"id": task_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("strategy task not found")
    decision = dict(row["decision"] or {})
    if approved:
        await _validate_current_strategy(session, row=dict(row), decision=decision)
        if decision.get("strategy_type") == "hold" or decision.get("priority") == "Hold":
            raise ValueError("Hold 策略必须先补齐诊断证据，不能批准执行")
        execution_type = "new_article" if decision.get("strategy_type") == "new_article" else "update_article"
        target_url = resolve_strategy_target_url(decision)
        execution_strategy = {**decision, **({"target_url": target_url} if target_url else {})}
        execution = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, keyword_id, post_id, article_id,
                   title, target_url, payload, required_data, decision, logs)
                VALUES
                  (:task_type, 'queued', :priority, :score, :site_id, :keyword_id, :post_id, :article_id,
                   :title, :target_url, CAST(:payload AS jsonb), :required_data, CAST(:decision AS jsonb),
                   jsonb_build_array(jsonb_build_object('stage', 'queued', 'message', '人工审核通过，已进入待执行队列', 'at', now())))
                RETURNING id
                """
            ),
            {
                "task_type": execution_type,
                "priority": decision.get("priority") or "P2",
                "score": decision.get("score") or 0,
                "site_id": row["site_id"],
                "keyword_id": row["keyword_id"],
                "post_id": row["post_id"],
                "article_id": row["article_id"],
                "title": f"执行策略：{row['title']}",
                "target_url": target_url,
                "payload": json.dumps({
                    "strategy_task_id": str(task_id),
                    "strategy": execution_strategy,
                    "scope_key": decision.get("scope_key"),
                    "strategy_fingerprint": decision.get("strategy_fingerprint"),
                    "evidence_fingerprint": decision.get("evidence_fingerprint"),
                    "policy_version": decision.get("policy_version"),
                    "auto_publish": execution_type == "new_article",
                }, ensure_ascii=False),
                "required_data": ["approved_strategy"],
                "decision": json.dumps({"source_strategy_id": str(task_id), "strategy_type": execution_type}, ensure_ascii=False),
            },
        )
        execution_id = execution.scalar_one()
        if row["keyword_id"]:
            await session.execute(
                text(
                    "UPDATE seo_agent.keywords SET assigned_site_id = CAST(:site_id AS uuid), "
                    "status = CASE WHEN :type = 'new_article' THEN 'queued' ELSE status END, updated_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND status NOT IN ('written', 'published')"
                ),
                {"site_id": row["site_id"], "type": execution_type, "id": row["keyword_id"]},
            )
        decision.update({"review_status": "approved", "execution_task_id": str(execution_id)})
        status = "done"
    else:
        decision["review_status"] = "rejected"
        status = "canceled"
        execution_id = None
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = :status, decision = CAST(:decision AS jsonb), "
            "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"
        ),
        {"status": status, "decision": json.dumps(decision, ensure_ascii=False), "id": task_id},
    )
    await session.commit()
    return {"ok": True, "status": decision["review_status"], "execution_task_id": execution_id}


async def execute_strategy(
    session: AsyncSession,
    *,
    task_id: str,
    allow_running: bool = False,
    execution_task_id: str | None = None,
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
            text("SELECT name, status, business_id, strategy_enabled, market, language_code FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"),
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
        return {"ok": False, "status": "blocked", "execution_task_id": str(execution_id), "error": message}
    if not execution["site_id"] or (execution["task_type"] == "new_article" and not execution["keyword_id"]):
        message = "新文章策略缺少关键词或目标站点" if execution["task_type"] == "new_article" else "更新策略缺少目标站点"
        await _finish_execution_task(session, execution_id, status="failed", error_message=message)
        return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": message}
    update_post_id = None
    if execution["task_type"] == "update_article":
        post = (
            await session.execute(
                text("SELECT external_id FROM seo_agent.posts WHERE id = CAST(:id AS uuid) AND site_id = CAST(:site_id AS uuid)"),
                {"id": execution["post_id"], "site_id": execution["site_id"]},
            )
        ).mappings().first()
        update_post_id = str(post["external_id"]) if post and post["external_id"] else None
        if not update_post_id:
            await _finish_execution_task(session, execution_id, status="failed", error_message="旧文缺少远端文章 ID")
            return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": "旧文缺少远端文章 ID"}

    keyword_context = None
    if execution["task_type"] == "update_article" and not execution["keyword_id"]:
        query = str(approved_decision.get("query") or "").strip()
        if not query:
            message = "更新策略缺少查询词"
            await _finish_execution_task(session, execution_id, status="failed", error_message=message)
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
        await ensure_effect(
            session,
            execution_task_id=str(execution_id),
            strategy_task_id=str(task_id),
            site_id=str(execution["site_id"]),
            article_id=None,
            strategy=approved_decision,
        )
        await session.commit()
    except Exception as error:  # noqa: BLE001
        await session.rollback()
        await _finish_execution_task(session, execution_id, status="failed", error_message=f"效果基线创建失败：{error}")
        await session.commit()
        return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "error": str(error)}

    heartbeat = asyncio.create_task(_execution_heartbeat(str(execution_id), asyncio.current_task()))
    from app.services.article_generation_service import generate_article_pipeline

    try:
        result = await generate_article_pipeline(
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
        scheduled_payload = dict(execution.get("payload") or {})
        if (scheduled_payload.get("auto_publish") or execution["task_type"] == "update_article") and article_id:
            from app.services.publish_service import publish_article

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
                published = await publish_article(
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
                await cancel_unpublished_effect(
                    session,
                    execution_task_id=str(execution_id),
                    reason=published.get("error") or "自动发布失败",
                )
                await _finish_execution_task(session, execution_id, status="failed", error_message=published.get("error") or "自动发布失败")
                await session.commit()
                return {"ok": False, "status": "failed", "execution_task_id": str(execution_id), "result": result, "publish": published}
            try:
                await mark_effect_published(
                    session,
                    execution_task_id=str(execution_id),
                    article_id=str(article_id),
                    target_url=published.get("url"),
                    action=approved_decision.get("strategy_type"),
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
                "logs = COALESCE(logs, '[]'::jsonb) || jsonb_build_array(jsonb_build_object('stage', 'done', 'message', '文章生成并保存完成', 'at', now())), "
                "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": execution_id, "article_id": article_id},
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


async def clear_strategy_queue(session: AsyncSession, *, business_id: str) -> dict[str, int]:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:business_id))"), {"business_id": business_id})
    running = int(
        (await session.execute(
            text(
                "SELECT count(*) FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id "
                "WHERE s.business_id = :business_id AND t.task_type IN ('new_article', 'update_article') AND t.status = 'running'"
            ),
            {"business_id": business_id},
        )).scalar_one()
        or 0
    )
    if running:
        raise ValueError("仍有任务正在执行，不能清空队列")

    executions = (
        await session.execute(
            text(
                "SELECT t.id::text, t.keyword_id::text, t.payload->>'strategy_task_id' AS review_id "
                "FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id "
                "WHERE s.business_id = :business_id AND t.task_type IN ('new_article', 'update_article') "
                "AND t.status = 'queued' AND t.article_id IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM seo_agent.articles a WHERE a.task_id = t.id)"
            ),
            {"business_id": business_id},
        )
    ).mappings().all()
    pending_reviews = (
        await session.execute(
            text(
                "SELECT t.id::text, t.keyword_id::text FROM seo_agent.tasks t "
                "WHERE t.task_type = 'review' AND t.status = 'queued' AND t.payload->>'kind' = 'seo_strategy' "
                "AND t.payload->>'business_id' = :business_id "
                "AND NOT EXISTS (SELECT 1 FROM seo_agent.tasks e WHERE e.payload->>'strategy_task_id' = t.id::text OR e.decision->>'source_strategy_id' = t.id::text) "
                "AND NOT EXISTS (SELECT 1 FROM seo_agent.articles a WHERE a.task_id = t.id)"
            ),
            {"business_id": business_id},
        )
    ).mappings().all()
    execution_ids = [row["id"] for row in executions]
    review_ids = [row["review_id"] for row in executions if row["review_id"]] + [row["id"] for row in pending_reviews]
    keyword_ids = [row["keyword_id"] for row in executions if row["keyword_id"]] + [row["keyword_id"] for row in pending_reviews if row["keyword_id"]]

    executions_canceled = 0
    if execution_ids:
        canceled = await session.execute(
            text("UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() WHERE id::text = ANY(:ids) RETURNING id"),
            {"ids": execution_ids},
        )
        executions_canceled = len(canceled.all())
    strategies_canceled = 0
    if review_ids:
        reviews = await session.execute(
            text("UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() WHERE id::text = ANY(:ids) RETURNING id"),
            {"ids": review_ids},
        )
        strategies_canceled = len(reviews.all())
    plans = await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE task_type = 'review' AND status = 'queued' AND payload->>'kind' = 'strategy_plan' "
            "AND payload->>'business_id' = :business_id RETURNING id"
        ),
        {"business_id": business_id},
    )
    plans_cleared = len(plans.all())
    workspace = await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE task_type = 'review' AND status = 'done' "
            "AND payload->>'kind' IN ('strategy_candidate', 'strategy_analysis_batch') "
            "AND payload->>'business_id' = :business_id RETURNING payload->>'kind' AS kind"
        ),
        {"business_id": business_id},
    )
    cleared_kinds = [row[0] for row in workspace.all()]
    if keyword_ids:
        await session.execute(
            text("UPDATE seo_agent.keywords SET status = 'analyzed', updated_at = now() WHERE id::text = ANY(:ids) AND status = 'queued'"),
            {"ids": keyword_ids},
        )
    await session.commit()
    return {
        "executions_canceled": executions_canceled,
        "strategies_canceled": strategies_canceled,
        "plans_cleared": plans_cleared,
        "candidates_cleared": cleared_kinds.count("strategy_candidate"),
        "analysis_batches_cleared": cleared_kinds.count("strategy_analysis_batch"),
    }


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
) -> None:
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            async with SessionLocal() as session:
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


async def _load_site_content(session: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    rows = await session.execute(
        text(
            """
            SELECT site_id, url, title, primary_keyword, topic_cluster
              FROM seo_agent.posts
             WHERE url IS NOT NULL AND url <> ''
            UNION ALL
            SELECT site_id, target_url AS url, title, primary_keyword, NULL AS topic_cluster
              FROM seo_agent.articles
             WHERE target_url IS NOT NULL AND target_url <> ''
            """
        )
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows.mappings().all():
        grouped.setdefault(str(row["site_id"]), []).append(dict(row))
    return grouped


def _build_internal_link_plan(query: str, content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_terms = _terms(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in content:
        terms = _terms(" ".join(str(item.get(key) or "") for key in ("title", "primary_keyword", "topic_cluster")))
        overlap = len(query_terms & terms)
        if overlap:
            scored.append((overlap, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "target_url": item["url"],
            "title": item.get("title"),
            "anchor": item.get("primary_keyword") or item.get("title"),
            "reason": "同站点相关主题文章",
        }
        for _, item in scored[:3]
    ]


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]+", value.lower()) if len(term) > 2}


def _build_strategy(row: dict[str, Any], ga4: dict[str, Any] | None, internal_link_plan: list[dict[str, Any]]) -> dict[str, Any] | None:
    impressions = int(row.get("impressions") or 0)
    clicks = int(row.get("clicks") or 0)
    ctr = float(row.get("ctr") or 0)
    position = float(row.get("avg_position") or 0)
    sessions = int((ga4 or {}).get("sessions") or 0)
    conversions = float((ga4 or {}).get("conversions") or 0)
    audit = row.get("audit_decision") or {}
    audit_action = str(audit.get("action") or "")
    profile = row.get("knowledge_profile") or {}
    knowledge_confirmed = "knowledge_profile" not in row or profile.get("status") == "confirmed"
    existing_target_without_analysis = bool((row.get("article_id") or row.get("post_id")) and not row.get("post_analysis_id"))
    existing_content = bool(row.get("article_id") or row.get("post_id"))
    evidence_level = "confirmed" if impressions >= 100 and clicks >= 5 else "directional"
    confidence = 0.82 if evidence_level == "confirmed" else 0.58
    if not knowledge_confirmed or audit_action == "hold" or existing_target_without_analysis:
        strategy_type = "hold"
    elif row.get("article_id"):
        strategy_type = "update_article"
    else:
        strategy_type = audit_action if audit_action in {"new_article", "update_article"} else ("update_article" if existing_content else "new_article")
    priority = "P1" if impressions >= 500 and position <= 20 else "P2"
    if not row.get("last_seen"):
        priority = row.get("keyword_priority") or "Hold"
        confidence = 0.58 if priority != "Hold" else 0.35
        evidence_level = "directional" if priority != "Hold" else "insufficient"
    elif impressions < 50:
        priority = "Hold"
        confidence = 0.35
        evidence_level = "insufficient"
    if strategy_type == "hold":
        priority = "Hold"
        confidence = min(confidence, 0.35)
        evidence_level = "insufficient"
    elif audit.get("priority"):
        priority = audit["priority"]
    if strategy_type != "hold" and audit.get("confidence") is not None:
        confidence = float(audit["confidence"])
    if strategy_type != "hold" and audit.get("evidence_level"):
        evidence_level = audit["evidence_level"]
    if priority == "Hold":
        strategy_type = "hold"
        confidence = min(confidence, 0.35)
        evidence_level = "insufficient"
    score = float(row.get("audit_score") or row.get("keyword_score") or 0) if not row.get("last_seen") else round(min(99, impressions / 10 + max(0, 21 - position) * 2 + (0 if ctr >= 0.03 else 10)), 2)
    if strategy_type == "hold":
        score = 0
    if strategy_type == "hold":
        title = f"暂缓策略：{row['query']}"
        action = "先确认站点知识画像，再决定更新或新建" if not knowledge_confirmed else "先同步已发布文章并完成正文诊断，再决定更新或新建"
    elif strategy_type == "update_article":
        title = f"优化已有内容：{row.get('article_title') or row.get('post_title') or row['query']}"
        action = "优化标题与 Meta，补充搜索意图段落、FAQ 和相关内链"
    else:
        title = f"创建新文章：{row['query']}"
        action = "创建新 Brief，生成文章大纲，加入相关内链并进入生文队列"
    audit_matches_strategy = audit_action == strategy_type
    if strategy_type != "hold" and audit_matches_strategy:
        action = audit.get("recommended_action") or action
    gsc_note = f"GSC 28 天展示 {impressions}、点击 {clicks}、CTR {ctr:.2%}、平均排名 {position:.1f}" if row.get("last_seen") else "该站点暂无 GSC 查询记录"
    serp_note = f"SERP 已缓存 {int(row.get('serp_result_count') or 0)} 条结果" if row.get("serp_snapshot_id") else "SERP 待执行阶段获取"
    positioning = str(profile.get("positioning") or "").strip()
    scope = str(row.get("content_scope") or "").strip() or "、".join(profile.get("in_scope_topics") or [])
    knowledge_note = f"站点定位：{positioning}" if knowledge_confirmed and positioning else "站点知识画像未确认"
    if scope:
        knowledge_note += f"；内容范围：{scope}"
    audit_note = "站点知识画像未确认" if not knowledge_confirmed else (
        "检测到站点文章已存在，但未进入文章诊断快照"
        if existing_target_without_analysis
        else str(audit.get("reason") or "").strip() if audit_matches_strategy
        else "站点库存事实与候选诊断冲突，已按库存事实纠正动作"
    )
    reason = f"{audit_note.rstrip('；。') + '；' if audit_note else ''}{gsc_note}；GA4 会话 {sessions}、转化 {conversions:g}；{serp_note}；{knowledge_note}。"
    return {
        "business_id": row.get("business_id"),
        "topic_cluster_id": row.get("topic_cluster_id"),
        "strategy_type": strategy_type,
        "title": title,
        "query": row["query"],
        "priority": priority,
        "score": score,
        "confidence": confidence,
        "evidence_level": evidence_level,
        "reason": reason,
        "recommended_action": action,
        "site_context": {
            "content_role": row.get("content_role"),
            "content_scope": row.get("content_scope"),
            "knowledge_profile": profile,
        },
        "internal_link_plan": internal_link_plan,
        "evidence": {
            "gsc": {"impressions": impressions, "clicks": clicks, "ctr": ctr, "avg_position": position},
            "ga4": {"sessions": sessions, "conversions": conversions},
            "serp": {"snapshot_id": str(row["serp_snapshot_id"]) if row.get("serp_snapshot_id") else None, "result_count": int(row.get("serp_result_count") or 0)},
            "site_content": {
                "article_id": str(row["article_id"]) if row.get("article_id") else None,
                "article_status": row.get("article_status"),
                "published_url": row.get("article_published_url") or row.get("post_url"),
                "published_post_id": row.get("article_published_post_id"),
                "post_id": str(row["post_id"]) if row.get("post_id") else None,
                "post_match_type": row.get("post_match_type"),
            },
            "content_audit": {
                "task_id": str(row["audit_task_id"]) if row.get("audit_task_id") else None,
                "scanned_at": row.get("audit_scanned_at"),
                "post_analysis": {
                    "id": str(row["post_analysis_id"]) if row.get("post_analysis_id") else None,
                    "analyzed_at": str(row["post_analysis_analyzed_at"]) if row.get("post_analysis_analyzed_at") else None,
                },
                "competitor_gap": (audit.get("ai") or {}).get("competitor_gap") if audit_matches_strategy else None,
            },
        },
    }


def _strategy_row(row: dict[str, Any]) -> dict[str, Any]:
    decision = row.pop("decision") or {}
    return {**row, **decision, "status": {"queued": "pending", "done": "approved", "canceled": "rejected"}.get(row.get("status"), row.get("status"))}
