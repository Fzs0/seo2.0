"""关键词分站决策。纯函数，从 rule_sets.payload.classifier 读判定规则与信号词。

设计要点：
- 所有信号词（risk / coreProduct / transaction / comparison / scenario / knowledge / mainBlogExclusions）
  从 payload.signals 读，保持与 seo-standard.json 同源。
- 决策链顺序与项目术语停用词也全部来自 payload.classifier。
- 不再有代码硬编码的 if/else 文本，所有"assignedSite 标签"由 payload.classifier.labels 提供。
"""
from __future__ import annotations

import re
from typing import Any

from app.engine.loader import get_store


def _has_any(text: str, words: list[str]) -> bool:
    """任一词出现在 text（忽略大小写、忽略空白）。"""
    text = (text or "").lower()
    return any(str(w).lower() in text for w in (words or []))


def _core_terms(project: dict[str, Any]) -> list[str]:
    """从项目定位里抽核心产品 / 主站页面术语。"""
    stopwords: set[str] = set(
        get_store().get("classifier.stopwords.projectTerms", []) or []
    )
    explicit = [
        t.strip().lower()
        for t in re.split(r"[\n,;，；/]+", str(project.get("coreProducts") or ""))
        if len(t.strip()) >= 3
    ]
    page_raw = str(project.get("mainPages") or "").lower()
    page_terms = [
        t
        for t in re.split(r"[^a-z0-9]+", page_raw)
        if len(t) >= 3 and t not in stopwords
    ]
    return list(dict.fromkeys(explicit + page_terms))


def classify_keyword(
    raw_keyword: str,
    raw_intent: str = "",
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据 payload.classifier.assignRules 的顺序逐档匹配，返回首个命中。"""
    store = get_store()
    signals = store.get("signals", {}) or {}
    labels = store.get("classifier.labels", {}) or {}
    assign_rules = store.get("classifier.assignRules", {}) or {}
    project = project or {}

    keyword = (raw_keyword or "").lower()
    intent = (raw_intent or "").lower()
    project_terms = _core_terms(project)

    has_risk = _has_any(keyword, signals.get("risk", []))
    has_core = _has_any(keyword, signals.get("coreProduct", [])) or _has_any(keyword, project_terms)
    is_transactional = "transactional" in intent or _has_any(keyword, signals.get("transaction", []))
    is_commercial = "commercial" in intent
    is_comparison = _has_any(keyword, signals.get("comparison", []))
    is_scenario = _has_any(keyword, signals.get("scenario", []))
    is_knowledge = "informational" in intent or _has_any(keyword, signals.get("knowledge", []))

    # 决策链：从 payload 里读顺序（默认按原 classifier.mjs 的 7 段顺序）
    chain = assign_rules.get(
        "order",
        ["risk", "transactionalCore", "comparisonCoreCommercial", "mainBlog", "comparison", "scenario", "knowledge"],
    )

    for rule_key in chain:
        rule = (assign_rules.get(rule_key) or {})
        when = rule.get("when", {}) or {}
        out = rule.get("output", {}) or {}

        if rule_key == "risk" and has_risk:
            return _output(out, labels, "Risk", "暂不做", "风险词池")
        if rule_key == "transactionalCore" and is_transactional and has_core and not is_comparison:
            return _output(out, labels, "Transactional", labels.get("mainCollection", "主站-集合页"), "集合页 / 产品列表页")
        if (
            rule_key == "comparisonCoreCommercial"
            and is_comparison
            and has_core
            and (is_commercial or "best" in keyword)
        ):
            return _output(out, labels, "Commercial Investigation", labels.get("mainBlog", "主站-博客"), "商业前教育文章 / Listicle")
        if rule_key == "mainBlog":
            mb = when
            if (
                has_core
                and is_knowledge
                and not _has_any(keyword, signals.get("mainBlogExclusions", []))
                and (
                    _has_any(keyword, mb.get("phrases", ["how to", "properly", "guide", "choose"]))
                    or is_commercial
                )
            ):
                return _output(out, labels, "Informational + Buyer Adjacent", labels.get("mainBlog", "主站-博客"), "主站教程文章")
        if rule_key == "comparison" and is_comparison:
            return _output(out, labels, "Comparison", labels.get("blogC", "博客C-对比评测"), "对比 / 评测 / 替代方案文章")
        if rule_key == "scenario" and is_scenario:
            return _output(out, labels, "Scenario", labels.get("blogB", "博客B-场景人群"), "场景 / 人群 / 口味灵感文章")
        if rule_key == "knowledge" and is_knowledge:
            return _output(out, labels, "Informational", labels.get("blogA", "博客A-知识教程"), "知识教程 / FAQ 文章")

    # fallback：默认走 knowledge blog
    fallback = assign_rules.get("fallback", {}) or {}
    fallback_out = fallback.get("output", {})
    return _output(
        fallback_out,
        labels,
        "Exploratory",
        labels.get("blogA", "博客A-知识教程"),
        "探索型文章",
    )


def _output(
    out: dict[str, Any],
    labels: dict[str, str],
    intent_bucket: str,
    default_site: str,
    default_page_type: str,
) -> dict[str, Any]:
    """组装 classify 的返回值。优先用 rule 自己的 output 字段。"""
    return {
        "assignedSite": out.get("assignedSite", default_site),
        "pageType": out.get("pageType", default_page_type),
        "reason": out.get(
            "reason",
            f"按 classifier.assignRules 命中 {intent_bucket} 分支。",
        ),
        "intentBucket": out.get("intentBucket", intent_bucket),
    }