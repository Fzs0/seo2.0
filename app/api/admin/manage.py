"""管理面路由：/admin/reload /admin/rule-sets /admin/health。"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.engine.loader import get_store
from app.services.rule_service import RuleServiceError, save_rule_set

router = APIRouter()
logger = structlog.get_logger(__name__)


class RuleSetBody(BaseModel):
    name: str
    version: str
    source: str = "api"
    payload: dict[str, Any]
    notes: str | None = None
    set_active: bool = False
    actor: str | None = None


@router.post("/reload")
async def reload() -> dict[str, Any]:
    store = get_store()
    await store.load_from_db()
    return {"reloaded": True, "version": store.version, "effective_at": str(store.effective_at)}


@router.get("/rule-sets/active")
async def get_active(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    result = await session.execute(
        text(
            "SELECT id, name, version, source, effective_at, created_at "
            "FROM seo_agent.v_active_rule_set LIMIT 1"
        )
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="no active rule set")
    return dict(row)


@router.get("/rule-sets")
async def list_rule_sets(session: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, name, version, source, is_active, effective_at, created_at "
            "FROM seo_agent.rule_sets ORDER BY created_at DESC LIMIT 50"
        )
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]


@router.post("/rule-sets")
async def create_rule_set(body: RuleSetBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    actor = body.actor or "admin:api"
    try:
        info = await save_rule_set(
            session,
            name=body.name,
            version=body.version,
            source=body.source,
            payload=body.payload,
            actor=actor,
            notes=body.notes,
            set_active=body.set_active,
        )
        await session.commit()
        return info
    except RuleServiceError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/health")
async def admin_health(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """比 /api/health 多一项：DB 可达性。"""
    db_ok = True
    db_error: str | None = None
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        db_ok = False
        db_error = str(e)
    return {"ok": db_ok, "db_error": db_error, "rule_version": get_store().version}