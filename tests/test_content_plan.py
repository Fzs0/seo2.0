"""content_plan 单测：outline / userQuestion / oneSentenceAnswer / imagePlan / cta 模板填充。

注意：所有模板字符串都来自 payload（不是代码硬编码），
修改 workflows/seo-standard.json 时要同步检查这些测试。
"""
from __future__ import annotations

import pytest

from app.engine.content_plan import (
    article_brief_template_for,
    article_type_for,
    cta_for,
    image_plan_for,
    one_sentence_answer_for,
    outline_for,
    reference_plan,
    user_question_for,
)


# ---------- outline_for ----------

def test_outline_main_collection_uses_payload_template(rule_payload):
    """assignedSite=主站-集合页 → outline 包含 payload.contentPlan.outlines.mainCollection 里的 H2。"""
    item = {"keyword": "buy vape online", "assignedSite": "主站-集合页"}
    outline = outline_for(item)
    assert len(outline) > 0
    # payload 里的 mainCollection outline 包含 "快速筛选"
    assert any("快速筛选" in line for line in outline), (
        f"outline 应包含 payload 里的 '快速筛选' H2，实际：{outline}"
    )


def test_outline_main_blog_uses_payload_template(rule_payload):
    """assignedSite=主站-博客 → outline 用 mainBlog 模板。"""
    item = {"keyword": "best disposable vapes", "assignedSite": "主站-博客"}
    outline = outline_for(item)
    assert any("先给结论" in line or "判断标准" in line for line in outline), (
        f"mainBlog outline 应含 '先给结论' 或 '判断标准'，实际：{outline}"
    )


def test_outline_comparison_uses_blog_c_template(rule_payload):
    """assignedSite 含 '对比' → outline 用 blogC 模板（含 Comparison Table）。"""
    item = {"keyword": "vape vs e-cigarette", "assignedSite": "博客C-对比评测"}
    outline = outline_for(item)
    # blogC outline 含 "快速结论表"
    assert any("结论" in line or "对比" in line for line in outline), outline


def test_outline_scenario_uses_blog_b_template(rule_payload):
    item = {"keyword": "vape flavor for beginner", "assignedSite": "博客B-场景人群"}
    outline = outline_for(item)
    assert any("场景" in line for line in outline), outline


def test_outline_knowledge_fallback(rule_payload):
    item = {"keyword": "what is vape", "assignedSite": "博客A-知识教程"}
    outline = outline_for(item)
    # knowledge outline 含 "用户真正想解决"
    assert len(outline) > 0


# ---------- user_question_for ----------

def test_user_question_main_collection(rule_payload):
    item = {"keyword": "buy vape", "assignedSite": "主站-集合页"}
    q = user_question_for(item)
    assert "buy vape" in q
    # mainCollection 模板提到"购买"或"产品"
    assert "购买" in q or "产品" in q or "选择" in q


def test_user_question_scenario(rule_payload):
    item = {"keyword": "vape flavor", "assignedSite": "博客B-场景人群"}
    q = user_question_for(item)
    assert "vape flavor" in q
    assert "场景" in q or "人群" in q or "口味" in q


def test_user_question_fallback(rule_payload):
    """不匹配任何站点 → 走 fallback 模板。"""
    item = {"keyword": "xyz test", "assignedSite": "未识别"}
    q = user_question_for(item)
    assert "xyz test" in q
    assert len(q) > 10


# ---------- one_sentence_answer_for ----------

def test_one_sentence_answer_main_collection(rule_payload):
    item = {"keyword": "buy vape", "assignedSite": "主站-集合页"}
    a = one_sentence_answer_for(item)
    # payload 模板用 {title} 占位（不是 {keyword}），会被 .title() → "Buy Vape"
    assert "Buy Vape" in a
    # mainCollection 模板提到 commercial landing page
    assert "commercial" in a.lower() or "landing" in a.lower() or "产品" in a or "分类" in a


def test_one_sentence_answer_knowledge(rule_payload):
    item = {"keyword": "what is vape", "assignedSite": "博客A-知识教程"}
    a = one_sentence_answer_for(item)
    assert "what is vape" in a


# ---------- cta_for ----------

def test_cta_main_collection_strong(rule_payload):
    item = {"keyword": "buy vape", "assignedSite": "主站-集合页"}
    asset = {"url": "/collections/vapes", "status": "existing"}
    cta = cta_for(item, asset)
    assert "强 CTA" in cta
    assert "/collections/vapes" in cta


def test_cta_knowledge_light(rule_payload):
    item = {"keyword": "what is vape", "assignedSite": "博客A-知识教程"}
    asset = {"url": "/blog/vape-guide", "status": "existing"}
    cta = cta_for(item, asset)
    assert "轻 CTA" in cta
    assert "/blog/vape-guide" in cta


def test_cta_comparison_medium(rule_payload):
    item = {"keyword": "best vape", "assignedSite": "博客C-对比评测"}
    asset = {"url": "/blog/best-vape", "status": "existing"}
    cta = cta_for(item, asset)
    assert "中强 CTA" in cta or "强 CTA" in cta  # 对比文用中强


# ---------- image_plan_for ----------

def test_image_plan_replaces_keyword_placeholder(rule_payload):
    item = {"keyword": "best disposable vapes"}
    images = image_plan_for(item)
    assert len(images) > 0
    # 每个 image 的 alt 字段应包含 keyword（不应该是 {keyword} 占位符）
    for img in images:
        assert "{keyword}" not in img["alt"], f"alt 模板未替换：{img}"
        assert "best disposable vapes" in img["alt"], f"alt 应含 keyword：{img}"


def test_image_plan_empty_item_returns_empty(rule_payload):
    assert image_plan_for(None) == []


# ---------- reference_plan ----------

def test_reference_no_trigger(rule_payload):
    item = {"keyword": "best vape"}
    refs = reference_plan(item)
    assert refs["triggered"] is False
    assert refs["sources"] == []


def test_reference_triggered_by_safety_keyword(rule_payload):
    """'safe' 在 FDA triggers.terms 里 → 触发 references。"""
    item = {"keyword": "is vaping safe for health"}
    refs = reference_plan(item)
    # FDA trigger terms: ['safe', 'safety', 'nicotine', 'e-cigarette', 'vaping', 'health', 'age', 'legal']
    # 'safe' 在里面 → trigger
    assert refs["triggered"] is True
    assert any(s["name"] == "FDA" for s in refs["sources"])


def test_reference_triggered_by_battery_keyword(rule_payload):
    """'battery' 触发 EPA references。"""
    item = {"keyword": "how to dispose vape battery"}
    refs = reference_plan(item)
    assert refs["triggered"] is True
    assert any(s["name"] == "EPA" for s in refs["sources"])


# ---------- article_type_for ----------

@pytest.mark.parametrize("site,expected_type", [
    ("主站-集合页", "集合页 / 商业承接页"),
    ("博客C-对比评测", "对比文 / Best / VS"),
    ("博客B-场景人群", "场景方案 / 人群方案"),
    ("博客A-知识教程", "教程 / FAQ / 合规科普"),
    ("主站-博客", "购买前教育文章"),
])
def test_article_type_for(site, expected_type):
    item = {"assignedSite": site, "intentBucket": "Commercial Investigation"}
    assert article_type_for(item) == expected_type


# ---------- article_brief_template_for 集成 ----------

def test_article_brief_template_includes_all_modules(rule_payload):
    """Brief 模板应包含 payload.articleBriefTemplate.modules 里所有 key。"""
    item = {
        "keyword": "best disposable vapes",
        "assignedSite": "主站-博客",
        "priority": "P0",
        "scores": {"total": 78},
    }
    template = article_brief_template_for(item, {})
    module_keys = [m.get("key") for m in template]
    # payload 里的 modules（contentPlan.articleBriefTemplate.modules）应全在
    payload_keys = (
        rule_payload.get("articleBriefTemplate", {}).get("modules") or []
    )
    payload_module_keys = [m.get("key") for m in payload_keys]
    for k in payload_module_keys:
        assert k in module_keys, f"module key {k!r} 缺失于 brief template"


def test_article_brief_template_none_item_returns_empty(rule_payload):
    assert article_brief_template_for(None, {}) == []