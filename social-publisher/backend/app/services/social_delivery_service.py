"""Prepare and confirm human-gated delivery through Hubstudio and the local executor."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.social_executor import SocialExecutorClient, SocialExecutorError
from app.connectors.secrets import ConnectorSecretError, SecretCipher
from app.connectors.social_connections import HubstudioConnection, SocialConnectionError
from app.core.config import get_settings
from app.services.social_platform_registry import platform_spec


def _cipher() -> SecretCipher:
    return SecretCipher(get_settings().connector_secret_key)


def _executor() -> SocialExecutorClient:
    settings = get_settings()
    return SocialExecutorClient(
        base_url=settings.social_executor_url,
        shared_secret=settings.social_executor_shared_secret or settings.connector_secret_key,
    )


async def prepare_social_job(session: AsyncSession, job_id: str, *, business_id: str) -> dict[str, Any]:
    row = await _load_job(session, job_id, business_id=business_id, expected_status="pending")
    secrets = _cipher().decrypt(row["encrypted_value"])
    config = dict(row["connection_config"] or {})
    hub = HubstudioConnection(
        base_url=str(config.get("base_url") or "http://127.0.0.1:6873"),
        app_id=secrets.get("app_id", ""), app_secret=secrets.get("app_secret", ""),
        group_code=secrets.get("group_code", ""),
    )
    try:
        opened = await hub.open_environment(str(row["container_code"]))
        result = await _executor().command({
            "command": "prepare", "job_id": str(row["id"]),
            "container_code": str(row["container_code"]),
            "debugging_port": int(opened["debugging_port"]), "platform": row["platform"],
            "content": _executor_content(row), "target_url": row["default_publish_url"],
        })
    except (SocialConnectionError, SocialExecutorError) as error:
        await _mark_manual(session, row, str(error))
        return {"ok": False, "job_id": job_id, "status": "manual_required", "error": str(error)}
    if result.get("status") != "awaiting_review" or not result.get("confirmation_token"):
        error = str(
            result.get("message")
            or result.get("reason_code")
            or "executor did not return an awaiting_review confirmation"
        )
        await _mark_manual(session, row, error, evidence={"executor": result})
        return {
            "ok": False, "job_id": job_id, "status": "manual_required",
            "error": error, "reason_code": result.get("reason_code"),
        }
    evidence = {key: value for key, value in result.items() if key != "confirmation_token"}
    encrypted = _cipher().encrypt({"confirmation_token": str(result["confirmation_token"])})
    await session.execute(
            text("""
                UPDATE social.publish_jobs
                   SET status = 'awaiting_review', executor_confirmation = :confirmation,
                       executor_evidence = CAST(:evidence AS jsonb), prepared_at = now()
                 WHERE id = :id AND business_id = :business_id AND status = 'pending'
            """),
            {"id": row["id"], "business_id": business_id, "confirmation": encrypted,
             "evidence": json.dumps(evidence, ensure_ascii=False)},
        )
    await _insert_attempt(session, row, status="awaiting_review", evidence=evidence)
    await session.commit()
    return {"ok": True, "job_id": job_id, "status": "awaiting_review", "evidence": evidence}


async def confirm_social_job(session: AsyncSession, job_id: str, *, business_id: str) -> dict[str, Any]:
    row = await _load_job(session, job_id, business_id=business_id, expected_status="awaiting_review")
    if not row["executor_confirmation"]:
        raise ValueError("publish job has no active confirmation token")
    confirmation = _cipher().decrypt(row["executor_confirmation"]).get("confirmation_token", "")
    try:
        result = await _executor().command({
            "command": "confirm", "job_id": str(row["id"]),
            "container_code": str(row["container_code"]), "confirmation_token": confirmation,
        })
    except SocialExecutorError as error:
        await _mark_manual(session, row, str(error))
        return {"ok": False, "job_id": job_id, "status": "manual_required", "error": str(error)}
    if result.get("status") != "published" or not _valid_post_url(row["platform"], result.get("post_url")):
        error = str(
            result.get("message")
            or result.get("reason_code")
            or "executor did not verify a real platform post URL"
        )
        await _mark_manual(session, row, error, evidence={"executor": result})
        return {
            "ok": False,
            "job_id": job_id,
            "status": "manual_required",
            "error": error,
            "reason_code": result.get("reason_code"),
            "evidence": {"executor": result},
        }
    post_url = str(result["post_url"])
    remote_status = result.get("remote_status") if result.get("remote_status") in {"published", "removed"} else "published"
    now = datetime.now(timezone.utc)
    await session.execute(
            text("""
                INSERT INTO social.posts
                  (business_id, job_id, package_version_id, binding_id, platform, remote_url,
                   status, remote_snapshot, published_at, last_verified_at)
                VALUES (:business_id, :job_id, :version_id, :binding_id, :platform, :url,
                        :remote_status, CAST(:snapshot AS jsonb), :now, :now)
            """),
            {"business_id": business_id, "job_id": row["id"], "version_id": row["package_version_id"],
             "binding_id": row["binding_id"], "platform": row["platform"], "url": post_url,
             "remote_status": remote_status,
             "snapshot": json.dumps({"executor": result}, ensure_ascii=False), "now": now},
        )
    await session.execute(
            text("""
                UPDATE social.publish_jobs
                   SET status = 'success', executor_confirmation = NULL, updated_at = now()
                 WHERE id = :id AND business_id = :business_id
            """),
            {"id": row["id"], "business_id": business_id},
        )
    await session.execute(
            text("UPDATE social.content_packages SET status = 'published' WHERE id = :id"),
            {"id": row["package_id"]},
        )
    await _insert_attempt(session, row, status="succeeded", evidence={"post_url": post_url})
    await session.commit()
    return {"ok": True, "job_id": job_id, "status": "success", "post_url": post_url}


async def confirm_social_jobs(
    session: AsyncSession, job_ids: list[str], *, business_id: str,
) -> dict[str, Any]:
    """Confirm several prepared jobs with one user action.

    Jobs deliberately run in sequence. Hubstudio browser environments are
    resource-heavy, and a failure for one target must not prevent the remaining
    prepared targets from being attempted.
    """
    unique_job_ids = list(dict.fromkeys(job_id.strip() for job_id in job_ids if job_id.strip()))
    if not unique_job_ids:
        raise ValueError("at least one publish job is required")
    if len(unique_job_ids) > 100:
        raise ValueError("no more than 100 publish jobs can be confirmed at once")

    items: list[dict[str, Any]] = []
    for job_id in unique_job_ids:
        try:
            result = await confirm_social_job(session, job_id, business_id=business_id)
        except (ValueError, ConnectorSecretError) as error:
            result = {
                "ok": False,
                "job_id": job_id,
                "status": "failed",
                "error": str(error),
            }
        items.append(result)

    succeeded = sum(1 for item in items if item.get("ok"))
    return {
        "ok": succeeded == len(items),
        "status": "success" if succeeded == len(items) else "partial_failure",
        "total": len(items),
        "succeeded": succeeded,
        "failed": len(items) - succeeded,
        "items": items,
    }


async def _load_job(session: AsyncSession, job_id: str, *, business_id: str, expected_status: str) -> Any:
    result = await session.execute(
        text("""
            SELECT j.*, p.platform, p.content_type, v.title, v.body, v.media, v.metadata,
                   b.container_code, b.default_publish_url, b.enabled,
                   c.config AS connection_config, c.status AS connection_status,
                   s.encrypted_value
              FROM social.publish_jobs j
              JOIN social.content_packages p ON p.id = j.package_id AND p.business_id = j.business_id
              JOIN social.content_package_versions v ON v.id = j.package_version_id AND v.business_id = j.business_id
              JOIN social.account_bindings b ON b.id = j.binding_id AND b.business_id = j.business_id
              JOIN social.connections c ON c.id = b.connection_id AND c.business_id = j.business_id
              JOIN social.connection_secrets s ON s.connection_id = c.id
             WHERE j.id = CAST(:id AS uuid) AND j.business_id = :business_id AND j.status = :status
        """),
        {"id": job_id, "business_id": business_id, "status": expected_status},
    )
    row = result.mappings().first()
    if not row:
        raise ValueError(f"social publish job is not {expected_status} or does not exist")
    if not row["enabled"] or row["connection_status"] != "verified":
        raise ValueError("social publish job requires an enabled binding and verified connection")
    return row


def _executor_content(row: Any) -> dict[str, Any]:
    metadata = dict(row["metadata"] or {})
    if row["platform"] == "x":
        return {"body": row["body"], "url": metadata.get("target_url") or ""}
    if row["platform"] == "quora":
        return {"body": row["body"]}
    if row["platform"] in {"tiktok", "youtube", "instagram", "facebook"}:
        media = list(row["media"] or [])
        first = media[0] if media and isinstance(media[0], dict) else {}
        return {
            "title": row["title"], "body": row["body"],
            "media_path": first.get("path") or "",
        }
    return {
        "title": row["title"], "body": row["body"],
        "url": metadata.get("target_url") or "", "subreddit": metadata.get("subreddit") or "",
    }


async def _mark_manual(
    session: AsyncSession, row: Any, error: str, *, evidence: dict[str, Any] | None = None
) -> None:
    await session.execute(
        text("""
            UPDATE social.publish_jobs
               SET status = 'manual_required', error_message = :error, executor_confirmation = NULL
             WHERE id = :id
        """),
        {"id": row["id"], "error": error},
    )
    await _insert_attempt(
        session, row, status="manual_required",
        evidence=evidence or {"error": error},
    )
    await session.commit()


async def _insert_attempt(session: AsyncSession, row: Any, *, status: str, evidence: dict[str, Any]) -> None:
    await session.execute(
        text("""
            INSERT INTO social.publish_attempts
              (business_id, job_id, attempt_no, status, evidence, finished_at)
            VALUES (:business_id, :job_id,
                    (SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM social.publish_attempts WHERE job_id = :job_id),
                    :status, CAST(:evidence AS jsonb), now())
        """),
        {"business_id": row["business_id"], "job_id": row["id"], "status": status,
         "evidence": json.dumps(evidence, ensure_ascii=False)},
    )


def _valid_post_url(platform: str, value: Any) -> bool:
    parsed = urlsplit(str(value or ""))
    return parsed.scheme == "https" and (parsed.hostname or "").casefold() in platform_spec(platform)["allowed_hosts"]


__all__ = ["confirm_social_job", "confirm_social_jobs", "prepare_social_job"]
