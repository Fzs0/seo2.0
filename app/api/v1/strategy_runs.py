"""Public Interface for the one AI-led SEO Strategy Run chain."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.v1.response_contract import ContractRoute, failure, success
from app.services.strategy_run_service import (
    cancel_strategy_run,
    create_strategy_run,
    get_strategy_run,
    list_strategy_run_events,
    list_strategy_runs,
    reconcile_strategy_run,
    retry_strategy_run,
    run_strategy_run,
)
from app.services.autonomous_strategy_orchestrator import (
    StrategyContractError,
    capture_research_portfolio,
    review_zero_action_run,
    submit_proposed_actions,
)


router = APIRouter(
    prefix="/strategy-runs",
    tags=["strategy-runs"],
    route_class=ContractRoute,
)


class StrategyRunCreateBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    site_ids: list[str] | None = None
    scope: Literal["all_sites", "selected_sites"] = "all_sites"
    mode: Literal["dry_run", "approval_execution"] = "dry_run"
    requested_by: str = Field(default="codex", min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=300)
    action_budget: int = Field(
        default=200,
        ge=0,
        le=200,
        description=(
            "Runaway safety ceiling only. It does not limit research, ranking, "
            "or qualification; overflow is persisted as deferred."
        ),
    )
    site_quotas: dict[str, int] = Field(default_factory=dict)
    approval_policy: Literal["use_site_capabilities"] = "use_site_capabilities"


class StrategyRunControlBody(BaseModel):
    requested_by: str = Field(default="codex", min_length=1, max_length=100)


class StrategyRunRetryBody(StrategyRunControlBody):
    idempotency_key: str = Field(min_length=8, max_length=300)


class EvidenceSourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal[
        "gsc",
        "ga4",
        "site_api",
        "serpapi",
        "public_search",
        "semrush_ui",
        "other",
    ]
    source_name: str = Field(min_length=1, max_length=300)
    captured_at: datetime
    data_window: dict[str, Any] = Field(default_factory=dict)
    market: str | None = Field(default=None, max_length=50)
    language: str | None = Field(default=None, max_length=50)
    device: str | None = Field(default=None, max_length=50)
    dimensions: list[str] = Field(default_factory=list, max_length=50)
    filters: dict[str, Any] = Field(default_factory=dict)
    freshness: Literal["current", "recent", "lagging", "unknown"] = "unknown"
    fact_scope: Literal[
        "product_fact",
        "first_party_performance",
        "user_behavior",
        "intent",
        "competitor_estimate",
        "other",
    ] = "other"
    artifact_refs: list[str] = Field(default_factory=list, max_length=100)
    collection_status: Literal["success", "partial", "empty", "failed"]
    limitations: list[str] = Field(default_factory=list, max_length=50)
    decision_use: str = Field(min_length=1, max_length=2000)


class ResearchTargetIdentityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_url: str | None = Field(default=None, max_length=3000)
    remote_object_id: str | None = Field(default=None, max_length=1000)
    local_object_id: str | None = Field(default=None, max_length=1000)
    intent_key: str | None = Field(default=None, max_length=1000)
    topic_cluster: str | None = Field(default=None, max_length=1000)


class ResearchMaterialOptionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=1, max_length=300)
    action: Literal[
        "new_article",
        "update_article",
        "on_page_fix",
    ]
    action_type: Literal[
        "homepage_seo",
        "product_seo",
        "category_seo",
        "product_image_alt",
    ] | None = None
    target_identity: ResearchTargetIdentityBody
    user_intent: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)
    outcome: Literal["qualified", "rejected", "blocked"]
    reason: str = Field(min_length=1, max_length=3000)
    blocker_code: str | None = Field(default=None, max_length=200)
    block_scope: Literal["url", "topic"] | None = None


class OpportunityExhaustionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surfaces_checked: list[str] = Field(default_factory=list, max_length=50)
    evaluated_option_ids: list[str] = Field(default_factory=list, max_length=100)
    conclusion: str | None = Field(default=None, max_length=3000)


class ActionAssessmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=3000)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)


class SiteResearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: UUID
    site_language: str | None = Field(default=None, max_length=50)
    site_market: str | None = Field(default=None, max_length=50)
    evidence_snapshot_id: str | None = Field(default=None, max_length=200)
    research_questions: list[str] = Field(default_factory=list, max_length=100)
    actions_considered: list[
        Literal[
            "new_article",
            "update_article",
            "on_page_fix",
            "hold",
            "configuration_repair",
        ]
    ] = Field(default_factory=list, max_length=5)
    material_options: list[ResearchMaterialOptionBody] = Field(
        default_factory=list, max_length=100
    )
    opportunity_exhaustion: OpportunityExhaustionBody = Field(
        default_factory=OpportunityExhaustionBody
    )
    action_assessments: dict[
        Literal[
            "new_article",
            "update_article",
            "on_page_fix",
            "hold",
            "configuration_repair",
        ],
        ActionAssessmentBody,
    ] = Field(default_factory=dict, max_length=5)
    hard_blockers: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    sources_attempted: list[str] = Field(default_factory=list, max_length=100)
    evidence_sources: list[EvidenceSourceBody] = Field(
        default_factory=list, max_length=200
    )
    missing_evidence: list[str] = Field(default_factory=list, max_length=100)
    evidence_conflicts: list[dict[str, Any]] = Field(
        default_factory=list, max_length=100
    )
    research_conclusion: str = Field(min_length=1, max_length=5000)


class ResearchPortfolioBody(StrategyRunControlBody):
    model_config = ConfigDict(extra="forbid")

    evidence_snapshot_id: str = Field(min_length=1, max_length=200)
    portfolio: list[SiteResearchBody] = Field(min_length=1, max_length=500)


class TargetIdentityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_url: str | None = Field(default=None, max_length=3000)
    remote_object_id: str | None = Field(default=None, max_length=1000)
    local_object_id: str | None = Field(default=None, max_length=1000)
    intent_key: str | None = Field(default=None, max_length=1000)
    topic_cluster: str | None = Field(default=None, max_length=1000)


class ProposedActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: UUID
    action: Literal[
        "new_article",
        "update_article",
        "on_page_fix",
        "hold",
        "configuration_repair",
    ]
    action_type: Literal[
        "homepage_seo",
        "product_seo",
        "category_seo",
        "product_image_alt",
    ] | None = None
    target_identity: TargetIdentityBody = Field(default_factory=TargetIdentityBody)
    schedule_request: Literal[
        "execute_now", "deferred", "hold", "configuration_repair"
    ]
    topic: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=500)
    user_intent: str = Field(min_length=1, max_length=2000)
    decision_reason: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)
    alternatives_considered: list[dict[str, Any]] = Field(
        default_factory=list, max_length=100
    )
    hypothesis: str | None = Field(default=None, max_length=3000)
    success_metrics: list[str] = Field(default_factory=list, max_length=50)
    reevaluation_condition: str | None = Field(default=None, max_length=2000)
    priority: Literal["P0", "P1", "P2", "P3", "Hold"] = "P2"
    risk_level: Literal["low", "medium", "high"] = "medium"
    page_type: Literal["homepage", "product", "category"] | None = None
    target_asset_id: str | None = Field(default=None, max_length=1000)
    corrective_of_action_id: UUID | None = None
    connector_id: UUID | None = None
    connector_type: str | None = Field(default=None, max_length=100)
    expected_fields: list[str] = Field(default_factory=list, max_length=50)


class ProposedActionsBody(StrategyRunControlBody):
    model_config = ConfigDict(extra="forbid")

    actions: list[ProposedActionBody] = Field(min_length=1, max_length=500)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_strategy_run_route(
    body: StrategyRunCreateBody,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    try:
        run = await create_strategy_run(
            session,
            business_id=body.business_id.strip(),
            site_ids=body.site_ids,
            scope=body.scope,
            mode=body.mode,
            requested_by=body.requested_by.strip(),
            idempotency_key=body.idempotency_key.strip(),
            action_budget=body.action_budget,
            site_quotas=body.site_quotas,
            approval_policy=body.approval_policy,
        )
        return success(request, run, status_code=status.HTTP_201_CREATED)
    except StrategyContractError as error:
        return failure(
            request,
            status_code=409,
            code=error.code,
            message=str(error),
            retryable=False,
        )
    except ValueError as error:
        return failure(
            request, status_code=400, code="STRATEGY_RUN_INVALID_REQUEST",
            message=str(error), retryable=False,
        )


@router.post("/{run_id}/research-portfolio")
async def capture_research_portfolio_route(
    run_id: str,
    body: ResearchPortfolioBody,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
):
    """Bind auditable, site-complete AI research to the current evidence."""
    try:
        return success(
            request,
            await capture_research_portfolio(
                session,
                run_id=run_id,
                requested_by=body.requested_by.strip(),
                idempotency_key=idempotency_key.strip(),
                evidence_snapshot_id=body.evidence_snapshot_id,
                portfolio=[
                    item.model_dump(mode="json") for item in body.portfolio
                ],
            ),
        )
    except StrategyContractError as error:
        return failure(
            request,
            status_code=409,
            code=error.code,
            message=str(error),
            retryable=False,
        )


@router.post("/{run_id}/proposed-actions")
async def submit_proposed_actions_route(
    run_id: str,
    body: ProposedActionsBody,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
):
    """Submit AI editorial decisions for backend-only safety review."""
    try:
        return success(
            request,
            await submit_proposed_actions(
                session,
                run_id=run_id,
                requested_by=body.requested_by.strip(),
                idempotency_key=idempotency_key.strip(),
                actions=[
                    item.model_dump(mode="json") for item in body.actions
                ],
            ),
        )
    except StrategyContractError as error:
        return failure(
            request,
            status_code=409,
            code=error.code,
            message=str(error),
            retryable=False,
        )


@router.post("/{run_id}/zero-action-review")
async def review_zero_action_route(
    run_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Return the persisted backend zero-action review for the Run."""
    try:
        return success(
            request,
            await review_zero_action_run(session, run_id=run_id),
        )
    except StrategyContractError as error:
        return failure(
            request,
            status_code=409,
            code=error.code,
            message=str(error),
            retryable=False,
        )


@router.get("")
async def list_strategy_runs_route(
    request: Request,
    business_id: str | None = None,
    run_status: str | None = Query(default=None, alias="status"),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
):
    try:
        items = await list_strategy_runs(
            session,
            business_id=business_id,
            status=run_status,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
        )
        return success(request, {"items": items})
    except ValueError as error:
        return failure(
            request, status_code=400, code="STRATEGY_RUN_INVALID_FILTER",
            message=str(error), retryable=False,
        )


@router.get("/{run_id}")
async def get_strategy_run_route(
    run_id: str, request: Request, session: AsyncSession = Depends(get_db)
):
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        return failure(
            request, status_code=404, code="STRATEGY_RUN_NOT_FOUND",
            message="Strategy run not found.", retryable=False,
        )
    return success(request, run)


@router.get("/{run_id}/events")
async def list_strategy_run_events_route(
    run_id: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_db),
):
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        return failure(
            request, status_code=404, code="STRATEGY_RUN_NOT_FOUND",
            message="Strategy run not found.", retryable=False,
        )
    return success(
        request,
        {"items": await list_strategy_run_events(session, run_id=run_id, limit=limit)},
    )


@router.post("/{run_id}/cancel")
async def cancel_strategy_run_route(
    run_id: str,
    body: StrategyRunControlBody,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
):
    try:
        return success(
            request,
            await cancel_strategy_run(
                session, run_id=run_id, requested_by=body.requested_by,
                idempotency_key=idempotency_key,
            ),
        )
    except ValueError as error:
        return failure(
            request, status_code=409, code="STRATEGY_RUN_CANCEL_REJECTED",
            message=str(error), retryable=False,
        )


@router.post("/{run_id}/retry", status_code=status.HTTP_201_CREATED)
async def retry_strategy_run_route(
    run_id: str,
    body: StrategyRunRetryBody,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    try:
        return success(
            request,
            await retry_strategy_run(
                session,
                run_id=run_id,
                requested_by=body.requested_by,
                idempotency_key=body.idempotency_key,
            ),
            status_code=status.HTTP_201_CREATED,
        )
    except ValueError as error:
        return failure(
            request, status_code=409, code="STRATEGY_RUN_RETRY_REJECTED",
            message=str(error), retryable=False,
        )


@router.post("/{run_id}/start")
async def start_strategy_run_route(
    run_id: str,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
):
    try:
        return success(
            request,
            await run_strategy_run(
                session, run_id=run_id, idempotency_key=idempotency_key
            ),
        )
    except ValueError as error:
        return failure(
            request, status_code=409, code="STRATEGY_RUN_START_REJECTED",
            message=str(error), retryable=False,
        )


@router.post("/{run_id}/reconcile")
async def reconcile_strategy_run_route(
    run_id: str,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
):
    """Idempotent operator fallback; normal Action execution calls this internally."""
    try:
        return success(
            request,
            await reconcile_strategy_run(
                session, run_id=run_id, idempotency_key=idempotency_key
            ),
        )
    except ValueError as error:
        return failure(
            request,
            status_code=409,
            code="STRATEGY_RUN_RECONCILE_REJECTED",
            message=str(error),
            retryable=False,
        )
