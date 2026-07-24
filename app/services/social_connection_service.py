"""Encrypted social connection persistence and real connection testing."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.secrets import SecretCipher
from app.connectors.social_connections import SocialConnectionError, connection_adapter
from app.core.config import get_settings
from app.services.social_platform_registry import platform_spec


def _cipher() -> SecretCipher:
    return SecretCipher(get_settings().connector_secret_key)


async def create_social_connection(
    session: AsyncSession, *, business_id: str, platform: str, name: str,
    config: dict[str, Any], secrets: dict[str, str],
) -> dict[str, Any]:
    spec = platform_spec(platform)
    key = spec["platform"]
    clean_config = _validate_config(key, config)
    _validate_secret_names(key, secrets)
    encrypted = _cipher().encrypt(secrets)
    async with session.begin():
        result = await session.execute(
            text("""
                INSERT INTO social.connections (business_id, platform, name, config)
                VALUES (:business_id, :platform, :name, CAST(:config AS jsonb))
                RETURNING *
            """),
            {"business_id": business_id, "platform": key, "name": name,
             "config": json.dumps(clean_config, ensure_ascii=False, sort_keys=True)},
        )
        row = result.mappings().one()
        await session.execute(
            text("""
                INSERT INTO social.connection_secrets
                  (connection_id, encrypted_value, secret_names)
                VALUES (:id, :encrypted, :names)
            """),
            {"id": row["id"], "encrypted": encrypted, "names": sorted(k for k, v in secrets.items() if v)},
        )
    return _public(row, secret_names=sorted(k for k, v in secrets.items() if v))


async def list_social_connections(
    session: AsyncSession, *, business_id: str, platform: str | None, limit: int,
) -> dict[str, Any]:
    where = "c.business_id = :business_id"
    params: dict[str, Any] = {"business_id": business_id, "limit": limit}
    if platform:
        where += " AND c.platform = :platform"
        params["platform"] = platform_spec(platform)["platform"]
    result = await session.execute(
        text(f"""
            SELECT c.*, COALESCE(s.secret_names, ARRAY[]::text[]) AS secret_names
              FROM social.connections c
              LEFT JOIN social.connection_secrets s ON s.connection_id = c.id
             WHERE {where} ORDER BY c.created_at DESC LIMIT :limit
        """),
        params,
    )
    items = [_public(row, secret_names=list(row["secret_names"] or [])) for row in result.mappings().all()]
    return {"items": items, "total": len(items)}


async def test_social_connection(
    session: AsyncSession, *, connection_id: str, business_id: str,
) -> dict[str, Any]:
    result = await session.execute(
        text("""
            SELECT c.*, s.encrypted_value, s.secret_names
              FROM social.connections c
              JOIN social.connection_secrets s ON s.connection_id = c.id
             WHERE c.id = CAST(:id AS uuid) AND c.business_id = :business_id
        """),
        {"id": connection_id, "business_id": business_id},
    )
    stored = result.mappings().first()
    if not stored:
        raise ValueError("social connection not found")
    secrets = _cipher().decrypt(stored["encrypted_value"])
    adapter = connection_adapter(stored["platform"], dict(stored["config"] or {}), secrets)
    try:
        tested = await adapter.test()
    except SocialConnectionError as error:
        await _record_test(
            session, stored, ok=False, capabilities=[], account={}, error=str(error),
        )
        return {"ok": False, "connection_id": connection_id, "status": "failed", "error": str(error)}
    await _record_test(
        session, stored, ok=True, capabilities=tested["capabilities"],
        account=tested.get("account") or {}, error=None,
    )
    return {
        "ok": True, "connection_id": connection_id, "status": "verified",
        "capabilities": tested["capabilities"], "account": tested.get("account") or {},
        "environments": tested.get("environments") or [],
    }


async def _record_test(
    session: AsyncSession, stored: Any, *, ok: bool, capabilities: list[str],
    account: dict[str, Any], error: str | None,
) -> None:
    status = "verified" if ok else "failed"
    await session.execute(
            text("""
                UPDATE social.connections
                   SET status = :status, capabilities = :capabilities,
                       account_snapshot = CAST(:account AS jsonb), last_tested_at = now(), last_error = :error
                 WHERE id = :id
            """),
            {"id": stored["id"], "status": status, "capabilities": capabilities,
             "account": json.dumps(account, ensure_ascii=False, sort_keys=True), "error": error},
        )
    await session.execute(
            text("""
                INSERT INTO social.connection_runs
                  (business_id, connection_id, status, summary, error_summary)
                VALUES (:business_id, :id, :run_status, CAST(:summary AS jsonb), :error)
            """),
            {"business_id": stored["business_id"], "id": stored["id"],
             "run_status": "succeeded" if ok else "failed",
             "summary": json.dumps({"capabilities": capabilities, "account": account}, ensure_ascii=False),
             "error": error},
        )
    await session.commit()


def _validate_config(platform: str, config: dict[str, Any]) -> dict[str, Any]:
    allowed = {"x": {"base_url"}, "reddit": {"base_url"}}[platform]
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"unsupported {platform} connection config fields: {', '.join(sorted(unknown))}")
    if platform in {"x", "reddit"}:
        base_url = str(config.get("base_url") or "http://127.0.0.1:6873").rstrip("/")
        if base_url != "http://127.0.0.1:6873":
            raise ValueError("Reddit Hubstudio connection must use http://127.0.0.1:6873")
        return {"base_url": base_url}
    return {}


def _validate_secret_names(platform: str, secrets: dict[str, str]) -> None:
    allowed = {"x": {"app_id", "app_secret", "group_code"}, "reddit": {"app_id", "app_secret", "group_code"}}[platform]
    unknown = set(secrets) - allowed
    if unknown:
        raise ValueError(f"unsupported {platform} secret fields: {', '.join(sorted(unknown))}")
    if platform in {"x", "reddit"}:
        supplied = [bool(secrets.get(name)) for name in ("app_id", "app_secret", "group_code")]
        if any(supplied) and not all(supplied):
            raise ValueError("Hubstudio app_id, app_secret and group_code must be provided together")


def _public(row: Any, *, secret_names: list[str]) -> dict[str, Any]:
    data = {key: value for key, value in dict(row).items() if key not in {"encrypted_value", "secret_names"}}
    data["configured_secret_names"] = secret_names
    return data


__all__ = ["create_social_connection", "list_social_connections", "test_social_connection"]
