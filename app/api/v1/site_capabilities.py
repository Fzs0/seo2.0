"""Read-only site capability manifest endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.response_contract import ContractRoute, failure, success
from app.core.database import get_db
from app.services.site_capability_service import (
    get_business_site_capabilities,
    get_site_capabilities,
)

router = APIRouter(route_class=ContractRoute)


@router.get("/businesses/{business_id}/sites/capabilities")
async def business_site_capabilities_route(
    business_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return success(
        request, await get_business_site_capabilities(session, business_id)
    )


@router.get("/sites/{site_id}/capabilities")
async def site_capabilities_route(
    site_id: str,
    request: Request,
    business_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await get_site_capabilities(
        session, site_id, expected_business_id=business_id
    )
    if result is None:
        return failure(
            request,
            status_code=404,
            code="SITE_NOT_FOUND",
            message="Site capability record was not found.",
            details={"site_id": site_id},
        )
    return success(request, result)


__all__ = ["router"]
