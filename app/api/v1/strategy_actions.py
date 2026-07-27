"""Independent PRD §10 action routes; safe defaults never call production writers."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.strategy_action_service import (
    ActionAdapter,
    BlockedActionAdapter,
    SQLActionStore,
    approve_action,
    execute_action,
    get_action,
    preview_action,
    recover_action,
    heartbeat_action,
    rollback_action,
    rollback_preview,
)
from app.api.v1.response_contract import ContractRoute, failure, success

router = APIRouter(
    prefix="/strategy-actions",
    tags=["strategy-actions"],
    route_class=ContractRoute,
)


def get_action_adapter() -> ActionAdapter:
    """Override at application composition time with a reviewed article/on-page adapter."""
    return BlockedActionAdapter()


class PreviewBody(BaseModel):
    patch: dict[str, Any] = Field(min_length=1)
    capability_snapshot_hash: str = Field(min_length=16)


class ApproveBody(BaseModel):
    snapshot_hash: str = Field(min_length=16)
    patch_hash: str = Field(min_length=16)
    generation_mode: str
    generation_provider: str | None = None
    generation_model: str | None = None
    generation_run_id: str | None = None
    capability_snapshot_hash: str = Field(min_length=16)


class RollbackBody(BaseModel):
    confirm: bool


class HeartbeatBody(BaseModel):
    execution_token: str = Field(min_length=16)
    lease_seconds: int = Field(default=300, ge=1, le=3600)


@router.get("/{action_id}")
async def get_strategy_action(
    action_id: str, request: Request, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await _call(request, get_action, SQLActionStore(session), action_id=action_id)


@router.post("/{action_id}/preview")
async def preview_strategy_action(
    action_id: str,
    body: PreviewBody,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
    adapter: ActionAdapter = Depends(get_action_adapter),
) -> dict[str, Any]:
    return await _call(
        request,
        preview_action,
        SQLActionStore(session),
        action_id=action_id,
        patch=body.patch,
        adapter=adapter,
        capability_snapshot_hash=body.capability_snapshot_hash,
        idempotency_key=idempotency_key,
    )


@router.post("/{action_id}/approve")
async def approve_strategy_action(
    action_id: str,
    body: ApproveBody,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _call(
        request,
        approve_action,
        SQLActionStore(session),
        action_id=action_id,
        snapshot_hash=body.snapshot_hash,
        patch_hash=body.patch_hash,
        generation_mode=body.generation_mode,
        generation_provider=body.generation_provider,
        generation_model=body.generation_model,
        generation_run_id=body.generation_run_id,
        capability_snapshot_hash=body.capability_snapshot_hash,
        idempotency_key=idempotency_key,
    )


@router.post("/{action_id}/execute")
async def execute_strategy_action(
    action_id: str,
    request: Request,
    capability_snapshot_hash: str = Header(min_length=16, alias="X-Capability-Snapshot-Hash"),
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
    adapter: ActionAdapter = Depends(get_action_adapter),
) -> dict[str, Any]:
    return await _call(
        request,
        execute_action,
        SQLActionStore(session),
        action_id=action_id,
        adapter=adapter,
        capability_snapshot_hash=capability_snapshot_hash,
        idempotency_key=idempotency_key,
    )


@router.post("/{action_id}/recover")
async def recover_strategy_action(
    action_id: str,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
    adapter: ActionAdapter = Depends(get_action_adapter),
) -> dict[str, Any]:
    return await _call(
        request,
        recover_action,
        SQLActionStore(session),
        action_id=action_id,
        adapter=adapter,
        idempotency_key=idempotency_key,
    )


@router.post("/{action_id}/heartbeat")
async def heartbeat_strategy_action(
    action_id: str,
    body: HeartbeatBody,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _call(
        request,
        heartbeat_action,
        SQLActionStore(session),
        action_id=action_id,
        execution_token=body.execution_token,
        lease_seconds=body.lease_seconds,
        idempotency_key=idempotency_key,
    )


@router.post("/{action_id}/rollback-preview")
async def preview_strategy_action_rollback(
    action_id: str,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _call(
        request,
        rollback_preview,
        SQLActionStore(session),
        action_id=action_id,
        idempotency_key=idempotency_key,
    )


@router.post("/{action_id}/rollback")
async def rollback_strategy_action(
    action_id: str,
    body: RollbackBody,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _call(
        request,
        rollback_action,
        SQLActionStore(session),
        action_id=action_id,
        confirm=body.confirm,
        idempotency_key=idempotency_key,
    )


async def _call(request: Request, function, store: SQLActionStore, **kwargs):
    try:
        return success(request, await function(store, **kwargs))
    except ValueError as error:
        return failure(
            request,
            status_code=409,
            code="STRATEGY_ACTION_CONFLICT",
            message=str(error),
            retryable=False,
        )


__all__ = ["router"]
