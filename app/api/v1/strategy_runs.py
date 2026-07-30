"""Public dry-run orchestration API for SEO strategy runs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, Field, model_validator
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
    submit_strategy_run_local_options,
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


class StrategyRunLocalOptionBody(BaseModel):
    site_id: UUID
    action: Literal[
        "new_article",
        "update_article",
        "on_page_fix",
        "hold",
        "configuration_repair",
    ]
    schedule_class: Literal[
        "execute_now", "deferred", "hold", "configuration_repair"
    ]
    topic: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=500)
    reason: str = Field(min_length=1, max_length=4000)
    user_intent: str = Field(min_length=1, max_length=2000)
    evidence: dict[str, Any] = Field(default_factory=dict)
    candidate_id: UUID | None = None
    keyword_id: UUID | None = None
    post_id: UUID | None = None
    article_id: UUID | None = None
    action_type: Literal[
        "homepage_seo",
        "product_seo",
        "category_seo",
        "product_image_alt",
    ] | None = None
    page_type: Literal["homepage", "product", "category"] | None = None
    target_asset_id: str | None = Field(default=None, min_length=1, max_length=500)
    remote_object_id: str | None = Field(default=None, min_length=1, max_length=1000)
    target_url: str | None = Field(default=None, max_length=3000)
    connector_id: UUID | None = None
    connector_type: str | None = Field(default=None, min_length=1, max_length=100)
    expected_fields: list[str] = Field(default_factory=list, max_length=20)
    priority: Literal["P0", "P1", "P2", "P3", "Hold"] = "P2"
    opportunity_score: float = Field(default=0, ge=0, le=100)
    readiness_score: float = Field(default=0.5, ge=0, le=1)
    risk_score: float = Field(default=0.5, ge=0, le=1)
    risk_level: Literal["low", "medium", "high"] = "medium"
    hypothesis: str | None = Field(default=None, max_length=3000)
    success_metrics: list[str] = Field(default_factory=list, max_length=20)
    rejected_alternatives: list[str] = Field(default_factory=list, max_length=20)
    candidate_influence: str | None = Field(default=None, max_length=1000)
    reevaluate_at: datetime | None = None
    reevaluation_condition: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_action_contract(self):
        if self.action in {"new_article", "update_article"} and not self.topic:
            raise ValueError("article run-local options require topic")
        if (
            self.action == "update_article"
            and not self.post_id
            and not self.article_id
            and not self.target_url
        ):
            raise ValueError(
                "update_article run-local options require a target identity"
            )
        page_type_by_action = {
            "homepage_seo": "homepage",
            "product_seo": "product",
            "category_seo": "category",
            "product_image_alt": "product",
        }
        if self.action == "on_page_fix":
            if not self.action_type:
                raise ValueError(
                    "on_page_fix run-local options require a concrete action_type"
                )
            expected_page_type = page_type_by_action[self.action_type]
            if self.page_type != expected_page_type:
                raise ValueError(
                    f"{self.action_type} requires page_type={expected_page_type}"
                )
            if (
                self.page_type != "homepage"
                and not self.target_asset_id
                and not self.remote_object_id
            ):
                raise ValueError(
                    "product/category on_page_fix options require target_asset_id "
                    "or remote_object_id"
                )
            if not self.target_url:
                raise ValueError(
                    "on_page_fix run-local options require target_url"
                )
            if not self.expected_fields:
                raise ValueError(
                    "on_page_fix run-local options require expected_fields"
                )
        elif any(
            (
                self.action_type,
                self.page_type,
                self.target_asset_id,
                self.remote_object_id,
                self.connector_id,
                self.connector_type,
                self.expected_fields,
            )
        ):
            raise ValueError(
                "concrete on-page target fields require action=on_page_fix"
            )
        expected_schedule = {
            "hold": "hold",
            "configuration_repair": "configuration_repair",
        }.get(self.action)
        if expected_schedule and self.schedule_class != expected_schedule:
            raise ValueError(
                f"{self.action} requires schedule_class={expected_schedule}"
            )
        if self.schedule_class in {"hold", "configuration_repair"} and (
            self.action != self.schedule_class
        ):
            raise ValueError(
                "hold/configuration_repair schedule must match the action"
            )
        if not self.evidence:
            raise ValueError("run-local options require current research evidence")
        return self


class StrategyRunLocalOptionsBody(StrategyRunControlBody):
    options: list[StrategyRunLocalOptionBody] = Field(min_length=1, max_length=200)


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
    except ValueError as error:
        return failure(
            request, status_code=400, code="STRATEGY_RUN_INVALID_REQUEST",
            message=str(error), retryable=False,
        )


@router.post("/{run_id}/run-local-options")
async def submit_strategy_run_local_options_route(
    run_id: str,
    body: StrategyRunLocalOptionsBody,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
):
    """Persist research-driven options before the formal planning stage."""
    try:
        return success(
            request,
            await submit_strategy_run_local_options(
                session,
                run_id=run_id,
                requested_by=body.requested_by.strip(),
                idempotency_key=idempotency_key.strip(),
                options=[
                    option.model_dump(mode="json") for option in body.options
                ],
            ),
        )
    except ValueError as error:
        return failure(
            request,
            status_code=409,
            code="STRATEGY_RUN_OPTIONS_REJECTED",
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
