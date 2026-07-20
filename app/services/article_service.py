"""文章服务：保存 / 列出 / 按 keyword 取。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def save_article(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """插入或更新一篇文章；按 task_id 或 (site_id, external_id) 判重。"""
    task_id = payload.get("task_id") or payload.get("taskId")
    site_id = payload.get("site_id") or payload.get("siteId")
    external_id = payload.get("external_id") or payload.get("externalId")
    keyword_id = payload.get("keyword_id") or payload.get("keywordId")
    serp_snapshot_id = payload.get("serp_snapshot_id") or payload.get("serpSnapshotId")

    sql = text(
        """
        INSERT INTO seo_agent.articles
          (task_id, site_id, keyword_id, serp_snapshot_id, title, slug, target_url,
           status, language_code, market, brief_md, prompt_text, content_md, content_html,
           article_parts, meta_title, meta_description, primary_keyword, secondary_keywords,
           internal_link_plan, image_plan, references_plan, qa_checklist,
           generation_provider, generation_model, raw_ai_response)
        VALUES
          (CAST(:task_id AS uuid), CAST(:site_id AS uuid), CAST(:keyword_id AS uuid),
           CAST(:serp_snapshot_id AS uuid),
           :title, :slug, :target_url,
           :status, :language_code, :market,
           :brief_md, :prompt_text, :content_md, :content_html,
           CAST(:article_parts AS jsonb), :meta_title, :meta_description,
           :primary_keyword, CAST(:secondary_keywords AS text[]),
           CAST(:internal_link_plan AS jsonb), CAST(:image_plan AS jsonb),
           CAST(:references_plan AS jsonb), CAST(:qa_checklist AS jsonb),
           :generation_provider, :generation_model, CAST(:raw_ai_response AS jsonb))
        ON CONFLICT (task_id) WHERE task_id IS NOT NULL
        DO UPDATE SET
          title = EXCLUDED.title,
          slug = EXCLUDED.slug,
          serp_snapshot_id = EXCLUDED.serp_snapshot_id,
          brief_md = EXCLUDED.brief_md,
          prompt_text = EXCLUDED.prompt_text,
          content_md = EXCLUDED.content_md,
          article_parts = EXCLUDED.article_parts,
          meta_title = EXCLUDED.meta_title,
          meta_description = EXCLUDED.meta_description,
          internal_link_plan = EXCLUDED.internal_link_plan,
          image_plan = EXCLUDED.image_plan,
          references_plan = EXCLUDED.references_plan,
          qa_checklist = EXCLUDED.qa_checklist,
          generation_provider = EXCLUDED.generation_provider,
          generation_model = EXCLUDED.generation_model,
          raw_ai_response = EXCLUDED.raw_ai_response,
          status = EXCLUDED.status,
          updated_at = now()
        RETURNING id, site_id, title, slug, status, language_code, market,
                  meta_title, meta_description, primary_keyword, created_at
        """
    )
    # 注意：v1 articles 表在 task_id 上有 FK，但 ON CONFLICT 用 task_id 风险大
    # 这里只支持纯 INSERT（task_id 留空时走默认 ON CONFLICT DO NOTHING）
    # 为了安全：当 task_id 缺失时改用 INSERT ... ON CONFLICT DO NOTHING
    sql_no_task = text(
        """
        INSERT INTO seo_agent.articles
          (task_id, site_id, keyword_id, serp_snapshot_id, title, slug, target_url,
           status, language_code, market, brief_md, prompt_text, content_md, content_html,
           article_parts, meta_title, meta_description, primary_keyword, secondary_keywords,
           internal_link_plan, image_plan, references_plan, qa_checklist,
           generation_provider, generation_model, raw_ai_response)
        VALUES
          (CAST(:task_id AS uuid), CAST(:site_id AS uuid), CAST(:keyword_id AS uuid),
           CAST(:serp_snapshot_id AS uuid),
           :title, :slug, :target_url,
           :status, :language_code, :market,
           :brief_md, :prompt_text, :content_md, :content_html,
           CAST(:article_parts AS jsonb), :meta_title, :meta_description,
           :primary_keyword, CAST(:secondary_keywords AS text[]),
           CAST(:internal_link_plan AS jsonb), CAST(:image_plan AS jsonb),
           CAST(:references_plan AS jsonb), CAST(:qa_checklist AS jsonb),
           :generation_provider, :generation_model, CAST(:raw_ai_response AS jsonb))
        ON CONFLICT DO NOTHING
        RETURNING id, site_id, title, slug, status, language_code, market,
                  meta_title, meta_description, primary_keyword, created_at
        """
    )
    params = {
        "task_id": task_id,
        "site_id": site_id,
        "keyword_id": keyword_id,
        "serp_snapshot_id": serp_snapshot_id,
        "title": payload.get("title") or "untitled",
        "slug": payload.get("slug"),
        "target_url": payload.get("target_url") or payload.get("targetUrl"),
        "status": payload.get("status") or "draft",
        "language_code": payload.get("language_code") or payload.get("languageCode"),
        "market": payload.get("market"),
        "brief_md": payload.get("brief_md") or payload.get("briefMd"),
        "prompt_text": payload.get("prompt_text") or payload.get("promptText"),
        "content_md": payload.get("content_md") or payload.get("contentMd"),
        "content_html": payload.get("content_html") or payload.get("contentHtml"),
        "article_parts": _to_json(payload.get("article_parts") or payload.get("articleParts") or {}),
        "meta_title": payload.get("meta_title") or payload.get("metaTitle"),
        "meta_description": payload.get("meta_description") or payload.get("metaDescription"),
        "primary_keyword": payload.get("primary_keyword") or payload.get("primaryKeyword"),
        "secondary_keywords": payload.get("secondary_keywords") or payload.get("secondaryKeywords") or [],
        "internal_link_plan": _to_json(payload.get("internal_link_plan") or payload.get("internalLinkPlan") or []),
        "image_plan": _to_json(payload.get("image_plan") or payload.get("imagePlan") or []),
        "references_plan": _to_json(payload.get("references_plan") or payload.get("referencesPlan") or []),
        "qa_checklist": _to_json(payload.get("qa_checklist") or payload.get("qaChecklist") or []),
        "generation_provider": payload.get("generation_provider") or payload.get("generationProvider"),
        "generation_model": payload.get("generation_model") or payload.get("generationModel"),
        "raw_ai_response": _to_json(payload.get("raw_ai_response") or payload.get("rawAiResponse") or {}),
    }
    sql_to_use = sql_no_task if not task_id else sql
    result = await session.execute(sql_to_use, params)
    await session.commit()
    row = result.mappings().first()
    return dict(row) if row else {"saved": True, "duplicate": True}


async def list_articles(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    keyword_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    where = "WHERE 1=1"
    params: dict[str, Any] = {"limit": max(1, min(limit, 200)), "offset": max(0, offset)}
    if site_id:
        where += " AND a.site_id = :site_id"
        params["site_id"] = site_id
    if keyword_id:
        where += " AND a.keyword_id = :keyword_id"
        params["keyword_id"] = keyword_id
    if status:
        where += " AND a.status = :status"
        params["status"] = status
    total = (
        await session.execute(
            text(f"SELECT count(*) AS n FROM seo_agent.articles a {where}"),
            params,
        )
    ).scalar_one()
    result = await session.execute(
        text(
            f"""
            SELECT a.id, a.task_id, a.site_id, s.name AS site_label,
                   a.keyword_id, k.keyword, a.title, a.slug, a.target_url, a.status,
                   a.language_code, a.market, a.meta_title, a.meta_description,
                   a.primary_keyword, a.generation_provider, a.generation_model,
                   a.created_at, a.updated_at, a.published_at, a.published_url
              FROM seo_agent.articles a
              LEFT JOIN seo_agent.sites s ON s.id = a.site_id
              LEFT JOIN seo_agent.keywords k ON k.id = a.keyword_id
              {where}
             ORDER BY a.created_at DESC
             LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {
        "items": [dict(r) for r in result.mappings().all()],
        "total": int(total or 0),
        "limit": params["limit"],
        "offset": params["offset"],
    }


async def get_article(session: AsyncSession, article_id: str) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, task_id, site_id, keyword_id, serp_snapshot_id, title, slug, target_url, "
            "status, language_code, market, brief_md, prompt_text, content_md, content_html, "
            "article_parts, meta_title, meta_description, primary_keyword, secondary_keywords, "
            "internal_link_plan, image_plan, references_plan, qa_checklist, "
            "generation_provider, generation_model, published_url, published_at, "
            "created_at, updated_at "
            "FROM seo_agent.articles WHERE id = :id"
        ),
        {"id": article_id},
    )
    row = result.mappings().first()
    if not row:
        return None
    d = dict(row)
    # jsonb 字段直接转 Python 对象
    for k in ("article_parts", "internal_link_plan", "image_plan", "references_plan", "qa_checklist"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except (ValueError, TypeError):
                pass
    return d


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
