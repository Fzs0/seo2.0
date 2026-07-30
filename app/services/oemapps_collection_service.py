"""Persistence and guarded SEO writes for OEMApps product collections."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.oemapps_collections import (
    OemAppsCollections,
    collection_snapshot_hash,
    derive_collection_members,
    prepare_collection_seo_update,
)
from app.services.custom_connector_service import load_oemapps_runtime


async def sync_oemapps_collections(
    session: AsyncSession, connector_id: str
) -> dict[str, Any]:
    stored, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsCollections(token)
    try:
        summaries = await client.list_all_collections()
        products = await client.list_all_products()
        details = []
        for summary in summaries:
            detail = await client.get_collection(summary["id"])
            details.append({**summary, **detail})
    finally:
        await client.aclose()
    prepared: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for detail in details:
        prepared.append((detail, derive_collection_members(detail, products)))
    async with session.begin():
        for detail, members in prepared:
            await _upsert_collection(session, stored, detail, members)
    return {
        "ok": True,
        "collections_received": len(summaries),
        "collections_upserted": len(prepared),
        "products_scanned": len(products),
        "seo_gaps": {
            "missing_meta_title": sum(not str(item.get("meta_title") or "").strip() for item, _ in prepared),
            "missing_meta_description": sum(
                not str(item.get("meta_descript") or "").strip() for item, _ in prepared
            ),
        },
    }


async def list_synced_collections(
    session: AsyncSession, connector_id: str, *, limit: int = 200
) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, external_id, site_id, source_connector_id, title, handle, url,
                       top_description, bottom_description, meta_title, meta_description,
                       meta_keywords, image_url, sort_order, manual_mode, product_count,
                       member_product_ids, seo_audit, source_updated_at, extracted_at, updated_at
                  FROM seo_agent.product_collections
                 WHERE source_connector_id = :connector_id
              ORDER BY title
                 LIMIT :limit
                """
            ),
            {"connector_id": connector_id, "limit": limit},
        )
    ).mappings().all()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


async def preview_collection_seo_update(
    session: AsyncSession,
    connector_id: str,
    collection_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    _, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsCollections(token)
    try:
        collection = await client.get_collection(collection_id)
        products = await client.list_all_products()
    finally:
        await client.aclose()
    members = derive_collection_members(collection, products)
    prepared = prepare_collection_seo_update(collection, members, patch)
    return {
        "ok": True,
        "collection_id": str(collection["id"]),
        "title": collection.get("title"),
        "current": {
            "meta_title": collection.get("meta_title") or "",
            "meta_description": collection.get("meta_descript") or "",
            "meta_keywords": collection.get("meta_keywords") or [],
        },
        "expected_snapshot_hash": prepared.snapshot_hash,
        "changes": prepared.changes,
        "change_count": len(prepared.changes),
        "member_product_ids": _member_ids(members),
        "warnings": [
            "OEMApps collection PUT requires the complete member list.",
            "The read API does not expose membership is_top flags; execution submits is_top=0.",
        ],
    }


async def execute_collection_seo_update(
    session: AsyncSession,
    connector_id: str,
    collection_id: str,
    patch: dict[str, Any],
    *,
    expected_snapshot_hash: str,
    confirm_membership_top_reset: bool,
) -> dict[str, Any]:
    if not confirm_membership_top_reset:
        raise ValueError("confirm_membership_top_reset must be true for OEMApps collection PUT")
    stored, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsCollections(token)
    run_id: Any = None
    try:
        before = await client.get_collection(collection_id)
        products_before = await client.list_all_products()
        members_before = derive_collection_members(before, products_before)
        actual_hash = collection_snapshot_hash(before, members_before)
        if actual_hash != expected_snapshot_hash:
            raise ValueError("collection changed after preview; preview the SEO update again")
        prepared = prepare_collection_seo_update(before, members_before, patch)
        if not prepared.changes:
            return {
                "ok": True,
                "no_op": True,
                "collection_id": str(before["id"]),
                "changes": [],
            }
        before_snapshot = {"collection": before, "members": members_before}
        async with session.begin():
            run_id = (
                await session.execute(
                    text(
                        """
                        INSERT INTO seo_agent.collection_seo_update_runs
                            (connector_id, collection_external_id, status, requested_patch,
                             approved_changes, expected_snapshot_hash, before_snapshot,
                             member_product_ids_before)
                        VALUES (:connector_id, :collection_id, 'running', CAST(:patch AS jsonb),
                                CAST(:changes AS jsonb), :snapshot_hash, CAST(:before AS jsonb),
                                CAST(:member_ids AS jsonb))
                        RETURNING id
                        """
                    ),
                    {
                        "connector_id": connector_id,
                        "collection_id": str(collection_id),
                        "patch": json.dumps(patch, ensure_ascii=False),
                        "changes": json.dumps(prepared.changes, ensure_ascii=False),
                        "snapshot_hash": actual_hash,
                        "before": json.dumps(before_snapshot, ensure_ascii=False, default=str),
                        "member_ids": json.dumps(_member_ids(members_before)),
                    },
                )
            ).scalar_one()
        await client.update_collection(collection_id, prepared.body)
        after = await client.get_collection(collection_id)
        products_after = await client.list_all_products()
        members_after = derive_collection_members(after, products_after)
        verification_errors = _verify_patch(after, patch)
        if _member_ids(members_before) != _member_ids(members_after):
            verification_errors.append("collection membership changed after write")
        status = "verification_failed" if verification_errors else "succeeded"
        after_snapshot = {"collection": after, "members": members_after}
        async with session.begin():
            await session.execute(
                text(
                    """
                    UPDATE seo_agent.collection_seo_update_runs
                       SET status = :status, after_snapshot = CAST(:after AS jsonb),
                           member_product_ids_after = CAST(:member_ids AS jsonb),
                           error_summary = :error, finished_at = now()
                     WHERE id = :id
                    """
                ),
                {
                    "id": run_id,
                    "status": status,
                    "after": json.dumps(after_snapshot, ensure_ascii=False, default=str),
                    "member_ids": json.dumps(_member_ids(members_after)),
                    "error": "; ".join(verification_errors) or None,
                },
            )
            await _upsert_collection(session, stored, after, members_after)
        return {
            "ok": not verification_errors,
            "no_op": False,
            "run_id": str(run_id),
            "collection_id": str(after.get("id") or collection_id),
            "changes": prepared.changes,
            "verification_errors": verification_errors,
            "member_product_ids_before": _member_ids(members_before),
            "member_product_ids_after": _member_ids(members_after),
        }
    except Exception as error:
        if run_id is not None:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE seo_agent.collection_seo_update_runs
                           SET status = 'failed', error_summary = :error, finished_at = now()
                         WHERE id = :id AND status = 'running'
                        """
                    ),
                    {"id": run_id, "error": str(error)[:1000]},
                )
        raise
    finally:
        await client.aclose()


async def list_collection_update_runs(
    session: AsyncSession, connector_id: str, *, limit: int = 50
) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, collection_external_id, status, requested_patch,
                       approved_changes, expected_snapshot_hash,
                       member_product_ids_before, member_product_ids_after,
                       error_summary, started_at, finished_at
                  FROM seo_agent.collection_seo_update_runs
                 WHERE connector_id = :connector_id
              ORDER BY started_at DESC
                 LIMIT :limit
                """
            ),
            {"connector_id": connector_id, "limit": limit},
        )
    ).mappings().all()
    return {
        "items": [{**dict(row), "id": str(row["id"])} for row in rows],
        "total": len(rows),
    }


async def _upsert_collection(
    session: AsyncSession,
    stored: dict[str, Any],
    collection: dict[str, Any],
    members: list[dict[str, Any]],
) -> None:
    meta_keywords = _string_list(collection.get("meta_keywords"))
    seo_audit = {
        "missing_tdk": [
            field
            for field, value in (
                ("title", collection.get("title")),
                ("meta_title", collection.get("meta_title")),
                ("meta_description", collection.get("meta_descript")),
            )
            if not str(value or "").strip()
        ],
        "missing_top_description": not bool(str(collection.get("top_descript") or "").strip()),
        "missing_bottom_description": not bool(
            str(collection.get("bottom_descript") or "").strip()
        ),
    }
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.product_collections
                (site_id, source_connector_id, external_id, title, handle, url,
                 top_description, bottom_description, meta_title, meta_description,
                 meta_keywords, image_url, sort_order, manual_mode, product_count,
                 member_product_ids, seo_audit, source_updated_at, extracted_at)
            VALUES
                (:site_id, :connector_id, :external_id, :title, :handle, :url,
                 :top_description, :bottom_description, :meta_title, :meta_description,
                 :meta_keywords, :image_url, CAST(:sort_order AS jsonb), :manual_mode,
                 :product_count, CAST(:member_ids AS jsonb), CAST(:seo_audit AS jsonb),
                 :source_updated_at, now())
            ON CONFLICT (source_connector_id, external_id) DO UPDATE SET
                title = EXCLUDED.title, handle = EXCLUDED.handle, url = EXCLUDED.url,
                top_description = EXCLUDED.top_description,
                bottom_description = EXCLUDED.bottom_description,
                meta_title = EXCLUDED.meta_title, meta_description = EXCLUDED.meta_description,
                meta_keywords = EXCLUDED.meta_keywords, image_url = EXCLUDED.image_url,
                sort_order = EXCLUDED.sort_order, manual_mode = EXCLUDED.manual_mode,
                product_count = EXCLUDED.product_count,
                member_product_ids = EXCLUDED.member_product_ids,
                seo_audit = EXCLUDED.seo_audit,
                source_updated_at = EXCLUDED.source_updated_at, extracted_at = now(),
                updated_at = now()
            """
        ),
        {
            "site_id": stored["site_id"],
            "connector_id": stored["id"],
            "external_id": str(collection["id"]),
            "title": collection["title"],
            "handle": collection.get("handle"),
            "url": collection.get("detail_url"),
            "top_description": collection.get("top_descript"),
            "bottom_description": collection.get("bottom_descript"),
            "meta_title": collection.get("meta_title"),
            "meta_description": collection.get("meta_descript"),
            "meta_keywords": meta_keywords,
            "image_url": collection.get("src"),
            "sort_order": json.dumps(collection.get("sort_order") or {}, ensure_ascii=False),
            "manual_mode": int(collection.get("manual_mod_index") or 0),
            "product_count": len(members),
            "member_ids": json.dumps(_member_ids(members)),
            "seo_audit": json.dumps(seo_audit, ensure_ascii=False),
            "source_updated_at": _source_datetime(collection.get("updated_at")),
        },
    )


def _verify_patch(collection: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    remote = {
        "meta_title": "meta_title",
        "meta_description": "meta_descript",
        "meta_keywords": "meta_keywords",
    }
    for public_name, remote_name in remote.items():
        if public_name in patch and collection.get(remote_name) != patch[public_name]:
            errors.append(f"{public_name} did not match after write")
    return errors


def _member_ids(members: list[dict[str, Any]]) -> list[str]:
    return [str(item["id"]) for item in members]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text_value = str(value or "").strip()
    if not text_value:
        return []
    if text_value.startswith("["):
        parsed = json.loads(text_value)
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text_value.split(",") if item.strip()]


def _source_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


__all__ = [
    "execute_collection_seo_update",
    "list_collection_update_runs",
    "list_synced_collections",
    "preview_collection_seo_update",
    "sync_oemapps_collections",
]
