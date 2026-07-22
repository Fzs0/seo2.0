"""Persistence and state invariants for social publishing.

The module stops at the delivery seam: it never invokes AI, Hubstudio, a
browser, or a social platform, and therefore cannot report publication success.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.secrets import SecretCipher
from app.connectors.social_connections import HubstudioConnection, SocialConnectionError
from app.core.config import get_settings
from app.services.social_platform_registry import (
    PLATFORMS,
    validate_binding_payload,
    validate_package_payload,
)

SUPPORTED_PLATFORMS = set(PLATFORMS)


def validate_platform(platform: str) -> str:
    value = platform.strip().casefold()
    if value not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported social platform: {platform}")
    return value


async def create_decision(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    platforms = [validate_platform(item) for item in payload["requested_platforms"]]
    decision = payload["decision"]
    allowed = bool(decision.get("allow_publish"))
    needs_review = bool(decision.get("requires_human_confirmation", True))
    status = "manual_review" if allowed and needs_review else ("allowed" if allowed else "blocked")
    result = await session.execute(
        text("""
            INSERT INTO seo_agent.social_decisions
              (business_id, topic, language_code, requested_platforms, status, decision, evidence, created_by)
            VALUES (:business_id, :topic, :language_code, :platforms, :status,
                    CAST(:decision AS jsonb), CAST(:evidence AS jsonb), :created_by)
            RETURNING *
        """),
        {**payload, "platforms": platforms, "status": status,
         "decision": _json(decision), "evidence": _json(payload.get("evidence") or {})},
    )
    await session.commit()
    return _row(result.mappings().one())


async def create_package(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    platform = validate_platform(payload["platform"])
    validate_package_payload(platform, payload["content_type"], payload)
    async with session.begin():
        decision_result = await session.execute(
            text("SELECT business_id, status, requested_platforms FROM seo_agent.social_decisions WHERE id = CAST(:id AS uuid) FOR UPDATE"),
            {"id": payload["decision_id"]},
        )
        decision = decision_result.mappings().first()
        if not decision:
            raise ValueError("social decision not found")
        if decision["business_id"] != payload["business_id"]:
            raise ValueError("decision does not belong to the requested business")
        if decision["status"] == "blocked":
            raise ValueError("blocked decision cannot create a publishing package")
        if platform not in (decision["requested_platforms"] or []):
            raise ValueError("package platform was not requested by the decision")
        package_result = await session.execute(
            text("""
                INSERT INTO seo_agent.social_content_packages
                  (business_id, decision_id, platform, content_type)
                VALUES (:business_id, CAST(:decision_id AS uuid), :platform, :content_type)
                RETURNING *
            """),
            {**payload, "platform": platform},
        )
        package = package_result.mappings().one()
        version = await _insert_version(session, package_id=str(package["id"]), version=1, payload=payload)
    return {**_row(package), "current": version}


async def create_package_version(
    session: AsyncSession, package_id: str, *, business_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    async with session.begin():
        result = await session.execute(
            text("""
                SELECT * FROM seo_agent.social_content_packages
                 WHERE id = CAST(:id AS uuid) AND business_id = :business_id FOR UPDATE
            """),
            {"id": package_id, "business_id": business_id},
        )
        package = result.mappings().first()
        if not package:
            raise ValueError("social content package not found")
        if package["status"] not in {"draft", "rejected"}:
            raise ValueError("only draft or rejected packages can receive a new version")
        validate_package_payload(package["platform"], package["content_type"], payload)
        version_no = int(package["current_version"]) + 1
        version = await _insert_version(
            session, package_id=package_id, version=version_no,
            payload={**payload, "business_id": business_id},
        )
        await session.execute(
            text("""
                UPDATE seo_agent.social_content_packages
                   SET current_version = :version, status = 'draft', review_note = NULL,
                       reviewed_by = NULL, reviewed_at = NULL
                 WHERE id = CAST(:id AS uuid) AND business_id = :business_id
            """),
            {"id": package_id, "business_id": business_id, "version": version_no},
        )
    return version


async def list_packages(session: AsyncSession, *, business_id: str, status: str | None, limit: int) -> dict[str, Any]:
    where = "p.business_id = :business_id"
    params: dict[str, Any] = {"business_id": business_id, "limit": limit}
    if status:
        where += " AND p.status = :status"
        params["status"] = status
    result = await session.execute(
        text(f"""
            SELECT p.*, v.id AS version_id, v.language_code, v.title, v.body, v.media,
                   v.metadata, v.risk_score, v.content_hash
              FROM seo_agent.social_content_packages p
              JOIN seo_agent.social_content_package_versions v
                ON v.package_id = p.id AND v.version = p.current_version
             WHERE {where} ORDER BY p.created_at DESC LIMIT :limit
        """),
        params,
    )
    items = [_row(item) for item in result.mappings().all()]
    return {"items": items, "total": len(items)}


async def submit_package(session: AsyncSession, package_id: str, *, business_id: str, expected_version: int) -> dict[str, Any]:
    return await _change_package_status(
        session, package_id, business_id=business_id, expected="draft", target="in_review",
        expected_version=expected_version,
    )


async def review_package(
    session: AsyncSession, package_id: str, *, business_id: str, expected_version: int,
    approve: bool, actor: str, note: str,
) -> dict[str, Any]:
    target = "approved" if approve else "rejected"
    async with session.begin():
        result = await session.execute(
            text("""
                UPDATE seo_agent.social_content_packages
                   SET status = :target, reviewed_by = :actor, review_note = :note, reviewed_at = now()
                 WHERE id = CAST(:id AS uuid) AND business_id = :business_id
                   AND status = 'in_review' AND current_version = :expected_version
             RETURNING *
            """),
            {"id": package_id, "business_id": business_id, "target": target, "actor": actor,
             "note": note, "expected_version": expected_version},
        )
        row = result.mappings().first()
        if not row:
            raise ValueError("package review state or expected_version is stale")
    return _row(row)


async def create_binding(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    platform = validate_platform(payload["platform"])
    payload = {**payload, "platform": platform}
    validate_binding_payload(payload)
    result = await session.execute(
        text("""
            INSERT INTO seo_agent.social_account_bindings
              (business_id, platform, connection_id, display_name, container_code, container_name, binding_name,
               default_publish_url, delivery_channel, publish_adapter, enabled)
            VALUES (:business_id, :platform, CAST(:connection_id AS uuid), :display_name, :container_code, :container_name,
                    :binding_name, :default_publish_url, :delivery_channel, :publish_adapter, :enabled)
            RETURNING *
        """),
        payload,
    )
    await session.commit()
    return _row(result.mappings().one())


async def activate_binding(session: AsyncSession, binding_id: str, *, business_id: str) -> dict[str, Any]:
    result = await session.execute(
        text("""
            SELECT b.*, c.platform AS connection_platform, c.status AS connection_status,
                   c.config AS connection_config, s.encrypted_value
              FROM seo_agent.social_account_bindings b
              JOIN seo_agent.social_connections c
                ON c.id = b.connection_id AND c.business_id = b.business_id
              JOIN seo_agent.social_connection_secrets s ON s.connection_id = c.id
             WHERE b.id = CAST(:id AS uuid) AND b.business_id = :business_id
        """),
        {"id": binding_id, "business_id": business_id},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("social account binding not found")
    if row["connection_platform"] != row["platform"] or row["connection_status"] != "verified":
        raise ValueError("binding requires a verified connection for the same platform")
    secrets = SecretCipher(get_settings().connector_secret_key).decrypt(row["encrypted_value"])
    config = dict(row["connection_config"] or {})
    adapter = HubstudioConnection(
        base_url=str(config.get("base_url") or "http://127.0.0.1:6873"),
        app_id=secrets.get("app_id", ""), app_secret=secrets.get("app_secret", ""),
        group_code=secrets.get("group_code", ""),
    )
    try:
        tested = await adapter.test()
    except SocialConnectionError as error:
        raise ValueError(str(error)) from error
    environments = {item["container_code"]: item for item in tested.get("environments") or []}
    if str(row["container_code"]) not in environments:
        raise ValueError("binding container_code is not accessible through this Hubstudio connection")
    environment = environments[str(row["container_code"])]
    update = await session.execute(
        text("""
            UPDATE seo_agent.social_account_bindings
               SET enabled = true, login_status = 'valid', last_tested_at = now(),
                   container_name = :container_name
             WHERE id = CAST(:id AS uuid) AND business_id = :business_id
         RETURNING *
        """),
        {"id": binding_id, "business_id": business_id,
         "container_name": environment.get("container_name") or row["container_name"]},
    )
    await session.commit()
    return _row(update.mappings().one())


async def list_bindings(session: AsyncSession, *, business_id: str, platform: str | None, limit: int) -> dict[str, Any]:
    where = "business_id = :business_id"
    params: dict[str, Any] = {"business_id": business_id, "limit": limit}
    if platform:
        where += " AND platform = :platform"
        params["platform"] = validate_platform(platform)
    result = await session.execute(
        text(f"SELECT * FROM seo_agent.social_account_bindings WHERE {where} ORDER BY created_at DESC LIMIT :limit"),
        params,
    )
    items = [_row(item) for item in result.mappings().all()]
    return {"items": items, "total": len(items)}


async def create_publish_job(
    session: AsyncSession, *, business_id: str, package_id: str, binding_id: str,
    expected_version: int, idempotency_key: str,
) -> dict[str, Any]:
    async with session.begin():
        replay = await session.execute(
            text("SELECT * FROM seo_agent.social_publish_jobs WHERE business_id = :business_id AND idempotency_key = :key"),
            {"business_id": business_id, "key": idempotency_key},
        )
        existing = replay.mappings().first()
        if existing:
            return _row(existing)
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"social:{business_id}:{package_id}"},
        )
        source_result = await session.execute(
            text("""
                SELECT p.platform, p.status AS package_status, p.current_version,
                       v.id AS package_version_id,
                       b.platform AS binding_platform, b.enabled, b.login_status
                  FROM seo_agent.social_content_packages p
                  JOIN seo_agent.social_content_package_versions v
                    ON v.package_id = p.id AND v.version = p.current_version AND v.business_id = p.business_id
                  JOIN seo_agent.social_account_bindings b
                    ON b.id = CAST(:binding_id AS uuid) AND b.business_id = p.business_id
                 WHERE p.id = CAST(:package_id AS uuid) AND p.business_id = :business_id
                 FOR UPDATE OF p
            """),
            {"business_id": business_id, "package_id": package_id, "binding_id": binding_id},
        )
        source = source_result.mappings().first()
        if not source:
            raise ValueError("publishing package or account binding not found")
        if source["package_status"] != "approved" or int(source["current_version"]) != expected_version:
            raise ValueError("package approval or expected_version is stale")
        if source["platform"] != source["binding_platform"]:
            raise ValueError("package and account binding platform must match")
        if not source["enabled"]:
            raise ValueError("account binding is disabled")
        if source["login_status"] not in {"unknown", "valid"}:
            raise ValueError("account binding requires manual login verification")
        job_result = await session.execute(
            text("""
                INSERT INTO seo_agent.social_publish_jobs
                  (business_id, package_id, package_version_id, binding_id, idempotency_key)
                VALUES (:business_id, CAST(:package_id AS uuid), CAST(:version_id AS uuid),
                        CAST(:binding_id AS uuid), :idempotency_key)
                RETURNING *
            """),
            {"business_id": business_id, "package_id": package_id,
             "version_id": source["package_version_id"], "binding_id": binding_id,
             "idempotency_key": idempotency_key},
        )
        await session.execute(
            text("UPDATE seo_agent.social_content_packages SET status = 'queued' WHERE id = CAST(:id AS uuid) AND business_id = :business_id"),
            {"id": package_id, "business_id": business_id},
        )
    return _row(job_result.mappings().one())


async def list_publish_jobs(session: AsyncSession, *, business_id: str, status: str | None, limit: int) -> dict[str, Any]:
    where = "j.business_id = :business_id"
    params: dict[str, Any] = {"business_id": business_id, "limit": limit}
    if status:
        where += " AND j.status = :status"
        params["status"] = status
    result = await session.execute(
        text(f"""
            SELECT j.*, p.platform, p.content_type, b.display_name, b.container_code
              FROM seo_agent.social_publish_jobs j
              JOIN seo_agent.social_content_packages p ON p.id = j.package_id
              JOIN seo_agent.social_account_bindings b ON b.id = j.binding_id
             WHERE {where} ORDER BY j.created_at DESC LIMIT :limit
        """),
        params,
    )
    items = [_row(item) for item in result.mappings().all()]
    return {"items": items, "total": len(items)}


async def cancel_publish_job(session: AsyncSession, job_id: str, *, business_id: str) -> dict[str, Any]:
    async with session.begin():
        result = await session.execute(
            text("""
                UPDATE seo_agent.social_publish_jobs SET status = 'cancelled'
                 WHERE id = CAST(:id AS uuid) AND business_id = :business_id
                   AND status IN ('pending', 'manual_required', 'failed')
             RETURNING *
            """),
            {"id": job_id, "business_id": business_id},
        )
        row = result.mappings().first()
        if not row:
            raise ValueError("only pending, failed, or manual_required jobs can be cancelled")
        await session.execute(
            text("UPDATE seo_agent.social_content_packages SET status = 'cancelled' WHERE id = :id"),
            {"id": row["package_id"]},
        )
    return _row(row)


async def _insert_version(
    session: AsyncSession, *, package_id: str, version: int, payload: dict[str, Any]
) -> dict[str, Any]:
    digest = _content_hash(payload)
    result = await session.execute(
        text("""
            INSERT INTO seo_agent.social_content_package_versions
              (business_id, package_id, version, language_code, title, body, media, metadata,
               risk_score, content_hash, created_by)
            VALUES (:business_id, CAST(:package_id AS uuid), :version, :language_code, :title, :body,
                    CAST(:media AS jsonb), CAST(:metadata AS jsonb), :risk_score, :content_hash, :created_by)
            RETURNING *
        """),
        {**payload, "package_id": package_id, "version": version,
         "media": _json(payload.get("media") or []), "metadata": _json(payload.get("metadata") or {}),
         "content_hash": digest, "created_by": payload.get("created_by") or "api"},
    )
    return _row(result.mappings().one())


async def _change_package_status(
    session: AsyncSession, package_id: str, *, business_id: str, expected: str,
    target: str, expected_version: int,
) -> dict[str, Any]:
    result = await session.execute(
        text("""
            UPDATE seo_agent.social_content_packages SET status = :target
             WHERE id = CAST(:id AS uuid) AND business_id = :business_id
               AND status = :expected AND current_version = :expected_version
         RETURNING *
        """),
        {"id": package_id, "business_id": business_id, "target": target,
         "expected": expected, "expected_version": expected_version},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("package state or expected_version is stale")
    await session.commit()
    return _row(row)


def _content_hash(payload: dict[str, Any]) -> str:
    content = {key: payload.get(key) for key in ("language_code", "title", "body", "media", "metadata", "risk_score")}
    return hashlib.sha256(_json(content).encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row(value: Any) -> dict[str, Any]:
    return dict(value)


__all__ = [
    "SUPPORTED_PLATFORMS", "activate_binding", "cancel_publish_job", "create_binding", "create_decision",
    "create_package", "create_package_version", "create_publish_job", "list_bindings",
    "list_packages", "list_publish_jobs", "review_package", "submit_package", "validate_platform",
]
