"""资产解析：把关键词解析为 target asset URL + status + contentAction。
全部从 payload.assets 读判定规则。
"""
from __future__ import annotations

import re
from typing import Any

from app.engine.loader import get_store


def _is_usable_url(value: str) -> bool:
    pattern = get_store().get("assets.urlPatterns.usable", r"^https?://|^/")
    return bool(re.match(pattern, value.strip()))


def _project_pages(project: dict[str, Any]) -> list[str]:
    raw = str(project.get("mainPages") or "")
    pages: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.search(r"https?://\S+|/[A-Za-z0-9_./?=&%#-]+", line)
        if m:
            pages.append(m.group(0).rstrip("，,。;；"))
    return pages


def _planned_url(item: dict[str, Any]) -> str:
    base = item.get("topicCluster") or item.get("pageGroup") or item.get("keyword") or ""
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return f"/{slug}"


def _fallback_asset(project: dict[str, Any]) -> str:
    site_type = str(project.get("siteType") or "").lower()
    table = get_store().get("assets.fallbackBySiteType", {}) or {}
    for key, url in table.items():
        if key.lower() in site_type:
            return url
    return get_store().get("assets.fallbackDefault", "/collections/all")


def resolve_target_asset(item: dict[str, Any] | None, project: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回 { url, status, contentAction, reason }。"""
    project = project or {}
    if not item:
        return {"url": "", "status": "missing", "contentAction": "needs_keyword", "reason": "未选择关键词。"}

    url = str(item.get("url") or "")
    if _is_usable_url(url):
        return {"url": url, "status": "existing", "contentAction": "update_or_link", "reason": "导入数据里已有可用 URL，视为已存在页面。"}

    pages = _project_pages(project)
    if pages:
        text = f"{item.get('keyword','')} {item.get('topicCluster','')} {item.get('pageGroup','')}".lower()
        terms = [t for t in re.split(r"[^a-z0-9]+", text) if len(t) >= 3]
        scored = sorted(
            ({"page": p, "score": sum(1 for t in terms if t in p.lower())} for p in pages),
            key=lambda x: x["score"],
            reverse=True,
        )
        matched = next((s for s in scored if s["score"] > 0), None)
        if matched:
            ca = (
                "optimize_existing_page"
                if item.get("assignedSite") == "主站-集合页"
                else "create_or_update_supporting_content"
            )
            return {"url": matched["page"], "status": "existing", "contentAction": ca, "reason": "已匹配到网站定位里填写的主站页面。"}

        blog_re = get_store().get("assets.blogPathPattern", r"blog|post|guide|article")
        default_page = next((p for p in pages if not re.search(blog_re, p, re.IGNORECASE)), pages[0])
        return {"url": default_page, "status": "needs_review", "contentAction": "manual_parent_review", "reason": "有主站页面可选，但没有与关键词明显匹配，需要人工确认承接页。"}

    planned = _planned_url(item) or _fallback_asset(project)
    ca = "create_commercial_page_first" if item.get("assignedSite") == "主站-集合页" else "create_new_article"
    return {"url": planned, "status": "planned", "contentAction": ca, "reason": "当前没有可确认的已存在页面，先作为规划页面，不应在正文生成真实内链。"}


def page_role_for(item: dict[str, Any]) -> str:
    if not item:
        return ""
    site = item.get("assignedSite") or ""
    if site == "主站-集合页":
        return "Commercial Hub"
    if site == "主站-博客":
        return "Buyer Education"
    if "知识" in site:
        return "Knowledge Support"
    if "场景" in site:
        return "Scenario Support"
    if "对比" in site:
        return "Comparison Support"
    if site == "暂不做":
        return "Hold"
    return "Content Support"