"""v1 业务路由：sites / keywords / articles / site-snapshot / serpapi / images。
全部与原 Node.js 路由的请求/响应体兼容。
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.image_provider import search_images
from app.clients.publishers import ImageUploadRequest, connector_for_site
from app.core.database import get_db
from app.services.article_service import (
    articles_kpi,
    articles_monthly_stats,
    articles_timeseries,
    get_article,
    list_articles,
    save_article,
)
from app.services.publish_service import get_publish_task, publish_article, sync_article_seo_metadata
from app.services.publish_service import PublishError as PublishServiceError
from app.services.product_service import (
    get_product,
    get_product_by_external_id,
    list_products,
)
from app.services.post_sync_service import list_posts, sync_all_site_posts, sync_site_posts
from app.services.article_url_reconciliation_service import reconcile_site_article_urls
from app.services.keyword_query_service import build_analysis_queue, get_keyword, list_keywords
from app.services.site_service import (
    delete_site,
    get_site,
    list_sites,
    upsert_site,
)
from app.services.site_knowledge_service import generate_site_knowledge, save_site_knowledge
from app.services.business_discovery_service import discover_business_from_site
from app.services.site_index_service import scan_site_index
from app.services.main_site_content_service import get_main_site_content_plan
from app.services.site_config_service import sync_sites_from_config
from app.services.shopify_connection_service import (
    activate_shopify_connection,
    configure_shopify_connection,
    get_shopify_connection,
    publisher_for_site_runtime,
    test_shopify_connection,
)
from app.services.shopify_product_service import (
    ShopifyProductError,
    list_shopify_product_seo,
    sync_shopify_products,
    update_shopify_product_seo,
)
from app.services.site_snapshot_service import probe_apis
from app.services.serp_snapshot_service import fetch_and_save_serp_snapshot

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
    business_id: str | None = None
    businessId: str | None = None
    strategy_enabled: bool | None = None
    strategyEnabled: bool | None = None
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
    knowledge_profile: dict[str, Any] | None = None


class SiteKnowledgeBody(BaseModel):
    status: str = "draft"
    site_mode: str = ""
    positioning: str = ""
    audience: str = ""
    products: list[str] = Field(default_factory=list)
    in_scope_topics: list[str] = Field(default_factory=list)
    out_of_scope_topics: list[str] = Field(default_factory=list)
    content_types: list[str] = Field(default_factory=list)
    tone: str = ""
    conversion_goals: list[str] = Field(default_factory=list)
    conversion_targets: list[str] = Field(default_factory=list)
    restricted_topics: list[str] = Field(default_factory=list)
    internal_link_rules: list[str] = Field(default_factory=list)
    editorial_rules: list[str] = Field(default_factory=list)
    core_pages: list[dict[str, Any]] = Field(default_factory=list)
    index_scan: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    verified_assets: list[dict[str, Any]] = Field(default_factory=list)
    generation_policy: dict[str, Any] = Field(default_factory=dict)
    generated_at: str | None = None
    updated_at: str | None = None


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


@router.post("/sites/{site_id}/knowledge/generate")
async def generate_site_knowledge_route(site_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await generate_site_knowledge(session, site_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sites/{site_id}/business-discovery")
async def discover_site_business_route(site_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Create a draft profile from public site evidence; never enables or publishes."""
    try:
        return await discover_business_from_site(session, site_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/sites/{site_id}/knowledge")
async def save_site_knowledge_route(
    site_id: str,
    body: SiteKnowledgeBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if body.status not in {"draft", "confirmed"}:
        raise HTTPException(status_code=400, detail="knowledge status must be draft or confirmed")
    try:
        profile = await save_site_knowledge(session, site_id, body.model_dump(exclude_none=True))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"site_id": site_id, "knowledge_profile": profile}


@router.post("/sites/{site_id}/index-scan")
async def scan_site_index_route(site_id: str, request: Request, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="请上传 Sitemap 或 URL 索引文件")
    filename = request.headers.get("x-filename") or "sitemap.xml"
    replace = request.query_params.get("replace", "true").lower() != "false"
    try:
        return await scan_site_index(session, site_id, content, filename, replace=replace)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/sites/{site_id}/main-content")
async def main_site_content_route(site_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await get_main_site_content_plan(session, site_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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
    business_id: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assigned_site_id: str | None = None,
    min_score: float | None = None,
    market: str | None = None,
    search: str | None = None,
    ai_analyzed: bool | None = None,
    intent: str | None = None,
    serp_feature: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_keywords(
        session,
        business_id=business_id,
        status=status,
        priority=priority,
        assigned_site_id=assigned_site_id,
        min_score=min_score,
        market=market,
        search=search,
        ai_analyzed=ai_analyzed,
        intent=intent,
        serp_feature=serp_feature,
        limit=limit,
        offset=offset,
    )


@router.get("/keywords/analysis-queue")
async def keyword_analysis_queue_route(
    limit: int = Query(100, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await build_analysis_queue(session, limit=limit)


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


@router.get("/articles/stats/monthly")
async def articles_monthly_stats_route(
    site_id: str | None = None,
    months: int = Query(12, ge=1, le=36),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await articles_monthly_stats(session, site_id=site_id, months=months)


@router.get("/articles/stats/kpi")
async def articles_kpi_route(
    site_id: str | None = None,
    date_field: str = Query("created_at", pattern="^(created_at|published_at)$"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await articles_kpi(session, site_id=site_id, date_field=date_field)


@router.get("/articles/stats/timeseries")
async def articles_timeseries_route(
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    site_id: str | None = None,
    granularity: str = Query("auto", pattern="^(auto|day|week|month)$"),
    date_field: str = Query("created_at", pattern="^(created_at|published_at)$"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await articles_timeseries(
        session,
        site_id=site_id,
        start=start,
        end=end,
        granularity=granularity,
        date_field=date_field,
    )


@router.get("/articles/{article_id}")
async def get_article_route(article_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    a = await get_article(session, article_id)
    if not a:
        raise HTTPException(status_code=404, detail="article not found")
    return a


@router.post("/articles/{article_id}/sync-seo-metadata")
async def sync_article_seo_metadata_route(article_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await sync_article_seo_metadata(session, article_id=article_id)
    except PublishServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------- /api/v1/posts: 外部站点已存在文章 ----------

@router.get("/posts")
async def list_posts_route(
    site_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_posts(session, site_id=site_id, limit=limit, offset=offset)


@router.get("/sites/{site_id}/connector")
async def inspect_site_connector_route(
    site_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await session.execute(
        text(
            "SELECT id, site_key, name, site_type, domain, base_url, api_base_url, api_config "
            "FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
        ),
        {"id": site_id},
    )

    site = row.mappings().first()
    if not site:
        raise HTTPException(status_code=404, detail="site not found")
    try:
        connector = await publisher_for_site_runtime(
            session, dict(site), dry_run=True, require_active=False
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = await connector.check_connection()
    return {"site_id": site_id, "site_name": site["name"], **result}


class SyncPostsBody(BaseModel):
    limit: int = 100


class SiteImageUploadBody(BaseModel):
    type: str
    url: str | None = None
    file: str | None = None
    base64: str | None = None
    filename: str | None = Field(default=None, max_length=180)
    alt_text: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=500)
    caption: str | None = Field(default=None, max_length=2000)
    dry_run: bool = True


class ShopifyConnectionBody(BaseModel):
    shop_domain: str = Field(min_length=1, max_length=255)
    blog_handle: str = Field(default="news", min_length=1, max_length=128)
    api_version: str = Field(default="2026-07", pattern=r"^\d{4}-\d{2}$")
    client_id: SecretStr | None = None
    client_secret: SecretStr | None = None


@router.get("/sites/{site_id}/shopify/connection")
async def get_shopify_connection_route(
    site_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await get_shopify_connection(session, site_id=site_id, include_missing=True)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sites/{site_id}/shopify/connection")
async def configure_shopify_connection_route(
    site_id: str,
    body: ShopifyConnectionBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await configure_shopify_connection(
            session,
            site_id=site_id,
            shop_domain=body.shop_domain,
            blog_handle=body.blog_handle,
            api_version=body.api_version,
            client_id=body.client_id.get_secret_value() if body.client_id else None,
            client_secret=body.client_secret.get_secret_value() if body.client_secret else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/sites/{site_id}/shopify/connection/test")
async def test_shopify_connection_route(
    site_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await test_shopify_connection(session, site_id=site_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/sites/{site_id}/shopify/connection/activate")
async def activate_shopify_connection_route(
    site_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await activate_shopify_connection(session, site_id=site_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


class ShopifyProductSyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=250, ge=1, le=1000)


class ShopifyProductSeoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0)
    expected_updated_at: str = Field(min_length=1, max_length=64)
    meta_title: str = Field(min_length=1, max_length=70)
    meta_description: str = Field(min_length=1, max_length=320)
    dry_run: bool = True
    confirm: bool = False
    preview_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    actor: str = Field(default="local_user", min_length=1, max_length=128)


def _require_local_shopify_write(request: Request) -> None:
    origin = str(request.headers.get("origin") or "").rstrip("/")
    if origin not in {"http://127.0.0.1:5173", "http://localhost:5173"}:
        raise HTTPException(status_code=403, detail="Shopify writes are restricted to the local management UI")


@router.post("/sites/{site_id}/shopify/products/sync")
async def sync_shopify_products_route(
    site_id: str,
    body: ShopifyProductSyncBody,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _require_local_shopify_write(request)
    try:
        return await sync_shopify_products(session, site_id=site_id, limit=body.limit)
    except ShopifyProductError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/sites/{site_id}/shopify/products/seo")
async def list_shopify_product_seo_route(
    site_id: str,
    missing_only: bool = True,
    limit: int = Query(100, ge=1, le=250),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await list_shopify_product_seo(
            session,
            site_id=site_id,
            missing_only=missing_only,
            limit=limit,
            offset=offset,
        )
    except ShopifyProductError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/sites/{site_id}/shopify/products/seo")
async def update_shopify_product_seo_route(
    site_id: str,
    body: ShopifyProductSeoBody,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _require_local_shopify_write(request)
    if not body.dry_run and not body.confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required for a live Shopify write")
    try:
        return await update_shopify_product_seo(
            session,
            site_id=site_id,
            product_id=body.product_id,
            expected_updated_at=body.expected_updated_at,
            meta_title=body.meta_title,
            meta_description=body.meta_description,
            actor="local_ui",
            dry_run=body.dry_run,
            preview_token=body.preview_token,
            request_id=body.request_id,
        )
    except ShopifyProductError as error:
        detail = str(error)
        status_code = 409 if "changed after review" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from error


@router.post("/sites/{site_id}/images/upload")
async def upload_site_image_route(
    site_id: str,
    body: SiteImageUploadBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await session.execute(
        text(
            "SELECT id, site_key, name, site_type, domain, base_url, api_base_url, status, api_config "
            "FROM seo_agent.sites WHERE id = CAST(:id AS uuid)"
        ),
        {"id": site_id},
    )
    site = row.mappings().first()
    if not site:
        raise HTTPException(status_code=404, detail="site not found")
    if site["status"] != "active":
        raise HTTPException(status_code=400, detail="site is not active")

    connector = connector_for_site(dict(site), dry_run=body.dry_run)
    result = await connector.upload_image(ImageUploadRequest(
        type=body.type,
        url=body.url,
        file=body.file,
        base64=body.base64,
        filename=body.filename,
        alt_text=body.alt_text,
        title=body.title,
        caption=body.caption,
    ))
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "image upload failed")
    return {
        "ok": True,
        "dry_run": result.dry_run,
        "site_id": site_id,
        "image_id": result.image_id,
        "src": result.src,
        "raw": result.raw,
    }


@router.post("/sites/{site_id}/posts/sync")
async def sync_site_posts_route(
    site_id: str,
    body: SyncPostsBody | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await sync_site_posts(session, site_id=site_id, limit=(body.limit if body else 100))


@router.post("/sites/{site_id}/articles/reconcile-public-urls")
async def reconcile_site_article_urls_route(
    site_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await reconcile_site_article_urls(session, site_id=site_id)
    await session.commit()
    return {"ok": result["failed"] == 0, "site_id": site_id, **result}


@router.get("/sites/{site_id}/articles/lookup")
async def lookup_remote_article_route(
    site_id: str,
    slug: str = Query(min_length=1, max_length=300),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    site = await get_site(session, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="site not found")
    publisher = await publisher_for_site_runtime(
        session,
        dict(site),
        dry_run=True,
        require_active=False,
    )
    article = await publisher.find_article_by_slug(slug)
    return {"ok": True, "site_id": site_id, "found": article is not None, "article": article}


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
    keyword: str = Field(min_length=1)
    gl: str | None = None
    hl: str | None = None


@router.post("/serpapi")
async def serpapi_route(
    body: SerpApiBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await fetch_and_save_serp_snapshot(
        session,
        keyword=body.keyword,
        gl=body.gl or "us",
        hl=body.hl or "en",
    )


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
    external_id: str,
    site_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    p = await get_product_by_external_id(session, external_id, site_id=site_id)
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
    site_id: str | None = None
    dry_run: bool = True
    actor: str | None = None
    update_post_id: str | None = None


@router.post("/publish")
async def publish_route(body: PublishBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await publish_article(
            session,
            article_id=body.article_id,
            site_id=body.site_id,
            dry_run=body.dry_run,
            actor=body.actor or "publish_api",
            update_post_id=body.update_post_id,
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

