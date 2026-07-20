from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai_provider import generate_ai_content, is_stage_configured
from app.clients.publishers import strip_markdown_frontmatter
from app.clients.serpapi import fetch_google_serp
from app.engine.content_plan import image_plan_for, reference_plan
from app.services.article_service import save_article
from app.services.brief_service import build_brief_with_optional_ai
from app.services.keyword_query_service import get_keyword
from app.services.site_service import resolve_site_id


async def generate_article_from_keyword(session: AsyncSession, keyword_id: str) -> dict[str, Any]:
    result = await generate_article_pipeline(session, keyword_id)
    if result.get("status") == "failed":
        failed = next((s for s in result["steps"] if s["status"] == "failed"), None)
        raise ValueError((failed or {}).get("message") or "article pipeline failed")
    return result


async def generate_article_pipeline(
    session: AsyncSession,
    keyword_id: str | None,
    *,
    forced_site_id: str | None = None,
    approved_strategy: dict[str, Any] | None = None,
    keyword_context: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    def ok(key: str, label: str, message: str = "", data: dict[str, Any] | None = None) -> None:
        steps.append({"key": key, "label": label, "status": "done", "message": message, "data": data or {}})

    def fail(key: str, label: str, message: str) -> dict[str, Any]:
        steps.append({"key": key, "label": label, "status": "failed", "message": message, "data": {}})
        return {"status": "failed", "steps": steps}

    if not approved_strategy:
        return fail("strategy", "校验已审核策略", "正式生文必须来自已审核的今日计划")
    strategy_type = approved_strategy.get("strategy_type")
    if not keyword_id and strategy_type != "update_article":
        return fail("keyword", "校验关键词", "新文章策略必须关联关键词")

    keyword = await get_keyword(session, keyword_id) if keyword_id else dict(keyword_context or {})
    if not keyword or not keyword.get("keyword"):
        return fail("keyword", "读取关键词", "keyword not found")
    keyword.setdefault("id", keyword_id)
    strategy = approved_strategy
    if not is_stage_configured("article_generation"):
        return fail("config", "检查 AI 配置", "article_generation AI is not configured")
    ok("keyword", "读取关键词", keyword["keyword"])

    item = _compat_keyword(keyword)
    if approved_strategy:
        item["strategy"] = approved_strategy
    project = _project_for(keyword)
    requested_site_id = forced_site_id or keyword.get("assigned_site_id")
    assigned_site_id = await resolve_site_id(
        session,
        site_id=requested_site_id,
        label=None if requested_site_id else keyword.get("assigned_site_label"),
        market=keyword.get("market"),
        language_code=keyword.get("language_code"),
    )
    if not assigned_site_id:
        return fail("strategy", "校验目标站点", "关键词语种与目标站点不匹配，已阻止生成")
    if assigned_site_id and keyword_id:
        await session.execute(
            text("UPDATE seo_agent.keywords SET assigned_site_id = :site_id, updated_at = now() WHERE id = CAST(:id AS uuid)"),
            {"site_id": assigned_site_id, "id": keyword_id},
        )

    ok(
        "strategy",
        "读取 AI 策略",
        (strategy or {}).get("briefDirection")
        or (strategy or {}).get("strategyReason")
        or (strategy or {}).get("recommended_action")
        or "未发现策略，使用关键词基础信息",
    )

    await _set_task_stage(session, task_id, "serp")
    serp = await _latest_or_fetch_serp(session, keyword)
    ok(
        "serp",
        "获取 SERP",
        "已获取真实搜索结果" if serp.get("configured") or serp.get("source") == "cache" else serp.get("status") or "SerpApi 未配置，未使用搜索结果",
        {"source": serp.get("source"), "id": serp.get("id"), "organicCount": len(serp.get("organic_results") or [])},
    )

    await _set_task_stage(session, task_id, "brief")
    brief = await build_brief_with_optional_ai({"keyword": item, "project": project, "aiStage": {"provider": "configured"}})
    brief_text = brief.get("brief") if isinstance(brief.get("brief"), str) else json.dumps(brief, ensure_ascii=False, default=str)
    ok(
        "brief",
        "生成 Brief",
        "AI-enhanced brief" if brief.get("aiEnhanced") else "local brief",
        {
            "aiEnhanced": bool(brief.get("aiEnhanced")),
            "reason": (brief.get("aiMeta") or {}).get("reason"),
            "preview": brief_text[:800],
        },
    )

    await _set_task_stage(session, task_id, "outline")
    outline = await _generate_outline(item, project, brief_text, serp, approved_strategy)
    if outline.get("status"):
        return fail("outline", "生成大纲", outline["status"])
    ok("outline", "生成文章大纲", "大纲已生成", {"preview": (outline.get("content") or "")[:1200]})

    await _set_task_stage(session, task_id, "article")
    prompt = _compose_article_prompt(item, project, brief_text, serp, outline.get("content") or "", approved_strategy)
    ai = await generate_ai_content(stage="article_generation", prompt=prompt, project=project, keyword=item)
    content, frontmatter = strip_markdown_frontmatter(ai.get("content") or "")
    if not content:
        return fail("article", "生成文章", ai.get("status") or "AI article generation failed")

    title = frontmatter.get("title") or _title(content, keyword["keyword"])
    meta_description = _normalize_meta_description(frontmatter.get("meta_description") or _meta_description(content))
    await _set_task_stage(session, task_id, "qa")
    qa = _qa(content, keyword["keyword"], meta_description, title, (approved_strategy or {}).get("internal_link_plan") or [])
    failed_qa = [check["key"] for check in qa if not check["ok"]]
    ok("article", "生成文章", f"{title}（{len(content)} 字符）", {"model": ai.get("model"), "preview": content[:1600]})

    await _set_task_stage(session, task_id, "save")
    saved = await save_article(
        session,
        {
            "task_id": task_id,
            "site_id": assigned_site_id,
            "keyword_id": keyword["id"],
            "serp_snapshot_id": serp.get("id"),
            "title": title,
            "slug": _slug(keyword["keyword"]),
            "target_url": keyword.get("target_asset_url"),
            "status": "failed" if failed_qa else "generated",
            "language_code": keyword.get("language_code"),
            "market": keyword.get("market"),
            "brief_md": brief_text,
            "prompt_text": prompt,
            "content_md": content,
            "article_parts": {"outline": outline.get("content") or "", "strategy": approved_strategy or {}},
            "meta_title": frontmatter.get("title") or title,
            "meta_description": meta_description,
            "primary_keyword": keyword["keyword"],
            "secondary_keywords": [],
            "internal_link_plan": (approved_strategy or {}).get("internal_link_plan") or [],
            "image_plan": image_plan_for(item),
            "references_plan": reference_plan(item),
            "qa_checklist": qa,
            "generation_provider": ai.get("provider"),
            "generation_model": ai.get("model"),
            "raw_ai_response": {"status": ai.get("status"), "serp": serp, "qa": qa, "steps": steps},
        },
    )
    if keyword_id and not failed_qa:
        await session.execute(
            text("UPDATE seo_agent.keywords SET status = 'written', updated_at = now() WHERE id = CAST(:id AS uuid)"),
            {"id": keyword_id},
        )
    await session.commit()
    ok("save", "保存结果", saved.get("title") or "saved", {"articleId": str(saved.get("id") or "")})
    if failed_qa:
        return fail("qa", "发布前质量检查", f"QA 未通过：{', '.join(failed_qa)}") | {"article": saved, "qa": qa}
    ok("qa", "发布前质量检查", "全部通过")
    return {
        "status": "done",
        "steps": steps,
        "article": saved,
        "brief": {
            "source": brief.get("briefSource"),
            "aiEnhanced": bool(brief.get("aiEnhanced")),
            "aiMeta": brief.get("aiMeta") or {},
            "text": brief_text,
        },
        "outline": outline.get("content") or "",
        "content": content,
        "contentPreview": content[:3000],
        "savedTo": {"table": "seo_agent.articles", "articleId": str(saved.get("id") or "")},
        "serp": serp,
        "qa": qa,
        "model": ai.get("model"),
        "contentLength": len(content),
    }


async def _set_task_stage(session: AsyncSession, task_id: str | None, stage: str) -> None:
    if not task_id:
        return
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET decision = jsonb_set(decision, '{current_stage}', to_jsonb(CAST(:stage AS text)), true),
                   logs = logs || jsonb_build_array(jsonb_build_object('stage', CAST(:stage AS text), 'at', now())),
                   updated_at = now()
             WHERE id = CAST(:id AS uuid) AND status = 'running'
            """
        ),
        {"id": task_id, "stage": stage},
    )
    await session.commit()


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


async def _generate_outline(
    item: dict[str, Any],
    project: dict[str, Any],
    brief: str,
    serp: dict[str, Any],
    approved_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = approved_strategy or item.get("strategy") or ((item.get("ai_review") or {}).get("strategy") if isinstance(item.get("ai_review"), dict) else {})
    prompt = "\n\n".join(
        [
            "Create an SEO article outline only. Do not write the article.",
            "Use Markdown headings. Include H1, H2/H3 sections, FAQ, CTA, and notes on what each section must answer.",
            f"Primary keyword: {item.get('keyword')}",
            "Approved strategy, site knowledge, competitor gap, recommended action and internal links. Treat these as mandatory constraints:",
            json.dumps(strategy, ensure_ascii=False, default=str, indent=2),
            "SERP context:",
            _serp_context(serp),
            "Brief:",
            brief,
            f"Project: {json.dumps(project, ensure_ascii=False)}",
        ]
    )
    ai = await generate_ai_content(stage="article_generation", prompt=prompt, project=project, keyword=item)
    return {"content": ai.get("content") or "", "status": ai.get("status") if not ai.get("content") else None}


def _compose_article_prompt(
    item: dict[str, Any],
    project: dict[str, Any],
    brief: str,
    serp: dict[str, Any],
    outline: str,
    approved_strategy: dict[str, Any] | None = None,
) -> str:
    strategy = approved_strategy or item.get("strategy") or ((item.get("ai_review") or {}).get("strategy") if isinstance(item.get("ai_review"), dict) else {})
    return "\n\n".join(
        [
            "Write a production-ready SEO article in Markdown. No placeholders. No fabricated rankings or URLs.",
            "Output reader-facing article content only; do not include editorial notes, publication instructions, review plans, or parent-page availability notes.",
            "Start with YAML frontmatter using the exact keys title and meta_description. Keep meta_description between 120 and 160 characters. Then write the article body beginning with one H1.",
            f"Current date: {date.today().isoformat()}. Use the current year for time-sensitive titles and metadata.",
            "Follow the brief and use SERP evidence only as competitive context.",
            "Required: H1, meta title, meta description, useful H2 sections, FAQ, natural CTA.",
            f"Primary keyword: {item.get('keyword')}",
            "Use the exact primary keyword phrase at least once in the H1 or opening paragraph; do not replace it with a synonym.",
            f"Semrush signals: intent={item.get('intent') or ''}; trend={json.dumps(item.get('trendData') or item.get('trend_data') or [], ensure_ascii=False)}; SERP features={json.dumps(item.get('serpFeatures') or item.get('serp_features') or [], ensure_ascii=False)}. Use these only to shape page type and sections; do not present them as page facts.",
            f"Target site/role: {item.get('assignedSite') or ''}",
            f"Project: {json.dumps(project, ensure_ascii=False)}",
            "Approved strategy, site knowledge, competitor gap, recommended action and internal links. Implement them; never invent missing evidence or URLs:",
            json.dumps(strategy, ensure_ascii=False, default=str, indent=2),
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
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70] or "article"


def _meta_description(content: str) -> str:
    m = re.search(r"meta description[:\n]\s*(.+)", content, re.I)
    candidates = [m.group(1)] if m else re.split(r"\n\s*\n", content)
    for candidate in candidates:
        if re.match(r"^\s*(?:#{1,6}\s|[-+*]\s|\d+[.)]\s|\||```)", candidate):
            continue
        plain = _normalize_meta_description(candidate)
        if len(plain) < 120:
            continue
        return plain
    return ""


def _normalize_meta_description(value: str) -> str:
    plain = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value or "")
    plain = re.sub(r"<[^>]+>|[*_`~]", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) > 160:
        shortened = plain[:157].rstrip(" ,;:-.")
        word_boundary = shortened.rsplit(" ", 1)[0]
        plain = (word_boundary if len(word_boundary) >= 120 else shortened) + "."
    return plain


def _qa(
    content: str,
    keyword: str,
    meta_description: str = "",
    title: str = "",
    internal_link_plan: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    h1 = re.findall(r"^#\s+(.+)$", content, re.M)
    title_years = re.findall(r"\b20\d{2}\b", " ".join([title, *h1]))
    planned_urls = [str(link.get("url") or "") for link in internal_link_plan or [] if link.get("url")]
    checks = {
        "has_h1": len(h1) == 1,
        "has_h2": bool(re.search(r"^##\s+", content, re.M)),
        "has_faq": bool(re.search(r"\bfaq\b|frequently asked questions", content, re.I)),
        "has_keyword": _contains_keyword(" ".join((content, title)), keyword),
        "has_meta_description": 120 <= len(meta_description.strip()) <= 160,
        "min_length_1200": len(content) >= 1200,
        "title_year_is_current": not title_years or all(year == str(date.today().year) for year in title_years),
        "has_planned_internal_link": not planned_urls or any(url in content for url in planned_urls),
    }
    return [{"key": k, "ok": v} for k, v in checks.items()]


def _contains_keyword(text: str, keyword: str) -> bool:
    tokens = re.findall(r"\w+", keyword.casefold(), re.UNICODE)
    if not tokens:
        return False
    pattern = r"(?<!\w)" + r"[\W_]+".join(re.escape(token) for token in tokens) + r"(?!\w)"
    return bool(re.search(pattern, text.casefold(), re.UNICODE))
