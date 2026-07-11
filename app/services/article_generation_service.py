from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai_provider import generate_ai_content, is_stage_configured
from app.clients.serpapi import fetch_google_serp
from app.engine.content_plan import image_plan_for, reference_plan
from app.services.article_service import save_article
from app.services.brief_service import build_brief_with_optional_ai
from app.services.keyword_query_service import get_keyword


async def generate_article_from_keyword(session: AsyncSession, keyword_id: str) -> dict[str, Any]:
    result = await generate_article_pipeline(session, keyword_id)
    if result.get("status") == "failed":
        failed = next((s for s in result["steps"] if s["status"] == "failed"), None)
        raise ValueError((failed or {}).get("message") or "article pipeline failed")
    return result


async def generate_article_pipeline(session: AsyncSession, keyword_id: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    def ok(key: str, label: str, message: str = "", data: dict[str, Any] | None = None) -> None:
        steps.append({"key": key, "label": label, "status": "done", "message": message, "data": data or {}})

    def fail(key: str, label: str, message: str) -> dict[str, Any]:
        steps.append({"key": key, "label": label, "status": "failed", "message": message, "data": {}})
        return {"status": "failed", "steps": steps}

    keyword = await get_keyword(session, keyword_id)
    if not keyword:
        return fail("keyword", "读取关键词", "keyword not found")
    if not is_stage_configured("article_generation"):
        return fail("config", "检查 AI 配置", "article_generation AI is not configured")
    ok("keyword", "读取关键词", keyword["keyword"])

    item = _compat_keyword(keyword)
    project = _project_for(keyword)

    strategy = (keyword.get("ai_review") or {}).get("strategy") if isinstance(keyword.get("ai_review"), dict) else None
    ok("strategy", "读取 AI 策略", (strategy or {}).get("briefDirection") or (strategy or {}).get("strategyReason") or "未发现策略，使用关键词基础信息")

    serp = await _latest_or_fetch_serp(session, keyword)
    ok(
        "serp",
        "获取 SERP",
        "已获取真实搜索结果" if serp.get("configured") or serp.get("source") == "cache" else serp.get("status") or "SerpApi 未配置，未使用搜索结果",
        {"source": serp.get("source"), "id": serp.get("id"), "organicCount": len(serp.get("organic_results") or [])},
    )

    brief = await build_brief_with_optional_ai({"keyword": item, "project": project, "aiStage": {"provider": "configured"}})
    brief_text = brief.get("brief") if isinstance(brief.get("brief"), str) else json.dumps(brief, ensure_ascii=False, default=str)
    ok(
        "brief",
        "生成 Brief",
        brief.get("briefSource") or "brief-ready",
        {
            "aiEnhanced": bool(brief.get("aiEnhanced")),
            "reason": (brief.get("aiMeta") or {}).get("reason"),
            "preview": brief_text[:800],
        },
    )

    outline = await _generate_outline(item, project, brief_text, serp)
    if outline.get("status"):
        return fail("outline", "生成大纲", outline["status"])
    ok("outline", "生成文章大纲", "大纲已生成", {"preview": (outline.get("content") or "")[:1200]})

    prompt = _compose_article_prompt(item, project, brief_text, serp, outline.get("content") or "")
    ai = await generate_ai_content(stage="article_generation", prompt=prompt, project=project, keyword=item)
    content = ai.get("content") or ""
    if not content:
        return fail("article", "生成文章", ai.get("status") or "AI article generation failed")

    title = _title(content, keyword["keyword"])
    qa = _qa(content, keyword["keyword"])
    ok("article", "生成文章", f"{title}（{len(content)} 字符）", {"model": ai.get("model"), "preview": content[:1600]})

    saved = await save_article(
        session,
        {
            "site_id": keyword.get("assigned_site_id"),
            "keyword_id": keyword["id"],
            "serp_snapshot_id": serp.get("id"),
            "title": title,
            "slug": _slug(title),
            "target_url": keyword.get("target_asset_url"),
            "status": "generated",
            "language_code": keyword.get("language_code"),
            "market": keyword.get("market"),
            "brief_md": brief_text,
            "prompt_text": prompt,
            "content_md": content,
            "article_parts": {"outline": outline.get("content") or ""},
            "meta_title": title,
            "meta_description": _meta_description(content),
            "primary_keyword": keyword["keyword"],
            "secondary_keywords": [],
            "internal_link_plan": [],
            "image_plan": image_plan_for(item),
            "references_plan": reference_plan(item),
            "qa_checklist": qa,
            "generation_provider": ai.get("provider"),
            "generation_model": ai.get("model"),
            "raw_ai_response": {"status": ai.get("status"), "serp": serp, "qa": qa, "steps": steps},
        },
    )
    await session.execute(
        text("UPDATE seo_agent.keywords SET status = 'written', updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": keyword_id},
    )
    await session.commit()
    ok("save", "保存结果", saved.get("title") or "saved", {"articleId": str(saved.get("id") or "")})
    return {
        "status": "done",
        "steps": steps,
        "article": saved,
        "brief": {"source": brief.get("briefSource"), "text": brief_text},
        "outline": outline.get("content") or "",
        "contentPreview": content[:3000],
        "savedTo": {"table": "seo_agent.articles", "articleId": str(saved.get("id") or "")},
        "serp": serp,
        "qa": qa,
        "model": ai.get("model"),
        "contentLength": len(content),
    }


async def _latest_or_fetch_serp(session: AsyncSession, keyword: dict[str, Any]) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT id, organic_results, related_questions, related_searches, requested_at
                  FROM seo_agent.serp_snapshots
                 WHERE keyword_id = CAST(:keyword_id AS uuid)
                 ORDER BY requested_at DESC
                 LIMIT 1
                """
            ),
            {"keyword_id": keyword["id"]},
        )
    ).mappings().first()
    if row:
        return {**dict(row), "id": str(row["id"]), "requested_at": str(row["requested_at"]), "source": "cache"}

    data = await fetch_google_serp(
        keyword["keyword"],
        gl=keyword.get("google_gl") or "us",
        hl=keyword.get("google_hl") or "en",
    )
    if not data.get("configured"):
        return {"id": None, "source": "serpapi", "status": "serpapi-not-configured"}

    saved = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.serp_snapshots
                  (keyword_id, keyword, google_gl, google_hl, top_result_count,
                   organic_results, related_questions, related_searches, raw)
                VALUES
                  (CAST(:keyword_id AS uuid), :keyword, :gl, :hl, :count,
                   CAST(:organic AS jsonb), CAST(:paa AS jsonb), CAST(:related AS jsonb), CAST(:raw AS jsonb))
                RETURNING id
                """
            ),
            {
                "keyword_id": keyword["id"],
                "keyword": keyword["keyword"],
                "gl": keyword.get("google_gl") or "us",
                "hl": keyword.get("google_hl") or "en",
                "count": len(data.get("organic_results") or []),
                "organic": json.dumps(data.get("organic_results") or [], ensure_ascii=False),
                "paa": json.dumps(data.get("related_questions") or [], ensure_ascii=False),
                "related": json.dumps(data.get("related_searches") or [], ensure_ascii=False),
                "raw": json.dumps(data, ensure_ascii=False),
            },
        )
    ).mappings().first()
    await session.commit()
    return {"id": str(saved["id"]) if saved else None, "source": "serpapi", **data}


def _compat_keyword(k: dict[str, Any]) -> dict[str, Any]:
    return {
        **k,
        "topicCluster": k.get("topic_cluster"),
        "pageGroup": k.get("page_group"),
        "seedKeyword": k.get("seed_keyword"),
        "assignedSite": k.get("assigned_site_label"),
        "pageType": k.get("page_type"),
        "pageRole": k.get("page_role"),
        "targetAsset": k.get("target_asset_url"),
        "assetStatus": k.get("asset_status"),
        "contentAction": k.get("content_action"),
    }


def _project_for(k: dict[str, Any]) -> dict[str, Any]:
    return {"market": k.get("market"), "languageCode": k.get("language_code"), "domain": k.get("assigned_site_label")}


async def _generate_outline(item: dict[str, Any], project: dict[str, Any], brief: str, serp: dict[str, Any]) -> dict[str, Any]:
    prompt = "\n\n".join(
        [
            "Create an SEO article outline only. Do not write the article.",
            "Use Markdown headings. Include H1, H2/H3 sections, FAQ, CTA, and notes on what each section must answer.",
            f"Primary keyword: {item.get('keyword')}",
            f"Strategy: {(item.get('ai_review') or {}).get('strategy') if isinstance(item.get('ai_review'), dict) else ''}",
            "SERP context:",
            _serp_context(serp),
            "Brief:",
            brief,
            f"Project: {json.dumps(project, ensure_ascii=False)}",
        ]
    )
    ai = await generate_ai_content(stage="article_generation", prompt=prompt, project=project, keyword=item)
    return {"content": ai.get("content") or "", "status": ai.get("status") if not ai.get("content") else None}


def _compose_article_prompt(item: dict[str, Any], project: dict[str, Any], brief: str, serp: dict[str, Any], outline: str) -> str:
    return "\n\n".join(
        [
            "Write a production-ready SEO article in Markdown. No placeholders. No fabricated rankings or URLs.",
            "Follow the brief and use SERP evidence only as competitive context.",
            "Required: H1, meta title, meta description, useful H2 sections, FAQ, natural CTA.",
            f"Primary keyword: {item.get('keyword')}",
            f"Target site/role: {item.get('assignedSite') or ''}",
            f"Project: {json.dumps(project, ensure_ascii=False)}",
            "SERP context:",
            _serp_context(serp),
            "Approved outline:",
            outline,
            "Brief:",
            brief,
        ]
    )


def _serp_context(serp: dict[str, Any]) -> str:
    organic = (serp.get("organic_results") or [])[:5]
    paa = (serp.get("related_questions") or [])[:5]
    related = (serp.get("related_searches") or [])[:5]
    return json.dumps(
        {
            "status": serp.get("status"),
            "source": serp.get("source"),
            "topResults": [{"title": r.get("title"), "link": r.get("link"), "snippet": r.get("snippet")} for r in organic],
            "peopleAlsoAsk": [q.get("question") or q.get("title") for q in paa],
            "relatedSearches": [r.get("query") or r.get("title") for r in related],
        },
        ensure_ascii=False,
        indent=2,
    )


def _title(content: str, fallback: str) -> str:
    return (re.search(r"^#\s+(.+)$", content, re.M) or [None, fallback])[1].strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80] or "article"


def _meta_description(content: str) -> str:
    m = re.search(r"meta description[:\n]\s*(.+)", content, re.I)
    return (m.group(1).strip()[:180] if m else "")


def _qa(content: str, keyword: str) -> list[dict[str, Any]]:
    checks = {
        "has_h1": bool(re.search(r"^#\s+", content, re.M)),
        "has_h2": bool(re.search(r"^##\s+", content, re.M)),
        "has_faq": "faq" in content.lower(),
        "has_keyword": keyword.lower() in content.lower(),
        "has_meta_description": bool(_meta_description(content)),
        "min_length_1200": len(content) >= 1200,
    }
    return [{"key": k, "ok": v} for k, v in checks.items()]
