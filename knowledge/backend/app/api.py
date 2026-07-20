from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_engine, get_knowledge_session
from .batch_ingestion import BatchIngestionService
from .errors import DatabaseUnavailableError
from .knowledge_service import KnowledgeService
from .quality_backfill import QualityGateService
from .signal_crawler import SignalCrawlService
from .signal_service import MarketSignalService
from .schemas import (
    ClaimListItem,
    ClaimListResponse,
    DocumentListResponse,
    HealthResponse,
    ImportDocumentRequest,
    ImportDocumentResponse,
    ImportUrlRequest,
    OverviewResponse,
    RetrieveRequest,
    RetrieveResponse,
    ReviewClaimRequest,
    SourceListResponse,
    BatchPreviewResponse,
    BatchRunItemsResponse,
    BatchRunResponse,
    BatchSourceSpec,
    QualityBackfillRequest,
    QualityRunItemsResponse,
    QualityRunResponse,
    ImportSignalsRequest,
    MarketSignalOut,
    SignalImportResponse,
    SignalListResponse,
    SignalOverviewResponse,
    SignalContentKind,
    SignalCrawlJobIn,
    SignalCrawlJobListResponse,
    SignalCrawlJobOut,
    SignalCrawlPreviewResponse,
    SignalCrawlRunOut,
)


router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
SessionDependency = Annotated[AsyncSession, Depends(get_knowledge_session)]


def _service(session: AsyncSession) -> KnowledgeService:
    return KnowledgeService(session)


@router.get("/health", response_model=HealthResponse)
async def health() -> dict[str, str]:
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            schema_state = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            to_regclass('knowledge.schema_migrations') IS NOT NULL
                            AND to_regclass('knowledge.sources') IS NOT NULL
                            AND to_regclass('knowledge.documents') IS NOT NULL
                            AND to_regclass('knowledge.claims') IS NOT NULL
                            AND to_regclass('knowledge.evidence') IS NOT NULL
                            AND to_regclass('knowledge.usages') IS NOT NULL
                            AND to_regclass('knowledge.ingestion_sources') IS NOT NULL
                            AND to_regclass('knowledge.sync_runs') IS NOT NULL
                            AND to_regclass('knowledge.crawl_items') IS NOT NULL
                            AND to_regclass('knowledge.claim_quality_reviews') IS NOT NULL
                            AND to_regclass('knowledge.quality_review_runs') IS NOT NULL
                            AND to_regclass('knowledge.quality_review_items') IS NOT NULL
                            AND to_regclass('knowledge.market_signals') IS NOT NULL
                            AND EXISTS (
                                SELECT 1 FROM knowledge.schema_migrations
                                WHERE version = '002_batch_ingestion'
                            )
                            AND EXISTS (
                                SELECT 1 FROM knowledge.schema_migrations
                                WHERE version = '003_claim_quality_gate'
                            )
                            AND EXISTS (
                                SELECT 1 FROM knowledge.schema_migrations
                                WHERE version = '004_market_signals'
                            )
                            AND EXISTS (
                                SELECT 1 FROM knowledge.schema_migrations
                                WHERE version = '005_signal_crawl_jobs'
                            )
                        """
                    )
                )
            ).scalar_one()
    except (DatabaseUnavailableError, SQLAlchemyError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "database": "unreachable",
                "schema": "unknown",
                "message": "knowledge database is unavailable",
            },
        ) from exc

    if not schema_state:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "database": "reachable",
                "schema": "uninitialized",
                "message": "knowledge schema is not initialized",
            },
        )
    return {"status": "ok", "database": "reachable", "schema": "initialized"}


@router.get("/overview", response_model=OverviewResponse)
async def overview(session: SessionDependency) -> dict:
    return await _service(session).overview()


@router.get("/sources", response_model=SourceListResponse)
async def sources(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return await _service(session).list_sources(limit=limit, offset=offset)


@router.get("/documents", response_model=DocumentListResponse)
async def documents(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return await _service(session).list_documents(limit=limit, offset=offset)


@router.get("/claims", response_model=ClaimListResponse)
async def claims(
    session: SessionDependency,
    review_status: Literal["pending", "approved", "rejected"] | None = None,
    quality_status: Literal["unreviewed", "keep", "reject", "uncertain", "error"] | None = None,
    query: Annotated[str | None, Query(max_length=200)] = None,
    source_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return await _service(session).list_claims(
        review_status=review_status,
        quality_status=quality_status,
        query=query,
        source_id=source_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.post("/signals/import", response_model=SignalImportResponse)
async def import_signals(
    request: ImportSignalsRequest, session: SessionDependency
) -> dict:
    return await MarketSignalService(session).import_signals(request)


@router.get("/signals", response_model=SignalListResponse)
async def signals(
    session: SessionDependency,
    query: Annotated[str | None, Query(max_length=200)] = None,
    source_id: UUID | None = None,
    content_kind: SignalContentKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return await MarketSignalService(session).list_signals(
        query=query,
        source_id=source_id,
        content_kind=content_kind,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/signals/overview", response_model=SignalOverviewResponse)
async def signal_overview(session: SessionDependency) -> dict:
    return await MarketSignalService(session).overview()


@router.post("/signals/crawl/preview", response_model=SignalCrawlPreviewResponse)
async def preview_signal_crawl(request: SignalCrawlJobIn) -> dict:
    result = await SignalCrawlService(None).preview(request)
    return {
        "searches": result.searches,
        "urls": list(result.urls),
        "total": len(result.urls),
        "warnings": list(result.warnings),
    }


@router.post("/signals/crawl/jobs", response_model=SignalCrawlJobOut)
async def create_signal_crawl_job(
    request: SignalCrawlJobIn, session: SessionDependency
) -> dict:
    return await SignalCrawlService(session).create_job(request)


@router.get("/signals/crawl/jobs", response_model=SignalCrawlJobListResponse)
async def signal_crawl_jobs(session: SessionDependency) -> dict:
    return await SignalCrawlService(session).list_jobs()


@router.post("/signals/crawl/jobs/{job_id}/run", response_model=SignalCrawlRunOut)
async def run_signal_crawl_job(job_id: UUID, session: SessionDependency) -> dict:
    return await SignalCrawlService(session).run_job(job_id)


@router.get("/signals/crawl/runs/{run_id}", response_model=SignalCrawlRunOut)
async def signal_crawl_run_status(run_id: UUID, session: SessionDependency) -> dict:
    return await SignalCrawlService(session).run_status(run_id)


@router.post("/documents/import", response_model=ImportDocumentResponse)
async def import_document(
    request: ImportDocumentRequest, session: SessionDependency
) -> dict:
    return await _service(session).import_document(request)


@router.post("/documents/import-url", response_model=ImportDocumentResponse)
async def import_url(request: ImportUrlRequest, session: SessionDependency) -> dict:
    return await _service(session).import_url(request)


@router.post("/batch/preview", response_model=BatchPreviewResponse)
async def preview_batch(request: BatchSourceSpec, session: SessionDependency) -> dict:
    return await BatchIngestionService(session).preview(request)


@router.post("/batch/runs", response_model=BatchRunResponse)
async def start_batch(request: BatchSourceSpec, session: SessionDependency) -> dict:
    return await BatchIngestionService(session).start(request)


@router.get("/batch/runs/{run_id}", response_model=BatchRunResponse)
async def batch_status(run_id: UUID, session: SessionDependency) -> dict:
    return await BatchIngestionService(session).status(run_id)


@router.get("/batch/runs/{run_id}/items", response_model=BatchRunItemsResponse)
async def batch_items(run_id: UUID, session: SessionDependency) -> dict:
    return await BatchIngestionService(session).items(run_id)


@router.post("/batch/runs/{run_id}/cancel", response_model=BatchRunResponse)
async def cancel_batch(run_id: UUID, session: SessionDependency) -> dict:
    return await BatchIngestionService(session).cancel(run_id)


@router.post("/quality/runs", response_model=QualityRunResponse)
async def start_quality_run(
    request: QualityBackfillRequest, session: SessionDependency
) -> dict:
    service = QualityGateService(session)
    run_id = await service.start_backfill(
        request.limit_documents, include_reviewed=request.include_reviewed
    )
    return await service.status(run_id)


@router.get("/quality/runs/latest", response_model=QualityRunResponse)
async def latest_quality_run(session: SessionDependency) -> dict:
    return await QualityGateService(session).latest()


@router.get("/quality/runs/{run_id}", response_model=QualityRunResponse)
async def quality_run_status(run_id: UUID, session: SessionDependency) -> dict:
    return await QualityGateService(session).status(run_id)


@router.get("/quality/runs/{run_id}/items", response_model=QualityRunItemsResponse)
async def quality_run_items(run_id: UUID, session: SessionDependency) -> dict:
    return await QualityGateService(session).items(run_id)


@router.post("/quality/runs/{run_id}/apply", response_model=QualityRunResponse)
async def apply_quality_run(run_id: UUID, session: SessionDependency) -> dict:
    return await QualityGateService(session).apply(run_id)


@router.post("/quality/runs/{run_id}/cancel", response_model=QualityRunResponse)
async def cancel_quality_run(run_id: UUID, session: SessionDependency) -> dict:
    return await QualityGateService(session).cancel(run_id)


@router.post("/claims/{claim_id}/quality/restore", response_model=ClaimListItem)
async def restore_quality_claim(claim_id: UUID, session: SessionDependency) -> dict:
    return await QualityGateService(session).restore(claim_id)


@router.post("/claims/{claim_id}/review", response_model=ClaimListItem)
async def review_claim(
    claim_id: UUID, request: ReviewClaimRequest, session: SessionDependency
) -> dict:
    return await _service(session).review_claim(claim_id, request)


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(request: RetrieveRequest, session: SessionDependency) -> dict:
    return await _service(session).retrieve(request)
