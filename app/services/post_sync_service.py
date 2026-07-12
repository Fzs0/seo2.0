"""从站点 API 拉已发布文章，写入 seo_agent.posts。"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.http_client import ExternalCallError, request_json


async def sync_site_posts(session: AsyncSession, *, site_id: str, limit: int = 100) -> dict[str, Any]:
    site = await _load_site(session, site_id)
    if not site:
        return {"ok": False, "error": "site not found", "fetched": 0, "saved": 0}
    posts = await fetch_site_posts(site, limit=limit)
    saved = 0
    for post in posts:
        await _upsert_post(session, site, post)
        saved += 1
    await session.commit()
    return {"ok": True, "site_id": site_id, "fetched": len(posts), "saved": saved}


async def sync_all_site_posts(session: AsyncSession, *, limit_per_site: int = 100) -> dict[str, Any]:
    rows = await session.execute(
        text("SELECT id FROM seo_agent.sites WHERE status = 'active' ORDER BY is_main DESC, name ASC")
    )
    results = []
    for row in rows.mappings().all():
        try:
            results.append(await sync_site_posts(session, site_id=str(row["id"]), limit=limit_per_site))
        except ExternalCallError as e:
            results.append({"ok": False, "site_id": str(row["id"]), "error": str(e), "fetched": 0, "saved": 0})
    return {
        "ok": all(r.get("ok") for r in results),
        "results": results,
        "saved": sum(int(r.get("saved") or 0) for r in results),
    }


async def list_posts(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    where = "WHERE 1=1"
    params: dict[str, Any] = {"limit": max(1, min(limit, 200)), "offset": max(0, offset)}
    if site_id:
        where += " AND site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    total = (await session.execute(text(f"SELECT count(*) FROM seo_agent.posts {where}"), params)).scalar_one()
    rows = await session.execute(
        text(
            f"""
            SELECT id, site_id, external_id, title, slug, url, status, author, category_id,
                   language_code, market, primary_keyword, topic_cluster, page_type,
                   excerpt, meta_title, meta_description, cover_url, published_at,
                   modified_at, fetched_at, source, created_at, updated_at
              FROM seo_agent.posts
              {where}
             ORDER BY published_at DESC NULLS LAST, fetched_at DESC
             LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {"items": [dict(r) for r in rows.mappings().all()], "total": int(total or 0), "limit": params["limit"], "offset": params["offset"]}


async def fetch_site_posts(site: dict[str, Any], *, limit: int = 100) -> list[dict[str, Any]]:
    if (site.get("site_type") or "").lower() == "wp":
        return await _fetch_wp_posts(site, limit=limit)
    return await _fetch_openapi_posts(site, limit=limit)


async def _load_site(session: AsyncSession, site_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, site_key, name, site_type, domain, base_url, api_base_url, market, language_code, api_config "
                "FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
            ),
            {"id": site_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _fetch_wp_posts(site: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    base = (site.get("base_url") or site.get("domain") or "").rstrip("/")
    if base and not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    cfg = site.get("api_config") or {}
    headers = {}
    if cfg.get("username") and cfg.get("applicationPassword"):
        token = base64.b64encode(f"{cfg['username']}:{cfg['applicationPassword']}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    data = await request_json(
        "GET",
        f"{base}/wp-json/wp/v2/posts",
        client_label="site_posts_wp",
        params={"per_page": min(limit, 100), "page": 1, "status": "publish,draft", "_embed": 1},
        headers=headers,
        timeout=60,
    )
    items = data if isinstance(data, list) else data.get("items") or data.get("posts") or []
    return [_normalize_wp(p) for p in items[:limit] if isinstance(p, dict)]


async def _fetch_openapi_posts(site: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    base = (site.get("api_base_url") or site.get("base_url") or "").rstrip("/")
    cfg = site.get("api_config") or {}
    headers = _openapi_headers(cfg)
    if not base or not headers:
        raise ExternalCallError("site post sync missing api_base_url or auth")
    data = await request_json(
        "GET",
        f"{base}/articles",
        client_label="site_posts_openapi",
        params={"limit": min(limit, 100)},
        headers=headers,
        timeout=60,
    )
    items = data if isinstance(data, list) else data.get("items") or data.get("articles") or data.get("data") or []
    return [_normalize_openapi(p) for p in items[:limit] if isinstance(p, dict)]


def _openapi_headers(cfg: dict[str, Any]) -> dict[str, str]:
    if cfg.get("openApiKey"):
        return {"openApiKey": cfg["openApiKey"]}
    if cfg.get("tokenB"):
        return {"token": cfg["tokenB"]}
    if cfg.get("tokenA"):
        return {"token": cfg["tokenA"]}
    return {}


def _normalize_wp(p: dict[str, Any]) -> dict[str, Any]:
    url = p.get("link")
    return {
        "external_id": str(p.get("id") or url or ""),
        "title": _rendered(p.get("title")) or "untitled",
        "slug": p.get("slug"),
        "url": url,
        "status": p.get("status"),
        "content_html": _rendered(p.get("content")),
        "excerpt": _rendered(p.get("excerpt")),
        "published_at": p.get("date_gmt") or p.get("date"),
        "modified_at": p.get("modified_gmt") or p.get("modified"),
        "source": "wp_api",
        "raw": p,
    }


def _normalize_openapi(p: dict[str, Any]) -> dict[str, Any]:
    url = p.get("url") or p.get("link")
    return {
        "external_id": str(p.get("id") or p.get("articleId") or p.get("external_id") or url or ""),
        "title": p.get("title") or "untitled",
        "slug": p.get("slug") or p.get("handle"),
        "url": url,
        "status": p.get("status"),
        "content_md": p.get("content_md") or p.get("contentMd"),
        "content_html": p.get("content_html") or p.get("contentHtml") or p.get("content"),
        "excerpt": p.get("excerpt") or p.get("description"),
        "meta_title": p.get("meta_title") or p.get("metaTitle"),
        "meta_description": p.get("meta_description") or p.get("metaDescription"),
        "published_at": p.get("published_at") or p.get("publishedAt"),
        "modified_at": p.get("modified_at") or p.get("updatedAt") or p.get("updated_at"),
        "source": "openapi",
        "raw": p,
    }


async def _upsert_post(session: AsyncSession, site: dict[str, Any], post: dict[str, Any]) -> None:
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.posts
              (site_id, external_id, title, slug, url, status, language_code, market,
               content_format, content_md, content_html, excerpt, meta_title, meta_description,
               published_at, modified_at, fetched_at, source, raw)
            VALUES
              (CAST(:site_id AS uuid), :external_id, :title, :slug, :url, :status, :language_code, :market,
               :content_format, :content_md, :content_html, :excerpt, :meta_title, :meta_description,
               :published_at, :modified_at, now(), :source, CAST(:raw AS jsonb))
            ON CONFLICT (site_id, external_id) WHERE external_id IS NOT NULL AND external_id <> ''
            DO UPDATE SET
              title = EXCLUDED.title,
              slug = EXCLUDED.slug,
              url = EXCLUDED.url,
              status = EXCLUDED.status,
              content_md = EXCLUDED.content_md,
              content_html = EXCLUDED.content_html,
              excerpt = EXCLUDED.excerpt,
              meta_title = EXCLUDED.meta_title,
              meta_description = EXCLUDED.meta_description,
              published_at = EXCLUDED.published_at,
              modified_at = EXCLUDED.modified_at,
              fetched_at = now(),
              raw = EXCLUDED.raw,
              updated_at = now()
            """
        ),
        {
            "site_id": str(site["id"]),
            "external_id": post.get("external_id") or None,
            "title": post.get("title") or "untitled",
            "slug": post.get("slug"),
            "url": post.get("url"),
            "status": post.get("status"),
            "language_code": site.get("language_code"),
            "market": site.get("market"),
            "content_format": "mixed" if post.get("content_md") and post.get("content_html") else ("markdown" if post.get("content_md") else "html" if post.get("content_html") else "unknown"),
            "content_md": post.get("content_md"),
            "content_html": post.get("content_html"),
            "excerpt": post.get("excerpt"),
            "meta_title": post.get("meta_title"),
            "meta_description": post.get("meta_description"),
            "published_at": _dt(post.get("published_at")),
            "modified_at": _dt(post.get("modified_at")),
            "source": post.get("source") or "api",
            "raw": json.dumps(post.get("raw") or post, ensure_ascii=False, default=str),
        },
    )


def _rendered(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("rendered")
    return value if isinstance(value, str) else None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
