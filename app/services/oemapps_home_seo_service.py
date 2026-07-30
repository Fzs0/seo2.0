"""Persistence and guarded writes for OEMApps homepage SEO."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.oemapps_home_seo import (
    OemAppsHomeSeo,
    home_seo_snapshot_hash,
    prepare_home_seo_update,
)
from app.services.custom_connector_service import load_oemapps_runtime


async def sync_oemapps_home_seo(
    session: AsyncSession, connector_id: str
) -> dict[str, Any]:
    stored, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsHomeSeo(token)
    try:
        current = await client.get_home_seo()
    finally:
        await client.aclose()
    async with session.begin():
        await _upsert_home_seo(session, stored, current)
    return {"ok": True, "home_seo": current, "seo_audit": _seo_audit(current)}


async def get_synced_home_seo(
    session: AsyncSession, connector_id: str
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id, site_id, source_connector_id, meta_title, meta_description,
                       meta_keywords, seo_audit, extracted_at, created_at, updated_at
                  FROM seo_agent.site_home_seo
                 WHERE source_connector_id = :connector_id
                """
            ),
            {"connector_id": connector_id},
        )
    ).mappings().one_or_none()
    return dict(row) if row else None


async def preview_home_seo_update(
    session: AsyncSession, connector_id: str, patch: dict[str, Any]
) -> dict[str, Any]:
    _, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsHomeSeo(token)
    try:
        current = await client.get_home_seo()
    finally:
        await client.aclose()
    prepared = prepare_home_seo_update(current, patch)
    return {
        "ok": True,
        "expected_snapshot_hash": prepared.snapshot_hash,
        "current": current,
        "current_public": {
            "meta_title": current.get("meta_title") or "",
            "meta_description": current.get("meta_descript") or "",
            "meta_keywords": current.get("meta_keywords") or [],
        },
        "changes": prepared.changes,
        "change_count": len(prepared.changes),
    }


async def execute_home_seo_update(
    session: AsyncSession,
    connector_id: str,
    patch: dict[str, Any],
    *,
    expected_snapshot_hash: str,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise ValueError("confirm must be true for homepage SEO PUT")
    stored, token = await load_oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsHomeSeo(token)
    run_id: Any = None
    try:
        before = await client.get_home_seo()
        actual_hash = home_seo_snapshot_hash(before)
        if actual_hash != expected_snapshot_hash:
            raise ValueError("homepage SEO changed after preview; preview the update again")
        prepared = prepare_home_seo_update(before, patch)
        if not prepared.changes:
            return {"ok": True, "no_op": True, "changes": [], "home_seo": before}
        async with session.begin():
            run_id = (
                await session.execute(
                    text(
                        """
                        INSERT INTO seo_agent.home_seo_update_runs
                            (connector_id, status, requested_patch, approved_changes,
                             expected_snapshot_hash, before_snapshot)
                        VALUES (:connector_id, 'running', CAST(:patch AS jsonb),
                                CAST(:changes AS jsonb), :snapshot_hash, CAST(:before AS jsonb))
                        RETURNING id
                        """
                    ),
                    {
                        "connector_id": connector_id,
                        "patch": json.dumps(patch, ensure_ascii=False),
                        "changes": json.dumps(prepared.changes, ensure_ascii=False),
                        "snapshot_hash": actual_hash,
                        "before": json.dumps(before, ensure_ascii=False),
                    },
                )
            ).scalar_one()
        await client.update_home_seo(prepared.body)
        after = await client.get_home_seo()
        verification_errors = _verify_patch(after, patch)
        status = "verification_failed" if verification_errors else "succeeded"
        async with session.begin():
            await session.execute(
                text(
                    """
                    UPDATE seo_agent.home_seo_update_runs
                       SET status = :status, after_snapshot = CAST(:after AS jsonb),
                           error_summary = :error, finished_at = now()
                     WHERE id = :id
                    """
                ),
                {
                    "id": run_id,
                    "status": status,
                    "after": json.dumps(after, ensure_ascii=False),
                    "error": "; ".join(verification_errors) or None,
                },
            )
            await _upsert_home_seo(session, stored, after)
        return {
            "ok": not verification_errors,
            "no_op": False,
            "run_id": str(run_id),
            "changes": prepared.changes,
            "home_seo": after,
            "verification_errors": verification_errors,
        }
    except Exception as error:
        if run_id is not None:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE seo_agent.home_seo_update_runs
                           SET status = 'failed', error_summary = :error, finished_at = now()
                         WHERE id = :id AND status = 'running'
                        """
                    ),
                    {"id": run_id, "error": str(error)[:1000]},
                )
        raise
    finally:
        await client.aclose()


async def list_home_seo_update_runs(
    session: AsyncSession, connector_id: str, *, limit: int = 50
) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, status, requested_patch, approved_changes,
                       expected_snapshot_hash, error_summary, started_at, finished_at
                  FROM seo_agent.home_seo_update_runs
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


async def _upsert_home_seo(
    session: AsyncSession, stored: dict[str, Any], value: dict[str, Any]
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.site_home_seo
                (site_id, source_connector_id, meta_title, meta_description,
                 meta_keywords, seo_audit, extracted_at)
            VALUES (:site_id, :connector_id, :meta_title, :meta_description,
                    :meta_keywords, CAST(:seo_audit AS jsonb), now())
            ON CONFLICT (source_connector_id) DO UPDATE SET
                meta_title = EXCLUDED.meta_title,
                meta_description = EXCLUDED.meta_description,
                meta_keywords = EXCLUDED.meta_keywords,
                seo_audit = EXCLUDED.seo_audit,
                extracted_at = now(), updated_at = now()
            """
        ),
        {
            "site_id": stored["site_id"],
            "connector_id": stored["id"],
            "meta_title": value.get("meta_title"),
            "meta_description": value.get("meta_descript"),
            "meta_keywords": value.get("meta_keywords") or [],
            "seo_audit": json.dumps(_seo_audit(value), ensure_ascii=False),
        },
    )


def _seo_audit(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "missing_tdk": [
            field
            for field, item in (
                ("meta_title", value.get("meta_title")),
                ("meta_description", value.get("meta_descript")),
            )
            if not str(item or "").strip()
        ],
        "missing_meta_keywords": not bool(value.get("meta_keywords")),
    }


def _verify_patch(value: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    remote = {
        "meta_title": "meta_title",
        "meta_description": "meta_descript",
        "meta_keywords": "meta_keywords",
    }
    for public_name, remote_name in remote.items():
        if public_name in patch and value.get(remote_name) != patch[public_name]:
            errors.append(f"{public_name} did not match after write")
    return errors


__all__ = [
    "execute_home_seo_update",
    "get_synced_home_seo",
    "list_home_seo_update_runs",
    "preview_home_seo_update",
    "sync_oemapps_home_seo",
]
