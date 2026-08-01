from __future__ import annotations

import json
import re
import hashlib
import unicodedata
from datetime import date
from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai_provider import generate_ai_content, is_stage_configured
from app.clients.publishers import strip_markdown_frontmatter
from app.clients.serpapi import fetch_google_serp, is_usable_serp_result
from app.core.content_signals import has_faq_signal
from app.engine.content_plan import image_plan_for, reference_plan
from app.services.article_service import save_article
from app.services.brief_service import build_brief_with_optional_ai
from app.services.generation_context_service import build_generation_context, context_prompt_block
from app.services.keyword_query_service import get_keyword
from app.services.site_service import resolve_site_id


logger = structlog.get_logger(__name__)


async def generate_legacy_article_preview(
    *,
    keyword: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    brief: str = "",
    prompt: str = "",
) -> dict[str, Any]:
    """Preserve the legacy mock-article contract behind the article module."""
    item = keyword or {}
    project_data = project or {}
    if is_stage_configured("article_generation"):
        prompt_text = _legacy_article_prompt(item, project_data, brief, prompt)
        ai = await generate_ai_content(
            stage="article_generation",
            prompt=prompt_text,
            project=project_data,
            keyword=item,
        )
        return {
            "content": ai.get("content") or "",
            "provider": ai.get("provider") or "",
            "model": ai.get("model") or "",
            "status": ai.get("status"),
            "generated": bool(ai.get("content")),
        }

    asset = {
        "url": item.get("targetAsset"),
        "status": item.get("assetStatus") or "planned",
        "contentAction": item.get("contentAction") or "create_new_article",
    }
    refs = reference_plan(item)
    images = image_plan_for(item)
    return {
        "content": _legacy_mock_markdown(item, asset, refs, images),
        "generated": False,
        "status": "ai-not-configured",
    }


def _legacy_article_prompt(
    item: dict[str, Any],
    project: dict[str, Any],
    brief: str,
    prompt: str,
) -> str:
    return "\n\n".join(
        [
            prompt or "Write an SEO article from the brief.",
            "Return Markdown only.",
            "Include: H1, intro, useful H2 sections, FAQ, meta title, meta description.",
            f"Primary keyword: {item.get('keyword') or ''}",
            f"Target site/role: {item.get('assignedSite') or item.get('assigned_site_label') or ''}",
            f"Project: {_json_dumps_compact(project)}",
            f"Brief: {brief}",
        ]
    )


def _legacy_mock_markdown(
    item: dict[str, Any],
    asset: dict[str, Any],
    refs: dict[str, Any],
    images: list[dict[str, str]],
) -> str:
    title = (item.get("keyword") or "untitled").title()
    return "\n".join(
        [
            f"## Title\n\n{title}\n",
            f"## Meta Title\n\n{title}: Practical Guide Before You Decide\n",
            f"## Meta Description\n\nLearn about {item.get('keyword')} with a clear decision path and SEO-ready structure.\n",
            f"## URL Slug\n\n{title.lower().replace(' ', '-')}\n",
            f"## Primary Keyword: {item.get('keyword')}\n",
            "## Secondary Keywords: (fill)\n",
            "## Last Updated\n\nToday\n",
            f"# {title}\n",
            "Outline by Brief template.\n",
            *(f"- {image['name']}: {image['position']}" for image in images),
            *(
                [f"## References\n\n- {source['name']}: [{source['label']}]({source['url']})" for source in refs.get("sources", [])]
                if refs.get("triggered")
                else []
            ),
        ]
    )


def _json_dumps_compact(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        return str(value)


async def generate_article_pipeline(
    session: AsyncSession,
    keyword_id: str | None,
    *,
    forced_site_id: str | None = None,
    approved_strategy: dict[str, Any] | None = None,
    keyword_context: dict[str, Any] | None = None,
    task_id: str | None = None,
    dry_run: bool = False,
    serp_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    generation_run_id = task_id or f"article-dry-run-{uuid4()}"

    def ok(key: str, label: str, message: str = "", data: dict[str, Any] | None = None) -> None:
        steps.append({"key": key, "label": label, "status": "done", "message": message, "data": data or {}})

    def fail(key: str, label: str, message: str) -> dict[str, Any]:
        steps.append({"key": key, "label": label, "status": "failed", "message": message, "data": {}})
        logger.warning(
            "article_generation_failed",
            generation_run_id=generation_run_id,
            stage=key,
            message=message,
            dry_run=dry_run,
        )
        return {"status": "failed", "steps": steps, "dryRun": dry_run}

    if not approved_strategy:
        return fail("strategy", "校验已审核策略", "正式生文必须来自已审核的今日计划")
    strategy_type = approved_strategy.get("strategy_type")
    if (
        not keyword_id
        and strategy_type != "update_article"
        and not (
            keyword_context
            and keyword_context.get("execution_evidence")
            and keyword_context.get("evidence_sources")
            and keyword_context.get("evidence_snapshot")
        )
    ):
        return fail("keyword", "校验关键词", "无关键词 ID 的新文章必须携带已验证的策略证据快照")

    keyword = await get_keyword(session, keyword_id) if keyword_id else dict(keyword_context or {})
    if not keyword or not keyword.get("keyword"):
        return fail("keyword", "读取关键词", "keyword not found")
    keyword.setdefault("id", keyword_id)
    strategy = approved_strategy
    logger.info(
        "article_generation_started",
        generation_run_id=generation_run_id,
        keyword=keyword.get("keyword"),
        keyword_id=keyword_id,
        requested_site_id=forced_site_id or keyword.get("assigned_site_id"),
        dry_run=dry_run,
    )
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
    if serp_override is not None:
        serp = dict(serp_override)
    elif dry_run and not keyword_id:
        serp = {"id": None, "source": "dry-run", "status": "not-requested", "organic_results": []}
    else:
        serp = await _latest_or_fetch_serp(session, keyword)
    ok(
        "serp",
        "获取 SERP",
        "已获取真实搜索结果"
        if is_usable_serp_result(serp)
        else serp.get("error_type") or serp.get("status") or "SerpApi 未配置，未使用搜索结果",
        {"source": serp.get("source"), "id": serp.get("id"), "organicCount": len(serp.get("organic_results") or [])},
    )
    logger.info(
        "article_generation_serp_ready",
        generation_run_id=generation_run_id,
        source=serp.get("source"),
        snapshot_id=str(serp.get("id") or ""),
        organic_count=len(serp.get("organic_results") or []),
    )

    generation_context = await build_generation_context(
        session,
        site_id=assigned_site_id,
        keyword=keyword,
        approved_strategy=approved_strategy,
        serp=serp,
    )
    hold_reasons = generation_context.get("hold_reasons") or []
    if hold_reasons:
        return fail("context", "校验文章素材卡", f"素材卡不完整，已转人工：{', '.join(hold_reasons)}")
    ok(
        "context",
        "冻结文章素材卡",
        f"{generation_context.get('site_role')} 站点素材卡已冻结",
        {"version": generation_context.get("context_version"), "hash": generation_context.get("context_hash")},
    )
    logger.info(
        "article_generation_context_ready",
        generation_run_id=generation_run_id,
        site_role=generation_context.get("site_role"),
        context_hash=str(generation_context.get("context_hash") or "")[:12],
        required_modules=generation_context.get("required_modules") or [],
        source_count=len(generation_context.get("facts_and_sources") or []),
    )

    await _set_task_stage(session, task_id, "brief")
    brief = await build_brief_with_optional_ai(
        {"keyword": item, "project": project, "aiStage": {"provider": "configured"}, "generationContext": generation_context}
    )
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
    logger.info(
        "article_generation_brief_ready",
        generation_run_id=generation_run_id,
        source=brief.get("briefSource") or "local",
        ai_enhanced=bool(brief.get("aiEnhanced")),
        characters=len(brief_text),
    )

    await _set_task_stage(session, task_id, "outline")
    outline = await _generate_outline(item, project, brief_text, serp, approved_strategy, generation_context)
    if outline.get("status"):
        return fail("outline", "生成大纲", outline["status"])
    ok("outline", "生成文章大纲", "大纲已生成", {"preview": (outline.get("content") or "")[:1200]})
    logger.info("article_generation_outline_ready", generation_run_id=generation_run_id, characters=len(outline.get("content") or ""))

    await _set_task_stage(session, task_id, "article")
    prompt = _compose_article_prompt(item, project, brief_text, serp, outline.get("content") or "", approved_strategy, generation_context)
    ai = await generate_ai_content(stage="article_generation", prompt=prompt, project=project, keyword=item)
    content, frontmatter = strip_markdown_frontmatter(ai.get("content") or "")
    if not content:
        return fail("article", "生成文章", ai.get("status") or "AI article generation failed")

    title = frontmatter.get("title") or _title(content, keyword["keyword"])
    meta_description = _normalize_meta_description(frontmatter.get("meta_description") or _meta_description(content))
    await _set_task_stage(session, task_id, "qa")
    internal_link_plan = (generation_context.get("link_policy") or {}).get("internal_links") or []
    qa = _qa(content, keyword["keyword"], meta_description, title, internal_link_plan, generation_context)
    failed_qa = [check["key"] for check in qa if not check["ok"]]
    logger.info(
        "article_generation_draft_ready",
        generation_run_id=generation_run_id,
        model=ai.get("model"),
        characters=len(content),
        failed_qa=failed_qa,
    )
    repair_attempts: list[dict[str, Any]] = []
    for attempt in range(1, 3):
        if not failed_qa:
            break
        repair = await generate_ai_content(
            stage="article_generation",
            prompt=_compose_repair_prompt(content, failed_qa, generation_context),
            project=project,
            keyword=item,
        )
        repaired_content, repaired_frontmatter = strip_markdown_frontmatter(repair.get("content") or "")
        if not repaired_content:
            repair_attempts.append({"attempt": attempt, "status": repair.get("status") or "empty_response", "failed_qa": failed_qa})
            logger.warning(
                "article_generation_repair_empty",
                generation_run_id=generation_run_id,
                attempt=attempt,
                failed_qa=failed_qa,
                status=repair.get("status"),
            )
            continue
        content = repaired_content
        frontmatter = repaired_frontmatter
        title = frontmatter.get("title") or _title(content, keyword["keyword"])
        meta_description = _normalize_meta_description(frontmatter.get("meta_description") or _meta_description(content))
        qa = _qa(content, keyword["keyword"], meta_description, title, internal_link_plan, generation_context)
        failed_qa = [check["key"] for check in qa if not check["ok"]]
        repair_attempts.append({"attempt": attempt, "status": "repaired" if not failed_qa else "still_failed", "failed_qa": failed_qa, "model": repair.get("model")})
        logger.info(
            "article_generation_repair_finished",
            generation_run_id=generation_run_id,
            attempt=attempt,
            model=repair.get("model"),
            failed_qa=failed_qa,
        )
        ai = repair
    ok("article", "生成文章", f"{title}（{len(content)} 字符）", {"model": ai.get("model"), "preview": content[:1600]})

    if dry_run:
        ok("save", "试生成未保存", "仅返回内容，不写文章、关键词、任务或效果观察数据")
        if failed_qa:
            return fail("qa", "发布前质量检查", f"QA 未通过：{', '.join(failed_qa)}") | {
                "article": None,
                "content": content,
                "contentPreview": content[:3000],
                "outline": outline.get("content") or "",
                "qa": qa,
                "model": ai.get("model"),
                "contentLength": len(content),
                "generationContext": generation_context,
            }
        ok("qa", "发布前质量检查", "全部通过")
        logger.info(
            "article_generation_finished",
            generation_run_id=generation_run_id,
            status="done",
            dry_run=True,
            characters=len(content),
            repair_attempts=len(repair_attempts),
        )
        return {
            "status": "done",
            "dryRun": True,
            "steps": steps,
            "article": None,
            "outline": outline.get("content") or "",
            "content": content,
            "contentPreview": content[:3000],
            "serp": serp,
            "qa": qa,
            "model": ai.get("model"),
            "contentLength": len(content),
            "generationContext": generation_context,
        }

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
            "target_url": (generation_context.get("target_asset") or {}).get("url") or keyword.get("target_asset_url"),
            "status": "failed" if failed_qa else "generated",
            "language_code": keyword.get("language_code"),
            "market": keyword.get("market"),
            "brief_md": brief_text,
            "prompt_text": prompt,
            "content_md": content,
            "article_parts": {
                "outline": outline.get("content") or "",
                "strategy": approved_strategy or {},
                "generation_context": generation_context,
                "generation_context_hash": generation_context.get("context_hash"),
                "repair_attempts": repair_attempts,
            },
            "meta_title": frontmatter.get("title") or title,
            "meta_description": meta_description,
            "primary_keyword": keyword["keyword"],
            "secondary_keywords": [],
            "internal_link_plan": internal_link_plan,
            "image_plan": image_plan_for(item),
            "references_plan": reference_plan(item),
            "qa_checklist": qa,
            "generation_provider": ai.get("provider") or "unknown",
            "generation_model": ai.get("model") or "unknown",
            "raw_ai_response": {
                "status": ai.get("status"),
                "serp": serp,
                "qa": qa,
                "repair_attempts": repair_attempts,
                "steps": steps,
                "generation_provenance": {
                    "provider": ai.get("provider") or "unknown",
                    "provider_source": ai.get("providerSource") or "unknown",
                    "requested_model": ai.get("requestedModel") or ai.get("model") or "unknown",
                    "actual_model": ai.get("model") or "unknown",
                    "model_source": ai.get("modelSource") or "unknown",
                    "api_format": ai.get("apiFormat") or "unknown",
                },
            },
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
    logger.info(
        "article_generation_finished",
        generation_run_id=generation_run_id,
        status="done",
        dry_run=False,
        article_id=str(saved.get("id") or ""),
        characters=len(content),
        repair_attempts=len(repair_attempts),
    )
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
                SELECT id, organic_results, related_questions, related_searches, requested_at, raw
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
        cached = {
            **dict(row),
            **(dict(row.get("raw") or {}) if isinstance(row.get("raw"), dict) else {}),
            "id": str(row["id"]),
            "requested_at": str(row["requested_at"]),
            "source": "cache",
        }
        if is_usable_serp_result(cached):
            return cached

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
    generation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = approved_strategy or item.get("strategy") or ((item.get("ai_review") or {}).get("strategy") if isinstance(item.get("ai_review"), dict) else {})
    prompt = "\n\n".join(
        [
            "Create an SEO article outline only. Do not write the article.",
            "Use Markdown headings and notes on what each section must answer. Include only the modules required by the frozen generation context; do not force FAQ or CTA.",
            f"Primary keyword: {item.get('keyword')}",
            "Approved strategy, site knowledge, competitor gap, recommended action and internal links. Treat these as mandatory constraints:",
            json.dumps(strategy, ensure_ascii=False, default=str, indent=2),
            "SERP context:",
            _serp_context(serp),
            "Frozen generation context. It is the source of truth for site role, target asset, facts, sources, modules and forbidden claims:",
            context_prompt_block(generation_context or {}),
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
    generation_context: dict[str, Any] | None = None,
) -> str:
    strategy = approved_strategy or item.get("strategy") or ((item.get("ai_review") or {}).get("strategy") if isinstance(item.get("ai_review"), dict) else {})
    return "\n\n".join(
        [
            "Write a production-ready SEO article in Markdown. No placeholders. No fabricated rankings or URLs.",
            "Output reader-facing article content only; do not include editorial notes, publication instructions, review plans, or parent-page availability notes.",
            "Start with YAML frontmatter using the exact keys title and meta_description. Keep meta_description between 120 and 160 characters. Then write the article body beginning with one H1.",
            f"Current date: {date.today().isoformat()}. Use the current year for time-sensitive titles and metadata.",
            "Follow the brief and use SERP evidence only as competitive context.",
            "Required: H1, meta title, meta description and useful H2 sections. FAQ, CTA, tables, images and references are required only when the frozen generation context requires them.",
            f"Primary keyword: {item.get('keyword')}",
            "Use the exact primary keyword phrase at least once in the H1 or opening paragraph; do not replace it with a synonym.",
            f"Semrush signals: intent={item.get('intent') or ''}; trend={json.dumps(item.get('trendData') or item.get('trend_data') or [], ensure_ascii=False)}; SERP features={json.dumps(item.get('serpFeatures') or item.get('serp_features') or [], ensure_ascii=False)}. Use these only to shape page type and sections; do not present them as page facts.",
            f"Target site/role: {item.get('assignedSite') or ''}",
            f"Project: {json.dumps(project, ensure_ascii=False)}",
            "Approved strategy, site knowledge, competitor gap, recommended action and internal links. Implement them; never invent missing evidence or URLs:",
            json.dumps(strategy, ensure_ascii=False, default=str, indent=2),
            "Frozen generation context. This is the only source of truth for the site role, positioning, target asset, linking policy, allowed sources, required modules and forbidden claims. If it lacks evidence for a claim, omit the claim rather than guessing:",
            context_prompt_block(generation_context or {}),
            "If you discuss a fact matched by risk_claim_terms in the frozen context, cite the matching allowed-source URL in the reader-facing article. Otherwise omit that claim entirely.",
            "SERP context:",
            _serp_context(serp),
            "Approved outline:",
            outline,
            "Brief:",
            brief,
        ]
    )


def _compose_repair_prompt(content: str, failed_qa: list[str], generation_context: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            "Repair the following Markdown article. Return the complete reader-facing article only, using the same YAML frontmatter protocol (title and meta_description) and one H1.",
            "Do not add editorial notes or publication instructions. Preserve accurate existing material, but fix only the listed quality failures.",
            f"Quality failures: {', '.join(failed_qa)}",
            "Frozen generation context. Do not invent facts, URLs, products, health, legal or safety claims beyond it:",
            context_prompt_block(generation_context),
            "For any remaining statement matched by risk_claim_terms, either add its matching allowed-source URL from the frozen context or remove the statement.",
            "Article to repair:",
            content,
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
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")[:70]
    return slug or f"article-{hashlib.sha1(value.encode('utf-8')).hexdigest()[:10]}"


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
    generation_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    h1 = re.findall(r"^#\s+(.+)$", content, re.M)
    title_years = re.findall(r"\b20\d{2}\b", " ".join([title, *h1]))
    planned_urls = [
        str(link.get("url") or link.get("target_url") or "")
        for link in internal_link_plan or []
        if link.get("url") or link.get("target_url")
    ]
    required_context_modules = set((generation_context or {}).get("required_modules") or [])
    faq_required = not generation_context or "faq" in required_context_modules
    metadata_policy = (generation_context or {}).get("metadata_policy") or {"min": 120, "max": 160}
    meta_min = int(metadata_policy.get("min", 120))
    meta_max = int(metadata_policy.get("max", 160))
    time_sensitive = not generation_context or bool((generation_context.get("article_goal") or {}).get("time_sensitive"))
    site_profile = (generation_context or {}).get("site_profile") or {}
    checks = {
        "has_h1": len(h1) == 1,
        "has_h2": bool(re.search(r"^##\s+", content, re.M)),
        "has_faq": not faq_required or _has_faq_heading(content, site_profile.get("faq_heading_patterns") or []),
        "has_keyword": _contains_keyword(" ".join((content, title)), keyword),
        "has_meta_description": meta_min <= len(meta_description.strip()) <= meta_max,
        "min_length_1200": bool(generation_context) or len(content) >= 1200,
        "title_year_is_current": not time_sensitive or not title_years or all(year == str(date.today().year) for year in title_years),
        "has_planned_internal_link": not planned_urls or any(url in content for url in planned_urls),
    }
    if generation_context:
        required_modules = required_context_modules
        target_asset = generation_context.get("target_asset") or {}
        site_profile = generation_context.get("site_profile") or {}
        user_questions = (generation_context.get("keyword_and_intent") or {}).get("user_questions") or []
        must_answer = (generation_context.get("article_goal") or {}).get("must_answer") or []
        allowed_sources = [str(item.get("url") or "") for item in generation_context.get("facts_and_sources") or [] if isinstance(item, dict)]
        checks.update(
            {
                "has_required_faq": "faq" not in required_modules or checks["has_faq"],
                "has_required_comparison_table": "comparison_table" not in required_modules or bool(re.search(r"^\s*\|.+\|\s*$", content, re.M)),
                "has_required_contextual_link": "contextual_internal_link" not in required_modules
                or bool(target_asset.get("url") and str(target_asset["url"]) in content)
                or any(url in content for url in planned_urls),
                "answers_context_questions": _answers_context_questions(content, [*user_questions, *must_answer]),
                "respects_topic_boundary": _respects_topic_boundary(content, site_profile.get("out_of_scope_topics") or []),
                "avoids_generic_opening": not _has_generic_opening(content, site_profile.get("generic_opening_patterns") or []),
                "high_risk_claims_have_sources": not generation_context.get("requires_claim_sources") or _high_risk_claims_have_sources(content, allowed_sources, generation_context.get("risk_claim_terms") or []),
            }
        )
    return [{"key": k, "ok": v} for k, v in checks.items()]


def _contains_keyword(text: str, keyword: str) -> bool:
    tokens = re.findall(r"\w+", keyword.casefold(), re.UNICODE)
    if not tokens:
        return False
    # Keep token order exact while allowing languages such as German to join
    # adjacent search terms into a grammatically correct compound word.
    pattern = r"(?<!\w)" + r"[\W_]*".join(re.escape(token) for token in tokens) + r"(?!\w)"
    return bool(re.search(pattern, text.casefold(), re.UNICODE))


def _answers_context_questions(content: str, questions: list[str]) -> bool:
    if not questions:
        return True
    words = _question_terms(content)
    for question in questions:
        terms = _question_terms(str(question))
        if terms and len(words & terms) / len(terms) < 0.5:
            return False
    return True


def _respects_topic_boundary(content: str, boundaries: list[str]) -> bool:
    lowered = content.casefold()
    return not any(boundary.casefold() in lowered for boundary in boundaries if str(boundary).strip())


def _has_generic_opening(content: str, configured_patterns: list[str] | None = None) -> bool:
    opening = re.sub(r"^#.*$", "", content, count=1, flags=re.M).strip().casefold()[:700]
    boilerplate = (
        "in today's fast-paced world",
        "whether you're a seasoned",
        "look no further",
        "in this comprehensive guide",
        "in the ever-evolving world",
    )
    return any(phrase.casefold() in opening for phrase in (*boilerplate, *(configured_patterns or [])))


def _high_risk_claims_have_sources(content: str, allowed_sources: list[str], configured_terms: list[str] | None = None) -> bool:
    claim_terms = configured_terms or ["health", "medical", "safe", "safety", "legal", "law", "underage", "battery", "dispose", "recycle", "bacteria", "mold"]
    lowered = content.casefold()
    if not any(str(term).casefold() in lowered for term in claim_terms if str(term).strip()):
        return True
    return bool(allowed_sources) and any(url in content for url in allowed_sources)


def _question_terms(value: str) -> set[str]:
    lowered = value.casefold()
    terms = set(re.findall(r"[a-z0-9]{4,}", lowered))
    for sequence in re.findall(r"[\u3400-\u9fff]{2,}", lowered):
        terms.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return terms


def _has_faq_heading(content: str, configured_patterns: list[str]) -> bool:
    return has_faq_signal(content, configured_patterns)
