"""v1 业务路由：完全复用 Node.js 路由的请求/响应体结构。

- GET/POST /api/v1/workflow/standard        配置标准读写
- POST /api/v1/workflow/standard/reload     重载
- GET  /api/v1/workflow/sample              样例
- POST /api/v1/workflow/analyze             分析关键词
- POST /api/v1/workflow/import-csv          CSV 导入
- POST /api/v1/workflow/import-file         文件导入
- POST /api/v1/workflow/brief               Brief（含 AI 增强，回退本地）
- POST /api/v1/workflow/prompt              Prompt
- POST /api/v1/workflow/project-package     项目包
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.engine.locale import locale_for_project
from app.engine.loader import get_store
from app.engine.semrush_strategy import preview_semrush_strategy_payload
from app.services.brief_service import build_brief_with_optional_ai, build_prompt_preview
from app.services.executor import upsert_keywords, upsert_sites
from app.services.keyword_service import (
    analyze_keywords,
    import_summary,
    importable_keywords,
    import_and_analyze_csv,
    import_and_analyze_file,
    prepare_semrush_strategy_import,
    prepare_stored_semrush_strategy_validation,
)
from app.services.keyword_ai_service import cancel_keyword_analysis, get_keyword_analysis, get_latest_keyword_analysis, start_keyword_analysis
from app.services.automation_service import get_execution_status
from app.services.strategy_effect_service import list_effects
from app.services.strategy_service import list_strategies
from app.services.content_audit_service import get_content_audit_batch, list_ai_reviews, start_content_audit
from app.services.article_generation_service import generate_article_pipeline

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------- 标准配置读写 ----------

class StandardBody(BaseModel):
    standard: dict[str, Any] | None = None


@router.get("/workflow/standard")
async def get_standard() -> dict[str, Any]:
    store = get_store()
    return {"standard": store.payload, "version": store.version, "source": store.source, "healthy": store.healthy}


@router.post("/workflow/standard/reload")
async def reload_standard() -> dict[str, Any]:
    store = get_store()
    await store.load_from_db()
    return {"standard": store.payload, "version": store.version, "source": store.source, "healthy": store.healthy, "reloaded": True}


@router.post("/workflow/standard")
async def save_standard(body: StandardBody) -> dict[str, Any]:
    """本接口只把 payload 写回内存 + 文件占位（v1 文件不变），

    真正的 DB 写库走 /api/v1/rule-sets（v2）。"""
    if not body.standard:
        return {"error": "missing standard body"}
    # 同步到文件（与 Node.js 兼容）
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    target = repo_root / "workflows" / "seo-standard.json"
    target.write_text(__import__("json").dumps(body.standard, ensure_ascii=False, indent=2), encoding="utf-8")
    store = get_store()
    # 重新从文件读一遍（注意：这里不读 DB，是为了和 Node.js 行为一致；v2 走 DB 路径）
    store._payload = body.standard  # noqa: SLF001 同步内存（admin/reload 会从 PG 再覆一次）
    store._version = body.standard.get("version", store.version)  # noqa: SLF001
    return {"standard": store.payload, "version": store.version, "saved": True}


@router.get("/workflow/sample")
async def get_sample() -> dict[str, Any]:
    store = get_store()
    return {
        "project": store.get("positioning.defaultProject", {}),
        "keywords": store.get("__sample", []),
        "sites": store.get("sites", []),
        "standardVersion": store.version,
    }


# ---------- 导入与分析 ----------

class AnalyzeBody(BaseModel):
    keywords: list[dict[str, Any]] = Field(default_factory=list)
    project: dict[str, Any] | None = None


class CsvBody(BaseModel):
    csv: str = ""
    project: dict[str, Any] | None = None
    save: bool = False
    filename: str | None = None


class FileBody(BaseModel):
    filename: str = ""
    contentBase64: str | None = None
    contentText: str | None = None
    project: dict[str, Any] | None = None
    save: bool = False


class SemrushStrategyImportBody(FileBody):
    businessId: str = Field(min_length=1)
    market: str = Field(min_length=1)
    confirmed: bool = False


class SemrushStrategyValidateBody(BaseModel):
    businessId: str = Field(min_length=1)
    sourceBatchId: str | None = None


@router.post("/workflow/semrush-strategy/preview")
async def preview_semrush_strategy(body: FileBody) -> dict[str, Any]:
    try:
        return {
            "filename": body.filename,
            **preview_semrush_strategy_payload(
                {"filename": body.filename, "contentBase64": body.contentBase64 or ""},
                get_store().get("keywordImportPolicy", {}),
            ),
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/workflow/semrush-strategy/import")
async def import_semrush_strategy(
    body: SemrushStrategyImportBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="必须先预览并明确确认导入")
    business_id = body.businessId.strip()
    locale = locale_for_project({"market": body.market.strip()})
    enabled_sites = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM seo_agent.sites "
                    "WHERE business_id = :business_id AND strategy_enabled = true AND status = 'active' "
                    "AND lower(coalesce(semrush_database, '')) = :semrush_database "
                    "AND upper(coalesce(market, '')) = :market "
                    "AND lower(coalesce(language_code, '')) = :language_code"
                ),
                {
                    "business_id": business_id,
                    "semrush_database": str(locale.get("semrushDatabase") or "").casefold(),
                    "market": str(locale.get("market") or "").upper(),
                    "language_code": str(locale.get("languageCode") or "").casefold(),
                },
            )
        ).scalar_one()
        or 0
    )
    if not enabled_sites:
        raise HTTPException(status_code=400, detail="目标业务没有与所选市场、语种和 Semrush Database 匹配的启用策略站点")

    source_batch_id = str(uuid4())
    try:
        rows, preview = prepare_semrush_strategy_import(
            {"filename": body.filename, "contentBase64": body.contentBase64 or ""},
            {"businessId": business_id, "market": body.market.strip()},
            source_batch_id,
            get_store().get("keywordImportPolicy", {}),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    saved = await upsert_keywords(session, rows)
    await session.commit()
    pool_count = int(
        (
            await session.execute(
                text("SELECT count(*) FROM seo_agent.keywords WHERE business_id = :business_id"),
                {"business_id": business_id},
            )
        ).scalar_one()
        or 0
    )
    return {
        "saved": saved,
        "poolCount": pool_count,
        "sourceBatchId": source_batch_id,
        "businessId": business_id,
        "market": body.market.strip(),
        "preview": {"filename": body.filename, "previewOnly": False, "sheet": "Keywords", **preview},
        "message": f"确认导入完成：处理 {saved} 条关键词；未启动 AI、分站或策略。",
    }


@router.post("/workflow/semrush-strategy/validate-imported")
async def validate_imported_semrush_strategy(
    body: SemrushStrategyValidateBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    business_id = body.businessId.strip()
    source_batch_id = body.sourceBatchId
    if not source_batch_id:
        source_batch_id = (
            await session.execute(
                text(
                    "SELECT source_batch_id FROM seo_agent.keywords "
                    "WHERE business_id = :business_id AND source = 'semrush_strategy_builder' "
                    "AND source_batch_id IS NOT NULL "
                    "GROUP BY source_batch_id ORDER BY max(created_at) DESC LIMIT 1"
                ),
                {"business_id": business_id},
            )
        ).scalar_one_or_none()
    if not source_batch_id:
        raise HTTPException(status_code=404, detail="当前业务没有已导入的 Strategy Builder 批次")

    records = (
        await session.execute(
            text(
                "SELECT id::text, keyword, semrush_database, volume, kd, intent, topic_cluster, "
                "topic_cluster_id, page_group, page_type, raw, source_file "
                "FROM seo_agent.keywords WHERE business_id = :business_id "
                "AND source = 'semrush_strategy_builder' AND source_batch_id = :source_batch_id "
                "ORDER BY topic_cluster_id, keyword"
            ),
            {"business_id": business_id, "source_batch_id": source_batch_id},
        )
    ).mappings().all()
    if not records:
        raise HTTPException(status_code=404, detail="未找到指定业务和批次的 Strategy Builder 关键词")
    try:
        updates, preview = prepare_stored_semrush_strategy_validation(
            [dict(record) for record in records],
            get_store().get("keywordImportPolicy", {}),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    await session.execute(
        text(
            "UPDATE seo_agent.keywords SET raw = CAST(:raw AS jsonb), cluster_role = :cluster_role, "
            "cluster_size = :cluster_size, pillar_keyword = :pillar_keyword "
            "WHERE id = CAST(:id AS uuid) AND business_id = :business_id"
        ),
        [
            {**update, "raw": json.dumps(update["raw"], ensure_ascii=False), "business_id": business_id}
            for update in updates
        ],
    )
    await session.commit()
    filename = str(records[0].get("source_file") or "")
    return {
        "saved": len(updates),
        "sourceBatchId": source_batch_id,
        "businessId": business_id,
        "preview": {"filename": filename, "previewOnly": False, "sheet": "Keywords", **preview},
        "message": f"已按本地规则整理 {len(updates)} 条关键词；未调用 AI、分配站点或执行策略。",
    }


class KeywordAiAnalyzeBody(BaseModel):
    businessId: str | None = Field(default=None, min_length=1)
    keywordIds: list[str] = Field(default_factory=list)
    limit: int = 10
    opportunityType: str | None = None


def _import_metadata(project: dict[str, Any]) -> tuple[str, str | None]:
    return str(uuid4()), project.get("businessId") or project.get("business_id") or project.get("domain") or None


@router.post("/workflow/analyze")
async def analyze(body: AnalyzeBody) -> dict[str, Any]:
    project = body.project or get_store().get("positioning.defaultProject", {})
    keywords = analyze_keywords(body.keywords, project, assign_site=False)
    return {"keywords": keywords, "sites": get_store().get("sites", []), "standardVersion": get_store().version}


@router.post("/workflow/import-csv")
async def import_csv(body: CsvBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    project = body.project or get_store().get("positioning.defaultProject", {})
    analyzed_keywords = import_and_analyze_csv(body.csv, project)
    keywords = importable_keywords(analyzed_keywords)
    summary = import_summary(analyzed_keywords, keywords)
    source_batch_id, business_id = _import_metadata(project)
    rows = [{**k, "sourceFile": body.filename, "sourceBatchId": source_batch_id, "businessId": business_id} for k in keywords]
    saved = await upsert_keywords(session, rows) if body.save else 0
    if body.save:
        await session.commit()
    return {"keywords": keywords, "saved": saved, "sourceBatchId": source_batch_id, "preflightSummary": summary, "sites": get_store().get("sites", []), "standardVersion": get_store().version, "analysis": None, "message": f"导入完成：原始 {summary['total']} 条，初筛保留 {summary['accepted']} 条，剔除 {summary['rejected']} 条；请确认后再启动 AI 分析。"}


@router.post("/workflow/import-file")
async def import_file(body: FileBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    project = body.project or get_store().get("positioning.defaultProject", {})
    analyzed_keywords = import_and_analyze_file(
        {"filename": body.filename, "contentBase64": body.contentBase64 or "", "contentText": body.contentText or ""},
        project,
    )
    keywords = importable_keywords(analyzed_keywords)
    summary = import_summary(analyzed_keywords, keywords)
    source_batch_id, business_id = _import_metadata(project)
    rows = [{**k, "sourceFile": body.filename, "sourceBatchId": source_batch_id, "businessId": business_id} for k in keywords]
    saved = await upsert_keywords(session, rows) if body.save else 0
    if body.save:
        await session.commit()
    return {
        "keywords": keywords,
        "saved": saved,
        "sites": get_store().get("sites", []),
        "standardVersion": get_store().version,
        "filename": body.filename,
        "sourceBatchId": source_batch_id,
        "preflightSummary": summary,
        "analysis": None,
        "message": f"导入完成：原始 {summary['total']} 条，初筛保留 {summary['accepted']} 条，剔除 {summary['rejected']} 条；请确认后再启动 AI 分析。",
    }


@router.post("/workflow/semrush-strategy/ai-analyze")
@router.post("/workflow/keywords/ai-analyze")
async def ai_analyze_keywords(body: KeywordAiAnalyzeBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await start_keyword_analysis(
            session,
            business_id=body.businessId,
            keyword_ids=body.keywordIds or None,
            limit=body.limit,
            opportunity_type=body.opportunityType,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/workflow/semrush-strategy/ai-analyze/latest")
async def latest_ai_analysis(business_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any] | None:
    """无历史任务时返回 null。"""
    return await get_latest_keyword_analysis(session, business_id.strip())


@router.get("/workflow/semrush-strategy/ai-analyze/{run_id}")
@router.get("/workflow/keywords/ai-analyze/{run_id}")
async def ai_analyze_status(run_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    result = await get_keyword_analysis(session, run_id)
    if not result:
        raise HTTPException(status_code=404, detail="AI 分析任务不存在")
    return result


@router.post("/workflow/semrush-strategy/ai-analyze/{run_id}/cancel")
@router.post("/workflow/keywords/ai-analyze/{run_id}/cancel")
async def ai_analyze_cancel(run_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await cancel_keyword_analysis(session, run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


class ContentAuditBody(BaseModel):
    businessId: str = Field(min_length=1)
    refresh: bool = True
    limitPerSite: int = Field(default=100, ge=1, le=500)
    limit: int = Field(default=200, ge=1, le=500)
    fetchSerp: bool = True
    useAi: bool = True
    aiLimit: int = Field(default=20, ge=1, le=50)


@router.post("/workflow/content-audit/scan")
async def scan_content_route(body: ContentAuditBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await start_content_audit(
            session,
            business_id=body.businessId.strip(),
            refresh=body.refresh,
            limit_per_site=body.limitPerSite,
            limit=body.limit,
            fetch_serp=body.fetchSerp,
            use_ai=body.useAi,
            ai_limit=body.aiLimit,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/workflow/content-audit/scans/{batch_id}")
async def get_content_audit_scan(batch_id: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    result = await get_content_audit_batch(session, batch_id)
    if result is None:
        raise HTTPException(status_code=404, detail="内容审计批次不存在")
    return result


@router.get("/workflow/content-audit/reviews")
async def list_content_audit_reviews(
    status: str = "pending",
    limit: int = 50,
    business_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return {"items": await list_ai_reviews(session, status=status, limit=limit, business_id=business_id)}


@router.get("/workflow/strategies/effects")
async def get_strategy_effects(
    business_id: str,
    limit: int = 200,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    scoped_business_id = business_id.strip()
    return {"items": await list_effects(session, business_id=scoped_business_id, limit=limit)}


@router.get("/workflow/strategies")
async def get_strategy_tasks(
    status: str = "pending",
    limit: int = 50,
    site_id: str | None = None,
    search: str | None = None,
    strategy_type: str | None = None,
    priority: str | None = None,
    evidence_level: str | None = None,
    business_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return {
        "items": await list_strategies(
            session,
            status=status,
            limit=limit,
            site_id=site_id,
            search=search,
            strategy_type=strategy_type,
            priority=priority,
            evidence_level=evidence_level,
            business_id=business_id,
        )
    }


@router.get("/workflow/automation/status")
async def automation_status(business_id: str | None = None, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await get_execution_status(session, business_id=business_id.strip() if business_id else None)


# ---------- Brief / Prompt / Article ----------

class BriefBody(BaseModel):
    keyword: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    aiStage: dict[str, Any] | None = None


class PromptBody(BaseModel):
    keyword: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    briefOverride: str | None = None
    brief: str | None = None


@router.post("/workflow/brief")
async def brief(body: BriefBody) -> dict[str, Any]:
    payload = {"keyword": body.keyword, "project": body.project, "aiStage": body.aiStage or {}}
    return await build_brief_with_optional_ai(payload)


@router.post("/workflow/prompt")
async def prompt(body: PromptBody) -> dict[str, Any]:
    return build_prompt_preview(
        keyword=body.keyword,
        project=body.project,
        brief_override=body.briefOverride,
        brief=body.brief,
    )


class ArticleTestBody(BaseModel):
    """A disposable article test: no strategy task, article row, publish or effect record."""

    siteId: str = Field(min_length=1)
    keyword: str = Field(min_length=2, max_length=180)
    market: str | None = None
    languageCode: str | None = None
    pageType: str | None = None
    briefDirection: str | None = None
    userQuestion: str | None = None
    targetAssetUrl: str | None = None
    internalLinkPlan: list[dict[str, Any]] = Field(default_factory=list)
    serpContext: dict[str, Any] | None = None


@router.post("/workflow/article-test")
async def article_test(body: ArticleTestBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Generate a reviewable draft without creating any operational SEO data."""
    strategy = {
        "strategy_type": "update_article",
        "strategy_source": "article_test",
        "briefDirection": body.briefDirection or "Generate a role-aware test article from the frozen context.",
        "user_question": body.userQuestion or "",
        "target_asset_url": body.targetAssetUrl or "",
        "internal_link_plan": body.internalLinkPlan,
    }
    result = await generate_article_pipeline(
        session,
        None,
        forced_site_id=body.siteId,
        approved_strategy=strategy,
        keyword_context={
            "id": None,
            "keyword": body.keyword.strip(),
            "assigned_site_id": body.siteId,
            "market": body.market,
            "language_code": body.languageCode,
            "page_type": body.pageType,
        },
        dry_run=True,
        serp_override=body.serpContext or {"id": None, "source": "article-test", "organic_results": [], "related_questions": [], "related_searches": []},
    )
    return {**result, "testMode": True, "persistence": "none"}


# ---------- sync workspace (PG) ----------

class SyncWorkspaceBody(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)


@router.post("/database/sync-workspace")
async def sync_workspace(body: SyncWorkspaceBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    snap = body.snapshot
    sites_n = await upsert_sites(session, snap.get("sites", []) or [])
    kw_n = await upsert_keywords(session, snap.get("keywords", []) or [])
    await session.commit()
    return {"sites": sites_n, "keywords": kw_n}
