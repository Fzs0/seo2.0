"""Independent PRD §10 action routes; safe defaults never call production writers."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.strategy_action_service import (
    ActionAdapter,
    SQLActionStore,
    approve_action,
    execute_action_and_reconcile,
    get_action,
    preview_action,
    recover_action_and_reconcile,
    heartbeat_action,
    rollback_action,
    rollback_preview,
)
from app.services.strategy_article_action_adapter import (
    get_article_generation_context,
    preflight_article_action,
    upload_strategy_article_image,
)
from app.services.strategy_action_adapter_router import (
    StrategyActionAdapterRouter,
)
from app.clients.publishers import ImageUploadRequest
from app.api.v1.response_contract import ContractRoute, failure, success

router = APIRouter(
    prefix="/strategy-actions",
    tags=["strategy-actions"],
    route_class=ContractRoute,
)


def get_action_adapter(
    session: AsyncSession = Depends(get_db),
) -> ActionAdapter:
    """Use exact platform/action routing; unknown pairs remain zero-write blocked."""
    return StrategyActionAdapterRouter(session)


class PreviewBody(BaseModel):
    patch: dict[str, Any] = Field(min_length=1)
    capability_snapshot_hash: str = Field(min_length=16)
    generation_mode: str | None = None
    generation_provider: str | None = None
    generation_model: str | None = None
    generation_run_id: str | None = None


class ApproveBody(BaseModel):
    snapshot_hash: str = Field(min_length=16)
    patch_hash: str = Field(min_length=16)
    generation_mode: str
    generation_provider: str | None = None
    generation_model: str | None = None
    generation_run_id: str | None = None
    capability_snapshot_hash: str = Field(min_length=16)
    side_effect_confirmations: dict[str, bool] = Field(default_factory=dict)


class RollbackBody(BaseModel):
    confirm: bool


class HeartbeatBody(BaseModel):
    execution_token: str = Field(min_length=16)
    lease_seconds: int = Field(default=300, ge=1, le=3600)


class StrategyImageUploadBody(BaseModel):
    type: str
    url: str | None = None
    file: str | None = None
    base64: str | None = None
    filename: str | None = Field(default=None, max_length=180)
    alt_text: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=500)
    caption: str | None = Field(default=None, max_length=2000)
    dry_run: bool = True


@router.get("/{action_id}")
async def get_strategy_action(
    action_id: str, request: Request, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await _call(request, get_action, SQLActionStore(session), action_id=action_id)


@router.get("/{action_id}/generation-context")
async def get_strategy_action_generation_context(
    action_id: str,
    request: Request,
    preflight_token: str = Header(
        min_length=16, alias="X-Strategy-Preflight-Token"
    ),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return success(
            request,
            await get_article_generation_context(
                session,
                action_id=action_id,
                preflight_token=preflight_token,
            ),
        )
    except ValueError as error:
        return failure(
            request,
            status_code=409,
            code="STRATEGY_ACTION_CONTEXT_UNAVAILABLE",
            message=str(error),
            retryable=False,
        )


@router.post("/{action_id}/preflight")
async def preflight_strategy_article_action(
    action_id: str,
    request: Request,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return success(
            request,
            await preflight_article_action(
                session,
                action_id=action_id,
                idempotency_key=idempotency_key,
            ),
        )
    except ValueError as error:
        return failure(
            request,
            status_code=409,
            code="STRATEGY_ACTION_PREFLIGHT_BLOCKED",
            message=str(error),
            retryable=False,
        )


@router.post("/{action_id}/images/upload")
async def upload_strategy_article_image_route(
    action_id: str,
    body: StrategyImageUploadBody,
    request: Request,
    preflight_token: str = Header(
        min_length=16, alias="X-Strategy-Preflight-Token"
    ),
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return success(
            request,
            await upload_strategy_article_image(
                session,
                action_id=action_id,
                preflight_token=preflight_token,
                idempotency_key=idempotency_key,
                request=ImageUploadRequest(
                    type=body.type,
                    url=body.url,
                    file=body.file,
                    base64=body.base64,
                    filename=body.filename,
                    alt_text=body.alt_text,
                    title=body.title,
                    caption=body.caption,
                ),
                dry_run=body.dry_run,
            ),
        )
    except ValueError as error:
        return failure(
            request,
            status_code=409,
            code="STRATEGY_ACTION_MEDIA_UPLOAD_BLOCKED",
            message=str(error),
            retryable=False,
        )
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
        generation_mode=body.generation_mode,
        generation_provider=body.generation_provider,
        generation_model=body.generation_model,
        generation_run_id=body.generation_run_id,
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
        side_effect_confirmations=body.side_effect_confirmations,
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
        execute_action_and_reconcile,
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
        recover_action_and_reconcile,
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
