"""assets 解析单测：5 种 URL / 主站页面场景。"""
from __future__ import annotations

import pytest

from app.engine.assets import (
    page_role_for,
    resolve_target_asset,
)


# ---------- 5 种 URL 场景 ----------

def test_url_already_provided_existing(rule_payload):
    """导入数据里 keyword.url 已经是 http(s) 完整 URL → existing。"""
    item = {"keyword": "best vape", "url": "https://example.com/collections/vapes"}
    asset = resolve_target_asset(item, {})
    assert asset["status"] == "existing"
    assert asset["url"] == "https://example.com/collections/vapes"
    assert asset["contentAction"] == "update_or_link"


def test_url_is_root_relative_path_existing(rule_payload):
    """url 是 /xxx 路径 → existing。"""
    item = {"keyword": "best vape", "url": "/collections/vapes"}
    asset = resolve_target_asset(item, {})
    assert asset["status"] == "existing"
    assert asset["url"] == "/collections/vapes"


def test_url_empty_with_matching_main_pages_existing(rule_payload):
    """url 空 + project.mainPages 有相关 URL → 匹配 → existing。"""
    item = {"keyword": "best disposable vapes", "url": ""}
    project = {"mainPages": "/collections/disposable-vapes\n/blog/post-1"}
    asset = resolve_target_asset(item, project)
    # "disposable vapes" / "best disposable" / "vapes" 都与 /collections/disposable-vapes 匹配
    assert asset["status"] == "existing"
    assert asset["url"] == "/collections/disposable-vapes"
    # 主站-集合页 → optimize_existing_page
    # 博客站 → create_or_update_supporting_content
    # 默认 assignedSite 是 Exploratory（fallback）→ 不是主站-集合页
    # 所以是 create_or_update_supporting_content
    assert asset["contentAction"] in ("optimize_existing_page", "create_or_update_supporting_content")


def test_url_empty_with_only_blog_pages_needs_review(rule_payload):
    """url 空 + mainPages 全是 /blog/* → 没有任何 page 匹配，fallback 到 needs_review。"""
    item = {"keyword": "best disposable vapes", "url": "", "assignedSite": "博客A-知识教程"}
    project = {"mainPages": "/blog/post-1\n/blog/post-2"}
    asset = resolve_target_asset(item, project)
    assert asset["status"] == "needs_review"
    # 第一个非 blog 页面（这里是 fallback 第一个）
    assert asset["contentAction"] == "manual_parent_review"


def test_url_empty_no_project_pages_planned(rule_payload):
    """url 空 + mainPages 空 → planned + plannedUrl。"""
    item = {"keyword": "best disposable vapes", "url": "", "topicCluster": "vapes"}
    project = {"mainPages": ""}
    asset = resolve_target_asset(item, project)
    assert asset["status"] == "planned"
    assert asset["url"] == "/vapes"  # topicCluster slug
    # 默认 assignedSite 是 fallback → Exploratory（不是主站-集合页）
    # 所以是 create_new_article
    assert asset["contentAction"] == "create_new_article"


# ---------- 边界 ----------

def test_asset_resolve_with_no_item_returns_missing(rule_payload):
    """空 item → missing + needs_keyword。"""
    asset = resolve_target_asset(None, {})
    assert asset["status"] == "missing"
    assert asset["contentAction"] == "needs_keyword"
    assert "未选择关键词" in asset["reason"]


def test_main_collection_assignment_creates_commercial_first(rule_payload):
    """url 空 + mainPages 空 + assignedSite='主站-集合页' → create_commercial_page_first。"""
    item = {"keyword": "buy vape", "url": "", "assignedSite": "主站-集合页"}
    project = {"mainPages": ""}
    asset = resolve_target_asset(item, project)
    assert asset["status"] == "planned"
    assert asset["contentAction"] == "create_commercial_page_first"


# ---------- page_role_for ----------

@pytest.mark.parametrize("site,expected_role", [
    ("主站-集合页", "Commercial Hub"),
    ("主站-博客", "Buyer Education"),
    ("博客A-知识教程", "Knowledge Support"),
    ("博客B-场景人群", "Scenario Support"),
    ("博客C-对比评测", "Comparison Support"),
    ("暂不做", "Hold"),
    ("未识别的新站点", "Content Support"),
    ("", "Content Support"),
])
def test_page_role_for(site, expected_role):
    assert page_role_for({"assignedSite": site}) == expected_role


def test_page_role_for_empty_item():
    """空 item → ''。"""
    assert page_role_for(None) == ""
    assert page_role_for({}) == ""