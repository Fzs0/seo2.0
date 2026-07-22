"""服务层：编排导入→分析→持久化。每个 service 只依赖 engine + clients。"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import structlog

from app.engine.assets import page_role_for, resolve_target_asset
from app.engine.classifier import classify_keyword, keyword_scope
from app.engine.content_plan import (
    article_brief_template_for,
    image_plan_for,
    reference_plan,
)
from app.engine.csv import import_csv_keywords, import_keywords_from_file
from app.engine.locale import locale_for_market, locale_for_project
from app.engine.keyword_preflight import preflight_keyword
from app.engine.semrush_strategy import parse_semrush_strategy_payload, preview_semrush_strategy_rows
from app.engine.scorer import enrich_keywords
from app.engine.topic_cluster import assign_topic_clusters
from app.services.site_config_service import _load

logger = structlog.get_logger(__name__)


def _import_rejection_reason(item: dict[str, Any]) -> str:
    defer_to_ai = item.get("source") == "import" and bool(item.get("allowUnresolvedScope"))
    if item.get("preflightStatus") != "ready" and not (defer_to_ai and item.get("preflightStatus") == "needs_review"):
        return item.get("preflightReason") or "未通过基础数据检查"
    if item.get("marketMismatch"):
        return item.get("marketMismatchReason") or "市场数据库不匹配"
    if item.get("scopeStatus") != "relevant" and not (defer_to_ai and item.get("scopeStatus") == "needs_review"):
        return item.get("reason") or "未命中当前业务范围"
    if item.get("priority") == "Hold" or item.get("status") == "hold":
        return item.get("reason") or "本地规则判定为 Hold"
    return ""


def importable_keywords(keywords: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only rows allowed into the persistent/AI keyword pool."""
    return [
        item
        for item in keywords
        if not _import_rejection_reason(item)
    ]


def import_summary(all_keywords: list[dict[str, Any]], accepted_keywords: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter

    accepted_ids = {id(item) for item in accepted_keywords}
    rejected = [item for item in all_keywords if id(item) not in accepted_ids]
    return {
        "total": len(all_keywords),
        "accepted": len(accepted_keywords),
        "rejected": len(rejected),
        "rejectionReasons": dict(Counter(_import_rejection_reason(item) for item in rejected)),
    }


def _configured_sites() -> list[dict[str, Any]]:
    return [
        *(_load("main-sites.local.json", "main")),
        *(_load("blog-sites.local.json", "blog")),
        *(_load("wp-sites.local.json", "wp")),
    ]


def _canonical_site_label(
    label: str | None,
    locale: dict[str, Any],
    sites: list[dict[str, Any]],
) -> str | None:
    """Use the concrete site name when one role has one locale-specific site."""
    if not label:
        return label
    candidates: list[dict[str, Any]] = []
    for site in sites:
        if site.get("contentRole") != label and site.get("content_role") != label:
            continue
        site_locale = locale_for_market(site.get("targetMarket") or "")
        if not site_locale.get("configured") and site.get("market"):
            site_locale = {
                "market": site.get("market"),
                "languageCode": site.get("language_code") or site.get("targetLanguage"),
            }
        if (
            site_locale.get("market") == locale.get("market")
            and site_locale.get("languageCode") == locale.get("languageCode")
        ):
            candidates.append(site)
    return candidates[0].get("name") if len(candidates) == 1 else label


def analyze_keywords(
    keywords: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    assign_site: bool = True,
) -> list[dict[str, Any]]:
    """导入 → 分类 → 评分 → 资产解析 → 拼装完整字段。"""
    locale = locale_for_project(project)
    configured_sites = _configured_sites()
    scored = enrich_keywords(keywords, project)
    if not assign_site:
        scored = [
            {**item, "assignedSite": "", "assigned_site_label": "", "assigned_site_id": None}
            for item in scored
        ]
    out: list[dict[str, Any]] = []
    for item in scored:
        if locale.get("configured"):
            item = {
                **item,
                "market": item.get("market") or locale.get("market"),
                "languageCode": item.get("languageCode") or item.get("language_code") or locale.get("languageCode"),
                "database": item.get("database") or locale.get("semrushDatabase"),
                "googleGl": item.get("googleGl") or locale.get("googleGl"),
                "googleHl": item.get("googleHl") or locale.get("googleHl"),
            }
        else:
            item = {
                **item,
                "status": "hold",
                "priority": "Hold",
                "contentAction": "needs_locale_review",
                "reason": locale.get("warning") or "未选择目标市场和语种。",
                "scopeStatus": "needs_review",
            }
        preflight = preflight_keyword(item)
        item = {
            **item,
            "keyword": preflight["keyword"],
            "intent": item.get("intent") if preflight["normalizedIntent"] == "unknown" else preflight["normalizedIntent"],
            "keywordType": preflight["keywordType"],
            "preflightStatus": preflight["preflightStatus"],
            "preflightReason": preflight["preflightReason"],
            "preflight": preflight,
        }
        if preflight["preflightStatus"] == "invalid":
            item = {
                **item,
                "status": "hold",
                "priority": "Hold",
                "contentAction": "preflight_invalid",
                "reason": preflight["preflightReason"],
                "scopeStatus": "needs_review",
            }
        asset = resolve_target_asset(item, project)
        # A normal file selected for one business is source-page evidence, not a
        # cross-business classifier input. Its relevance is decided by the
        # business's confirmed site profile in the later AI review; applying
        # global product terms here would leak another business's vocabulary.
        scope = (
            {
                "status": "needs_review",
                "matched": [],
                "reason": "站点来源关键词待结合当前业务画像进行 AI 复核。",
            }
            if item.get("allowUnresolvedScope")
            else keyword_scope(item.get("keyword", ""), project)
        )
        defer_unresolved_scope = bool(item.get("allowUnresolvedScope")) and scope["status"] == "needs_review"
        if item.get("preflightStatus") != "invalid" and scope["status"] != "relevant" and not defer_unresolved_scope:
            item = {
                **item,
                "status": "hold",
                "priority": "Hold",
                "contentAction": "needs_scope_review",
                "reason": scope["reason"],
                "scopeStatus": scope["status"],
            }
        if item.get("status") != "hold" and assign_site and locale.get("configured"):
            item = {
                **item,
                "assignedSite": _canonical_site_label(item.get("assignedSite"), locale, configured_sites),
            }
        if item.get("status") != "hold" and not assign_site:
            item = {
                **item,
                "status": "imported",
                "assignedSite": "",
                "assigned_site_label": "",
                "assigned_site_id": None,
            }
        keyword_db = str(item.get("database") or "").strip().lower()
        expected_db = str(locale.get("semrushDatabase") or "").strip().lower()
        market_mismatch = bool(keyword_db and expected_db and keyword_db != expected_db)
        if market_mismatch:
            item = {
                **item,
                "status": "hold",
                "priority": "Hold",
                "contentAction": "needs_locale_review",
                "reason": f"Semrush 数据库 {keyword_db} 与目标市场数据库 {expected_db} 不一致。",
                "scopeStatus": "needs_review",
            }
        if item.get("status") == "hold":
            item = {**item, "assignedSite": "", "assigned_site_label": ""}
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
                "scopeStatus": item.get("scopeStatus") or scope["status"],
                "topicCluster": item.get("topicCluster") or item.get("pageGroup") or item.get("seedKeyword") or item.get("keyword"),
                "pageRole": page_role_for(item),
                "targetAsset": asset["url"],
                "assetStatus": asset["status"],
                "contentAction": item.get("contentAction") or asset["contentAction"],
                "assetReason": asset["reason"],
                "parentPage": asset["url"] if asset["status"] in ("existing", "needs_review") else "",
                "plannedUrl": asset["url"] if asset["status"] == "planned" else "",
                "reference": reference_plan(item),
                "imagePlan": image_plan_for(item),
            }
        )
    return assign_topic_clusters(out)


def import_and_analyze_csv(csv_text: str, project: dict[str, Any]) -> list[dict[str, Any]]:
    return analyze_keywords(
        [{**item, "source": "import", "allowUnresolvedScope": True} for item in import_csv_keywords(csv_text)],
        project,
        assign_site=False,
    )


def import_and_analyze_file(payload: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    """Analyze a normal keyword file while retaining source-page evidence in ``raw``.

    This route is intentionally separate from Semrush Strategy Builder: ordinary
    site keyword tables do not claim to contain Semrush-curated page clusters.
    """
    return analyze_keywords(
        [{**item, "source": "import", "allowUnresolvedScope": True} for item in import_keywords_from_file(payload)],
        project,
        assign_site=False,
    )


def prepare_semrush_strategy_import(
    payload: dict[str, Any],
    project: dict[str, Any],
    source_batch_id: str,
    policy: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prepare confirmed Strategy Builder rows without generic clustering or site assignment."""
    parsed = parse_semrush_strategy_payload(payload)
    preview = preview_semrush_strategy_rows(parsed, policy)
    if not parsed:
        raise ValueError("Keywords sheet 没有可导入关键词")

    blocking = {
        "missing_page",
        "missing_keyword",
        "missing_topic",
        "missing_page_type",
        "unsupported_page_type",
        "mixed_topic",
        "mixed_page_type",
        "keyword_in_multiple_pages",
    }
    blocked = [item["label"] for item in preview["anomalies"] if item["code"] in blocking]
    if blocked:
        raise ValueError(f"存在结构异常，不能确认导入：{'；'.join(blocked)}")

    locale = locale_for_project(project)
    if not locale.get("configured"):
        raise ValueError("确认导入前必须选择目标市场和语种")
    required_locale = ("market", "languageCode", "googleGl", "googleHl", "semrushDatabase")
    if any(not locale.get(key) for key in required_locale):
        raise ValueError("目标市场配置不完整，缺少语种或搜索数据库参数")
    databases = preview["databases"]
    expected_database = str(locale.get("semrushDatabase") or "").casefold()
    if len(databases) != 1 or databases[0] != expected_database:
        raise ValueError(
            f"文件 Semrush Database {', '.join(databases) or '缺失'} 与目标市场数据库 {expected_database or '未配置'} 不一致"
        )

    business_id = str(project.get("businessId") or project.get("business_id") or "").strip()
    if not business_id:
        raise ValueError("确认导入前必须选择目标业务")

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in parsed:
        groups[item["pageClusterId"]].append(item)
    validation_by_cluster = {item["id"]: item for item in preview["clusters"]}

    rows: list[dict[str, Any]] = []
    invalid: list[str] = []
    filename = str(payload.get("filename") or "")
    for cluster_id, items in groups.items():
        validation = validation_by_cluster[cluster_id]
        representative = next(
            item for item in items if item["keyword"] == validation["validationCenterKeyword"]
        )
        for item in items:
            primary_intent = next(iter(str(item["intent"] or "").split(",")), "").strip()
            preflight = preflight_keyword({**item, "intent": primary_intent})
            if preflight["preflightStatus"] == "invalid":
                invalid.append(item["keyword"] or "(空关键词)")
                continue
            page_type = str(item["pageType"] or "").casefold()
            raw = {
                **item["raw"],
                "_strategy_builder": {
                    "topic": item["topic"],
                    "page": item["page"],
                    "page_type": item["pageType"],
                    "intent": item["intent"],
                    "content_references": item["contentReferences"],
                    "top10_urls": item["top10Urls"],
                    "cluster_validation": {
                        "rule_version": preview["clusterValidation"]["ruleVersion"],
                        "status": validation["validationStatus"],
                        "center_keyword": validation["validationCenterKeyword"],
                        "reason": validation["validationReason"],
                    },
                },
            }
            rows.append(
                {
                    "keyword": preflight["keyword"],
                    "source": "semrush_strategy_builder",
                    "sourceFile": filename,
                    "sourceBatchId": source_batch_id,
                    "businessId": business_id,
                    "database": item["database"].casefold(),
                    "market": locale.get("market"),
                    "languageCode": locale.get("languageCode"),
                    "googleGl": locale.get("googleGl"),
                    "googleHl": locale.get("googleHl"),
                    "volume": item["volume"],
                    "kd": item["kd"],
                    "cpc": item["cpc"],
                    "intent": preflight["normalizedIntent"],
                    "serpFeatures": item["serpFeatures"],
                    "trend": item["trend"][-1] if item["trend"] else 0,
                    "trendData": item["trend"],
                    "competitiveDensity": item["competitiveDensity"],
                    "serpResults": item["serpResults"],
                    "keywordType": preflight["keywordType"],
                    "preflightStatus": preflight["preflightStatus"],
                    "preflightReason": preflight["preflightReason"],
                    "topicCluster": item["topic"],
                    "topicClusterId": cluster_id,
                    "clusterRole": (
                        "standalone" if len(items) == 1 else "pillar" if item is representative else "supporting"
                    ),
                    "clusterSize": len(items),
                    "pillarKeyword": representative["keyword"],
                    "seedKeyword": item["seedKeyword"],
                    "pageGroup": item["page"],
                    "pageType": item["pageType"],
                    "pageRole": "pillar_page" if page_type == "pillar page" else "sub_page",
                    "status": "imported",
                    "reason": preflight["preflightReason"],
                    "raw": raw,
                }
            )
    if invalid:
        raise ValueError(f"存在 {len(invalid)} 条无效关键词，不能确认导入：{', '.join(invalid[:5])}")
    return rows, preview


def prepare_stored_semrush_strategy_validation(
    records: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Re-run the local page-cluster rule for an imported Strategy Builder batch."""
    rows: list[dict[str, Any]] = []
    for record in records:
        raw = dict(record.get("raw") or {})
        strategy = dict(raw.get("_strategy_builder") or {})
        rows.append(
            {
                "recordId": str(record["id"]),
                "recordRaw": raw,
                "database": record.get("semrush_database") or "",
                "keyword": record.get("keyword") or "",
                "page": strategy.get("page") or record.get("page_group") or "",
                "pageClusterId": record.get("topic_cluster_id") or "",
                "topic": strategy.get("topic") or record.get("topic_cluster") or "",
                "pageType": strategy.get("page_type") or record.get("page_type") or "",
                "intent": strategy.get("intent") or record.get("intent") or "",
                "volume": float(record.get("volume") or 0),
                "kd": float(record.get("kd") or 0),
                "top10Urls": list(strategy.get("top10_urls") or []),
                "contentReferences": list(strategy.get("content_references") or []),
            }
        )
    if not rows:
        raise ValueError("没有可整理的 Strategy Builder 关键词")
    if any(not item["pageClusterId"] for item in rows):
        raise ValueError("导入批次缺少页面簇 ID，不能安全回填校验结果")

    preview = preview_semrush_strategy_rows(rows, policy)
    validation_by_cluster = {item["id"]: item for item in preview["clusters"]}
    cluster_sizes = Counter(item["pageClusterId"] for item in rows)
    updates = []
    for item in rows:
        validation = validation_by_cluster[item["pageClusterId"]]
        raw = item["recordRaw"]
        strategy = dict(raw.get("_strategy_builder") or {})
        strategy["cluster_validation"] = {
            "rule_version": preview["clusterValidation"]["ruleVersion"],
            "status": validation["validationStatus"],
            "center_keyword": validation["validationCenterKeyword"],
            "reason": validation["validationReason"],
        }
        raw["_strategy_builder"] = strategy
        size = cluster_sizes[item["pageClusterId"]]
        updates.append(
            {
                "id": item["recordId"],
                "raw": raw,
                "cluster_role": (
                    "standalone"
                    if size == 1
                    else "pillar"
                    if item["keyword"] == validation["validationCenterKeyword"]
                    else "supporting"
                ),
                "cluster_size": size,
                "pillar_keyword": validation["validationCenterKeyword"],
            }
        )
    return updates, preview


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
