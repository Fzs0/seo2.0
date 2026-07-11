"""Brief 模板填充。所有场景文案、H2 模板、用户问题模板、CTA、内链计划都从
payload.contentPlan.* 读，调用方只负责传入 item + project。

模板里的占位符 {keyword} / {title} / {asset} 由 _render() 替换。
"""
from __future__ import annotations

from typing import Any

from app.engine.assets import resolve_target_asset
from app.engine.loader import get_store


def _status_label(status: str) -> str:
    return {
        "existing": "已存在",
        "planned": "规划 URL",
        "needs_review": "需要人工确认",
        "missing": "缺失",
    }.get(status, status or "未确定")


def _keyword_context(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(k, "")) for k in ("keyword", "intent", "intentBucket", "pageType", "assignedSite")
    ).lower()


def _render(template: str | None, keyword: str, **extra: str) -> str:
    """模板占位符替换。payload 里用 {keyword} / {title} / {asset} 占位。"""
    if not template:
        return ""
    return (
        template
        .replace("{keyword}", keyword or "")
        .replace("{title}", (keyword or "").title())
        .replace("{asset}", extra.get("asset", ""))
    )


def outline_for(item: dict[str, Any] | None) -> list[str]:
    if not item:
        return []
    site = item.get("assignedSite") or ""
    title = (item.get("keyword") or "").title()
    templates = get_store().get("contentPlan.outlines", {}) or {}

    def _render_outline(raw_list: list[str]) -> list[str]:
        return [_render(line, item.get("keyword") or "") for line in raw_list]

    if site == "主站-集合页":
        return _render_outline(templates.get("mainCollection", [])) or [f"H1: {title}"]
    if site == "主站-博客":
        return _render_outline(templates.get("mainBlog", [])) or [f"H1: {title}"]
    if "对比" in site:
        return _render_outline(templates.get("blogC", [])) or [f"H1: {title}"]
    if "场景" in site:
        return _render_outline(templates.get("blogB", [])) or [f"H1: {title}"]
    return _render_outline(templates.get("knowledge", [])) or [f"H1: {title}"]


def image_plan_for(item: dict[str, Any] | None) -> list[dict[str, str]]:
    if not item:
        return []
    placements = get_store().get("imagePlacements", []) or []
    keyword = item.get("keyword") or ""
    return [
        {
            "name": p.get("name", ""),
            "position": p.get("position", ""),
            "alt": _render(p.get("altTemplate", ""), keyword),
        }
        for p in placements
    ]


def article_type_for(item: dict[str, Any]) -> str:
    if not item:
        return "未选择"
    site = item.get("assignedSite") or ""
    text = _keyword_context(item)
    signals = get_store().get("signals", {}) or {}
    if site == "主站-集合页":
        return "集合页 / 商业承接页"
    if "对比" in site or any(w in text for w in signals.get("comparison", [])):
        return "对比文 / Best / VS"
    if "场景" in site or any(w in text for w in signals.get("scenario", [])):
        return "场景方案 / 人群方案"
    if any(w in text for w in signals.get("transaction", [])):
        return "购买指南 / 商业前教育"
    if "知识" in site or any(w in text for w in signals.get("knowledge", [])):
        return "教程 / FAQ / 合规科普"
    if site == "主站-博客":
        return "购买前教育文章"
    return item.get("pageType") or "实用指南"


def user_question_for(item: dict[str, Any]) -> str:
    keyword = item.get("keyword") or ""
    site = item.get("assignedSite") or ""
    text = _keyword_context(item)
    signals = get_store().get("signals", {}) or {}
    templates = get_store().get("contentPlan.userQuestion", {}) or {}
    if site == "主站-集合页":
        return _render(templates.get("mainCollection"), keyword)
    if any(w in text for w in signals.get("comparison", [])):
        return _render(templates.get("comparison"), keyword)
    if any(w in text for w in signals.get("transaction", [])):
        return _render(templates.get("transaction"), keyword)
    if any(w in text for w in signals.get("scenario", [])):
        return _render(templates.get("scenario"), keyword)
    if any(w in text for w in signals.get("knowledge", [])):
        return _render(templates.get("knowledge"), keyword)
    return _render(templates.get("fallback"), keyword)


def one_sentence_answer_for(item: dict[str, Any]) -> str:
    keyword = item.get("keyword") or ""
    site = item.get("assignedSite") or ""
    text = _keyword_context(item)
    signals = get_store().get("signals", {}) or {}
    templates = get_store().get("contentPlan.oneSentenceAnswer", {}) or {}
    if site == "主站-集合页":
        return _render(templates.get("mainCollection"), keyword)
    if any(w in text for w in signals.get("comparison", [])):
        return _render(templates.get("comparison"), keyword)
    if any(w in text for w in signals.get("transaction", [])):
        return _render(templates.get("transaction"), keyword)
    if any(w in text for w in signals.get("knowledge", [])):
        return _render(templates.get("knowledge"), keyword)
    return _render(templates.get("fallback"), keyword)


def cta_for(item: dict[str, Any], asset: dict[str, Any]) -> str:
    target = asset.get("url") or "待确认承接页"
    keyword = item.get("keyword") or ""
    site = item.get("assignedSite") or ""
    templates = get_store().get("contentPlan.cta", {}) or {}
    if site == "主站-集合页":
        return _render(templates.get("mainCollection"), keyword, asset=target)
    if "知识" in site:
        return _render(templates.get("knowledge"), keyword, asset=target)
    if "对比" in site or site == "主站-博客":
        return _render(templates.get("default"), keyword, asset=target)
    return _render(templates.get("fallback"), keyword, asset=target)


def internal_link_plan_for(item: dict[str, Any], asset: dict[str, Any], project: dict[str, Any]) -> list[str]:
    lines = [
        f"目标资产：{asset.get('url') or '未确定'}（{_status_label(asset.get('status', ''))}）",
        f"内容动作：{asset.get('contentAction')}",
    ]
    if asset.get("status") == "existing":
        lines.append("正文允许自然链接到目标资产，但同一 URL 不重复完全相同锚文本。")
    elif asset.get("status") == "needs_review":
        lines.append("目标资产需要人工确认，正文只输出 Internal Link Suggestions，不生成可点击硬链接。")
    else:
        lines.append("目标资产尚未真实存在，先放入 Internal Link Suggestions，等页面创建后再上线链接。")
    if project.get("mainPages"):
        lines.append("优先从已填写的主站商业页面里选择最相关承接页。")
    lines.append("下链相关博客：补充 1-3 篇同主题支持文章，用不同自然锚文本连接主题集群。")
    return lines


def reference_plan(item: dict[str, Any]) -> dict[str, Any]:
    if not item:
        return {"triggered": False, "reason": "未选择关键词。", "sources": []}
    text = " ".join(str(item.get(k, "")) for k in ("keyword", "intent", "pageType")).lower()
    triggers = get_store().get("references.triggers", {}) or {}
    sources = []
    for name, rule in triggers.items():
        terms = rule.get("terms", []) if isinstance(rule, dict) else []
        if any(str(t).lower() in text for t in terms):
            sources.append({"name": name, "label": rule.get("label", ""), "url": rule.get("url", "")})
    if not sources:
        return {
            "triggered": False,
            "reason": "未触发官方引用：普通选购/口味/场景/经验型文章可 0 引用。",
            "sources": [],
        }
    return {"triggered": True, "reason": "触发官方引用：涉及健康/安全/法规/电池回收等。", "sources": sources}


def article_brief_template_for(item: dict[str, Any] | None, project: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not item:
        return []
    project = project or {}
    asset = resolve_target_asset(item, project)
    score = (item.get("scores") or {}).get("total", "未评分")
    priority = item.get("priority", "未定")
    refs = reference_plan(item)
    computed: dict[str, dict[str, Any]] = {
        "primaryKeyword": {
            "value": f"{item.get('keyword')}（{priority} / {score}）",
            "review": "一个主词只服务一个明确意图。",
        },
        "parentCommercialPage": {
            "value": f"{asset.get('url') or '未确定'}（{_status_label(asset.get('status', ''))}）",
            "review": "上线前必须确认承接页真实存在。",
        },
        "userQuestion": {"value": user_question_for(item), "review": "正文第一屏必须回应这个问题。"},
        "articleType": {"value": article_type_for(item), "review": "文章类型要匹配 SERP 获胜类型。"},
        "oneSentenceAnswer": {"value": one_sentence_answer_for(item), "review": "开头先给结论。"},
        "h2Structure": {"value": outline_for(item), "review": "H2 顺序必须服务用户决策路径。"},
        "internalLinks": {
            "value": internal_link_plan_for(item, asset, project),
            "review": "上链商业页，下链相关博客。",
        },
        "cta": {"value": cta_for(item, asset), "review": "CTA 强弱跟意图匹配。"},
        "visualAssets": {"value": image_plan_for(item), "review": "表格/流程图/场景图优先。"},
        "imagePlacement": {"value": image_plan_for(item), "review": "必须写清楚插入位置。"},
        "faq": {"value": get_store().get("contentPlan.faqTemplate", []), "review": "围绕真实搜索问题 3-6 条。"},
        "referencesJudgment": {
            "value": (
                ["需要 References"] + [f"{s['name']}: [{s['label']}]({s['url']})" for s in refs["sources"]]
                if refs["triggered"]
                else ["0 引用，不添加 References", refs["reason"]]
            ),
            "review": "触发才加 References。",
        },
        "eeat": {"value": get_store().get("contentPlan.eeat", []), "review": "用经验、证据降低 AI 味。"},
        "postPublishReview": {
            "value": get_store().get("contentPlan.reviewMetrics", []),
            "review": "30-60 天复盘。",
        },
        "updateDecision": {
            "value": get_store().get("contentPlan.updateDecision", []),
            "review": "有排名苗头就更新。",
        },
    }
    modules = get_store().get("articleBriefTemplate.modules", []) or []
    return [
        {**m, "value": computed.get(m["key"], {}).get("value", "待补充"), "review": computed.get(m["key"], {}).get("review", "待复盘")}
        for m in modules
    ]