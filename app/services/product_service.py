"""产品服务：列出 + 按 id 取 + 模糊搜索。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_products(
    session: AsyncSession,
    *,
    site_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """返回 { items: [...], total: int, limit, offset }。"""
    where = "WHERE 1=1"
    params: dict[str, Any] = {"limit": max(1, min(limit, 500)), "offset": max(0, offset)}
    if site_id:
        where += " AND site_id = :site_id"
        params["site_id"] = site_id
    if status:
        where += " AND status = :status"
        params["status"] = status
    if category:
        where += " AND category = :category"
        params["category"] = category
    if min_price is not None:
        where += " AND price >= :min_price"
        params["min_price"] = float(min_price)
    if max_price is not None:
        where += " AND price <= :max_price"
        params["max_price"] = float(max_price)
    if search:
        where += " AND (title ILIKE :search OR handle ILIKE :search)"
        params["search"] = f"%{search}%"

    total = (
        await session.execute(
            text(f"SELECT count(*) AS n FROM seo_agent.products {where}"),
            params,
        )
    ).scalar_one()

    result = await session.execute(
        text(
            f"""
            SELECT id, external_id, site_id, handle, title, url, category, price,
                   status, image, description, keywords, source, extracted_at,
                   source_connector_id, canonical_url, meta_title, meta_description,
                   meta_keywords, images, variants, seo_audit, source_updated_at,
                   created_at, updated_at
              FROM seo_agent.products
              {where}
             ORDER BY created_at DESC
             LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return {
        "items": [_row_to_dict(r) for r in result.mappings().all()],
        "total": int(total or 0),
        "limit": params["limit"],
        "offset": params["offset"],
    }


async def get_product(session: AsyncSession, product_id: int) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, external_id, site_id, handle, title, url, category, price, "
            "status, image, description, keywords, source, extracted_at, "
            "source_connector_id, canonical_url, meta_title, meta_description, "
            "meta_keywords, images, variants, seo_audit, source_updated_at, "
            "created_at, updated_at "
            "FROM seo_agent.products WHERE id = :id"
        ),
        {"id": product_id},
    )
    row = result.mappings().first()
    return _row_to_dict(row) if row else None


async def get_product_by_external_id(
    session: AsyncSession, external_id: str, *, site_id: str | None = None
) -> dict[str, Any] | None:
    site_clause = " AND site_id = :site_id" if site_id else ""
    params: dict[str, Any] = {"eid": external_id}
    if site_id:
        params["site_id"] = site_id
    result = await session.execute(
        text(
            "SELECT id, external_id, site_id, handle, title, url, category, price, "
            "status, image, description, keywords, source, extracted_at, "
            "source_connector_id, canonical_url, meta_title, meta_description, "
            "meta_keywords, images, variants, seo_audit, source_updated_at, "
            "created_at, updated_at "
            f"FROM seo_agent.products WHERE external_id = :eid{site_clause} "
            "ORDER BY source_connector_id NULLS FIRST LIMIT 1"
        ),
        params,
    )
    row = result.mappings().first()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    # jsonb 字段反序列化
    for k in ("image", "images", "variants", "seo_audit", "raw"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except (ValueError, TypeError):
                pass
    return d
