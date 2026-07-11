"""AI 关键词策略分析：结果写回 keywords.ai_review。"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai_provider import generate_ai_content
from app.engine.loader import get_store
from app.services.site_service import resolve_site_id


async def analyze_keyword_strategy(
    session: AsyncSession,
    *,
    keyword_ids: list[str] | None = None,
    limit: int = 10,
    opportunity_type: str | None = None,
) -> dict[str, Any]:
    keywords = await _load_keywords(session, keyword_ids=keyword_ids, limit=limit, opportunity_type=opportunity_type)
    if not keywords:
        return {"items": [], "updated": 0}

    ai = await generate_ai_content(stage="keyword_analysis", prompt=_prompt(keywords, opportunity_type))
    parsed = _parse_json(ai.get("content") or "")
    if not parsed:
        return {"items": keywords, "updated": 0, "ai": {**ai, "content": ""}, "error": ai.get("status") or "ai-empty-or-invalid-json"}

    by_id = {str(x.get("keywordId") or x.get("id") or ""): x for x in parsed if isinstance(x, dict)}
    updated = 0
    for kw in keywords:
        strategy = by_id.get(str(kw["id"]))
        if strategy:
            await _save_strategy(session, kw, strategy, ai)
            updated += 1
    await session.commit()
    return {"items": parsed, "updated": updated, "ai": {**ai, "content": ""}}


async def _load_keywords(
    session: AsyncSession,
    *,
    keyword_ids: list[str] | None,
    limit: int,
    opportunity_type: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
    where = "WHERE status IN ('imported', 'analyzed', 'planned')"
    if keyword_ids:
        where = "WHERE id::text = ANY(:ids)"
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
            SELECT id, keyword, volume, kd, cpc, intent, topic_cluster, page_group,
                   assigned_site_id, assigned_site_label, page_type, page_role, target_asset_url,
                   asset_status, content_action, priority, score, status, reason, ai_review
              FROM seo_agent.keywords
              {where}
             ORDER BY score DESC NULLS LAST, volume DESC NULLS LAST
             LIMIT :limit
            """
        ),
        params,
    )
    return [dict(r) for r in rows.mappings().all()]


def _prompt(keywords: list[dict[str, Any]], opportunity_type: str | None) -> str:
    standard = get_store().get("siteContentAudit", {}) or {}
    focus = {
        "long_tail": "Focus on long-tail SEO opportunities with specific search intent.",
        "qa": "Focus on question-answer opportunities suitable for FAQ or guide articles.",
        "low_kd": "Focus on lower-difficulty keywords that can be executed quickly.",
    }.get(opportunity_type or "", "Focus on the best content opportunities.")
    return "\n".join(
        [
            "You are an SEO strategist. Analyze these imported keywords and return ONLY a JSON array.",
            focus,
            "Do not invent traffic, rankings, URLs, or facts. Use null when unsure.",
            "Allowed priority: P0, P1, P2, P3, Hold.",
            "Each item fields: keywordId, intent, priority, pageType, pageRole, contentAction, assignedSiteLabel, topicCluster, targetAssetUrl, strategyReason, briefDirection, confidence.",
            json.dumps({"keywords": keywords, "rules": standard}, ensure_ascii=False, default=str),
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


async def _save_strategy(session: AsyncSession, keyword: dict[str, Any], strategy: dict[str, Any], ai: dict[str, Any]) -> None:
    assigned_site_label = strategy.get("assignedSiteLabel") or keyword.get("assigned_site_label")
    assigned_site_id = await resolve_site_id(
        session,
        site_id=keyword.get("assigned_site_id"),
        label=assigned_site_label,
        market=keyword.get("market"),
        language_code=keyword.get("language_code"),
    )
    await session.execute(
        text(
            """
            UPDATE seo_agent.keywords
               SET intent = COALESCE(:intent, intent),
                   topic_cluster = COALESCE(:topic_cluster, topic_cluster),
                   assigned_site_id = COALESCE(:assigned_site_id, assigned_site_id),
                   assigned_site_label = COALESCE(:assigned_site_label, assigned_site_label),
                   page_type = COALESCE(:page_type, page_type),
                   page_role = COALESCE(:page_role, page_role),
                   target_asset_url = COALESCE(:target_asset_url, target_asset_url),
                   content_action = COALESCE(:content_action, content_action),
                   priority = COALESCE(:priority, priority),
                   status = 'analyzed',
                   reason = COALESCE(:reason, reason),
                   ai_review = CAST(:ai_review AS jsonb),
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "id": str(keyword["id"]),
            "intent": strategy.get("intent"),
            "topic_cluster": strategy.get("topicCluster"),
            "assigned_site_id": assigned_site_id,
            "assigned_site_label": assigned_site_label,
            "page_type": strategy.get("pageType"),
            "page_role": strategy.get("pageRole"),
            "target_asset_url": strategy.get("targetAssetUrl"),
            "content_action": strategy.get("contentAction"),
            "priority": strategy.get("priority") if strategy.get("priority") in {"P0", "P1", "P2", "P3", "Hold"} else None,
            "reason": strategy.get("strategyReason"),
            "ai_review": json.dumps(
                {"strategy": strategy, "previous": keyword.get("ai_review") or {}, "provider": ai.get("provider"), "model": ai.get("model")},
                ensure_ascii=False,
                default=str,
            ),
        },
    )
