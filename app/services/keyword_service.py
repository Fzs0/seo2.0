"""服务层：编排导入→分析→持久化。每个 service 只依赖 engine + clients。"""
from __future__ import annotations

from typing import Any

import structlog

from app.engine.assets import page_role_for, resolve_target_asset
from app.engine.classifier import classify_keyword
from app.engine.content_plan import (
    article_brief_template_for,
    image_plan_for,
    reference_plan,
)
from app.engine.csv import import_csv_keywords, import_keywords_from_file
from app.engine.locale import locale_for_project
from app.engine.scorer import enrich_keywords

logger = structlog.get_logger(__name__)


def analyze_keywords(keywords: list[dict[str, Any]], project: dict[str, Any]) -> list[dict[str, Any]]:
    """导入 → 分类 → 评分 → 资产解析 → 拼装完整字段。"""
    locale = locale_for_project(project)
    scored = enrich_keywords(keywords, project)
    out: list[dict[str, Any]] = []
    for item in scored:
        asset = resolve_target_asset(item, project)
        keyword_db = str(item.get("database") or "").strip().lower()
        expected_db = str(locale.get("semrushDatabase") or "").strip().lower()
        market_mismatch = bool(keyword_db and expected_db and keyword_db != expected_db)
        out.append(
            {
                **item,
                "locale": locale,
                "marketMismatch": market_mismatch,
                "marketMismatchReason": (
                    f"Keyword database \"{item.get('database')}\" does not match selected market database \"{locale.get('semrushDatabase')}\"."
                    if market_mismatch
                    else ""
                ),
                "topicCluster": item.get("topicCluster") or item.get("pageGroup") or item.get("seedKeyword") or item.get("keyword"),
                "pageRole": page_role_for(item),
                "targetAsset": asset["url"],
                "assetStatus": asset["status"],
                "contentAction": asset["contentAction"],
                "assetReason": asset["reason"],
                "parentPage": asset["url"] if asset["status"] in ("existing", "needs_review") else "",
                "plannedUrl": asset["url"] if asset["status"] == "planned" else "",
                "reference": reference_plan(item),
                "imagePlan": image_plan_for(item),
            }
        )
    return out


def import_and_analyze_csv(csv_text: str, project: dict[str, Any]) -> list[dict[str, Any]]:
    return analyze_keywords(import_csv_keywords(csv_text), project)


def import_and_analyze_file(payload: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    return analyze_keywords(import_keywords_from_file(payload), project)


def build_brief(item: dict[str, Any] | None, project: dict[str, Any]) -> dict[str, Any]:
    if not item:
        return {"error": "请先选择关键词"}
    asset = resolve_target_asset(item, project)
    locale = locale_for_project(project)
    return {
        "keyword": item,
        "project": project,
        "locale": locale,
        "targetAsset": asset,
        "parentPage": asset["url"] if asset["status"] in ("existing", "needs_review") else "",
        "imagePlan": image_plan_for(item),
        "reference": reference_plan(item),
        "articleBriefTemplate": article_brief_template_for(item, project),
    }