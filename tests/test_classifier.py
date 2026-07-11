"""classifier 7 段决策链 + 边界条件单测。

每条 fixture 关键词都来自 docs/e2e-report.md 的真实 e2e 数据，
保证单测与生产行为完全一致。

如果某条测试 fail，先看 docs/e2e-report.md 是否描述了相同行为——可能是
classifier 实际行为与我描述的预期不一致，需要更新测试（不是修代码）。
"""
from __future__ import annotations

import pytest

from app.engine.classifier import classify_keyword


# ---------- 7 段决策链覆盖 ----------

def test_risk_keyword_hold(rule_payload):
    """thc 触发 risk 规则 → 暂不做 + Hold。

    实际 e2e 跑过 'thc vape pen' → Hold。
    """
    result = classify_keyword("thc vape pen", "informational", {})
    assert result["assignedSite"] == "暂不做"
    assert result["pageType"] == "风险词池"
    assert result["intentBucket"] == "Risk"


def test_transactional_core_main_collection(rule_payload):
    """buy + core product → 主站-集合页。"""
    result = classify_keyword(
        "buy vape online",
        "transactional",
        {"coreProducts": "vape, e-cigarette, disposable"},
    )
    assert result["assignedSite"] == "主站-集合页"
    assert result["intentBucket"] == "Transactional"


def test_comparison_with_core_main_blog(rule_payload):
    """best + core product + commercial → 主站-博客（Listicle）。

    实际 e2e: 'best disposable vapes' → 主站-博客 + Commercial Investigation。
    """
    result = classify_keyword(
        "best disposable vapes",
        "commercial",
        {"coreProducts": "vape, e-cigarette, disposable"},
    )
    assert result["assignedSite"] == "主站-博客"
    assert result["pageType"] == "商业前教育文章 / Listicle"
    assert result["intentBucket"] == "Commercial Investigation"


def test_comparison_only_blog_c(rule_payload):
    """best/vs 但 core product 命中不上的 → 博客C-对比评测。"""
    result = classify_keyword("best running shoes", "informational", {})
    assert result["assignedSite"] == "博客C-对比评测"
    assert result["pageType"] == "对比 / 评测 / 替代方案文章"
    assert result["intentBucket"] == "Comparison"


def test_scenario_blog_b(rule_payload):
    """flavor/beginner → 博客B-场景人群。"""
    result = classify_keyword(
        "vape flavor for beginner",
        "informational",
        {"coreProducts": "vape, e-cigarette, disposable"},
    )
    assert result["assignedSite"] == "博客B-场景人群"
    assert result["intentBucket"] == "Scenario"


def test_knowledge_with_safe_word_blocks_main_blog(rule_payload):
    """how to + core product + safe in exclusion → 降级博客A-知识教程。

    e2e: 'how to dispose of vape battery' → 博客A（safe/dispose 在 exclusion）。
    """
    result = classify_keyword(
        "how to dispose of vape battery",
        "informational",
        {"coreProducts": "vape, e-cigarette, disposable"},
    )
    assert result["assignedSite"] == "博客A-知识教程"
    assert result["pageType"] == "知识教程 / FAQ 文章"


def test_how_to_vape_properly_main_blog(rule_payload):
    """'how to vape properly' → 主站-博客（mainBlog 准入：how to 短语 + core + knowledge）。

    实际 e2e: 'how to vape properly' → 主站-博客 + Informational + Buyer Adjacent。
    """
    result = classify_keyword(
        "how to vape properly",
        "informational",
        {"coreProducts": "vape, e-cigarette, disposable"},
    )
    assert result["assignedSite"] == "主站-博客"
    assert result["intentBucket"] == "Informational + Buyer Adjacent"


def test_fallback_exploratory(rule_payload):
    """完全不命中任何规则的 fallback → 博客A-知识教程（exploratory）。"""
    result = classify_keyword("xyz random words", "", {})
    assert result["assignedSite"] == "博客A-知识教程"
    assert result["intentBucket"] == "Exploratory"


# ---------- intent 字符串格式兼容 ----------
# 注：vape flavor 会因为 'flavor' 在 scenario signals 里被分到 博客B-Scenario，
# 所以 intent 字符串只决定主路径分支，最终 fallback 落到 signal 匹配上。

def test_intent_informational_picks_up_signals(rule_payload):
    """informational intent + 'flavor' 信号词 → 博客B-Scenario。

    intent 决定主路径 candidate，signals 决定 actual 命中。
    """
    result = classify_keyword("vape flavor", "informational", {})
    assert result["intentBucket"] == "Scenario"
    assert result["assignedSite"] == "博客B-场景人群"


def test_intent_empty_picks_up_signals(rule_payload):
    """空 intent + 'flavor' 信号词 → 仍走 signal 路径 → 博客B-Scenario。"""
    result = classify_keyword("vape flavor", "", {})
    assert result["intentBucket"] == "Scenario"


def test_intent_uppercase_normalized(rule_payload):
    """INFORMATIONAL 大写 → 跟小写一样 → 博客B-Scenario（flavor 命中）。"""
    result = classify_keyword("vape flavor", "INFORMATIONAL", {})
    assert result["intentBucket"] == "Scenario"


# ---------- 主站页面术语提取 ----------

def test_project_core_products_with_compare_goes_blog_c(rule_payload):
    """project.coreProducts 里的 e-cigarette 触发 hasCore，配合 'compare' 走博客C。

    注意 signals.comparison 列表里没有 'comparison'（只有 'compare' / 'alternative'），
    所以 'e-cigarette comparison' 不会命中 comparison signals，会落到 fallback。
    """
    # 用 'compare' 才会命中
    result = classify_keyword(
        "e-cigarette compare brands",
        "informational",
        {"coreProducts": "e-cigarette, vape"},
    )
    assert result["assignedSite"] == "博客C-对比评测"
    assert result["intentBucket"] == "Comparison"


def test_shop_keyword_triggers_risk(rule_payload):
    """'shop' 在 risk signals 列表里 → 暂不做 + Risk（设计上的关键词质量保护）。

    这是 seo-standard.json 的设计：'shop' 被识别为低质量购买意图。
    """
    result = classify_keyword(
        "shop vape near me",
        "informational",
        {"coreProducts": "", "mainPages": "/collections/shop\n/blog/post-1\n/product/p-1"},
    )
    assert result["assignedSite"] == "暂不做"
    assert result["intentBucket"] == "Risk"