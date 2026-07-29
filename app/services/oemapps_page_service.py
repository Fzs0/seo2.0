"""Guarded read and write workflow for OEMApps custom pages."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.oemapps_pages import (
    OemAppsPages,
    build_editable_page,
    page_snapshot_hash,
    prepare_page_update,
)
from app.services.custom_connector_service import load_oemapps_runtime


async def list_oemapps_pages(
    session: AsyncSession, connector_id: str
) -> dict[str, Any]:
    _, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsPages(token)
    try:
        pages = await client.list_pages()
    finally:
        await client.aclose()
    return {
        "items": [_public_page(page) for page in pages],
        "total": len(pages),
    }


async def preview_page_update(
    session: AsyncSession,
    connector_id: str,
    page_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    _, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsPages(token)
    try:
        page = await client.get_page(page_id)
    finally:
        await client.aclose()
    prepared = prepare_page_update(page, patch)
    return {
        "ok": True,
        "page_id": str(page["id"]),
        "title": page.get("title"),
        "expected_snapshot_hash": prepared.snapshot_hash,
        "current": _public_page(page),
        "changes": prepared.changes,
        "change_count": len(prepared.changes),
        "warnings": [
            "OEMApps custom page updates use a complete PUT body.",
            "Execute only after reviewing every reported field change.",
        ],
    }


async def execute_page_update(
    session: AsyncSession,
    connector_id: str,
    page_id: str,
    patch: dict[str, Any],
    *,
    expected_snapshot_hash: str,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise ValueError("confirm must be true for OEMApps custom page PUT")
    _, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsPages(token)
    try:
        before = await client.get_page(page_id)
        actual_hash = page_snapshot_hash(before)
        if actual_hash != expected_snapshot_hash:
            raise ValueError(
                "custom page changed after preview; preview the update again"
            )
        prepared = prepare_page_update(before, patch)
        if not prepared.changes:
            return {
                "ok": True,
                "no_op": True,
                "page_id": str(before["id"]),
                "changes": [],
                "page": _public_page(before),
            }
        await client.update_page(page_id, prepared.body)
        after = await client.get_page(page_id)
        verification_errors = _verify_patch(after, patch)
        return {
            "ok": not verification_errors,
            "no_op": False,
            "page_id": str(after.get("id") or page_id),
            "changes": prepared.changes,
            "page": _public_page(after),
            "verification_errors": verification_errors,
        }
    finally:
        await client.aclose()


def _public_page(page: dict[str, Any]) -> dict[str, Any]:
    return {
        key: page.get(key)
        for key in (
            "id",
            "handle",
            "title",
            "meta_title",
            "meta_descript",
            "meta_keywords",
            "is_default",
            "from_id",
            "from_name",
            "content",
            "created_at",
            "updated_at",
        )
        if key in page
    }


def _verify_patch(page: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    remote_names = {
        "handle": "handle",
        "title": "title",
        "meta_title": "meta_title",
        "meta_description": "meta_descript",
        "meta_keywords": "meta_keywords",
        "is_default": "is_default",
        "from_id": "from_id",
        "from_name": "from_name",
        "content": "content",
    }
    current = build_editable_page(page)
    expected = prepare_page_update(page, patch).body
    for public_name, remote_name in remote_names.items():
        if public_name in patch and current[remote_name] != expected[remote_name]:
            errors.append(f"{public_name} did not match after write")
    return errors


__all__ = [
    "execute_page_update",
    "list_oemapps_pages",
    "preview_page_update",
]
