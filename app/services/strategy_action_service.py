"""A small interface for the PRD §10 controlled action lifecycle."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_exception_service import record_exception

RESULTS = {"created", "updated", "already_applied", "blocked", "failed", "readback_mismatch"}


class ActionAdapter(Protocol):
    async def preview(self, action: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]: ...
    async def execute(self, action: dict[str, Any]) -> dict[str, Any]: ...
    async def recover(self, action: dict[str, Any]) -> dict[str, Any]: ...


class ActionStore(Protocol):
    async def create(self, action: dict[str, Any]) -> dict[str, Any]: ...
    async def get(self, action_id: str, *, lock: bool = False) -> dict[str, Any]: ...
    async def save(self, action: dict[str, Any]) -> None: ...
    async def record_exception(self, exception: dict[str, Any]) -> dict[str, Any]: ...
    async def create_observation(self, observation: dict[str, Any]) -> dict[str, Any]: ...


class BlockedActionAdapter:
    """Safe default: callers must inject a reviewed article/on-page adapter."""

    async def preview(self, action: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        return {"result": "blocked", "block_reason": "action_adapter_not_configured"}

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"result": "blocked", "block_reason": "action_adapter_not_configured"}

    async def recover(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"recovery_status": "unknown"}


class SQLActionStore:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, action: dict[str, Any]) -> dict[str, Any]:
        scoped_key = (
            f"{action['business_id']}:{action['site_id']}:{action['idempotency_key']}"
        )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": scoped_key},
        )
        existing = (
            await self.session.execute(
                text(
                    "SELECT payload FROM seo_agent.tasks WHERE task_type='review' "
                    "AND payload->>'kind'='strategy_action' "
                    "AND payload->>'business_id'=:business_id "
                    "AND payload->>'site_id'=:site_id "
                    "AND payload->>'idempotency_key'=:key ORDER BY created_at DESC LIMIT 1"
                ),
                {
                    "business_id": action["business_id"],
                    "site_id": action["site_id"],
                    "key": action["idempotency_key"],
                },
            )
        ).mappings().first()
        if existing:
            return dict(existing["payload"] or {})
        await self.session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, priority, site_id, target_url, title, payload, decision)
                VALUES
                  (CAST(:id AS uuid), 'review', 'queued', :priority, CAST(:site_id AS uuid),
                   :target_url, :title, CAST(:payload AS jsonb), '{}'::jsonb)
                """
            ),
            {
                "id": action["action_id"],
                "priority": "P1" if action["risk_level"] == "high" else "P2",
                "site_id": action["site_id"],
                "target_url": action.get("target_url"),
                "title": f"strategy action: {action['action_type']}",
                "payload": json.dumps({"kind": "strategy_action", **action}, ensure_ascii=False),
            },
        )
        await self.session.commit()
        return action

    async def get(self, action_id: str, *, lock: bool = False) -> dict[str, Any]:
        row = (
            await self.session.execute(
                text(
                    "SELECT payload FROM seo_agent.tasks "
                    "WHERE id=CAST(:id AS uuid) AND task_type='review' "
                    "AND payload->>'kind'='strategy_action'" + (" FOR UPDATE" if lock else "")
                ),
                {"id": action_id},
            )
        ).mappings().first()
        if not row:
            raise ValueError("strategy action not found")
        return dict(row["payload"] or {})

    async def save(self, action: dict[str, Any]) -> None:
        await self.session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status=:status, target_url=:target_url,
                       payload=CAST(:payload AS jsonb), decision=CAST(:decision AS jsonb),
                       finished_at=CASE WHEN :terminal THEN now() ELSE NULL END,
                       updated_at=now()
                 WHERE id=CAST(:id AS uuid) AND task_type='review'
                   AND payload->>'kind'='strategy_action'
                """
            ),
            {
                "id": action["action_id"],
                "status": _task_status(action["status"]),
                "target_url": action.get("target_url"),
                "payload": json.dumps({"kind": "strategy_action", **action}, ensure_ascii=False, default=str),
                "decision": json.dumps(
                    {"result": action.get("result"), "status": action["status"]}, ensure_ascii=False
                ),
                "terminal": action["status"] in {"completed", "failed", "blocked", "canceled"},
            },
        )
        await self.session.commit()

    async def record_exception(self, exception: dict[str, Any]) -> dict[str, Any]:
        result = await record_exception(self.session, exception)
        await self.session.commit()
        return result

    async def create_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        key = f"strategy-observation:{observation['action_id']}"
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key}
        )
        existing = (
            await self.session.execute(
                text(
                    "SELECT payload FROM seo_agent.tasks WHERE task_type='review' "
                    "AND payload->>'kind'='strategy_action_observation' "
                    "AND payload->>'action_id'=:action_id ORDER BY created_at DESC LIMIT 1"
                ),
                {"action_id": observation["action_id"]},
            )
        ).mappings().first()
        if existing:
            return dict(existing["payload"] or {})
        payload = {"kind": "strategy_action_observation", **observation}
        await self.session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, priority, site_id, target_url, title, payload, decision)
                VALUES
                  (CAST(:id AS uuid), 'review', 'queued', 'P2',
                   CAST(:site_id AS uuid), :target_url, :title,
                   CAST(:payload AS jsonb), '{}'::jsonb)
                """
            ),
            {
                "id": observation["observation_id"],
                "site_id": observation["site_id"],
                "target_url": observation.get("target_url"),
                "title": f"observe strategy action: {observation['action_type']}",
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
            },
        )
        await self.session.commit()
        return payload


async def get_action(store: ActionStore, *, action_id: str) -> dict[str, Any]:
    return await store.get(action_id)


async def create_action(store: ActionStore, **values: Any) -> dict[str, Any]:
    required = ("run_id", "business_id", "site_id", "action_type", "idempotency_key")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError(f"strategy action missing required fields: {missing}")
    action = {
        "action_id": str(values.get("action_id") or uuid4()),
        "status": "planned",
        "risk_level": "medium",
        "approval_requirement": "approval_required",
        "before_snapshot": {},
        "proposed_patch": {},
        "submitted_patch": None,
        "remote_response": None,
        "readback": None,
        "rollback_snapshot": {},
        "observation_id": None,
        "legacy_capability_compatibility": False,
        **values,
    }
    return await store.create(action)


async def preview_action(
    store: ActionStore,
    *,
    action_id: str,
    patch: dict[str, Any],
    adapter: ActionAdapter | None = None,
    capability_snapshot_hash: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    action = await store.get(action_id, lock=True)
    request_hash = _hash({"patch": patch, "capability_snapshot_hash": capability_snapshot_hash})
    replay = _idempotency_replay(action, "preview", idempotency_key, request_hash)
    if replay is not None:
        return replay
    capability_error = _capability_error(action, patch, capability_snapshot_hash)
    if capability_error:
        response = {**_response(action, "blocked"), "block_reason": capability_error}
        await _save_receipt(store, action, "preview", idempotency_key, request_hash, response)
        return response
    if action.get("approval_requirement") == "forbidden" or action.get("risk_level") == "forbidden":
        return {**_response(action, "blocked"), "block_reason": "action_forbidden"}
    if action.get("status") in {"completed", "failed", "canceled"}:
        return _response(action, "already_applied" if action["status"] == "completed" else "blocked")
    preview_token = str(uuid4())
    action.update({"status": "previewing", "preview_token": preview_token})
    await store.save(action)
    result = await (adapter or BlockedActionAdapter()).preview(action, patch)
    action = await store.get(action_id, lock=True)
    if action.get("preview_token") != preview_token:
        raise ValueError("stale preview claim cannot submit results")
    if result.get("result") == "blocked":
        action.update({"status": "planned", "preview_token": None})
        response = {**_response(action, "blocked"), **result}
        await _save_receipt(store, action, "preview", idempotency_key, request_hash, response)
        return response
    before = dict(result.get("before_snapshot") or {})
    proposed = dict(result.get("proposed_patch") or patch)
    action.update(
        {
            "status": "previewed",
            "before_snapshot": before,
            "proposed_patch": proposed,
            "snapshot_hash": _hash(before),
            "patch_hash": _hash(proposed),
            "rollback_snapshot": before,
            "previewed_at": _now(),
            "capability_snapshot_hash": capability_snapshot_hash,
        }
    )
    await store.save(action)
    response = _response(action, "updated")
    await _save_receipt(store, action, "preview", idempotency_key, request_hash, response)
    return response


async def approve_action(
    store: ActionStore,
    *,
    action_id: str,
    snapshot_hash: str,
    patch_hash: str,
    capability_snapshot_hash: str | None = None,
    generation_mode: str,
    generation_provider: str | None = None,
    generation_model: str | None = None,
    generation_run_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    action = await store.get(action_id, lock=True)
    request_hash = _hash(
        {
            "snapshot_hash": snapshot_hash,
            "patch_hash": patch_hash,
            "capability_snapshot_hash": capability_snapshot_hash,
            "generation_mode": generation_mode,
            "generation_provider": generation_provider,
            "generation_model": generation_model,
            "generation_run_id": generation_run_id,
        }
    )
    replay = _idempotency_replay(action, "approve", idempotency_key, request_hash)
    if replay is not None:
        return replay
    if action.get("status") != "previewed":
        raise ValueError("only a previewed action can be approved")
    if snapshot_hash != action.get("snapshot_hash") or patch_hash != action.get("patch_hash"):
        raise ValueError("approval does not match the current snapshot and patch")
    if capability_snapshot_hash != action.get("capability_snapshot_hash"):
        raise ValueError("approval does not match the current capability snapshot")
    provenance = _provenance(
        generation_mode, generation_provider, generation_model, generation_run_id
    )
    action.update(
        {
            "status": "approved",
            "approved_snapshot_hash": snapshot_hash,
            "approved_patch_hash": patch_hash,
            "approved_at": _now(),
            "approved_patch": dict(action.get("proposed_patch") or {}),
            "approved_capability_snapshot_hash": capability_snapshot_hash,
            **provenance,
        }
    )
    response = _response(action, "updated")
    await _save_receipt(store, action, "approve", idempotency_key, request_hash, response)
    return response


async def execute_action(
    store: ActionStore,
    *,
    action_id: str,
    adapter: ActionAdapter | None = None,
    capability_snapshot_hash: str | None = None,
    lease_seconds: int = 300,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    action = await store.get(action_id, lock=True)
    request_hash = _hash({"capability_snapshot_hash": capability_snapshot_hash})
    replay = _idempotency_replay(action, "execute", idempotency_key, request_hash)
    if replay is not None:
        return replay
    if action.get("status") == "completed":
        return _response(action, "already_applied")
    if action.get("status") != "approved":
        return {**_response(action, "blocked"), "block_reason": "action_not_approved"}
    if capability_snapshot_hash != action.get("approved_capability_snapshot_hash"):
        action.update(
            {
                "status": "previewed",
                "result": "blocked",
                "approved_at": None,
                "approved_snapshot_hash": None,
                "approved_patch_hash": None,
            }
        )
        await store.save(action)
        return {**_response(action, "blocked"), "block_reason": "capability_snapshot_changed"}
    capability_error = _capability_error(
        action, action.get("proposed_patch") or {}, capability_snapshot_hash
    )
    if capability_error:
        action.update({"status": "previewed", "result": "blocked"})
        await store.save(action)
        return {**_response(action, "blocked"), "block_reason": capability_error}
    if (
        _hash(action.get("before_snapshot") or {}) != action.get("approved_snapshot_hash")
        or _hash(action.get("proposed_patch") or {}) != action.get("approved_patch_hash")
    ):
        action.update({"status": "blocked", "result": "blocked"})
        await store.save(action)
        return {**_response(action, "blocked"), "block_reason": "approved_content_changed"}
    # Persist a short-transaction execution claim before invoking a potentially
    # slow remote adapter. Concurrent callers now observe ``executing`` and
    # cannot repeat the write. A process crash remains visibly recoverable
    # instead of reverting to an apparently safe-to-retry approved state.
    action.update(
        {
            "status": "executing",
            "execution_token": str(uuid4()),
            "execution_claimed_at": _now(),
            "execution_lease_expires_at": (
                datetime.now(UTC) + timedelta(seconds=max(1, lease_seconds))
            ).isoformat(),
            "execution_attempt": int(action.get("execution_attempt") or 0) + 1,
            "last_heartbeat_at": _now(),
        }
    )
    await store.save(action)
    execution_token = action["execution_token"]
    try:
        result = await (adapter or BlockedActionAdapter()).execute(action)
    except Exception as error:
        action.update({"status": "failed", "result": "failed", "executed_at": _now()})
        await store.save(action)
        await store.record_exception(
            {
                "run_id": action.get("run_id"),
                "action_id": action_id,
                "business_id": action.get("business_id"),
                "site_id": action.get("site_id"),
                "target_url": action.get("target_url"),
                "type": "connector_error",
                "error_code": "ACTION_CONNECTOR_ERROR",
                "stage": "executing",
                "severity": "P1",
                "summary": "The controlled action adapter failed.",
                "raw_error": str(error),
                "remote_write_occurred": None,
                "retryable": True,
                "responsibility_type": "connector_owner",
                "unlock_condition": "Repair the connector and create a fresh preview before retrying.",
            }
        )
        return _response(action, "failed")
    response = await complete_execution(
        store,
        action_id=action_id,
        execution_token=execution_token,
        result=result,
    )
    latest = await store.get(action_id, lock=True)
    await _save_receipt(store, latest, "execute", idempotency_key, request_hash, response)
    return response


async def complete_execution(
    store: ActionStore,
    *,
    action_id: str,
    execution_token: str,
    result: dict[str, Any],
    allow_expired_lease: bool = False,
) -> dict[str, Any]:
    """Commit a writer result only while its persisted execution lease still owns the action."""
    action = await store.get(action_id, lock=True)
    if action.get("execution_token") != execution_token:
        raise ValueError("stale execution token cannot submit results")
    if action.get("status") != "executing":
        raise ValueError("execution token no longer owns an executing action")
    lease_expires_at = _parse_time(action.get("execution_lease_expires_at"))
    if (
        not allow_expired_lease
        and lease_expires_at is not None
        and lease_expires_at <= datetime.now(UTC)
    ):
        raise ValueError("expired execution token cannot submit results")
    semantic = str(result.get("result") or "failed")
    if semantic not in RESULTS:
        semantic = "failed"
    submitted_patch = dict(result.get("submitted_patch") or action.get("proposed_patch") or {})
    if semantic in {"blocked", "failed"}:
        action.update(
            {
                "submitted_patch": submitted_patch,
                "remote_response": result.get("remote_response"),
                "readback": result.get("readback"),
                "executed_at": _now(),
                "status": semantic,
                "result": semantic,
                "field_differences": [],
                "observation_id": None,
            }
        )
        await store.save(action)
        return {
            **_response(action, semantic),
            **(
                {"block_reason": result.get("block_reason")}
                if result.get("block_reason")
                else {}
            ),
        }
    differences = compare_readback_fields(
        action.get("approved_patch") or action.get("proposed_patch") or {},
        submitted_patch,
        result.get("readback") or {},
        read_only_fields=set(result.get("read_only_fields") or ()),
    )
    action.update(
        {
            "status": "verifying",
            "submitted_patch": submitted_patch,
            "remote_response": result.get("remote_response"),
            "readback": result.get("readback"),
            "executed_at": _now(),
            "result": semantic,
            "field_differences": differences,
        }
    )
    await store.save(action)
    platform_matches = bool(differences) and all(item["match"] for item in differences)
    if semantic == "readback_mismatch" or not platform_matches:
        action.update({"status": "failed", "result": "readback_mismatch", "observation_id": None})
        await store.save(action)
        exception = await store.record_exception(
            {
                "run_id": action.get("run_id"),
                "action_id": action_id,
                "business_id": action.get("business_id"),
                "site_id": action.get("site_id"),
                "target_url": action.get("target_url"),
                "type": "readback_mismatch",
                "error_code": "ACTION_READBACK_MISMATCH",
                "stage": "verifying",
                "severity": "P1",
                "summary": "Remote write succeeded but readback did not match the approved patch.",
                "remote_write_occurred": True,
                "retryable": False,
                "responsibility_type": "connector_owner",
                "unlock_condition": "Resolve connector readback mismatch and create a new preview.",
            }
        )
        action["exception_id"] = exception.get("exception_id")
        await store.save(action)
        return _response(action, "readback_mismatch")
    if semantic in {"created", "updated", "already_applied"}:
        observation = await store.create_observation(_observation_for(action))
        action.update(
            {"status": "completed", "observation_id": observation.get("observation_id")}
        )
    elif semantic == "blocked":
        action["status"] = "blocked"
    else:
        action["status"] = "failed"
    await store.save(action)
    return {**_response(action, semantic), **({"block_reason": result.get("block_reason")} if result.get("block_reason") else {})}


async def recover_action(
    store: ActionStore,
    *,
    action_id: str,
    adapter: ActionAdapter | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Recover an expired execution lease only after a remote readback classification."""
    action = await store.get(action_id, lock=True)
    request_hash = _hash({})
    replay = _idempotency_replay(action, "recover", idempotency_key, request_hash)
    if replay is not None:
        return replay
    if action.get("status") != "executing":
        return {**_response(action, "blocked"), "block_reason": "action_not_executing"}
    expires_at = _parse_time(action.get("execution_lease_expires_at"))
    if expires_at is None or expires_at > datetime.now(UTC):
        return {**_response(action, "blocked"), "block_reason": "execution_lease_active"}
    recovery_token = str(uuid4())
    action.update({"status": "recovering", "recovery_token": recovery_token})
    await store.save(action)
    recovery = await (adapter or BlockedActionAdapter()).recover(action)
    action = await store.get(action_id, lock=True)
    if action.get("recovery_token") != recovery_token:
        raise ValueError("stale recovery claim cannot submit results")
    status = recovery.get("recovery_status")
    if status == "confirmed_not_applied":
        action.update(
            {
                "status": "approved",
                "execution_token": None,
                "execution_claimed_at": None,
                "execution_lease_expires_at": None,
                "last_heartbeat_at": _now(),
                "recovery_status": status,
            }
        )
    elif status == "confirmed_applied":
        action.update({"last_heartbeat_at": _now(), "recovery_status": status})
        action["status"] = "executing"
        await store.save(action)
        response = await complete_execution(
            store,
            action_id=action_id,
            execution_token=str(action["execution_token"]),
            result={
                "result": recovery.get("result") or "updated",
                "submitted_patch": recovery.get("submitted_patch") or action.get("proposed_patch"),
                "remote_response": recovery.get("remote_response"),
                "readback": recovery.get("readback"),
                "read_only_fields": recovery.get("read_only_fields") or [],
            },
            allow_expired_lease=True,
        )
        latest = await store.get(action_id, lock=True)
        await _save_receipt(store, latest, "recover", idempotency_key, request_hash, response)
        return response
    else:
        action.update(
            {
                "status": "blocked",
                "result": "blocked",
                "recovery_status": status if status in {"partially_applied", "unknown"} else "unknown",
            }
        )
        exception = await store.record_exception(
            {
                "run_id": action.get("run_id"),
                "action_id": action_id,
                "business_id": action.get("business_id"),
                "site_id": action.get("site_id"),
                "target_url": action.get("target_url"),
                "type": "execution_recovery_uncertain",
                "error_code": "EXECUTION_RECOVERY_UNCERTAIN",
                "stage": "executing",
                "severity": "P1",
                "summary": "Expired execution could not be safely classified as unapplied.",
                "remote_write_occurred": status != "confirmed_not_applied",
                "retryable": False,
                "responsibility_type": "human_operator",
                "unlock_condition": "Verify the remote fields and explicitly resolve the action.",
            }
        )
        action["exception_id"] = exception.get("exception_id")
    response = _response(action, action.get("result") or "blocked")
    await _save_receipt(store, action, "recover", idempotency_key, request_hash, response)
    return response


async def heartbeat_action(
    store: ActionStore,
    *,
    action_id: str,
    execution_token: str,
    lease_seconds: int = 300,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    action = await store.get(action_id, lock=True)
    request_hash = _hash(
        {"execution_token": execution_token, "lease_seconds": lease_seconds}
    )
    replay = _idempotency_replay(action, "heartbeat", idempotency_key, request_hash)
    if replay is not None:
        return replay
    if action.get("status") != "executing" or action.get("execution_token") != execution_token:
        raise ValueError("stale execution token cannot renew lease")
    action.update(
        {
            "last_heartbeat_at": _now(),
            "execution_lease_expires_at": (
                datetime.now(UTC) + timedelta(seconds=max(1, lease_seconds))
            ).isoformat(),
        }
    )
    response = _response(action, "updated")
    await _save_receipt(store, action, "heartbeat", idempotency_key, request_hash, response)
    return response


def compare_readback_fields(
    approved_patch: dict[str, Any],
    submitted_patch: dict[str, Any],
    readback: dict[str, Any],
    *,
    read_only_fields: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Compare approved, submitted, and remote values without trusting adapter booleans."""
    ignored = read_only_fields or set()
    rows: list[dict[str, Any]] = []
    for field, expected in approved_patch.items():
        if field in ignored:
            continue
        submitted = submitted_patch.get(field)
        actual = readback.get(field)
        approved_matches_submission = _normalize_value(expected) == _normalize_value(submitted)
        remote_matches = _normalize_value(expected) == _normalize_value(actual)
        rows.append(
            {
                "field": field,
                "expected": expected,
                "submitted": submitted,
                "actual": actual,
                "match": approved_matches_submission and remote_matches,
            }
        )
    return rows


def _idempotency_replay(
    action: dict[str, Any],
    operation: str,
    key: str | None,
    request_hash: str,
) -> dict[str, Any] | None:
    if not key:
        return None
    receipt = (action.get("operation_receipts") or {}).get(f"{operation}:{key}")
    if not receipt:
        return None
    if receipt.get("request_hash") != request_hash:
        raise ValueError("idempotency key was already used with a different request")
    return dict(receipt.get("response") or {})


async def _save_receipt(
    store: ActionStore,
    action: dict[str, Any],
    operation: str,
    key: str | None,
    request_hash: str,
    response: dict[str, Any],
) -> None:
    if key:
        receipts = dict(action.get("operation_receipts") or {})
        receipts[f"{operation}:{key}"] = {
            "request_hash": request_hash,
            "response": {
                child_key: child_value
                for child_key, child_value in response.items()
                if child_key != "operation_receipts"
            },
        }
        action["operation_receipts"] = receipts
    await store.save(action)


def _capability_error(
    action: dict[str, Any],
    patch: dict[str, Any],
    snapshot_hash: str | None,
) -> str | None:
    capability = action.get("capability_snapshot")
    if not isinstance(capability, dict):
        if action.get("legacy_capability_compatibility") is True:
            return None
        return "capability_snapshot_missing"
    if snapshot_hash != capability.get("capability_snapshot_hash"):
        return "capability_snapshot_changed"
    action_type = str(action.get("action_type") or "")
    policy = (capability.get("supported_actions") or {}).get(action_type)
    if policy in {None, "forbidden"}:
        return "capability_not_declared"
    fields = capability.get("supported_fields") or {}
    allowed = set(fields.get(action_type) or fields.get("articles") or ())
    if any(field not in allowed for field in patch):
        return "field_not_allowed"
    connector_by_action = {
        "product_seo": "products",
        "product_image_alt": "products",
        "category_seo": "collections",
        "homepage_seo": "homepage",
        "new_article": "articles",
        "update_article": "articles",
    }
    connector = (capability.get("connectors") or {}).get(connector_by_action.get(action_type), {})
    if connector.get("status") != "available" or connector.get("write") is not True:
        return "connector_not_writable"
    checked_at = _parse_time(connector.get("checked_at"))
    if checked_at is None or checked_at < datetime.now(UTC) - timedelta(hours=24):
        return "capability_snapshot_stale"
    if capability.get("configuration_issues"):
        return "capability_configuration_issue"
    side_effects = (capability.get("side_effects") or {}).get(action_type) or {}
    confirmations = action.get("side_effect_confirmations") or {}
    required = [key for key, enabled in side_effects.items() if key.startswith("requires_") and enabled]
    if any(not confirmations.get(key) for key in required):
        return "side_effect_confirmation_required"
    return None


async def rollback_preview(
    store: ActionStore, *, action_id: str, idempotency_key: str | None = None
) -> dict[str, Any]:
    action = await store.get(action_id, lock=True)
    request_hash = _hash({})
    replay = _idempotency_replay(action, "rollback_preview", idempotency_key, request_hash)
    if replay is not None:
        return replay
    response = {
        **_response(action, "blocked"),
        "block_reason": "rollback_capability_not_declared",
        "rollback_snapshot": action.get("rollback_snapshot") or {},
        "remote_write_occurred": False,
    }
    await _save_receipt(
        store, action, "rollback_preview", idempotency_key, request_hash, response
    )
    return response


async def rollback_action(
    store: ActionStore,
    *,
    action_id: str,
    confirm: bool,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise ValueError("explicit rollback confirmation is required")
    action = await store.get(action_id, lock=True)
    request_hash = _hash({"confirm": confirm})
    replay = _idempotency_replay(action, "rollback", idempotency_key, request_hash)
    if replay is not None:
        return replay
    response = {
        **_response(action, "blocked"),
        "block_reason": "rollback_capability_not_declared",
        "remote_write_occurred": False,
    }
    await _save_receipt(store, action, "rollback", idempotency_key, request_hash, response)
    return response


def _response(action: dict[str, Any], result: str) -> dict[str, Any]:
    return {**action, "result": result}


def _normalize_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        normalized = re.sub(r">\s+<", "><", value.strip())
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = re.sub(r">\s+", ">", normalized)
        normalized = re.sub(r"\s+<", "<", normalized)
        return normalized
    if isinstance(value, dict):
        return {key: _normalize_value(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_value(child) for child in value]
    return value


def _observation_for(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "observation_id": str(uuid4()),
        "business_id": action.get("business_id"),
        "site_id": action.get("site_id"),
        "run_id": action.get("run_id"),
        "action_id": action.get("action_id"),
        "target_url": action.get("target_url"),
        "action_type": action.get("action_type"),
        "strategy": action.get("strategy"),
        "topic": action.get("topic"),
        "generation_mode": action.get("generation_mode"),
        "generation_provider": action.get("generation_provider"),
        "generation_model": action.get("generation_model"),
        "before_evidence_snapshot": action.get("before_snapshot") or {},
        "executed_at": action.get("executed_at"),
        "checkpoints_days": [7, 14, 28, 56],
        "checkpoint_statuses": {
            "7": "pending",
            "14": "pending",
            "28": "pending",
            "56": "pending",
        },
    }


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _hash(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _provenance(mode: str, provider: str | None, model: str | None, run_id: str | None) -> dict[str, Any]:
    mode = str(mode or "").strip().casefold()
    if mode == "manual":
        return {
            "generation_mode": "manual",
            "generation_provider": None,
            "generation_model": None,
            "generation_run_id": None,
        }
    if mode != "model":
        raise ValueError("generation_mode must be manual or model")
    generic = {"", "unknown", "gpt", "gpt-4", "gpt-5", "claude", "gemini", "latest", "default", "auto"}
    if str(provider or "").strip().casefold() in generic or str(model or "").strip().casefold() in generic:
        raise ValueError("model content requires exact generation provider and model")
    if re.fullmatch(r"(?:gpt-\d+(?:\.0)?|claude-\d+|gemini-\d+(?:\.\d+)?)", str(model).casefold()):
        raise ValueError("model content requires exact generation provider and model")
    return {
        "generation_mode": "model",
        "generation_provider": provider,
        "generation_model": model,
        "generation_run_id": run_id,
    }


def _task_status(status: str) -> str:
    return {
        "planned": "queued",
        "previewed": "queued",
        "approved": "queued",
        "executing": "running",
        "completed": "done",
        "failed": "failed",
        "blocked": "blocked",
        "canceled": "canceled",
    }.get(status, "queued")


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "ActionAdapter",
    "BlockedActionAdapter",
    "RESULTS",
    "SQLActionStore",
    "approve_action",
    "compare_readback_fields",
    "complete_execution",
    "create_action",
    "execute_action",
    "get_action",
    "heartbeat_action",
    "preview_action",
    "recover_action",
    "rollback_action",
    "rollback_preview",
]
