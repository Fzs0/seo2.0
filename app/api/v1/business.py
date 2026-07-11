"""v1 业务路由：sites / keywords / articles / site-snapshot / serpapi / images。
全部与原 Node.js 路由的请求/响应体兼容。
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.image_provider import search_images
from app.clients.serpapi import fetch_google_serp
from app.core.database import get_db
from app.services.article_service import (
    get_article,
    list_articles,
    save_article,
)
from app.services.publish_service import get_publish_task, publish_article
from app.services.publish_service import PublishError as PublishServiceError
from app.services.product_service import (
    get_product,
    get_product_by_external_id,
    list_products,
)
from app.services.post_sync_service import list_posts, sync_all_site_posts, sync_site_posts
from app.services.keyword_query_service import get_keyword, list_keywords
from app.services.site_service import (
    delete_site,
    get_site,
    list_sites,
    upsert_site,
)
from app.services.site_config_service import sync_sites_from_config
from app.services.site_snapshot_service import probe_apis

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------- /api/v1/sites ----------

class SiteUpsertBody(BaseModel):
    site_key: str | None = None
    siteKey: str | None = None
    name: str | None = None
    site_type: str | None = None
    siteType: str | None = None
    domain: str | None = None
    base_url: str | None = None
    baseUrl: str | None = None
    api_base_url: str | None = None
    apiBaseUrl: str | None = None
    market: str | None = None
    language_code: str | None = None
    languageCode: str | None = None
    google_gl: str | None = None
    googleGl: str | None = None
    google_hl: str | None = None
    googleHl: str | None = None
    semrush_database: str | None = None
    semrushDatabase: str | None = None
    content_role: str | None = None
    contentRole: str | None = None
    content_scope: str | None = None
    contentScope: str | None = None
    is_main: bool = False
    isMain: bool = False
    allow_external_links: bool = False
    allowExternalLinks: bool = False
    publish_config: dict[str, Any] | None = None
    publishConfig: dict[str, Any] | None = None
    api_config: dict[str, Any] | None = None
    apiConfig: dict[str, Any] | None = None
    status: str | None = None
    notes: str | None = None
    raw: dict[str, Any] | None = None


@router.get("/sites")
async def list_sites_route(
    market: str | None = None,
    language_code: str | None = None,
    site_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    items = await list_sites(
        session,
        market=market,
        language_code=language_code,
        site_type=site_type,
        limit=limit,
    )
    return {"items": items, "total": len(items)}


@router.get("/sites/{site_id}")
async def get_site_route(site_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    s = await get_site(session, site_id)
    if not s:
        raise HTTPException(status_code=404, detail="site not found")
    return s


@router.post("/sites")
async def upsert_site_route(body: SiteUpsertBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        info = await upsert_site(session, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return info


@router.post("/sites/sync-config")
async def sync_sites_config_route(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await sync_sites_from_config(session)


@router.delete("/sites/{site_id}")
async def delete_site_route(site_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ok = await delete_site(session, site_id)
    if not ok:
        raise HTTPException(status_code=404, detail="site not found")
    return {"deleted": True}


# ---------- /api/v1/keywords ----------

@router.get("/keywords")
async def list_keywords_route(
    status: str | None = None,
    priority: str | None = None,
    assigned_site_id: str | None = None,
    min_score: float | None = None,
    market: str | None = None,
    search: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_keywords(
        session,
        status=status,
        priority=priority,
        assigned_site_id=assigned_site_id,
        min_score=min_score,
        market=market,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/keywords/{keyword_id}")
async def get_keyword_route(keyword_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    k = await get_keyword(session, keyword_id)
    if not k:
        raise HTTPException(status_code=404, detail="keyword not found")
    return k


# ---------- /api/v1/articles ----------

class ArticleSaveBody(BaseModel):
    task_id: str | None = None
    taskId: str | None = None
    site_id: str | None = None
    siteId: str | None = None
    keyword_id: str | None = None
    keywordId: str | None = None
    serp_snapshot_id: str | None = None
    serpSnapshotId: str | None = None
    title: str
    slug: str | None = None
    target_url: str | None = None
    targetUrl: str | None = None
    status: str | None = None
    language_code: str | None = None
    languageCode: str | None = None
    market: str | None = None
    brief_md: str | None = None
    briefMd: str | None = None
    prompt_text: str | None = None
    promptText: str | None = None
    content_md: str | None = None
    contentMd: str | None = None
    content_html: str | None = None
    contentHtml: str | None = None
    article_parts: dict[str, Any] | None = None
    articleParts: dict[str, Any] | None = None
    meta_title: str | None = None
    metaTitle: str | None = None
    meta_description: str | None = None
    metaDescription: str | None = None
    primary_keyword: str | None = None
    primaryKeyword: str | None = None
    secondary_keywords: list[str] | None = None
    secondaryKeywords: list[str] | None = None
    internal_link_plan: list[dict[str, Any]] | None = None
    internalLinkPlan: list[dict[str, Any]] | None = None
    image_plan: list[dict[str, Any]] | None = None
    imagePlan: list[dict[str, Any]] | None = None
    references_plan: list[dict[str, Any]] | None = None
    referencesPlan: list[dict[str, Any]] | None = None
    qa_checklist: list[dict[str, Any]] | None = None
    qaChecklist: list[dict[str, Any]] | None = None
    generation_provider: str | None = None
    generationProvider: str | None = None
    generation_model: str | None = None
    generationModel: str | None = None
    raw_ai_response: dict[str, Any] | None = None
    rawAiResponse: dict[str, Any] | None = None


@router.post("/articles/save")
async def save_article_route(body: ArticleSaveBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await save_article(session, body.model_dump(exclude_none=True))


@router.get("/articles")
async def list_articles_route(
    site_id: str | None = None,
    keyword_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_articles(
        session,
        site_id=site_id,
        keyword_id=keyword_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/articles/{article_id}")
async def get_article_route(article_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    a = await get_article(session, article_id)
    if not a:
        raise HTTPException(status_code=404, detail="article not found")
    return a


# ---------- /api/v1/posts: 外部站点已存在文章 ----------

@router.get("/posts")
async def list_posts_route(
    site_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_posts(session, site_id=site_id, limit=limit, offset=offset)


class SyncPostsBody(BaseModel):
    limit: int = 100


@router.post("/sites/{site_id}/posts/sync")
async def sync_site_posts_route(
    site_id: str,
    body: SyncPostsBody | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await sync_site_posts(session, site_id=site_id, limit=(body.limit if body else 100))


@router.post("/posts/sync-all")
async def sync_all_posts_route(
    body: SyncPostsBody | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await sync_all_site_posts(session, limit_per_site=(body.limit if body else 100))


# ---------- /api/v1/site-snapshot ----------

class SiteSnapshotBody(BaseModel):
    apis: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/site-snapshot")
async def site_snapshot_route(body: SiteSnapshotBody) -> dict[str, Any]:
    results = await probe_apis(body.apis)
    return {"siteResults": results, "total": len(results)}


# ---------- /api/v1/serpapi ----------

class SerpApiBody(BaseModel):
    keyword: str
    gl: str | None = None
    hl: str | None = None


@router.post("/serpapi")
async def serpapi_route(body: SerpApiBody) -> dict[str, Any]:
    return await fetch_google_serp(body.keyword, gl=body.gl or "us", hl=body.hl or "en")


# ---------- /api/v1/images ----------

class ImageSearchBody(BaseModel):
    provider: str = "pexels"
    query: str
    per_page: int = 10
    page: int = 1


@router.post("/images/search")
async def images_search_route(body: ImageSearchBody) -> dict[str, Any]:
    return await search_images(
        provider=body.provider,
        query=body.query,
        per_page=body.per_page,
        page=body.page,
    )

# ---------- /api/v1/products ----------

@router.get("/products")
async def list_products_route(
    site_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_products(
        session,
        site_id=site_id,
        status=status,
        category=category,
        search=search,
        min_price=min_price,
        max_price=max_price,
        limit=limit,
        offset=offset,
    )


@router.get("/products/by-external/{external_id}")
async def get_product_by_external_id_route(
    external_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    p = await get_product_by_external_id(session, external_id)
    if not p:
        raise HTTPException(status_code=404, detail="product not found")
    return p


@router.get("/products/{product_id}")
async def get_product_route(
    product_id: int, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    p = await get_product(session, product_id)
    if not p:
        raise HTTPException(status_code=404, detail="product not found")
    return p


# ---------- /api/v1/publish ----------

class PublishBody(BaseModel):
    article_id: str
    site_id: str
    dry_run: bool = True
    actor: str | None = None


@router.post("/publish")
async def publish_route(body: PublishBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await publish_article(
            session,
            article_id=body.article_id,
            site_id=body.site_id,
            dry_run=body.dry_run,
            actor=body.actor or "publish_api",
        )
    except PublishServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/publish/{task_id}")
async def get_publish_task_route(
    task_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    task = await get_publish_task(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="publish task not found")
    return task

