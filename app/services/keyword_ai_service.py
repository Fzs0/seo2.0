"""AI 关键词策略分析：结果写回 keywords.ai_review。"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai_provider import generate_ai_content
from app.core.database import SessionLocal
from app.services.site_service import resolve_site


_ANALYSIS_RUNS: dict[str, asyncio.Task[None]] = {}
_AI_REQUEST_SIZE = 10


async def analyze_keyword_strategy(
    session: AsyncSession,
    *,
    business_id: str,
    keyword_ids: list[str] | None = None,
    limit: int = 10,
    opportunity_type: str | None = None,
) -> dict[str, Any]:
    keywords = await _load_keywords(
        session,
        business_id=business_id,
        keyword_ids=keyword_ids,
        limit=limit,
        opportunity_type=opportunity_type,
    )
    if not keywords:
        return {"items": [], "updated": 0}

    sites = await _load_sites(session, {str(keyword.get("business_id") or "") for keyword in keywords} - {""})
    ai = await generate_ai_content(stage="keyword_analysis", prompt=_prompt(keywords, opportunity_type, sites))
    parsed = _parse_json(ai.get("content") or "")
    if not parsed:
        return {"items": keywords, "updated": 0, "ai": {**ai, "content": ""}, "error": ai.get("error") or ai.get("status") or "ai-empty-or-invalid-json"}

    by_id = {str(x.get("keywordId") or x.get("id") or ""): x for x in parsed if isinstance(x, dict)}
    updated = 0
    for kw in keywords:
        strategy = by_id.get(str(kw["id"]))
        if strategy:
            await _save_strategy(session, kw, strategy, ai, sites)
            updated += 1
    await session.commit()
    return {"items": parsed, "selected": len(keywords), "updated": updated, "ai": {**ai, "content": ""}}


async def start_keyword_analysis(
    session: AsyncSession,
    *,
    business_id: str | None = None,
    keyword_ids: list[str] | None = None,
    limit: int = 10,
    opportunity_type: str | None = None,
) -> dict[str, Any]:
    business_id = await _resolve_analysis_business(session, keyword_ids, business_id)
    active = (
        await session.execute(
            text(
                "SELECT id, status FROM seo_agent.tasks "
                "WHERE task_type = 'keyword_review' AND status IN ('queued', 'running') "
                "AND payload->>'business_id' = :business_id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    if active and str(active["id"]) in _ANALYSIS_RUNS:
        return {"run_id": str(active["id"]), "status": active["status"]}
    if active:
        await session.execute(
            text(
                "UPDATE seo_agent.tasks SET status = 'failed', error_message = '服务已重启，请重新分析未完成关键词', "
                "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": str(active["id"])},
        )
        await session.commit()

    total = await _count_unanalyzed(session, business_id, keyword_ids)
    if not total:
        return {
            "run_id": "",
            "status": "done",
            "decision": {"total": 0, "updated": 0, "remaining": 0, "message": "当前业务的可用页面簇均已完成 AI 分析"},
        }

    await clear_unreviewed_assignments(session, business_id=business_id, keyword_ids=keyword_ids)
    row = (
        await session.execute(
            text(
                "INSERT INTO seo_agent.tasks "
                "(task_type, status, priority, title, payload) "
                "VALUES ('keyword_review', 'queued', 'P1', :title, CAST(:payload AS jsonb)) "
                "RETURNING id"
            ),
            {
                "title": "AI 页面簇策略分析",
                "payload": json.dumps(
                    {"business_id": business_id, "analysis_unit": "page_cluster", "limit": limit, "opportunity_type": opportunity_type, "keyword_ids": keyword_ids or []},
                    ensure_ascii=False,
                ),
            },
        )
    ).scalar_one()
    run_id = str(row)
    await session.commit()
    _ANALYSIS_RUNS[run_id] = asyncio.create_task(
        _run_keyword_analysis(
            run_id,
            business_id=business_id,
            keyword_ids=keyword_ids,
            limit=limit,
            opportunity_type=opportunity_type,
        )
    )
    return {"run_id": run_id, "status": "queued"}


async def _run_keyword_analysis(
    run_id: str,
    *,
    business_id: str,
    keyword_ids: list[str] | None,
    limit: int,
    opportunity_type: str | None,
) -> None:
    try:
        async with SessionLocal() as session:
            await _set_analysis_status(session, run_id, "running")
            total = await _count_unanalyzed(session, business_id, keyword_ids)
            updated = 0
            while True:
                if updated >= total:
                    break
                await _set_analysis_status(
                    session,
                    run_id,
                    "running",
                    decision={"total": total, "updated": updated, "remaining": max(0, total - updated), "message": f"正在分析第 {updated + 1}-{min(total, updated + _AI_REQUEST_SIZE)} 个页面簇"},
                )
                result = await analyze_keyword_strategy(
                    session,
                    business_id=business_id,
                    keyword_ids=keyword_ids,
                    limit=_AI_REQUEST_SIZE,
                    opportunity_type=opportunity_type,
                )
                if result.get("error"):
                    raise RuntimeError(str(result["error"]))
                selected = int(result.get("selected") or 0)
                if not selected:
                    break
                batch_updated = int(result.get("updated") or 0)
                if not batch_updated:
                    raise RuntimeError("AI 未返回可保存的页面簇策略")
                updated += batch_updated
                await _set_analysis_status(
                    session,
                    run_id,
                    "running",
                    decision={"total": total, "updated": updated, "remaining": max(0, total - updated), "message": f"已分析 {updated}/{total} 个页面簇"},
                )
            await _set_analysis_status(
                session,
                run_id,
                "done",
                decision={"total": total, "updated": updated, "remaining": 0, "message": f"全部 {updated} 个页面簇分析完成"},
            )
    except asyncio.CancelledError:
        async with SessionLocal() as session:
            await _set_analysis_status(session, run_id, "canceled", error_message="用户停止了 AI 分析")
    except Exception as error:  # noqa: BLE001
        async with SessionLocal() as session:
            await _set_analysis_status(session, run_id, "failed", error_message=str(error))
    finally:
        _ANALYSIS_RUNS.pop(run_id, None)


async def cancel_keyword_analysis(session: AsyncSession, run_id: str) -> dict[str, Any]:
    row = (
        await session.execute(
            text("SELECT status FROM seo_agent.tasks WHERE id = CAST(:id AS uuid) AND task_type = 'keyword_review'"),
            {"id": run_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("AI 分析任务不存在")
    if row["status"] in {"done", "failed", "canceled"}:
        return {"run_id": run_id, "status": row["status"]}
    await _set_analysis_status(session, run_id, "canceled", error_message="用户停止了 AI 分析")
    task = _ANALYSIS_RUNS.get(run_id)
    if task and not task.done():
        task.cancel()
    return {"run_id": run_id, "status": "canceled"}


async def get_keyword_analysis(session: AsyncSession, run_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, status, title, decision, logs, error_message, started_at, finished_at, created_at, updated_at "
                "FROM seo_agent.tasks WHERE id = CAST(:id AS uuid) AND task_type = 'keyword_review'"
            ),
            {"id": run_id},
        )
    ).mappings().first()
    if not row:
        return None
    if row["status"] in {"queued", "running"} and run_id not in _ANALYSIS_RUNS:
        await _set_analysis_status(session, run_id, "failed", error_message="分析进程已中断，请重新启动剩余关键词分析")
        return {**dict(row), "status": "failed", "error_message": "分析进程已中断，请重新启动剩余关键词分析"}
    return dict(row)


async def get_latest_keyword_analysis(session: AsyncSession, business_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, status, title, decision, logs, error_message, started_at, finished_at, created_at, updated_at "
                "FROM seo_agent.tasks WHERE task_type = 'keyword_review' "
                "AND payload->>'business_id' = :business_id ORDER BY created_at DESC LIMIT 1"
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    return {**dict(row), "id": str(row["id"])} if row else None


async def _set_analysis_status(
    session: AsyncSession,
    run_id: str,
    status: str,
    *,
    decision: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = CAST(:status AS text), "
            "decision = COALESCE(decision, '{}'::jsonb) || COALESCE(CAST(:decision AS jsonb), '{}'::jsonb), "
            "logs = COALESCE(logs, '[]'::jsonb) || jsonb_build_array(jsonb_build_object("
            "'at', now(), 'stage', CAST(:status AS text), 'message', COALESCE(CAST(:error_message AS text), CAST(:decision AS jsonb)->>'message', ''))), "
            "error_message = CAST(:error_message AS text), started_at = CASE WHEN CAST(:status AS text) = 'running' THEN COALESCE(started_at, now()) ELSE started_at END, "
            "finished_at = CASE WHEN CAST(:status AS text) IN ('done', 'failed', 'canceled') THEN now() ELSE finished_at END, updated_at = now() "
            "WHERE id = CAST(:id AS uuid) AND task_type = 'keyword_review' AND status NOT IN ('done', 'failed', 'canceled')"
        ),
        {"id": run_id, "status": status, "decision": json.dumps(decision, ensure_ascii=False) if decision is not None else None, "error_message": error_message},
    )
    await session.commit()


async def _load_keywords(
    session: AsyncSession,
    *,
    business_id: str,
    keyword_ids: list[str] | None,
    limit: int,
    opportunity_type: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"business_id": business_id, "limit": max(1, min(limit, _AI_REQUEST_SIZE))}
    where = "WHERE business_id = :business_id AND status IN ('imported', 'analyzed', 'planned') AND (COALESCE(preflight_status, 'ready') = 'ready' OR (source = 'import' AND preflight_status = 'needs_review')) AND COALESCE(priority, '') <> 'Hold' AND cluster_role IN ('pillar', 'standalone') AND (source <> 'semrush_strategy_builder' OR raw #>> '{_strategy_builder,cluster_validation,status}' IN ('validated', 'provisional')) AND NOT (COALESCE(ai_review, '{}'::jsonb) ?| ARRAY['strategy', 'reservation'])"
    if keyword_ids:
        where += " AND topic_cluster_id IN (SELECT topic_cluster_id FROM seo_agent.keywords WHERE business_id = :business_id AND id::text = ANY(:ids))"
        params["ids"] = keyword_ids
    elif opportunity_type == "long_tail":
        where += " AND array_length(regexp_split_to_array(trim(keyword), '\\s+'), 1) >= 3"
    elif opportunity_type == "qa":
        where += " AND keyword ~* '^(who|what|when|where|why|how|can|does|is|are|which)\\b|\\?'"
    elif opportunity_type == "low_kd":
        where += " AND kd <= 35"
    rows = await session.execute(
        text(
            f"""
            SELECT id, keyword, volume, kd, cpc, intent, serp_features, trend, trend_data, pkd,
                   potential_traffic, competitive_density, serp_results, keyword_type,
                   preflight_status, preflight_reason, source_batch_id, business_id,
                   topic_cluster, topic_cluster_id, cluster_role, cluster_size, pillar_keyword, page_group,
                   market, language_code, google_gl, google_hl,
                   assigned_site_id, assigned_site_label, page_type, page_role, target_asset_url,
                   asset_status, content_action, priority, score, status, reason, ai_review,
                   raw #> '{{_strategy_builder,cluster_validation}}' AS cluster_validation,
                   raw #> '{{_strategy_builder,top10_urls}}' AS top10_urls,
                   raw->>'url' AS imported_page_url,
                   raw->>'title' AS imported_page_title,
                   raw->>'description' AS imported_page_description,
                   (
                     SELECT jsonb_agg(member ORDER BY member.volume DESC, member.kd ASC)
                       FROM (
                         SELECT m.keyword, m.volume, m.kd, m.intent
                           FROM seo_agent.keywords m
                          WHERE m.business_id = seo_agent.keywords.business_id
                            AND m.topic_cluster_id = seo_agent.keywords.topic_cluster_id
                          ORDER BY m.volume DESC NULLS LAST, m.kd ASC NULLS LAST
                          LIMIT 15
                       ) member
                   ) AS member_keywords
              FROM seo_agent.keywords
              {where}
             ORDER BY CASE WHEN status IN ('imported', 'planned') THEN 0 ELSE 1 END,
                      updated_at ASC, score DESC NULLS LAST, volume DESC NULLS LAST
             LIMIT :limit
            """
        ),
        params,
    )
    return [dict(r) for r in rows.mappings().all()]


async def _resolve_analysis_business(
    session: AsyncSession,
    keyword_ids: list[str] | None,
    business_id: str | None,
) -> str:
    requested = (business_id or "").strip()
    ids = list(dict.fromkeys(keyword_ids or []))
    if not ids:
        if requested:
            return requested
        raise ValueError("启动关键词 AI 分析需要 businessId 或同一业务的 keywordIds")

    rows = (
        await session.execute(
            text(
                "SELECT business_id, count(*) AS count FROM seo_agent.keywords "
                "WHERE id::text = ANY(:ids) GROUP BY business_id"
            ),
            {"ids": ids},
        )
    ).mappings().all()
    if sum(int(row["count"]) for row in rows) != len(ids):
        raise ValueError("部分关键词不存在")
    businesses = {str(row["business_id"] or "").strip() for row in rows}
    if len(businesses) != 1 or not next(iter(businesses), ""):
        raise ValueError("所选关键词必须属于同一业务")
    resolved = next(iter(businesses))
    if requested and requested != resolved:
        raise ValueError("businessId 与所选关键词不一致")
    return resolved


async def _load_sites(session: AsyncSession, business_ids: set[str]) -> list[dict[str, Any]]:
    if not business_ids:
        return []
    rows = await session.execute(
        text(
            """
            SELECT id, site_key, name, business_id, site_type, market, language_code, content_role,
                   content_scope,
                   CASE WHEN knowledge_profile->>'status' = 'confirmed'
                        THEN knowledge_profile ELSE '{}'::jsonb END AS knowledge_profile
             FROM seo_agent.sites
             WHERE status = 'active' AND strategy_enabled = true AND business_id = ANY(:business_ids)
             ORDER BY name
            """
        ),
        {"business_ids": sorted(business_ids)},
    )
    return [dict(row) for row in rows.mappings().all()]


def _prompt(
    keywords: list[dict[str, Any]],
    opportunity_type: str | None,
    sites: list[dict[str, Any]],
    planning_context: dict[str, Any] | None = None,
) -> str:
    profile_keys = (
        "positioning", "audience", "products", "in_scope_topics", "out_of_scope_topics",
        "content_types", "restricted_topics", "editorial_rules",
    )
    allowed_sites = [
        {
            **{key: site.get(key) for key in ("id", "name", "business_id", "site_type", "market", "language_code", "content_role", "content_scope")},
            "knowledge_profile": {
                key: (site.get("knowledge_profile") or {}).get(key)
                for key in profile_keys
                if (site.get("knowledge_profile") or {}).get(key)
            },
        }
        for site in sites
    ]
    focus = {
        "long_tail": "Focus on long-tail SEO opportunities with specific search intent.",
        "qa": "Focus on question-answer opportunities suitable for FAQ or guide articles.",
        "low_kd": "Focus on lower-difficulty keywords that can be executed quickly.",
    }.get(opportunity_type or "", "Focus on the best content opportunities.")
    return "\n".join(
        [
            "You are an SEO strategist. Each input item represents one validated page cluster. Analyze each cluster once and return ONLY a JSON array.",
            focus,
            "Do not invent traffic, rankings, URLs, or facts. Use null when unsure.",
            "Allowed priority: P0, P1, P2, P3, Hold.",
            "First classify relevance as relevant, irrelevant, or needs_review. Irrelevant and needs_review keywords must have no site assignment.",
            "Choose assignedSiteId and assignedSiteLabel only from allowedSites. Match market/language and content role; return null when no site is suitable.",
            "If an allowed site has a confirmed knowledge_profile, use its positioning, audience, products, in_scope_topics, out_of_scope_topics, content_types, and editorial_rules as hard content boundaries. Never use a draft or empty profile as a fact.",
            "Use Semrush signals as evidence: intent, keywordType, volume, KD, PKD, potentialTraffic, competitiveDensity, serpResults, trendData, and serpFeatures. SERP features should influence pageType and required sections (for example People also ask suggests FAQ), but never be treated as facts about the page.",
            "When importedPageUrl, importedPageTitle, or importedPageDescription is present, it is user-supplied context for an existing page. Use it to decide whether to update or avoid duplicating that page; do not treat its description as independently verified factual evidence.",
            "Each output item fields: keywordId, relevance, intent, priority, pageType, pageRole, contentAction, assignedSiteId, assignedSiteLabel, topicCluster, targetAssetUrl, strategyReason, briefDirection, confidence. keywordId must be the input representative id.",
            "member_keywords and top10_urls are cluster evidence, not separate assignments. Return one decision for the whole topicClusterId and never split individual members across sites.",
            f"Content planning context: {json.dumps(planning_context, ensure_ascii=False, default=str)}" if planning_context else "",
            json.dumps({"pageClusters": keywords, "allowedSites": allowed_sites}, ensure_ascii=False, default=str),
        ]
    )


def _parse_json(content: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", content)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    return data if isinstance(data, list) else []


async def _save_strategy(
    session: AsyncSession,
    keyword: dict[str, Any],
    strategy: dict[str, Any],
    ai: dict[str, Any],
    sites: list[dict[str, Any]],
) -> None:
    strategy = {
        **strategy,
        "topicClusterId": keyword.get("topic_cluster_id"),
        "representativeKeyword": keyword.get("keyword"),
        "clusterValidation": keyword.get("cluster_validation") or {},
    }
    relevance = str(strategy.get("relevance") or "needs_review").lower()
    if relevance != "relevant" or not keyword.get("market") or not keyword.get("language_code"):
        await _mark_scope_hold(
            session,
            keyword,
            strategy.get("strategyReason") or ("关键词缺少市场或语种，不能自动分配站点。" if relevance == "relevant" else "AI 判定关键词不适合当前业务。"),
            strategy=strategy,
            ai=ai,
        )
        return
    cluster_site_id = None
    if keyword.get("topic_cluster_id"):
        cluster_site = await session.execute(
            text(
                "SELECT assigned_site_id FROM seo_agent.keywords "
                "WHERE topic_cluster_id = :cluster_id AND id <> CAST(:id AS uuid) "
                "AND business_id = :business_id "
                "AND assigned_site_id IS NOT NULL ORDER BY updated_at ASC LIMIT 1"
            ),
            {"cluster_id": keyword["topic_cluster_id"], "id": str(keyword["id"]), "business_id": keyword.get("business_id")},
        )
        cluster_site_id = cluster_site.scalar_one_or_none()
    allowed_site_ids = {
        str(site["id"])
        for site in sites
        if site.get("business_id") == keyword.get("business_id")
    }
    assigned_site = await resolve_site(
        session,
        site_id=str(cluster_site_id or strategy.get("assignedSiteId") or strategy.get("assigned_site_id") or "") or None,
        label=str(strategy.get("assignedSiteLabel") or strategy.get("assigned_site_label") or "") or None,
        market=keyword.get("market"),
        language_code=keyword.get("language_code"),
    )
    if assigned_site and str(assigned_site["id"]) not in allowed_site_ids:
        assigned_site = None
    assigned_site_id = str(assigned_site["id"]) if assigned_site else None
    assigned_site_label = assigned_site.get("name") if assigned_site else None
    saved_status = "analyzed" if assigned_site_id else "hold"
    await session.execute(
        text(
            """
            UPDATE seo_agent.keywords
               SET assigned_site_id = CAST(:assigned_site_id AS uuid),
                   assigned_site_label = :assigned_site_label,
                   priority = COALESCE(:priority, priority),
                   preflight_status = CASE WHEN source = 'import' AND preflight_status = 'needs_review' THEN 'ready' ELSE preflight_status END,
                   status = :status,
                   reason = COALESCE(:reason, reason),
                   ai_review = CAST(:ai_review AS jsonb),
                   updated_at = now()
             WHERE business_id = :business_id AND topic_cluster_id = :topic_cluster_id
            """
        ),
        {
            "business_id": keyword.get("business_id"),
            "topic_cluster_id": keyword.get("topic_cluster_id"),
            "assigned_site_id": assigned_site_id,
            "assigned_site_label": assigned_site_label,
            "priority": "Hold" if not assigned_site_id else (strategy.get("priority") if strategy.get("priority") in {"P0", "P1", "P2", "P3", "Hold"} else None),
            "status": saved_status,
            "reason": strategy.get("strategyReason"),
            "ai_review": json.dumps(
                {"strategy": strategy, "previous": keyword.get("ai_review") or {}, "provider": ai.get("provider"), "model": ai.get("model")},
                ensure_ascii=False,
                default=str,
            ),
        },
    )


async def _mark_scope_hold(
    session: AsyncSession,
    keyword: dict[str, Any],
    reason: str,
    *,
    strategy: dict[str, Any],
    ai: dict[str, Any],
) -> None:
    await session.execute(
        text(
            """
            UPDATE seo_agent.keywords
               SET assigned_site_id = NULL,
                   assigned_site_label = NULL,
                   priority = 'Hold',
                   status = 'hold',
                   reason = :reason,
                   ai_review = CAST(:ai_review AS jsonb),
                   updated_at = now()
             WHERE business_id = :business_id AND topic_cluster_id = :topic_cluster_id
            """
        ),
        {
            "business_id": keyword.get("business_id"),
            "topic_cluster_id": keyword.get("topic_cluster_id"),
            "reason": reason,
            "ai_review": json.dumps(
                {"strategy": strategy, "scope": "blocked", "reason": reason, "previous": keyword.get("ai_review") or {}, "provider": ai.get("provider"), "model": ai.get("model")},
                ensure_ascii=False,
                default=str,
            ),
        },
    )


async def clear_unreviewed_assignments(
    session: AsyncSession,
    *,
    business_id: str | None = None,
    keyword_ids: list[str] | None = None,
) -> None:
    business_id = (business_id or "").strip()
    if not business_id:
        return
    scope = ""
    params: dict[str, Any] = {"business_id": business_id}
    if keyword_ids:
        scope = " AND id::text = ANY(:keyword_ids)"
        params["keyword_ids"] = keyword_ids
    await session.execute(
        text(
            "UPDATE seo_agent.keywords SET assigned_site_id = NULL, assigned_site_label = NULL, "
            "status = CASE WHEN status IN ('analyzed', 'planned', 'hold') THEN 'imported' ELSE status END, updated_at = now() "
            "WHERE business_id = :business_id AND assigned_site_id IS NOT NULL AND COALESCE(preflight_status, 'ready') = 'ready' "
            "AND NOT (COALESCE(ai_review, '{}'::jsonb) ?| ARRAY['strategy', 'reservation'])" + scope
        ),
        params,
    )
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', error_message = '关键词尚未完成 AI 分析', "
            "finished_at = now(), updated_at = now() WHERE status = 'queued' AND task_type IN ('new_article', 'update_article') "
            "AND keyword_id IN (SELECT id FROM seo_agent.keywords WHERE business_id = :business_id AND COALESCE(preflight_status, 'ready') = 'ready' "
            "AND NOT (COALESCE(ai_review, '{}'::jsonb) ?| ARRAY['strategy', 'reservation'])" + scope + ")"
        ),
        params,
    )
    await session.commit()


async def _count_unanalyzed(session: AsyncSession, business_id: str, keyword_ids: list[str] | None) -> int:
    where = "business_id = :business_id AND status IN ('imported', 'analyzed', 'planned') AND (COALESCE(preflight_status, 'ready') = 'ready' OR (source = 'import' AND preflight_status = 'needs_review')) AND COALESCE(priority, '') <> 'Hold' AND cluster_role IN ('pillar', 'standalone') AND (source <> 'semrush_strategy_builder' OR raw #>> '{_strategy_builder,cluster_validation,status}' IN ('validated', 'provisional')) AND NOT (COALESCE(ai_review, '{}'::jsonb) ?| ARRAY['strategy', 'reservation'])"
    params: dict[str, Any] = {"business_id": business_id}
    if keyword_ids:
        where += " AND topic_cluster_id IN (SELECT topic_cluster_id FROM seo_agent.keywords WHERE business_id = :business_id AND id::text = ANY(:ids))"
        params["ids"] = keyword_ids
    return int((await session.execute(text(f"SELECT count(*) FROM seo_agent.keywords WHERE {where}"), params)).scalar_one() or 0)
