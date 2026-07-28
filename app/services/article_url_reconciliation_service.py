"""Idempotently coordinate a confirmed public article URL across persistence records."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.article_urls import resolve_article_public_url


class PublicArticleUrlError(ValueError):
    code = "unconfirmed_public_article_url"


async def reconcile_article_public_url(
    session: AsyncSession,
    *,
    article_id: str,
    remote_url: str | None = None,
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT a.id::text AS article_id, a.slug, a.published_post_id,
                       a.published_url, a.target_url,
                       s.id::text AS site_id, s.site_type, s.domain, s.base_url,
                       s.api_base_url, COALESCE(s.api_config, '{}'::jsonb) AS api_config
                  FROM seo_agent.articles a
                  JOIN seo_agent.sites s ON s.id = a.site_id
                 WHERE a.id = CAST(:article_id AS uuid)
                 FOR UPDATE OF a
                """
            ),
            {"article_id": article_id},
        )
    ).mappings().first()
    if not row:
        raise PublicArticleUrlError("article_not_found")
    item = dict(row)
    site = {
        key: item.get(key)
        for key in ("site_id", "site_type", "domain", "base_url", "api_base_url", "api_config")
    }
    canonical = resolve_article_public_url(
        site,
        slug=item.get("slug"),
        article_id=item.get("published_post_id"),
        remote_url=remote_url or item.get("published_url"),
    )
    allowed_host = urlsplit(
        str(site.get("base_url") or site.get("domain") or "")
        if "://" in str(site.get("base_url") or site.get("domain") or "")
        else f"https://{site.get('base_url') or site.get('domain') or ''}"
    ).hostname
    if not canonical or not allowed_host or (urlsplit(canonical).hostname or "").casefold() != allowed_host.casefold():
        raise PublicArticleUrlError("public_url_host_not_allowed")

    old_url = str(item.get("published_url") or "")
    params = {"article_id": article_id, "url": canonical, "old_url": old_url}
    article_result = await session.execute(
        text(
            """
            UPDATE seo_agent.articles
               SET published_url=:url,
                   target_url=CASE
                     WHEN target_url IS NULL OR target_url='' OR target_url=:old_url THEN :url
                     ELSE target_url
                   END,
                   updated_at=now()
             WHERE id=CAST(:article_id AS uuid)
               AND (published_url IS DISTINCT FROM :url
                    OR ((target_url IS NULL OR target_url='' OR target_url=:old_url)
                        AND target_url IS DISTINCT FROM :url))
            """
        ),
        params,
    )
    task_result = await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET target_url=:url,
                   payload=CASE
                     WHEN task_type='review' AND payload->>'kind'='strategy_effect'
                     THEN jsonb_set(COALESCE(payload, '{}'::jsonb), '{target_url}', to_jsonb(CAST(:url AS text)), true)
                     ELSE payload
                   END,
                   decision=jsonb_set(
                     COALESCE(decision, '{}'::jsonb),
                     '{url_reconciliation}',
                     jsonb_strip_nulls(jsonb_build_object(
                       'original_target_url', NULLIF(target_url, ''),
                       'canonical_public_url', :url
                     )),
                     true
                   ),
                   updated_at=now()
             WHERE article_id=CAST(:article_id AS uuid)
               AND status <> 'canceled'
               AND (
                 (task_type='publish' AND status='done')
                 OR (task_type IN ('new_article', 'update_article') AND status='done')
                 OR (task_type='review' AND payload->>'kind'='strategy_effect')
               )
               AND target_url IS DISTINCT FROM :url
            """
        ),
        params,
    )
    return {
        "article_id": article_id,
        "canonical_public_url": canonical,
        "article_changes": article_result.rowcount or 0,
        "task_changes": task_result.rowcount or 0,
    }


async def reconcile_site_article_urls(session: AsyncSession, *, site_id: str) -> dict[str, int]:
    rows = await session.execute(
        text(
            "SELECT id::text AS id FROM seo_agent.articles "
            "WHERE site_id=CAST(:site_id AS uuid) AND published_post_id IS NOT NULL"
        ),
        {"site_id": site_id},
    )
    scanned = changed = failed = 0
    for row in rows.mappings().all():
        scanned += 1
        try:
            result = await reconcile_article_public_url(session, article_id=str(row["id"]))
            changed += int(bool(result["article_changes"] or result["task_changes"]))
        except PublicArticleUrlError:
            failed += 1
    return {"scanned": scanned, "changed": changed, "failed": failed}


__all__ = [
    "PublicArticleUrlError",
    "reconcile_article_public_url",
    "reconcile_site_article_urls",
]
