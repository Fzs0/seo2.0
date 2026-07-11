"""Brief 服务：本地 brief + 可选 AI 增强 + 缓存 + 失败回退。

兼容 v1 Node.js 路由响应体：{ brief, locale, articleBriefTemplate, reference,
parentPage, targetAsset, imagePlan, recommendedUrl, briefSource, aiEnhanced, aiMeta }
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

from app.clients.ai_provider import generate_ai_content, is_stage_configured
from app.engine.content_plan import (
    article_brief_template_for,
    image_plan_for,
    reference_plan,
)
from app.engine.locale import locale_for_project, locale_instruction
from app.engine.loader import get_store
from app.services.cache import get_brief_cache
from app.services.keyword_service import build_brief

logger = structlog.get_logger(__name__)


def _ai_meta(stage: str, ai_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": stage,
        "configured": bool(ai_result.get("configured")),
        "provider": ai_result.get("provider") or "",
        "model": ai_result.get("model") or "",
        "apiFormat": ai_result.get("apiFormat") or "",
        "status": ai_result.get("status"),
    }


async def build_brief_with_optional_ai(body: dict[str, Any]) -> dict[str, Any]:
    keyword = body.get("keyword")
    project = body.get("project") or {}
    ai_stage = body.get("aiStage") or {}
    stage_name = "brief_generation"

    local = build_brief(keyword, project) if keyword else {"error": "missing-keyword"}
    if not keyword:
        return {**local, "briefSource": "local", "aiEnhanced": False, "aiMeta": {"stage": stage_name, "reason": "missing-keyword"}}

    # 只有显式配置了供应商 + 用户给的是 proxy / 自定义端点 才走 AI
    if not (is_stage_configured("brief_generation") and ai_stage.get("provider") not in (None, "", "local")):
        return {
            **local,
            "briefSource": "local",
            "aiEnhanced": False,
            "aiMeta": {"stage": stage_name, "reason": "local-stage", "configured": False},
        }

    cache_key = hashlib.md5(
        json.dumps({"k": keyword, "p": project, "ai": ai_stage}, sort_keys=True, default=str).encode()
    ).hexdigest()
    cache = get_brief_cache()
    cached = cache.get(cache_key)
    if cached:
        return {**cached, "aiMeta": {**cached.get("aiMeta", {}), "cached": True}}

    ai_result = await generate_ai_content(stage="brief_generation", prompt=_build_ai_prompt(keyword, project, local))
    enhanced = (ai_result.get("content") or "").strip()
    if not enhanced or ai_result.get("status"):
        return {
            **local,
            "briefSource": "local",
            "aiEnhanced": False,
            "aiMeta": _ai_meta(stage_name, ai_result) | {"reason": ai_result.get("status") or "ai-request-failed"},
        }
    payload = {
        **local,
        "brief": enhanced,
        "briefSource": "ai-enhanced",
        "aiEnhanced": True,
        "aiMeta": _ai_meta(stage_name, ai_result) | {"cached": False},
    }
    cache.set(cache_key, payload)
    return payload


def _build_ai_prompt(keyword: dict[str, Any], project: dict[str, Any], local: dict[str, Any]) -> str:
    store = get_store()
    standard_subset = {
        "articleBriefTemplate": store.get("articleBriefTemplate"),
        "articleRules": store.get("articleRules"),
        "references": store.get("references"),
        "anchorTextRules": store.get("anchorTextRules"),
        "articleOutputFormat": store.get("articleOutputFormat"),
    }
    parts = [
        "# Task",
        "Enhance the local SEO brief below. Do not write the article.",
        "",
        "# Hard Rules",
        "- Keep every hard constraint from the local brief unless the input data clearly contradicts it.",
        "- Do not fabricate Google rankings, traffic, SERP checks, competitor data, product specs, prices, legal claims, health claims, or URLs.",
        "- If evidence is missing, add it to `## Evidence Needed` instead of guessing.",
        "- If a target asset is not marked `existing`, do not approve clickable internal links to it.",
        "",
        f"# Locale\n{locale_instruction(locale_for_project(project))}",
        "",
        "# Project And Keyword Data",
        json.dumps(
            {"project": project, "keyword": keyword, "locale": local.get("locale"), "targetAsset": local.get("targetAsset"), "imagePlan": local.get("imagePlan"), "standard": standard_subset},
            default=str,
            ensure_ascii=False,
            indent=2,
        ),
        "",
        "# Local Brief To Enhance",
        local.get("brief") if isinstance(local.get("brief"), str) else json.dumps(local.get("brief"), default=str, ensure_ascii=False, indent=2),
    ]
    return "\n".join(parts)