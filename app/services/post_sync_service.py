"""从站点 API 拉已发布文章，写入 seo_agent.posts。"""
from __future__ import annotations

import json
from html.parser import HTMLParser
from datetime import datetime
from typing import Any
from urllib.parse import quote, urljoin

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.http_client import ExternalCallError, request_text
from app.clients.publishers import PublisherBase
from app.core.article_urls import resolve_article_public_url
from app.services.article_url_reconciliation_service import reconcile_article_public_url
from app.services.shopify_connection_service import publisher_for_site_runtime
from app.services.post_analysis_service import persist_post_analysis
from app.services.strategy_effect_service import reconcile_effect_target_url


async def sync_site_posts(session: AsyncSession, *, site_id: str, limit: int = 100) -> dict[str, Any]:
    site = await _load_site(session, site_id)
    if not site:
        return {"ok": False, "error": "site not found", "fetched": 0, "saved": 0}
    try:
        connector = await publisher_for_site_runtime(
            session, site, dry_run=True, require_active=True
        )
        posts = await fetch_site_posts(site, limit=limit, connector=connector)
    except (ExternalCallError, ValueError) as error:
        return {"ok": False, "site_id": site_id, "error": str(error), "fetched": 0, "saved": 0}
    saved = 0
    for post in posts:
        await _upsert_post(session, site, post)
        saved += 1
    if len(posts) < limit:
        external_ids = [str(post.get("external_id")) for post in posts if post.get("external_id")]
        await session.execute(
            text(
                """
                UPDATE seo_agent.posts
                   SET status = 'remote_missing', updated_at = now()
                 WHERE site_id = CAST(:site_id AS uuid)
                   AND external_id IS NOT NULL
                   AND external_id <> ALL(CAST(:external_ids AS text[]))
                   AND status IS DISTINCT FROM 'remote_missing'
                """
            ),
            {"site_id": site_id, "external_ids": external_ids},
        )
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status = 'canceled',
                       error_message = '远端文章已不再返回，本地库存标记为 remote_missing',
                       finished_at = COALESCE(finished_at, now()), updated_at = now()
                 WHERE status = 'queued'
                   AND post_id IN (
                     SELECT id FROM seo_agent.posts
                      WHERE site_id = CAST(:site_id AS uuid) AND status = 'remote_missing'
                   )
                """
            ),
            {"site_id": site_id},
        )
    await session.commit()
    return {"ok": True, "site_id": site_id, "fetched": len(posts), "saved": saved}


async def sync_all_site_posts(
    session: AsyncSession,
    *,
    limit_per_site: int = 100,
    business_id: str | None = None,
    strategy_only: bool = False,
) -> dict[str, Any]:
    where = "WHERE status = 'active'"
    params: dict[str, Any] = {}
    if strategy_only:
        if not business_id:
            raise ValueError("business_id is required for strategy-scoped sync")
        where += " AND business_id = :business_id AND strategy_enabled = true"
        params["business_id"] = business_id
    rows = await session.execute(
        text(f"SELECT id FROM seo_agent.sites {where} ORDER BY is_main DESC, name ASC"),
        params,
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
    where = "WHERE COALESCE(status, '') <> 'remote_missing'"
    params: dict[str, Any] = {"limit": max(1, min(limit, 200)), "offset": max(0, offset)}
    if site_id:
        where += " AND site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    total = (await session.execute(text(f"SELECT count(*) FROM seo_agent.posts {where}"), params)).scalar_one()
    rows = await session.execute(
        text(
            f"""
            SELECT id, site_id, external_id, title, slug, url, status, author, category_id,
                   language_code, market, primary_keyword, meta_keywords, topic_cluster, page_type,
                   excerpt, meta_title, meta_description, cover_url, published_at,
                   modified_at, fetched_at, source, created_at, updated_at,
                   (SELECT pa.analysis FROM seo_agent.post_analyses pa
                     WHERE pa.post_id = seo_agent.posts.id
                     ORDER BY pa.analyzed_at DESC LIMIT 1) AS analysis
              FROM seo_agent.posts
              {where}
             ORDER BY published_at DESC NULLS LAST, fetched_at DESC
             LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {"items": [dict(r) for r in rows.mappings().all()], "total": int(total or 0), "limit": params["limit"], "offset": params["offset"]}


async def fetch_site_posts(
    site: dict[str, Any], *, limit: int = 100, connector: PublisherBase | None = None
) -> list[dict[str, Any]]:
    if connector is None:
        raise ValueError("fetch_site_posts requires an explicit runtime connector")
    items = await connector.read_articles(limit=limit)
    if connector.connector_type == "wordpress":
        normalizer = _normalize_wp
    elif connector.connector_type == "shopify":
        normalizer = _normalize_shopify
    else:
        normalizer = _normalize_openapi
    posts = [normalizer(item, site) for item in items]
    for post in posts:
        await _enrich_from_public_html(post)
    return posts


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


def _normalize_wp(p: dict[str, Any], _site: dict[str, Any] | None = None) -> dict[str, Any]:
    url = p.get("link")
    meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
    seo = p.get("yoast_head_json") if isinstance(p.get("yoast_head_json"), dict) else {}
    return {
        "external_id": str(p.get("id") or url or ""),
        "title": _rendered(p.get("title")) or "untitled",
        "slug": p.get("slug"),
        "url": url,
        "status": p.get("status"),
        "content_html": _rendered(p.get("content")),
        "excerpt": _rendered(p.get("excerpt")),
        "meta_title": meta.get("rank_math_title") or meta.get("_yoast_wpseo_title") or seo.get("title"),
        "meta_description": meta.get("rank_math_description") or meta.get("_yoast_wpseo_metadesc") or seo.get("description"),
        "meta_keywords": _split_keywords(meta.get("rank_math_focus_keyword") or meta.get("_yoast_wpseo_focuskw")),
        "primary_keyword": meta.get("rank_math_focus_keyword") or meta.get("_yoast_wpseo_focuskw"),
        "published_at": p.get("date_gmt") or p.get("date"),
        "modified_at": p.get("modified_gmt") or p.get("modified"),
        "source": "wp_api",
        "raw": p,
    }


def _normalize_shopify(p: dict[str, Any], site: dict[str, Any] | None = None) -> dict[str, Any]:
    site = site or {}
    handle = p.get("handle")
    article_id = p.get("id") or handle
    title_tag = p.get("titleTag") if isinstance(p.get("titleTag"), dict) else {}
    description_tag = (
        p.get("descriptionTag")
        if isinstance(p.get("descriptionTag"), dict)
        else {}
    )
    return {
        "external_id": str(article_id or ""),
        "title": p.get("title") or "untitled",
        "slug": handle,
        "url": _shopify_article_url(site, handle),
        "content_html": p.get("body") or p.get("body_html"),
        "excerpt": p.get("summary") or p.get("excerpt"),
        "meta_title": title_tag.get("value"),
        "meta_description": description_tag.get("value"),
        "status": "published" if p.get("isPublished") else p.get("status"),
        "published_at": p.get("publishedAt") or p.get("published_at"),
        "modified_at": p.get("updatedAt") or p.get("updated_at"),
        "source": "shopify_api",
        "raw": p,
    }


def _normalize_openapi(p: dict[str, Any], site: dict[str, Any] | None = None) -> dict[str, Any]:
    site = site or {}
    slug = p.get("slug") or p.get("handle")
    article_id = p.get("id") or p.get("articleId") or p.get("external_id")
    url = resolve_article_public_url(
        site,
        slug=slug,
        article_id=article_id,
        remote_url=p.get("url") or p.get("link") or p.get("detail_url") or p.get("detailUrl"),
        canonical_url=p.get("canonical_url") or p.get("canonicalUrl"),
    )
    return {
        "external_id": str(article_id or url or ""),
        "title": p.get("title") or "untitled",
        "slug": slug,
        "url": url,
        "author": p.get("author") or p.get("author_name"),
        "cover_url": p.get("cover_url") or p.get("coverUrl") or p.get("src"),
        "status": _openapi_status(p.get("status")),
        "content_md": p.get("content_md") or p.get("contentMd"),
        "content_html": p.get("content_html") or p.get("contentHtml") or p.get("content"),
        "excerpt": p.get("excerpt") or p.get("description") or p.get("descript"),
        "meta_title": p.get("meta_title") or p.get("metaTitle"),
        "meta_description": p.get("meta_description") or p.get("metaDescription") or p.get("meta_descript"),
        "meta_keywords": p.get("meta_keywords") or p.get("metaKeywords") or [],
        "primary_keyword": p.get("primary_keyword") or p.get("primaryKeyword"),
        "published_at": p.get("published_at") or p.get("publishedAt"),
        "modified_at": p.get("modified_at") or p.get("updatedAt") or p.get("updated_at"),
        "source": "openapi",
        "raw": p,
    }


def _split_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip()]


def _shopify_article_url(site: dict[str, Any], handle: Any) -> str | None:
    if not handle:
        return None
    cfg = site.get("api_config") or {}
    blog_handle = cfg.get("blogHandle") or cfg.get("blog_handle") or "news"
    base = str(site.get("base_url") or site.get("domain") or "").strip()
    if not base:
        return None
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    return urljoin(f"{base.rstrip('/')}/", f"blogs/{blog_handle}/{quote(str(handle), safe='')}" )


async def _enrich_from_public_html(post: dict[str, Any]) -> None:
    """列表接口缺正文或 TDK 时，从公开页面 HTML 源码补齐。"""
    if not post.get("url") or all(post.get(key) for key in ("content_html", "meta_title", "meta_description")):
        return
    try:
        html = await request_text(
            "GET",
            str(post["url"]),
            client_label="connector_public_article_html",
            headers={"User-Agent": "SEO-Agent-Workbench/1.0"},
            timeout=60,
        )
    except ExternalCallError as error:
        post["fetch_error"] = str(error)
        return
    parsed = _parse_public_article_html(html)
    if not post.get("content_html") and parsed.get("content_html"):
        post["content_html"] = parsed["content_html"]
        post["source"] = f"{post.get('source') or 'api'}+public_html"
    if not post.get("meta_title"):
        post["meta_title"] = parsed.get("meta_title")
    if not post.get("meta_description"):
        post["meta_description"] = parsed.get("meta_description")
    if not post.get("meta_keywords") and parsed.get("meta_keywords"):
        post["meta_keywords"] = parsed["meta_keywords"]


class _PublicArticleParser(HTMLParser):
    _container_classes = ("article-content", "entry-content", "post-content", "blog-content", "t4s-article-content")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0
        self._active: list[dict[str, Any]] = []
        self._candidates: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        lower_tag = tag.lower()
        if lower_tag == "meta":
            name = (attrs_map.get("name") or attrs_map.get("property") or "").lower()
            content = attrs_map.get("content", "").strip()
            if content and name in {"description", "og:description", "keywords", "og:title", "twitter:title"}:
                self.meta.setdefault(name, content)
        if lower_tag == "title":
            self._in_title = True
        if lower_tag in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        token = self.get_starttag_text() or f"<{tag}>"
        for candidate in self._active:
            candidate["html"].append(token)
        if _is_article_container(lower_tag, attrs_map):
            candidate = {"tag": lower_tag, "html": [token], "text": []}
            self._active.append(candidate)
            self._candidates.append(candidate)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            return
        token = self.get_starttag_text() or f"<{tag}/>"
        for candidate in self._active:
            candidate["html"].append(token)

    def handle_endtag(self, tag: str) -> None:
        lower_tag = tag.lower()
        if lower_tag in {"script", "style", "noscript", "template"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        for candidate in self._active:
            candidate["html"].append(f"</{tag}>")
        if self._active and self._active[-1]["tag"] == lower_tag:
            self._active.pop()
        if lower_tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._skip_depth or not data.strip():
            return
        for candidate in self._active:
            candidate["text"].append(data.strip())
            candidate["html"].append(data)


def _is_article_container(tag: str, attrs: dict[str, str]) -> bool:
    if tag in {"article", "main"}:
        return True
    marker = f"{attrs.get('id', '')} {attrs.get('class', '')}".lower()
    return any(name in marker for name in _PublicArticleParser._container_classes)


def _parse_public_article_html(source: str) -> dict[str, Any]:
    parser = _PublicArticleParser()
    try:
        parser.feed(source or "")
        parser.close()
    except Exception:  # noqa: BLE001
        return {}
    candidate = max(parser._candidates, key=lambda item: len(" ".join(item["text"])), default=None)
    text = " ".join(candidate["text"]) if candidate else ""
    return {
        "content_html": "".join(candidate["html"]).strip() if candidate and len(text) >= 80 else None,
        "meta_title": parser.meta.get("og:title") or parser.meta.get("twitter:title") or " ".join(parser.title_parts).strip() or None,
        "meta_description": parser.meta.get("description") or parser.meta.get("og:description"),
        "meta_keywords": _split_keywords(parser.meta.get("keywords")),
    }


def _openapi_status(value: Any) -> str | None:
    if value in (1, "1", "published", "publish"):
        return "published"
    if value in (0, "0", "draft"):
        return "draft"
    return str(value) if value is not None else None


async def _upsert_post(session: AsyncSession, site: dict[str, Any], post: dict[str, Any]) -> None:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.posts
                  (site_id, external_id, title, slug, url, status, author, language_code, market,
                   content_format, content_md, content_html, excerpt, meta_title, meta_description,
                   meta_keywords, primary_keyword, cover_url, published_at, modified_at, fetched_at, source, raw)
                VALUES
                  (CAST(:site_id AS uuid), :external_id, :title, :slug, :url, :status, :author, :language_code, :market,
                   :content_format, :content_md, :content_html, :excerpt, :meta_title, :meta_description,
                   CAST(:meta_keywords AS jsonb), :primary_keyword, :cover_url, :published_at, :modified_at, now(), :source, CAST(:raw AS jsonb))
                ON CONFLICT (site_id, external_id) WHERE external_id IS NOT NULL AND external_id <> ''
                DO UPDATE SET
                  title = EXCLUDED.title,
                  slug = EXCLUDED.slug,
                  url = EXCLUDED.url,
                  status = EXCLUDED.status,
                  author = EXCLUDED.author,
                  content_md = EXCLUDED.content_md,
                  content_html = EXCLUDED.content_html,
                  excerpt = EXCLUDED.excerpt,
                  meta_title = EXCLUDED.meta_title,
                  meta_description = EXCLUDED.meta_description,
                  meta_keywords = EXCLUDED.meta_keywords,
                  primary_keyword = EXCLUDED.primary_keyword,
                  cover_url = EXCLUDED.cover_url,
                  published_at = EXCLUDED.published_at,
                  modified_at = EXCLUDED.modified_at,
                  fetched_at = now(),
                  source = EXCLUDED.source,
                  raw = EXCLUDED.raw,
                  updated_at = now()
                RETURNING id, fetched_at
                """
            ),
            {
                "site_id": str(site["id"]),
                "external_id": post.get("external_id") or None,
                "title": post.get("title") or "untitled",
                "slug": post.get("slug"),
                "url": post.get("url"),
                "status": post.get("status"),
                "author": post.get("author"),
                "language_code": site.get("language_code"),
                "market": site.get("market"),
                "content_format": "mixed" if post.get("content_md") and post.get("content_html") else ("markdown" if post.get("content_md") else "html" if post.get("content_html") else "unknown"),
                "content_md": post.get("content_md"),
                "content_html": post.get("content_html"),
                "excerpt": post.get("excerpt"),
                "meta_title": post.get("meta_title"),
                "meta_description": post.get("meta_description"),
                "meta_keywords": json.dumps(post.get("meta_keywords") or [], ensure_ascii=False),
                "primary_keyword": post.get("primary_keyword"),
                "cover_url": post.get("cover_url"),
                "published_at": _dt(post.get("published_at")),
                "modified_at": _dt(post.get("modified_at")),
                "source": post.get("source") or "api",
                "raw": json.dumps(post.get("raw") or post, ensure_ascii=False, default=str),
            },
        )
    ).mappings().one()
    await persist_post_analysis(session, str(row["id"]), {**post, "fetched_at": row["fetched_at"]})
    await _sync_linked_article_url(
        session,
        post_id=str(row["id"]),
        site_id=str(site["id"]),
        external_id=post.get("external_id"),
        slug=post.get("slug"),
        url=post.get("url"),
    )


async def _sync_linked_article_url(
    session: AsyncSession,
    *,
    post_id: str,
    site_id: str,
    external_id: Any,
    slug: Any,
    url: Any,
) -> None:
    if not external_id or not url:
        return
    linked = await session.execute(
        text(
            """
            WITH linked AS (
              SELECT id
                FROM seo_agent.articles
               WHERE site_id = CAST(:site_id AS uuid) AND published_post_id = :external_id
            ),
            updated AS (
              UPDATE seo_agent.articles article
                 SET slug = COALESCE(NULLIF(:slug, ''), article.slug),
                     target_url = CASE
                       WHEN article.target_url = article.published_url THEN :url
                       ELSE article.target_url
                     END,
                     published_url = :url,
                     updated_at = now()
                FROM linked
               WHERE article.id = linked.id
                 AND (
                   article.slug IS DISTINCT FROM COALESCE(NULLIF(:slug, ''), article.slug)
                   OR article.published_url IS DISTINCT FROM :url
                   OR (
                     article.target_url = article.published_url
                     AND article.target_url IS DISTINCT FROM :url
                   )
                 )
              RETURNING article.id
            )
            SELECT id::text AS id FROM linked
            """
        ),
        {
            "site_id": site_id,
            "external_id": str(external_id),
            "slug": str(slug or ""),
            "url": str(url),
        },
    )
    article_ids = [str(row["id"]) for row in linked.mappings().all()]
    if article_ids:
        effects = await session.execute(
            text(
                """
                SELECT id::text AS id, target_url, payload
                  FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = 'strategy_effect'
                   AND status = 'queued'
                   AND article_id::text = ANY(CAST(:article_ids AS text[]))
                   FOR UPDATE
                """
            ),
            {"article_ids": article_ids},
        )
        for effect in effects.mappings().all():
            original_payload = dict(effect["payload"] or {})
            payload = reconcile_effect_target_url(
                original_payload,
                current_target_url=effect["target_url"],
                new_target_url=url,
            )
            if str(effect["target_url"] or "") == str(url) and payload == original_payload:
                continue
            await session.execute(
                text(
                    """
                    UPDATE seo_agent.tasks
                       SET target_url = :url,
                           payload = CAST(:payload AS jsonb),
                           updated_at = now()
                     WHERE id = CAST(:id AS uuid) AND status = 'queued'
                    """
                ),
                {
                    "id": str(effect["id"]),
                    "url": str(url),
                    "payload": json.dumps(payload, ensure_ascii=False, default=str),
                },
            )
        for article_id in article_ids:
            await reconcile_article_public_url(
                session,
                article_id=article_id,
                remote_url=str(url),
            )
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET payload = jsonb_set(payload, '{item,url}', to_jsonb(CAST(:url AS text))),
                   updated_at = now()
             WHERE task_type = 'review' AND payload->>'kind' = 'content_audit'
               AND status = 'queued'
               AND post_id = CAST(:post_id AS uuid)
            """
        ),
        {"post_id": post_id, "url": str(url)},
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
