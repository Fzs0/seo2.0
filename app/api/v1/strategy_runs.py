"""Public dry-run orchestration API for SEO strategy runs."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.v1.response_contract import ContractRoute, failure, success
from app.services.strategy_run_service import (
    cancel_strategy_run,
    create_strategy_run,
    get_strategy_run,
    list_strategy_run_events,
    list_strategy_runs,
    retry_strategy_run,
    run_strategy_run,
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
    action_budget: int = Field(default=4, ge=0, le=200)
    site_quotas: dict[str, int] = Field(default_factory=dict)
    approval_policy: Literal["use_site_capabilities"] = "use_site_capabilities"


class StrategyRunControlBody(BaseModel):
    requested_by: str = Field(default="codex", min_length=1, max_length=100)


class StrategyRunRetryBody(StrategyRunControlBody):
    idempotency_key: str = Field(min_length=8, max_length=300)


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
