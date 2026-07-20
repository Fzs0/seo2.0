"""为站点生成、保存可确认的内容知识画像。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai_provider import generate_ai_content


async def generate_site_knowledge(session: AsyncSession, site_id: str) -> dict[str, Any]:
    site = await _load_site(session, site_id)
    if not site:
        raise ValueError("site not found")

    posts = await _load_posts(session, site_id)
    keywords = await _load_keywords(session, site_id)
    products = await _load_products(session, site_id)
    evidence = _build_evidence(site, posts, keywords, products)
    prompt = _build_prompt(site, posts, keywords, products)
    ai = await generate_ai_content(stage="keyword_analysis", prompt=prompt)
    parsed_profile = _parse_profile(ai.get("content") or "")
    existing_profile = site.get("knowledge_profile") if isinstance(site.get("knowledge_profile"), dict) else {}
    profile = parsed_profile or _fallback_profile(site, posts, keywords)
    if not parsed_profile:
        ai_status = ai.get("status") or "ai-empty-response"
    else:
        ai_status = "completed"

    profile = _normalize_profile({**existing_profile, **profile, "status": "draft", "evidence": evidence})
    profile["generated_at"] = datetime.now(timezone.utc).isoformat()
    await save_site_knowledge(session, site_id, profile)
    return {
        "site_id": site_id,
        "knowledge_profile": profile,
        "ai": {
            "status": ai_status,
            "configured": bool(ai.get("configured")),
            "provider": ai.get("provider") or "local-evidence",
            "model": ai.get("model") or "",
        },
        "sources": {"posts": len(posts), "keywords": len(keywords)},
    }


async def save_site_knowledge(session: AsyncSession, site_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    current = (
        await session.execute(
            text("SELECT id, knowledge_profile FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"),
            {"id": site_id},
        )
    ).mappings().first()
    if not current:
        raise ValueError("site not found")
    normalized = _normalize_profile(profile)
    await session.execute(
        text("UPDATE seo_agent.sites SET knowledge_profile = CAST(:profile AS jsonb), updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": site_id, "profile": json.dumps(normalized, ensure_ascii=False)},
    )
    await session.commit()
    return normalized


async def _load_site(session: AsyncSession, site_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id, site_key, name, domain, base_url, market, language_code,
                       content_role, content_scope, is_main, notes, knowledge_profile
                  FROM seo_agent.sites
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": site_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _load_posts(session: AsyncSession, site_id: str) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            """
            SELECT title, excerpt, primary_keyword, meta_title, meta_description,
                   content_md, content_html
              FROM seo_agent.posts
             WHERE site_id = CAST(:site_id AS uuid)
               AND COALESCE(status, '') <> 'remote_missing'
             ORDER BY published_at DESC NULLS LAST, fetched_at DESC
             LIMIT 24
            """
        ),
        {"site_id": site_id},
    )
    items = []
    for row in rows.mappings().all():
        item = dict(row)
        item["content_excerpt"] = _plain_text(item.get("content_html") or item.get("content_md"))[:500]
        item.pop("content_html", None)
        item.pop("content_md", None)
        items.append(item)
    return items


async def _load_keywords(session: AsyncSession, site_id: str) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            """
            SELECT keyword, intent, volume, kd, topic_cluster, page_type, page_role
              FROM seo_agent.keywords
             WHERE assigned_site_id = CAST(:site_id AS uuid)
             ORDER BY volume DESC NULLS LAST, score DESC NULLS LAST
             LIMIT 40
            """
        ),
        {"site_id": site_id},
    )
    return [dict(row) for row in rows.mappings().all()]


async def _load_products(session: AsyncSession, site_id: str) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            """
            SELECT title, url, category, description, keywords
              FROM seo_agent.products
             WHERE site_id = CAST(:site_id AS uuid)
             ORDER BY updated_at DESC NULLS LAST
             LIMIT 80
            """
        ),
        {"site_id": site_id},
    )
    return [dict(row) for row in rows.mappings().all()]


def _build_prompt(site: dict[str, Any], posts: list[dict[str, Any]], keywords: list[dict[str, Any]], products: list[dict[str, Any]]) -> str:
    existing_scan = site.get("knowledge_profile") if isinstance(site.get("knowledge_profile"), dict) else {}
    payload = {
        "site": {key: site.get(key) for key in ("site_key", "name", "domain", "market", "language_code", "content_role", "content_scope", "is_main", "notes")},
        "published_articles": posts,
        "products": products,
        "main_site_index_scan": existing_scan.get("index_scan") or {},
    }
    return """你是站点内容规划师。根据输入事实，为这个站点生成一个可供 SEO 和内容团队执行的知识画像。
只使用输入中出现的事实；数据不足时明确保留空数组或写“待确认”，不要臆造产品、品牌和受众。
返回且只返回 JSON 对象，不要 Markdown，字段必须包含：
site_mode（站点模式）、positioning（站点定位）、audience（目标受众）、products（产品或服务）、in_scope_topics（应该持续写的主题）、out_of_scope_topics（暂不应该写的主题）、content_types（适合的内容类型）、tone（写作语气）、conversion_goals（转化目标）、conversion_targets（转化页面）、internal_link_rules（内链规则）、restricted_topics（限制主题）、editorial_rules（编辑规则）。
如果这是主站，必须区分商业页面和文章：文章负责获取和解释搜索需求，产品页/分类页负责承接转化；根据扫描到的核心页面提出内链方向。
已分配关键词不是站点定位或内容范围的权威证据，不得用它扩展 in_scope_topics。
不要返回 confidence 或任何分数。""" + "\n\n输入事实：\n" + json.dumps(payload, ensure_ascii=False, default=str)


def _build_evidence(site: dict[str, Any], posts: list[dict[str, Any]], keywords: list[dict[str, Any]], products: list[dict[str, Any]]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for label, value in (("内容角色", site.get("content_role")), ("内容范围", site.get("content_scope")), ("市场", site.get("market")), ("语言", site.get("language_code"))):
        if value:
            evidence.append({"source": "site_config", "fact": f"{label}：{value}"})
    titles = [str(item.get("title") or "").strip() for item in posts if str(item.get("title") or "").strip()]
    if titles:
        evidence.append({"source": "site_content", "fact": f"已读取 {len(titles)} 篇文章，标题样本：{'；'.join(titles[:8])}"})
    if products:
        evidence.append({"source": "product_catalog", "fact": f"已读取 {len(products)} 个产品记录，样本：{'；'.join(str(item.get('title') or '') for item in products[:8])}"})
    index_scan = site.get("knowledge_profile") if isinstance(site.get("knowledge_profile"), dict) else {}
    summary = (index_scan.get("index_scan") or {}).get("summary") or {}
    if summary:
        evidence.append({"source": "site_index", "fact": f"主站索引扫描 {summary.get('pages') or 0} 页，发现 {summary.get('issues') or 0} 个 SEO 页面问题"})
    return evidence


def _fallback_profile(site: dict[str, Any], posts: list[dict[str, Any]], keywords: list[dict[str, Any]]) -> dict[str, Any]:
    topics = _unique([
        *_list(site.get("content_scope")),
        *(item.get("primary_keyword") for item in posts),
        *(item.get("title") for item in posts),
    ])
    return {
        "site_mode": "commercial_hub" if site.get("is_main") else "content_site",
        "positioning": site.get("content_role") or "待确认站点定位",
        "audience": "待确认",
        "products": [],
        "in_scope_topics": topics[:12],
        "out_of_scope_topics": [],
        "content_types": ["教程、指南"] if posts else [],
        "tone": "待确认",
        "conversion_goals": [],
        "conversion_targets": [item.get("url") for item in (site.get("knowledge_profile") or {}).get("core_pages", []) if item.get("page_type") in {"product", "category", "service"} and item.get("url")] if isinstance(site.get("knowledge_profile"), dict) else [],
        "internal_link_rules": ["文章优先链接到相关产品页或分类页"] if site.get("is_main") else [],
        "editorial_rules": [],
    }


def _parse_profile(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    if isinstance(value, dict) and isinstance(value.get("profile"), dict):
        value = value["profile"]
    return value if isinstance(value, dict) else {}


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    result = {
        "status": profile.get("status") if profile.get("status") in {"draft", "confirmed"} else "draft",
        "site_mode": _string(profile.get("site_mode")),
        "positioning": _string(profile.get("positioning")),
        "audience": _string(profile.get("audience")),
        "tone": _string(profile.get("tone")),
        "products": _list(profile.get("products")),
        "in_scope_topics": _list(profile.get("in_scope_topics")),
        "out_of_scope_topics": _list(profile.get("out_of_scope_topics")),
        "content_types": _list(profile.get("content_types")),
        "conversion_goals": _list(profile.get("conversion_goals")),
        "editorial_rules": _list(profile.get("editorial_rules")),
        "conversion_targets": _list(profile.get("conversion_targets")),
        "restricted_topics": _list(profile.get("restricted_topics")),
        "internal_link_rules": _list(profile.get("internal_link_rules")),
        "core_pages": [item for item in profile.get("core_pages", []) if isinstance(item, dict)][:80] if isinstance(profile.get("core_pages"), list) else [],
        "index_scan": profile.get("index_scan") if isinstance(profile.get("index_scan"), dict) else {},
        "evidence": [item for item in profile.get("evidence", []) if isinstance(item, dict)][:20] if isinstance(profile.get("evidence"), list) else [],
    }
    for key in ("generated_at", "updated_at"):
        if profile.get(key):
            result[key] = str(profile[key])
    return result


def _plain_text(value: Any) -> str:
    value = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", str(value or ""), flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,，\n]", value)
    return _unique([str(item).strip() for item in value] if isinstance(value, list) else [])[:20]


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


def _string(value: Any) -> str:
    return str(value or "").strip()[:500]
