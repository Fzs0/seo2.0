"""最小 SEO 策略闭环：GSC/GA4 证据 -> 审核任务 -> 执行任务。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def generate_strategies(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    limit: int = 20,
    min_impressions: int = 20,
) -> dict[str, Any]:
    rows = await session.execute(
        text(
            """
            SELECT q.site_id, q.query, q.clicks, q.impressions, q.ctr, q.avg_position,
                   q.last_seen, s.name AS site_name,
                   k.id AS keyword_id, a.id AS article_id, a.title AS article_title
              FROM seo_agent.v_gsc_query_28d q
              JOIN seo_agent.sites s ON s.id = q.site_id
              LEFT JOIN seo_agent.keywords k
                ON k.assigned_site_id = q.site_id
               AND lower(k.keyword) = lower(q.query)
              LEFT JOIN seo_agent.articles a
                ON a.site_id = q.site_id
               AND lower(a.primary_keyword) = lower(q.query)
             WHERE (:site_id IS NULL OR q.site_id = CAST(:site_id AS uuid))
               AND q.impressions >= :min_impressions
             ORDER BY q.impressions DESC
             LIMIT :limit
            """
        ),
        {"site_id": site_id, "min_impressions": max(1, min_impressions), "limit": max(1, min(limit, 100))},
    )
    gsc_rows = [dict(row) for row in rows.mappings().all()]
    ga4_rows = await session.execute(
        text("SELECT site_id, sessions, conversions FROM seo_agent.v_ga4_site_28d")
    )
    ga4_by_site = {str(row["site_id"]): dict(row) for row in ga4_rows.mappings().all()}

    created: list[dict[str, Any]] = []
    for row in gsc_rows:
        strategy = _build_strategy(row, ga4_by_site.get(str(row["site_id"])))
        duplicate = await session.execute(
            text(
                """
                SELECT id FROM seo_agent.tasks
                 WHERE task_type = 'review'
                   AND status = 'queued'
                   AND site_id = :site_id
                   AND (keyword_id = :keyword_id OR (:keyword_id IS NULL AND keyword_id IS NULL))
                   AND payload->>'kind' = 'seo_strategy'
                   AND decision->>'strategy_type' = :strategy_type
                 LIMIT 1
                """
            ),
            {
                "site_id": row["site_id"],
                "keyword_id": row.get("keyword_id"),
                "strategy_type": strategy["strategy_type"],
            },
        )
        if duplicate.first():
            continue
        inserted = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, keyword_id, article_id,
                   title, payload, required_data, decision)
                VALUES
                  ('review', 'queued', :priority, :score, :site_id, :keyword_id, :article_id,
                   :title, CAST(:payload AS jsonb), :required_data, CAST(:decision AS jsonb))
                RETURNING id
                """
            ),
            {
                "priority": strategy["priority"],
                "score": strategy["score"],
                "site_id": row["site_id"],
                "keyword_id": row.get("keyword_id"),
                "article_id": row.get("article_id"),
                "title": strategy["title"],
                "payload": json.dumps({"kind": "seo_strategy", "evidence": strategy["evidence"]}, ensure_ascii=False),
                "required_data": ["gsc_28d", "ga4_28d"],
                "decision": json.dumps(strategy, ensure_ascii=False),
            },
        )
        task_id = inserted.scalar_one()
        created.append({"id": str(task_id), "site_name": row.get("site_name"), **strategy})

    await session.commit()
    return {"items": created, "created": len(created), "candidates": len(gsc_rows)}


async def list_strategies(session: AsyncSession, *, status: str | None = "pending", limit: int = 50) -> list[dict[str, Any]]:
    status_sql = {"pending": "queued", "approved": "done", "rejected": "canceled"}.get(status or "")
    where = "WHERE t.task_type = 'review' AND t.payload->>'kind' = 'seo_strategy'"
    params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
    if status_sql:
        where += " AND t.status = :status"
        params["status"] = status_sql
    rows = await session.execute(
        text(
            f"""
            SELECT t.id, t.status, t.priority, t.score, t.site_id, s.name AS site_name,
                   t.keyword_id, t.article_id, t.title, t.decision, t.created_at, t.updated_at
              FROM seo_agent.tasks t
              LEFT JOIN seo_agent.sites s ON s.id = t.site_id
              {where}
             ORDER BY t.score DESC NULLS LAST, t.created_at DESC
             LIMIT :limit
            """
        ),
        params,
    )
    return [_strategy_row(dict(row)) for row in rows.mappings().all()]


async def review_strategy(session: AsyncSession, *, task_id: str, approved: bool) -> dict[str, Any]:
    row = (
        await session.execute(
            text("SELECT id, site_id, keyword_id, article_id, title, decision FROM seo_agent.tasks WHERE id = CAST(:id AS uuid) AND task_type = 'review'"),
            {"id": task_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("strategy task not found")
    decision = dict(row["decision"] or {})
    if approved:
        execution_type = "new_article" if decision.get("strategy_type") == "new_article" else "update_article"
        execution = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, keyword_id, article_id,
                   title, payload, required_data, decision)
                VALUES
                  (:task_type, 'queued', :priority, :score, :site_id, :keyword_id, :article_id,
                   :title, CAST(:payload AS jsonb), :required_data, CAST(:decision AS jsonb))
                RETURNING id
                """
            ),
            {
                "task_type": execution_type,
                "priority": decision.get("priority") or "P2",
                "score": decision.get("score") or 0,
                "site_id": row["site_id"],
                "keyword_id": row["keyword_id"],
                "article_id": row["article_id"],
                "title": f"执行策略：{row['title']}",
                "payload": json.dumps({"strategy_task_id": str(task_id), "strategy": decision}, ensure_ascii=False),
                "required_data": ["approved_strategy"],
                "decision": json.dumps({"source_strategy_id": str(task_id), "strategy_type": execution_type}, ensure_ascii=False),
            },
        )
        execution_id = execution.scalar_one()
        if execution_type == "new_article" and row["keyword_id"]:
            await session.execute(
                text("UPDATE seo_agent.keywords SET status = 'queued', updated_at = now() WHERE id = :id AND status NOT IN ('written', 'published')"),
                {"id": row["keyword_id"]},
            )
        decision.update({"review_status": "approved", "execution_task_id": str(execution_id)})
        status = "done"
    else:
        decision["review_status"] = "rejected"
        status = "canceled"
        execution_id = None
    await session.execute(
        text("UPDATE seo_agent.tasks SET status = :status, decision = CAST(:decision AS jsonb), finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"status": status, "decision": json.dumps(decision, ensure_ascii=False), "id": task_id},
    )
    await session.commit()
    return {"ok": True, "status": decision["review_status"], "execution_task_id": execution_id}


def _build_strategy(row: dict[str, Any], ga4: dict[str, Any] | None) -> dict[str, Any]:
    impressions = int(row.get("impressions") or 0)
    clicks = int(row.get("clicks") or 0)
    ctr = float(row.get("ctr") or 0)
    position = float(row.get("avg_position") or 0)
    sessions = int((ga4 or {}).get("sessions") or 0)
    conversions = float((ga4 or {}).get("conversions") or 0)
    existing_article = bool(row.get("article_id"))
    evidence_level = "confirmed" if impressions >= 100 and clicks >= 5 else "directional"
    confidence = 0.82 if evidence_level == "confirmed" else 0.58
    strategy_type = "update_article" if existing_article else "new_article"
    priority = "P1" if impressions >= 500 and position <= 20 else "P2"
    if impressions < 50:
        priority = "Hold"
        confidence = 0.35
        evidence_level = "insufficient"
    score = round(min(99, impressions / 10 + max(0, 21 - position) * 2 + (0 if ctr >= 0.03 else 10)), 2)
    if strategy_type == "update_article":
        title = f"优化已有文章：{row.get('article_title') or row['query']}"
        action = "优化标题与 Meta，补充搜索意图段落、FAQ 和相关内链"
    else:
        title = f"创建新文章：{row['query']}"
        action = "创建新 Brief，生成文章大纲并进入生文队列"
    reason = f"GSC 28 天展示 {impressions}、点击 {clicks}、CTR {ctr:.2%}、平均排名 {position:.1f}；GA4 会话 {sessions}、转化 {conversions:g}。"
    return {
        "strategy_type": strategy_type,
        "title": title,
        "query": row["query"],
        "priority": priority,
        "score": score,
        "confidence": confidence,
        "evidence_level": evidence_level,
        "reason": reason,
        "recommended_action": action,
        "evidence": {"gsc": {"impressions": impressions, "clicks": clicks, "ctr": ctr, "avg_position": position}, "ga4": {"sessions": sessions, "conversions": conversions}},
    }


def _strategy_row(row: dict[str, Any]) -> dict[str, Any]:
    decision = row.pop("decision") or {}
    return {**row, **decision, "status": {"queued": "pending", "done": "approved", "canceled": "rejected"}.get(row.get("status"), row.get("status"))}
