"""Secure local pairing and task transport for the Hubstudio browser extension."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings


PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ALLOWED_STAGES = {
    "received",
    "opening_page",
    "filling_content",
    "uploading_media",
    "awaiting_review",
    "publishing",
    "published",
    "manual_required",
    "failed",
}


def normalize_pairing_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def hash_extension_secret(value: str, *, pepper: str) -> str:
    return hashlib.sha256(f"{pepper}\0{value}".encode("utf-8")).hexdigest()


def _pepper() -> str:
    settings = get_settings()
    value = settings.connector_secret_key
    if len(value) < 32:
        raise ValueError("CONNECTOR_SECRET_KEY must be configured for extension pairing")
    return value


async def create_pairing_code(
    session: AsyncSession,
    *,
    business_id: str,
    container_code: str,
    display_name: str,
) -> dict[str, Any]:
    code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    result = await session.execute(
        text("""
            INSERT INTO social.extension_pairing_codes
              (business_id, container_code, display_name, code_hash, expires_at)
            SELECT :business_id, :container_code,
                   COALESCE(NULLIF(MAX(container_name), ''), :display_name),
                   :code_hash, :expires_at
              FROM social.account_bindings
             WHERE business_id = :business_id AND container_code = :container_code
             GROUP BY business_id, container_code
            RETURNING id
        """),
        {
            "business_id": business_id,
            "container_code": container_code,
            "display_name": display_name,
            "code_hash": hash_extension_secret(code, pepper=_pepper()),
            "expires_at": expires_at,
        },
    )
    pairing_id = result.scalar_one_or_none()
    if not pairing_id:
        await session.rollback()
        raise ValueError("Hubstudio environment is not bound to this business")
    await session.commit()
    return {
        "pairing_id": str(pairing_id),
        "pairing_code": f"{code[:4]}-{code[4:]}",
        "expires_at": expires_at.isoformat(),
    }


async def pair_extension(
    session: AsyncSession,
    *,
    pairing_code: str,
    extension_instance_id: str,
) -> dict[str, Any]:
    code_hash = hash_extension_secret(
        normalize_pairing_code(pairing_code), pepper=_pepper()
    )
    token = secrets.token_urlsafe(48)
    token_hash = hash_extension_secret(token, pepper=_pepper())
    async with session.begin():
        result = await session.execute(
            text("""
                UPDATE social.extension_pairing_codes
                   SET consumed_at = now()
                 WHERE code_hash = :code_hash AND consumed_at IS NULL
                   AND expires_at > now()
             RETURNING business_id, container_code, display_name
            """),
            {"code_hash": code_hash},
        )
        pairing = result.mappings().first()
        if not pairing:
            raise ValueError("pairing code is invalid, expired, or already used")
        await session.execute(
            text("""
                UPDATE social.extension_devices
                   SET revoked_at = now(), updated_at = now()
                 WHERE business_id = :business_id
                   AND container_code = :container_code
                   AND extension_instance_id <> :extension_instance_id
                   AND revoked_at IS NULL
            """),
            {
                **dict(pairing),
                "extension_instance_id": extension_instance_id,
            },
        )
        device = await session.execute(
            text("""
                INSERT INTO social.extension_devices
                  (business_id, container_code, display_name, extension_instance_id,
                   token_hash, last_seen_at)
                VALUES (:business_id, :container_code, :display_name,
                        :extension_instance_id, :token_hash, now())
                ON CONFLICT (business_id, container_code, extension_instance_id)
                DO UPDATE SET token_hash = EXCLUDED.token_hash,
                              display_name = EXCLUDED.display_name,
                              revoked_at = NULL, last_seen_at = now(), updated_at = now()
                RETURNING id, business_id, container_code, display_name
            """),
            {
                **dict(pairing),
                "extension_instance_id": extension_instance_id,
                "token_hash": token_hash,
            },
        )
    row = device.mappings().one()
    return {**{key: str(value) for key, value in row.items()}, "access_token": token}


async def authenticate_extension(
    session: AsyncSession, authorization: str | None
) -> dict[str, Any]:
    scheme, _, token_value = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or len(token_value) < 32:
        raise ValueError("valid extension bearer token is required")
    result = await session.execute(
        text("""
            UPDATE social.extension_devices SET last_seen_at = now(), updated_at = now()
             WHERE token_hash = :token_hash AND revoked_at IS NULL
         RETURNING id, business_id, container_code, display_name
        """),
        {"token_hash": hash_extension_secret(token_value, pepper=_pepper())},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("extension bearer token is invalid or revoked")
    await session.commit()
    return {key: str(value) for key, value in row.items()}


async def heartbeat_extension(session: AsyncSession, device: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "device": device, "server_time": datetime.now(timezone.utc).isoformat()}


async def list_extension_devices(
    session: AsyncSession, *, business_id: str, container_code: str | None = None
) -> dict[str, Any]:
    container_filter = (
        "AND d.container_code = :container_code" if container_code is not None else ""
    )
    result = await session.execute(
        text(f"""
            SELECT d.id, d.business_id, d.container_code, d.display_name,
                   COALESCE(
                     (
                       SELECT NULLIF(b.container_name, '')
                         FROM social.account_bindings b
                        WHERE b.business_id = d.business_id
                          AND b.container_code = d.container_code
                        ORDER BY b.updated_at DESC
                        LIMIT 1
                     ),
                     NULLIF(d.display_name, ''),
                     'Hubstudio ' || d.container_code
                   ) AS environment_name,
                   d.last_seen_at, d.created_at
              FROM social.extension_devices d
             WHERE d.business_id = :business_id AND d.revoked_at IS NULL
               {container_filter}
             ORDER BY d.last_seen_at DESC NULLS LAST, d.created_at DESC
        """),
        {"business_id": business_id, "container_code": container_code},
    )
    items_by_environment: dict[str, dict[str, Any]] = {}
    for row in result.mappings().all():
        item = dict(row)
        code = str(item["container_code"])
        existing = items_by_environment.get(code)
        if existing is None:
            item["paired_instances"] = 1
            items_by_environment[code] = item
        else:
            existing["paired_instances"] += 1
    items = list(items_by_environment.values())
    return {"items": items, "total": len(items)}


async def offer_extension_task(
    session: AsyncSession, *, job_id: str, business_id: str, device_id: str
) -> dict[str, Any]:
    result = await session.execute(
        text("""
            UPDATE social.publish_jobs j
               SET extension_device_id = CAST(:device_id AS uuid),
                   extension_stage = 'offered', extension_claimed_at = NULL
              FROM social.extension_devices d, social.account_bindings b
             WHERE j.id = CAST(:job_id AS uuid) AND j.business_id = :business_id
               AND j.status IN ('pending', 'manual_required')
               AND d.id = CAST(:device_id AS uuid) AND d.business_id = j.business_id
               AND d.container_code = b.container_code AND d.revoked_at IS NULL
               AND b.id = j.binding_id
         RETURNING j.id, j.status, j.extension_stage
        """),
        {"job_id": job_id, "business_id": business_id, "device_id": device_id},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("job and extension device are not eligible for delivery")
    await session.commit()
    return dict(row)


async def next_extension_task(
    session: AsyncSession, *, device: dict[str, Any]
) -> dict[str, Any]:
    result = await session.execute(
        text("""
            SELECT j.id, j.status, j.extension_stage, b.platform, b.default_publish_url,
                   v.title, v.body, v.media, v.metadata
              FROM social.publish_jobs j
              JOIN social.account_bindings b ON b.id = j.binding_id
              JOIN social.content_package_versions v ON v.id = j.package_version_id
             WHERE j.business_id = :business_id
               AND j.extension_device_id = CAST(:device_id AS uuid)
               AND j.extension_stage = 'offered'
               AND j.status IN ('pending', 'manual_required')
             ORDER BY j.created_at
             LIMIT 1
        """),
        {"business_id": device["business_id"], "device_id": device["id"]},
    )
    row = result.mappings().first()
    if not row:
        return {"task": None}
    claimed = await session.execute(
        text("""
            UPDATE social.publish_jobs
               SET extension_stage = 'received', extension_claimed_at = now()
             WHERE id = :id AND extension_stage = 'offered'
         RETURNING id
        """),
        {"id": row["id"]},
    )
    if not claimed.first():
        await session.rollback()
        return {"task": None}
    await session.commit()
    item = dict(row)
    item["id"] = str(item["id"])
    item["target_url"] = item.pop("default_publish_url")
    item["media"] = list(item.get("media") or [])
    item["metadata"] = dict(item.get("metadata") or {})
    return {"task": item}


async def record_extension_stage(
    session: AsyncSession,
    *,
    device: dict[str, Any],
    job_id: str,
    sequence_no: int,
    stage: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    if stage not in ALLOWED_STAGES:
        raise ValueError("unsupported extension task stage")
    safe_details = json.loads(json.dumps(details))
    async with session.begin():
        owned = await session.execute(
            text("""
                SELECT 1 FROM social.publish_jobs
                 WHERE id = CAST(:job_id AS uuid) AND business_id = :business_id
                   AND extension_device_id = CAST(:device_id AS uuid)
            """),
            {
                "job_id": job_id,
                "business_id": device["business_id"],
                "device_id": device["id"],
            },
        )
        if not owned.first():
            raise ValueError("extension task was not assigned to this device")
        await session.execute(
            text("""
                INSERT INTO social.extension_task_events
                  (business_id, job_id, device_id, sequence_no, stage, details)
                VALUES (:business_id, CAST(:job_id AS uuid), CAST(:device_id AS uuid),
                        :sequence_no, :stage, CAST(:details AS jsonb))
                ON CONFLICT (job_id, device_id, sequence_no) DO NOTHING
            """),
            {
                "business_id": device["business_id"],
                "job_id": job_id,
                "device_id": device["id"],
                "sequence_no": sequence_no,
                "stage": stage,
                "details": json.dumps(safe_details, ensure_ascii=False),
            },
        )
        await session.execute(
            text("""
                UPDATE social.publish_jobs SET extension_stage = :stage
                 WHERE id = CAST(:job_id AS uuid) AND business_id = :business_id
            """),
            {"job_id": job_id, "business_id": device["business_id"], "stage": stage},
        )
    return {"ok": True, "job_id": job_id, "stage": stage, "sequence_no": sequence_no}


__all__ = [
    "authenticate_extension",
    "create_pairing_code",
    "hash_extension_secret",
    "heartbeat_extension",
    "list_extension_devices",
    "next_extension_task",
    "normalize_pairing_code",
    "offer_extension_task",
    "pair_extension",
    "record_extension_stage",
]
