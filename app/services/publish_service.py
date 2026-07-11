"""Publish service: ?? article + site + publisher?

???
1. ?? article by id????? + ????
2. ?? site by id???????
3. ? publisher.publish(dry_run or not)
4. ? tasks ??type=publish, status=done/failed?
5. ????? articles ??????????
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.publishers import PublishRequest, PublishResult, publisher_for_site


class PublishError(Exception):
    pass


async def publish_article(
    session: AsyncSession,
    *,
    article_id: str,
    site_id: str,
    dry_run: bool = True,
    actor: str = "publish_api",
) -> dict[str, Any]:
    """????????????"""
    a = (
        await session.execute(
            text(
                "SELECT id, title, slug, target_url, status, content_md, meta_title, meta_description, "
                "primary_keyword, language_code, market, published_post_id, published_url, published_at "
                "FROM seo_agent.articles WHERE id = CAST(:id AS uuid)"
            ),
            {"id": article_id},
        )
    ).mappings().first()
    if not a:
        raise PublishError(f"article id={article_id} not found")
    if not a["content_md"]:
        raise PublishError(f"article id={article_id} has empty content_md")

    s = (
        await session.execute(
            text(
                "SELECT id, site_key, name, site_type, domain, base_url, api_base_url, status, api_config, content_role "
                "FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
            ),
            {"id": site_id},
        )
    ).mappings().first()
    if not s:
        raise PublishError(f"site id={site_id} not found")
    if s["status"] != "active":
        raise PublishError(f"site id={site_id} is not active (status={s['status']})")
    if not dry_run and not s["api_config"]:
        raise PublishError(f"site id={site_id} has empty api_config")

    req = PublishRequest(
        title=a["title"] or a["slug"] or "untitled",
        slug=a["slug"] or f"article-{a['id']}",
        content_md=a["content_md"],
        meta_title=a["meta_title"] or a["title"] or "",
        meta_description=a["meta_description"] or "",
        status="draft" if dry_run else "publish",
        excerpt=a["meta_description"] or "",
        author=None,
        category_id=None,
    )

    publisher = publisher_for_site(dict(s), dry_run=dry_run)
    result: PublishResult = await publisher.publish(req)

    task_row = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, site_id, keyword_id, article_id, target_url, title,
                   payload, decision, error_message, run_after, started_at, finished_at)
                VALUES
                  ('publish',
                   :status,
                   'P1',
                   CAST(:site_id AS uuid),
                   (SELECT keyword_id FROM seo_agent.articles WHERE id = CAST(:article_id AS uuid)),
                   CAST(:article_id AS uuid),
                   :target_url,
                   :title,
                   CAST(:payload AS jsonb),
                   CAST(:decision AS jsonb),
                   :error_message,
                   now(), now(), now())
                RETURNING id
                """
            ),
            {
                "status": "done" if result.ok else "failed",
                "site_id": site_id,
                "article_id": article_id,
                "target_url": result.url or a["target_url"] or "",
                "title": f"publish article {article_id} -> {s['site_key']}",
                "payload": json.dumps({"dry_run": dry_run, "actor": actor}, ensure_ascii=False),
                "decision": json.dumps(
                    {"adapter": publisher.__class__.__name__, "ok": result.ok, "dry_run": dry_run},
                    ensure_ascii=False,
                ),
                "error_message": result.error,
            },
        )
    ).mappings().first()
    task_id = task_row["id"] if task_row else None

    if result.ok and not result.dry_run:
        await session.execute(
            text(
                """
                UPDATE seo_agent.articles
                   SET site_id = CAST(:site_id AS uuid),
                       published_post_id = :post_id,
                       published_url = :url,
                       published_at = :ts,
                       status = :status,
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "site_id": site_id,
                "post_id": result.post_id or "",
                "url": result.url or "",
                "ts": datetime.utcnow(),
                "status": "published",
                "id": article_id,
            },
        )
    elif result.ok and result.dry_run:
        await session.execute(
            text(
                "UPDATE seo_agent.articles "
                "SET site_id = CAST(:site_id AS uuid), status = 'approved', updated_at = now() "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"site_id": site_id, "id": article_id},
        )

    await session.commit()

    return {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "post_id": result.post_id,
        "url": result.url,
        "error": result.error,
        "raw": result.raw,
        "task_id": task_id,
        "article_id": article_id,
        "site_id": site_id,
    }


async def get_publish_task(session: AsyncSession, task_id: str) -> dict[str, Any] | None:
    """? publish ?????"""
    row = (
        await session.execute(
            text(
                "SELECT id, task_type, status, priority, article_id, target_url, title, "
                "       decision, error_message, started_at, finished_at, created_at "
                "FROM seo_agent.tasks WHERE id = CAST(:id AS uuid) AND task_type = 'publish'"
            ),
            {"id": task_id},
        )
    ).mappings().first()
    return dict(row) if row else None


__all__ = ["PublishError", "publish_article", "get_publish_task"]
