"""Persistence and execution orchestration for read-only custom connectors."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.custom_data import ConnectorConfig, ConnectorPreview, CustomDataConnector
from app.connectors.oemapps_products import (
    OemAppsProductError,
    OemAppsProducts,
    build_oemapps_connector_config,
    prepare_seo_update,
    snapshot_hash,
)
from app.connectors.safe_http import SafeJsonHttpClient
from app.connectors.secrets import SecretCipher
from app.core.config import get_settings


_SENSITIVE_KEY = re.compile(r"(?:auth|token|secret|password|api[_-]?key)", re.IGNORECASE)


def _cipher() -> SecretCipher:
    return SecretCipher(get_settings().connector_secret_key)


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "site_id": str(row["site_id"]),
        "name": row["name"],
        "capability": row["capability"],
        "status": row["status"],
        "current_version": row["current_version"],
        "active_version": row.get("active_version"),
        "config": row.get("config") or {},
        "configured_secret_names": list(row.get("secret_names") or []),
        "schema_fingerprint": row.get("schema_fingerprint"),
        "verified_at": row.get("verified_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def create_connector(
    session: AsyncSession,
    *,
    site_id: str,
    config: ConnectorConfig,
    secrets: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with session.begin():
        site_exists = (
            await session.execute(text("SELECT 1 FROM seo_agent.sites WHERE id = :id"), {"id": site_id})
        ).scalar_one_or_none()
        if not site_exists:
            raise ValueError("site not found")
        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO seo_agent.custom_connectors (site_id, name, capability)
                    VALUES (:site_id, :name, :capability)
                    RETURNING id, site_id, name, capability, status, current_version,
                              active_version, created_at, updated_at
                    """
                ),
                {"site_id": site_id, "name": config.name, "capability": config.capability},
            )
        ).mappings().one()
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.custom_connector_versions (connector_id, version, config)
                VALUES (:connector_id, 1, CAST(:config AS jsonb))
                """
            ),
            {"connector_id": row["id"], "config": config.model_dump_json()},
        )
        if secrets:
            encrypted = _cipher().encrypt(secrets)
            await session.execute(
                text(
                    """
                    INSERT INTO seo_agent.custom_connector_secrets
                        (connector_id, encrypted_value, secret_names)
                    VALUES (:connector_id, :encrypted_value, :secret_names)
                    """
                ),
                {
                    "connector_id": row["id"],
                    "encrypted_value": encrypted,
                    "secret_names": sorted(key for key, value in secrets.items() if value),
                },
            )
    configured_secret_names = sorted(name for name, value in (secrets or {}).items() if value)
    return {
        **_public_row(dict(row)),
        "config": config.model_dump(mode="json"),
        "configured_secret_names": configured_secret_names,
    }


async def configure_oemapps_connector(
    session: AsyncSession, *, site_id: str, token: str
) -> dict[str, Any]:
    """Create or rotate the token for the invariant OEMApps product connector."""
    if not token.strip():
        raise ValueError("OEMApps token is required")
    config = build_oemapps_connector_config()
    existing = (
        await session.execute(
            text(
                """
                SELECT c.id
                  FROM seo_agent.custom_connectors c
                  JOIN seo_agent.custom_connector_versions v
                    ON v.connector_id = c.id AND v.version = c.current_version
                 WHERE c.site_id = :site_id
                   AND v.config ->> 'adapter' = 'oemapps'
                 LIMIT 1
                """
            ),
            {"site_id": site_id},
        )
    ).scalar_one_or_none()
    await session.commit()
    if existing is not None:
        return await update_connector(
            session, str(existing), config=config, secrets={"token": token}
        )
    return await create_connector(
        session, site_id=site_id, config=config, secrets={"token": token}
    )


async def list_connectors(
    session: AsyncSession, *, site_id: str | None = None, limit: int = 100
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    where = ""
    if site_id:
        where = "WHERE c.site_id = :site_id"
        params["site_id"] = site_id
    rows = (
        await session.execute(
            text(
                f"""
                SELECT c.id, c.site_id, c.name, c.capability, c.status,
                       c.current_version, c.active_version, c.created_at, c.updated_at,
                       v.config, v.schema_fingerprint, v.verified_at,
                       COALESCE(s.secret_names, ARRAY[]::text[]) AS secret_names
                  FROM seo_agent.custom_connectors c
                  JOIN seo_agent.custom_connector_versions v
                    ON v.connector_id = c.id AND v.version = c.current_version
             LEFT JOIN seo_agent.custom_connector_secrets s ON s.connector_id = c.id
                  {where}
              ORDER BY c.updated_at DESC
                 LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()
    return {"items": [_public_row(dict(row)) for row in rows], "total": len(rows)}


async def get_connector(session: AsyncSession, connector_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT c.id, c.site_id, c.name, c.capability, c.status,
                       c.current_version, c.active_version, c.created_at, c.updated_at,
                       v.config, v.schema_fingerprint, v.verified_at,
                       COALESCE(s.secret_names, ARRAY[]::text[]) AS secret_names
                  FROM seo_agent.custom_connectors c
                  JOIN seo_agent.custom_connector_versions v
                    ON v.connector_id = c.id AND v.version = c.current_version
             LEFT JOIN seo_agent.custom_connector_secrets s ON s.connector_id = c.id
                 WHERE c.id = :id
                """
            ),
            {"id": connector_id},
        )
    ).mappings().one_or_none()
    return _public_row(dict(row)) if row else None


async def update_connector(
    session: AsyncSession,
    connector_id: str,
    *,
    config: ConnectorConfig,
    secrets: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with session.begin():
        current = (
            await session.execute(
                text("SELECT current_version FROM seo_agent.custom_connectors WHERE id = :id FOR UPDATE"),
                {"id": connector_id},
            )
        ).scalar_one_or_none()
        if current is None:
            raise ValueError("connector not found")
        version = int(current) + 1
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.custom_connector_versions (connector_id, version, config)
                VALUES (:connector_id, :version, CAST(:config AS jsonb))
                """
            ),
            {"connector_id": connector_id, "version": version, "config": config.model_dump_json()},
        )
        await session.execute(
            text(
                """
                UPDATE seo_agent.custom_connectors
                   SET name = :name, capability = :capability,
                       current_version = :version, status = 'draft'
                 WHERE id = :id
                """
            ),
            {"id": connector_id, "name": config.name, "capability": config.capability, "version": version},
        )
        if secrets:
            existing_row = (
                await session.execute(
                    text("SELECT encrypted_value FROM seo_agent.custom_connector_secrets WHERE connector_id = :id"),
                    {"id": connector_id},
                )
            ).scalar_one_or_none()
            merged = _cipher().decrypt(existing_row) if existing_row is not None else {}
            merged.update({key: value for key, value in secrets.items() if value})
            await session.execute(
                text(
                    """
                    INSERT INTO seo_agent.custom_connector_secrets
                        (connector_id, encrypted_value, secret_names)
                    VALUES (:id, :encrypted, :names)
                    ON CONFLICT (connector_id) DO UPDATE SET
                        encrypted_value = EXCLUDED.encrypted_value,
                        secret_names = EXCLUDED.secret_names,
                        updated_at = now()
                    """
                ),
                {"id": connector_id, "encrypted": _cipher().encrypt(merged), "names": sorted(merged)},
            )
    result = await get_connector(session, connector_id)
    if result is None:
        raise ValueError("connector not found")
    return result


async def preview_config(config: ConnectorConfig, payload: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    return CustomDataConnector(config).preview(payload, item_limit=limit).as_dict()


async def test_config_request(
    config: ConnectorConfig, *, secrets: dict[str, str] | None = None, limit: int = 20
) -> dict[str, Any]:
    connector = CustomDataConnector(config, secrets=secrets)
    preview, fingerprint = await _fetch_pages(connector, item_limit=limit)
    return {
        "ok": preview.mapping_errors == 0 and preview.mapped_items > 0,
        "schema_fingerprint": fingerprint,
        **preview.as_dict(),
    }


async def list_connector_versions(session: AsyncSession, connector_id: str) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                """
                SELECT version, config, schema_fingerprint, verified_at, created_at
                  FROM seo_agent.custom_connector_versions
                 WHERE connector_id = :id
              ORDER BY version DESC
                """
            ),
            {"id": connector_id},
        )
    ).mappings().all()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


async def list_connector_runs(
    session: AsyncSession, connector_id: str, *, limit: int = 50
) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, version, run_type, status, items_received, items_mapped,
                       items_rejected, items_upserted, schema_fingerprint, summary,
                       error_summary, started_at, finished_at
                  FROM seo_agent.custom_connector_runs
                 WHERE connector_id = :id
              ORDER BY started_at DESC
                 LIMIT :limit
                """
            ),
            {"id": connector_id, "limit": limit},
        )
    ).mappings().all()
    return {"items": [{**dict(row), "id": str(row["id"])} for row in rows], "total": len(rows)}


async def test_connector(session: AsyncSession, connector_id: str) -> dict[str, Any]:
    stored, secrets = await _load_runtime(session, connector_id, require_active=False)
    config = ConnectorConfig.model_validate(stored["config"])
    connector = CustomDataConnector(config, secrets=secrets)
    await session.commit()
    preview, fingerprint = await _fetch_pages(
        connector,
        item_limit=config.request.pagination.max_items,
        include_raw=True,
    )
    succeeded = preview.mapping_errors == 0 and preview.mapped_items > 0
    async with session.begin():
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.custom_connector_runs
                    (connector_id, version, run_type, status, items_received,
                     items_mapped, items_rejected, schema_fingerprint, summary, finished_at)
                VALUES (:id, :version, 'test', :status, :received,
                        :mapped, :rejected, :fingerprint, CAST(:summary AS jsonb), now())
                """
            ),
            {
                "id": connector_id,
                "version": stored["current_version"],
                "status": "succeeded" if succeeded else "failed",
                "received": preview.total_items,
                "mapped": preview.mapped_items,
                "rejected": preview.mapping_errors,
                "fingerprint": fingerprint,
                "summary": json.dumps({"errors": preview.errors[:20]}, ensure_ascii=False),
            },
        )
        if succeeded:
            await session.execute(
                text(
                    """
                    UPDATE seo_agent.custom_connector_versions
                       SET schema_fingerprint = :fingerprint, verified_at = now()
                     WHERE connector_id = :id AND version = :version
                    """
                ),
                {"id": connector_id, "version": stored["current_version"], "fingerprint": fingerprint},
            )
            await session.execute(
                text("UPDATE seo_agent.custom_connectors SET status = 'verified' WHERE id = :id"),
                {"id": connector_id},
            )
    return {"ok": succeeded, "schema_fingerprint": fingerprint, **preview.as_dict()}


async def activate_connector(session: AsyncSession, connector_id: str) -> dict[str, Any]:
    async with session.begin():
        row = (
            await session.execute(
                text(
                    """
                    UPDATE seo_agent.custom_connectors c
                       SET status = 'active', active_version = current_version
                     WHERE c.id = :id AND c.status = 'verified'
                       AND EXISTS (
                           SELECT 1 FROM seo_agent.custom_connector_versions v
                            WHERE v.connector_id = c.id AND v.version = c.current_version
                              AND v.verified_at IS NOT NULL
                       )
                    RETURNING id
                    """
                ),
                {"id": connector_id},
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("only the current verified connector version can be activated")
    result = await get_connector(session, connector_id)
    if result is None:
        raise ValueError("connector not found")
    return result


async def sync_connector_products(session: AsyncSession, connector_id: str) -> dict[str, Any]:
    stored, secrets = await _load_runtime(session, connector_id, require_active=True)
    config = ConnectorConfig.model_validate(stored["config"])
    connector = CustomDataConnector(config, secrets=secrets)
    await session.commit()
    preview, fingerprint = await _fetch_pages(
        connector, item_limit=config.request.pagination.max_items
    )
    expected = stored.get("schema_fingerprint")
    if expected and fingerprint != expected:
        async with session.begin():
            await session.execute(
                text("UPDATE seo_agent.custom_connectors SET status = 'schema_changed' WHERE id = :id"),
                {"id": connector_id},
            )
        raise ValueError("connector response schema changed; test and verify the mapping again")
    upserted = 0
    async with session.begin():
        for product in preview.items:
            await _upsert_product(session, stored, product)
            upserted += 1
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.custom_connector_runs
                    (connector_id, version, run_type, status, items_received, items_mapped,
                     items_rejected, items_upserted, schema_fingerprint, summary, finished_at)
                VALUES (:id, :version, 'sync', 'succeeded', :received, :mapped,
                        :rejected, :upserted, :fingerprint, '{}'::jsonb, now())
                """
            ),
            {
                "id": connector_id,
                "version": stored["current_version"],
                "received": preview.total_items,
                "mapped": preview.mapped_items,
                "rejected": preview.mapping_errors,
                "upserted": upserted,
                "fingerprint": fingerprint,
            },
        )
    return {
        "ok": preview.mapping_errors == 0,
        "items_received": preview.total_items,
        "items_mapped": preview.mapped_items,
        "items_rejected": preview.mapping_errors,
        "items_upserted": upserted,
        "errors": preview.errors[:20],
    }


async def preview_oemapps_seo_update(
    session: AsyncSession,
    connector_id: str,
    product_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    _, token = await _oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsProducts(token)
    try:
        product = await client.get_product(product_id)
    finally:
        await client.aclose()
    prepared = prepare_seo_update(product, patch)
    return {
        "ok": True,
        "product_id": str(product["id"]),
        "title": product.get("title"),
        "expected_snapshot_hash": prepared.snapshot_hash,
        "changes": prepared.changes,
        "change_count": len(prepared.changes),
        "variant_ids": _variant_ids(product),
        "warnings": [
            "OEMApps uses a full product PUT and may regenerate variant IDs even for SEO-only changes."
        ],
    }


async def execute_oemapps_seo_update(
    session: AsyncSession,
    connector_id: str,
    product_id: str,
    patch: dict[str, Any],
    *,
    expected_snapshot_hash: str,
    confirm_variant_recreation: bool,
) -> dict[str, Any]:
    if not confirm_variant_recreation:
        raise ValueError("confirm_variant_recreation must be true for OEMApps product PUT")
    _, token = await _oemapps_runtime(session, connector_id)
    await session.commit()
    client = OemAppsProducts(token)
    run_id: Any = None
    try:
        before = await client.get_product(product_id)
        actual_hash = snapshot_hash(before)
        if actual_hash != expected_snapshot_hash:
            raise ValueError("product changed after preview; preview the SEO update again")
        prepared = prepare_seo_update(before, patch)
        if not prepared.changes:
            return {
                "ok": True,
                "no_op": True,
                "product_id": str(before["id"]),
                "changes": [],
                "variant_ids_changed": False,
            }
        async with session.begin():
            run_id = (
                await session.execute(
                    text(
                        """
                        INSERT INTO seo_agent.product_seo_update_runs
                            (connector_id, product_external_id, status, requested_patch,
                             approved_changes, expected_snapshot_hash, before_snapshot,
                             variant_ids_before)
                        VALUES (:connector_id, :product_id, 'running', CAST(:patch AS jsonb),
                                CAST(:changes AS jsonb), :snapshot_hash, CAST(:before AS jsonb),
                                CAST(:variant_ids AS jsonb))
                        RETURNING id
                        """
                    ),
                    {
                        "connector_id": connector_id,
                        "product_id": str(product_id),
                        "patch": json.dumps(patch, ensure_ascii=False),
                        "changes": json.dumps(prepared.changes, ensure_ascii=False),
                        "snapshot_hash": actual_hash,
                        "before": json.dumps(_sanitize_raw(before), ensure_ascii=False),
                        "variant_ids": json.dumps(_variant_ids(before)),
                    },
                )
            ).scalar_one()
        await client.update_product(product_id, prepared.body)
        after = await client.get_product(product_id)
        verification_errors = _verify_seo_patch(after, patch)
        before_ids = _variant_ids(before)
        after_ids = _variant_ids(after)
        status = "verification_failed" if verification_errors else "succeeded"
        async with session.begin():
            await session.execute(
                text(
                    """
                    UPDATE seo_agent.product_seo_update_runs
                       SET status = :status, after_snapshot = CAST(:after AS jsonb),
                           variant_ids_after = CAST(:variant_ids AS jsonb),
                           error_summary = :error, finished_at = now()
                     WHERE id = :id
                    """
                ),
                {
                    "id": run_id,
                    "status": status,
                    "after": json.dumps(_sanitize_raw(after), ensure_ascii=False),
                    "variant_ids": json.dumps(after_ids),
                    "error": "; ".join(verification_errors) or None,
                },
            )
        return {
            "ok": not verification_errors,
            "no_op": False,
            "run_id": str(run_id),
            "product_id": str(after.get("id") or product_id),
            "changes": prepared.changes,
            "verification_errors": verification_errors,
            "variant_ids_before": before_ids,
            "variant_ids_after": after_ids,
            "variant_ids_changed": before_ids != after_ids,
        }
    except Exception as error:
        if run_id is not None:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE seo_agent.product_seo_update_runs
                           SET status = 'failed', error_summary = :error, finished_at = now()
                         WHERE id = :id AND status = 'running'
                        """
                    ),
                    {"id": run_id, "error": str(error)[:1000]},
                )
        raise
    finally:
        await client.aclose()


async def list_oemapps_seo_update_runs(
    session: AsyncSession, connector_id: str, *, limit: int = 50
) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, product_external_id, status, requested_patch, approved_changes,
                       expected_snapshot_hash, variant_ids_before, variant_ids_after,
                       error_summary, started_at, finished_at
                  FROM seo_agent.product_seo_update_runs
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


async def _oemapps_runtime(
    session: AsyncSession, connector_id: str
) -> tuple[dict[str, Any], str]:
    stored, secrets = await _load_runtime(session, connector_id, require_active=True)
    config = ConnectorConfig.model_validate(stored["config"])
    if config.adapter != "oemapps":
        raise ValueError("connector is not an OEMApps self-hosted product connector")
    token = secrets.get("token", "")
    if not token:
        raise ValueError("OEMApps connector token is not configured")
    return stored, token


def _variant_ids(product: dict[str, Any]) -> list[str]:
    return [str(item["id"]) for item in product.get("variants") or [] if item.get("id")]


def _verify_seo_patch(product: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    remote_names = {
        "meta_title": "meta_title",
        "meta_description": "meta_descript",
        "meta_keywords": "meta_keywords",
    }
    for public_name, remote_name in remote_names.items():
        if public_name in patch and product.get(remote_name) != patch[public_name]:
            errors.append(f"{public_name} did not match after write")
    images = {str(item.get("image_id")): item for item in product.get("images") or []}
    for image_id, expected_alt in (patch.get("image_alts") or {}).items():
        if (images.get(str(image_id)) or {}).get("alt", "") != expected_alt:
            errors.append(f"image {image_id} alt did not match after write")
    return errors


async def _load_runtime(
    session: AsyncSession, connector_id: str, *, require_active: bool
) -> tuple[dict[str, Any], dict[str, str]]:
    version_clause = "c.active_version" if require_active else "c.current_version"
    row = (
        await session.execute(
            text(
                f"""
                SELECT c.id, c.site_id, c.name, c.status, c.current_version, c.active_version,
                       v.config, v.schema_fingerprint, s.encrypted_value
                  FROM seo_agent.custom_connectors c
                  JOIN seo_agent.custom_connector_versions v
                    ON v.connector_id = c.id AND v.version = {version_clause}
             LEFT JOIN seo_agent.custom_connector_secrets s ON s.connector_id = c.id
                 WHERE c.id = :id
                """
            ),
            {"id": connector_id},
        )
    ).mappings().one_or_none()
    if row is None or (require_active and row["status"] != "active"):
        raise ValueError("active connector not found" if require_active else "connector not found")
    secrets = _cipher().decrypt(row["encrypted_value"]) if row["encrypted_value"] is not None else {}
    return dict(row), secrets


async def _fetch_pages(
    connector: CustomDataConnector, *, item_limit: int, include_raw: bool = False
) -> tuple[ConnectorPreview, str]:
    client = SafeJsonHttpClient()
    pagination = connector.config.request.pagination
    page = pagination.start_page
    total_received = 0
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_external_ids: set[str] = set()
    fingerprint: str | None = None
    last_pagination: dict[str, Any] | list[Any] | None = None
    try:
        for page_index in range(pagination.max_pages):
            request = connector.build_request(page=page)
            payload = await client.request_json(
                method=request["method"],
                url=request["url"],
                headers=request["headers"],
                params=request["params"],
                json_body=request["json"],
                timeout_seconds=request["timeout_seconds"],
                max_response_bytes=request["max_response_bytes"],
                allowed_hosts=set(request["allowed_hosts"]),
            )
            current_fingerprint = _schema_fingerprint(payload)
            if fingerprint is None:
                fingerprint = current_fingerprint
            elif current_fingerprint != fingerprint:
                raise ValueError("connector response schema changed between pages")
            remaining = max(0, item_limit - len(items))
            if remaining == 0:
                break
            preview = connector.preview(
                payload,
                item_limit=remaining,
                include_raw=include_raw,
            )
            total_received += min(preview.total_items, remaining)
            last_pagination = preview.pagination
            for error in preview.errors:
                errors.append({**error, "page": page, "page_index": page_index})
            for product in preview.items:
                external_id = str(product.get("external_id") or "")
                if external_id in seen_external_ids:
                    errors.append(
                        {
                            "page": page,
                            "field": "external_id",
                            "error": f"duplicate external_id {external_id!r} across pages",
                        }
                    )
                    continue
                seen_external_ids.add(external_id)
                items.append(product)
                if len(items) >= item_limit:
                    break
            if len(items) >= item_limit:
                break
            next_page = connector.next_page(payload, current_page=page)
            if next_page is None:
                break
            page = next_page
    finally:
        await client.aclose()
    if fingerprint is None:
        raise ValueError("connector returned no response")
    return (
        ConnectorPreview(
            total_items=total_received,
            mapped_items=len(items),
            mapping_errors=len(errors),
            items=items,
            errors=errors,
            pagination=last_pagination,
        ),
        fingerprint,
    )


async def _upsert_product(session: AsyncSession, stored: dict[str, Any], product: dict[str, Any]) -> None:
    collections = product.get("collections") or []
    category = None
    if collections and isinstance(collections[0], dict):
        category = collections[0].get("title") or collections[0].get("name")
    image = (product.get("images") or [{}])[0] if product.get("images") else {}
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.products
                (external_id, site_id, source_connector_id, handle, title, url,
                 canonical_url, category, price, status, image, images, variants,
                 description, keywords, meta_title, meta_description, meta_keywords,
                 seo_audit, source, raw, source_updated_at, extracted_at)
            VALUES
                (:external_id, :site_id, :connector_id, :handle, :title, :url,
                 :canonical_url, :category, :price, :status, CAST(:image AS jsonb),
                 CAST(:images AS jsonb), CAST(:variants AS jsonb), :description,
                 :keywords, :meta_title, :meta_description, :meta_keywords,
                 CAST(:seo_audit AS jsonb), :source, CAST(:raw AS jsonb),
                 :source_updated_at, now())
            ON CONFLICT (source_connector_id, external_id)
              WHERE source_connector_id IS NOT NULL
            DO UPDATE SET
                handle = EXCLUDED.handle, title = EXCLUDED.title, url = EXCLUDED.url,
                canonical_url = EXCLUDED.canonical_url, category = EXCLUDED.category,
                price = EXCLUDED.price, status = EXCLUDED.status, image = EXCLUDED.image,
                images = EXCLUDED.images, variants = EXCLUDED.variants,
                description = EXCLUDED.description, keywords = EXCLUDED.keywords,
                meta_title = EXCLUDED.meta_title, meta_description = EXCLUDED.meta_description,
                meta_keywords = EXCLUDED.meta_keywords, seo_audit = EXCLUDED.seo_audit,
                raw = EXCLUDED.raw, source_updated_at = EXCLUDED.source_updated_at,
                extracted_at = now()
            """
        ),
        {
            "external_id": product["external_id"],
            "site_id": stored["site_id"],
            "connector_id": stored["id"],
            "handle": product.get("handle"),
            "title": product["title"],
            "url": product.get("url"),
            "canonical_url": product.get("canonical_url") or product.get("url"),
            "category": category or product.get("product_type"),
            "price": product.get("price") or product.get("price_min"),
            "status": product.get("status") or ("active" if product.get("available") else "draft"),
            "image": json.dumps(image, ensure_ascii=False),
            "images": json.dumps(product.get("images") or [], ensure_ascii=False),
            "variants": json.dumps(product.get("variants") or [], ensure_ascii=False),
            "description": product.get("description") or product.get("description_html"),
            "keywords": product.get("tags") or [],
            "meta_title": product.get("meta_title"),
            "meta_description": product.get("meta_description"),
            "meta_keywords": product.get("meta_keywords") or [],
            "seo_audit": json.dumps(product.get("seo_audit") or {}, ensure_ascii=False),
            "source": f"custom_connector:{stored['id']}",
            "raw": json.dumps(_sanitize_raw(product.get("raw") or {}), ensure_ascii=False, default=str),
            "source_updated_at": _as_datetime(product.get("source_updated_at")),
        },
    )


def _sanitize_raw(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else _sanitize_raw(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_raw(item) for item in value]
    return value


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError(f"unsupported datetime value: {type(value).__name__}")


def _schema_fingerprint(payload: dict[str, Any]) -> str:
    def shape(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: shape(item) for key, item in sorted(value.items()) if not _SENSITIVE_KEY.search(str(key))}
        if isinstance(value, list):
            # Item fields are often sparse and the final page may have a different
            # first item. Required mappings validate item contracts separately;
            # the fingerprint protects the response envelope across pages/runs.
            return "array"
        if value is None:
            return "null"
        return type(value).__name__

    encoded = json.dumps(shape(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "activate_connector",
    "create_connector",
    "get_connector",
    "list_connectors",
    "list_connector_runs",
    "list_connector_versions",
    "preview_config",
    "sync_connector_products",
    "test_connector",
    "test_config_request",
    "update_connector",
]
