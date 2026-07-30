"""A small interface for the PRD §10 controlled action lifecycle."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_exception_service import record_exception
from app.core.remote_outcomes import policy_for_remote_outcome
from app.services.strategy_plan_contract import (
    validate_strategy_action_lineage,
    validate_strategy_action_wave,
)

RESULTS = {"created", "updated", "already_applied", "blocked", "failed", "readback_mismatch"}
ON_PAGE_ACTION_TYPES = {
    "homepage_seo",
    "product_seo",
    "category_seo",
    "product_image_alt",
}


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
        await self.validate_lineage(action, lock=True)
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

    async def validate_lineage(
        self, action: dict[str, Any], *, lock: bool = False
    ) -> dict[str, Any]:
        return await validate_strategy_action_lineage(
            self.session, action, lock=lock
        )

    async def validate_wave(self, action: dict[str, Any]) -> None:
        await validate_strategy_action_wave(self.session, action)

    async def reconcile_parent_run(self, run_id: str) -> None:
        """Close the parent run after an action reaches a terminal outcome."""
        # Keep the dependency one-way at import time: the run service creates
        # actions, while production action completion coordinates the parent run.
        from app.services.strategy_run_service import reconcile_strategy_run

        await reconcile_strategy_run(self.session, run_id=run_id)

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
    required = (
        "run_id",
        "plan_id",
        "source_strategy_task_id",
        "business_id",
        "site_id",
        "action_type",
        "idempotency_key",
    )
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError(f"strategy action missing required fields: {missing}")
    capability = values.get("capability_snapshot")
    permission = (
        (capability.get("supported_actions") or {}).get(values["action_type"])
        if isinstance(capability, dict)
        else None
    )
    if permission not in {"approval_required", "allowed", "execute"}:
        raise ValueError(
            "strategy action cannot be created without an executable capability snapshot"
        )
    if values["action_type"] in ON_PAGE_ACTION_TYPES:
        adapter_identity = values.get("adapter_identity")
        if (
            not isinstance(adapter_identity, dict)
            or adapter_identity.get("read") is not True
            or adapter_identity.get("write") is not True
            or adapter_identity.get("readback") is not True
            or str(adapter_identity.get("connector_type") or "").casefold()
            != str(values.get("connector_type") or "").casefold()
        ):
            raise ValueError(
                "on-page strategy action requires an exact readable, writable, "
                "independently readable adapter identity"
            )
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
    generation_mode: str | None = None,
    generation_provider: str | None = None,
    generation_model: str | None = None,
    generation_run_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    action = await store.get(action_id, lock=True)
    await _validate_store_lineage(store, action)
    preview_provenance = (
        _provenance(
            generation_mode,
            generation_provider,
            generation_model,
            generation_run_id,
        )
        if generation_mode is not None
        else {}
    )
    request_hash = _hash(
        {
            "patch": patch,
            "capability_snapshot_hash": capability_snapshot_hash,
            **preview_provenance,
        }
    )
    replay = _idempotency_replay(action, "preview", idempotency_key, request_hash)
    if replay is not None:
        return replay
    capability_error = _capability_error(
        action,
        patch,
        capability_snapshot_hash,
        require_side_effect_confirmations=False,
    )
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
    await _validate_store_lineage(store, action)
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
            **preview_provenance,
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
    side_effect_confirmations: dict[str, bool] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    action = await store.get(action_id, lock=True)
    await _validate_store_lineage(store, action)
    request_hash = _hash(
        {
            "snapshot_hash": snapshot_hash,
            "patch_hash": patch_hash,
            "capability_snapshot_hash": capability_snapshot_hash,
            "generation_mode": generation_mode,
            "generation_provider": generation_provider,
            "generation_model": generation_model,
            "generation_run_id": generation_run_id,
            "side_effect_confirmations": side_effect_confirmations or {},
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
    confirmations = dict(side_effect_confirmations or {})
    confirmation_error = _side_effect_confirmation_error(action, confirmations)
    if confirmation_error:
        raise ValueError(confirmation_error)
    provenance = _provenance(
        generation_mode, generation_provider, generation_model, generation_run_id
    )
    preview_provenance = {
        key: action.get(key)
        for key in (
            "generation_mode",
            "generation_provider",
            "generation_model",
            "generation_run_id",
        )
    }
    if preview_provenance["generation_mode"] is not None and (
        preview_provenance != provenance
    ):
        raise ValueError(
            "approval generation provenance does not match the current preview"
        )
    action.update(
        {
            "status": "approved",
            "approved_snapshot_hash": snapshot_hash,
            "approved_patch_hash": patch_hash,
            "approved_at": _now(),
            "approved_patch": dict(action.get("proposed_patch") or {}),
            "approved_capability_snapshot_hash": capability_snapshot_hash,
            "approved_adapter_identity": action.get("adapter_identity"),
            "approved_target_identity": _target_identity(action),
            "side_effect_confirmations": confirmations,
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
    await _validate_store_lineage(store, action)
    request_hash = _hash({"capability_snapshot_hash": capability_snapshot_hash})
    replay = _idempotency_replay(action, "execute", idempotency_key, request_hash)
    if replay is not None:
        return replay
    if action.get("status") == "completed":
        return _response(action, "already_applied")
    if action.get("status") != "approved":
        return {**_response(action, "blocked"), "block_reason": "action_not_approved"}
    if action.get("run_mode") != "approval_execution":
        return {
            **_response(action, "blocked"),
            "block_reason": "dry_run_actions_cannot_execute",
        }
    await _validate_store_wave(store, action)
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
    if action.get("adapter_identity") != action.get("approved_adapter_identity"):
        action.update({"status": "previewed", "result": "blocked"})
        await store.save(action)
        return {
            **_response(action, "blocked"),
            "block_reason": "adapter_identity_changed",
        }
    if _target_identity(action) != action.get("approved_target_identity"):
        action.update({"status": "previewed", "result": "blocked"})
        await store.save(action)
        return {
            **_response(action, "blocked"),
            "block_reason": "target_identity_changed",
        }
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
        action.update({
            "status": "blocked",
            "result": "blocked",
            "recovery_status": "unknown_remote_state",
            "submitted_patch": dict(
                action.get("approved_patch")
                or action.get("proposed_patch")
                or {}
            ),
            "remote_response": {
                "ok": False,
                "remote_outcome": "unknown_remote_state",
                "error_code": "ACTION_CONNECTOR_ERROR",
                "retry_policy": "manual_readback_required",
            },
            "executed_at": _now(),
        })
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
                "retryable": False,
                "responsibility_type": "connector_owner",
                "unlock_condition": "Repair the connector and create a fresh preview before retrying.",
            }
        )
        return _response(action, "blocked")
    response = await complete_execution(
        store,
        action_id=action_id,
        execution_token=execution_token,
        result=result,
    )
    latest = await store.get(action_id, lock=True)
    await _save_receipt(store, latest, "execute", idempotency_key, request_hash, response)
    return response


async def execute_action_and_reconcile(
    store: ActionStore,
    *,
    action_id: str,
    adapter: ActionAdapter | None = None,
    capability_snapshot_hash: str | None = None,
    lease_seconds: int = 300,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Execute one formal action and advance its parent run when terminal.

    The action response is returned unchanged so execute idempotency receipts
    retain their exact public contract.  Run state remains independently
    queryable through the Strategy Run API.
    """
    response = await execute_action(
        store,
        action_id=action_id,
        adapter=adapter,
        capability_snapshot_hash=capability_snapshot_hash,
        lease_seconds=lease_seconds,
        idempotency_key=idempotency_key,
    )
    await _reconcile_parent_run_if_terminal(store, response)
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
        remote_outcome = result.get("remote_outcome")
        outcome_policy = policy_for_remote_outcome(remote_outcome)
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
                "recovery_status": remote_outcome or action.get("recovery_status"),
                "article_id": result.get("article_id") or action.get("article_id"),
                "execution_task_id": result.get("execution_task_id")
                or action.get("execution_task_id"),
                "publish_task_id": result.get("publish_task_id")
                or action.get("publish_task_id"),
                "effect_id": result.get("effect_id") or action.get("effect_id"),
            }
        )
        await store.save(action)
        if outcome_policy and outcome_policy.task_status == "blocked":
            exception = await store.record_exception(
                {
                    "run_id": action.get("run_id"),
                    "action_id": action_id,
                    "business_id": action.get("business_id"),
                    "site_id": action.get("site_id"),
                    "target_url": action.get("target_url"),
                    "type": "remote_write_uncertain",
                    "error_code": outcome_policy.error_code,
                    "stage": "executing",
                    "severity": outcome_policy.severity,
                    "summary": "Remote write outcome requires manual readback before retry.",
                    "remote_write_occurred": outcome_policy.remote_write_occurred,
                    "retryable": outcome_policy.auto_retry_allowed,
                    "responsibility_type": "human_operator",
                    "unlock_condition": "Complete a fresh remote readback and resolve the exception.",
                }
            )
            action["exception_id"] = exception.get("exception_id")
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
            "target_url": result.get("target_url") or action.get("target_url"),
            "article_id": result.get("article_id") or action.get("article_id"),
            "execution_task_id": result.get("execution_task_id")
            or action.get("execution_task_id"),
            "publish_task_id": result.get("publish_task_id")
            or action.get("publish_task_id"),
            "effect_id": result.get("effect_id") or action.get("effect_id"),
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
    """Recover an expired or false-negative execution using read-only remote evidence."""
    action = await store.get(action_id, lock=True)
    request_hash = _hash({})
    replay = _idempotency_replay(action, "recover", idempotency_key, request_hash)
    if replay is not None:
        return replay
    original_status = str(action.get("status") or "")
    terminal_reconciliation = original_status in {"failed", "blocked"}
    if original_status != "executing" and not terminal_reconciliation:
        return {**_response(action, "blocked"), "block_reason": "action_not_executing"}
    if original_status == "executing":
        expires_at = _parse_time(action.get("execution_lease_expires_at"))
        if expires_at is None or expires_at > datetime.now(UTC):
            return {**_response(action, "blocked"), "block_reason": "execution_lease_active"}
    elif not action.get("submitted_patch") or not action.get("remote_response"):
        return {
            **_response(action, "blocked"),
            "block_reason": "terminal_action_has_no_remote_write_evidence",
        }
    recovery_token = str(uuid4())
    action.update(
        {
            "status": "recovering",
            "recovery_token": recovery_token,
            "recovery_from_status": original_status,
        }
    )
    await store.save(action)
    recovery = await (adapter or BlockedActionAdapter()).recover(action)
    action = await store.get(action_id, lock=True)
    if action.get("recovery_token") != recovery_token:
        raise ValueError("stale recovery claim cannot submit results")
    status = recovery.get("recovery_status")
    if status in {"confirmed_not_applied", "confirmed_absent"}:
        if terminal_reconciliation:
            action.update(
                {
                    "status": "blocked",
                    "result": "blocked",
                    "last_heartbeat_at": _now(),
                    "recovery_status": status,
                }
            )
        else:
            action.update(
                {
                    "status": "planned",
                    "execution_token": None,
                    "execution_claimed_at": None,
                    "execution_lease_expires_at": None,
                    "preview_token": None,
                    "snapshot_hash": None,
                    "patch_hash": None,
                    "capability_snapshot_hash": None,
                    "approved_at": None,
                    "approved_snapshot_hash": None,
                    "approved_patch_hash": None,
                    "approved_patch": None,
                    "approved_capability_snapshot_hash": None,
                    "approved_adapter_identity": None,
                    "approved_target_identity": None,
                    "side_effect_confirmations": {},
                    "last_heartbeat_at": _now(),
                    "recovery_status": status,
                }
            )
    elif status == "confirmed_applied":
        action.update({"last_heartbeat_at": _now(), "recovery_status": status})
        if terminal_reconciliation:
            action["execution_token"] = recovery_token
        action["status"] = "executing"
        if terminal_reconciliation and not action.get("exception_id"):
            exception = await store.record_exception(
                {
                    "run_id": action.get("run_id"),
                    "action_id": action_id,
                    "business_id": action.get("business_id"),
                    "site_id": action.get("site_id"),
                    "target_url": recovery.get("target_url") or action.get("target_url"),
                    "type": "remote_write_reconciled",
                    "error_code": "REMOTE_WRITE_RECONCILED",
                    "stage": "reconciling",
                    "severity": "P1",
                    "status": "resolved",
                    "summary": (
                        "A previously failed local action was reconciled from an exact "
                        "remote readback without repeating the write."
                    ),
                    "remote_write_occurred": True,
                    "retryable": False,
                    "responsibility_type": "connector_owner",
                    "unlock_condition": "No further action; regression coverage prevents recurrence.",
                }
            )
            action["exception_id"] = exception.get("exception_id")
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
                "target_url": recovery.get("target_url"),
                "article_id": recovery.get("article_id"),
                "execution_task_id": recovery.get("execution_task_id"),
                "publish_task_id": recovery.get("publish_task_id"),
                "effect_id": recovery.get("effect_id"),
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
                "recovery_status": (
                    status
                    if status in {"partially_applied", "unknown_remote_state"}
                    else "unknown_remote_state"
                ),
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
                "remote_write_occurred": True if status == "partially_applied" else None,
                "retryable": False,
                "responsibility_type": "human_operator",
                "unlock_condition": "Verify the remote fields and explicitly resolve the action.",
            }
        )
        action["exception_id"] = exception.get("exception_id")
    response = _response(action, action.get("result") or "blocked")
    await _save_receipt(store, action, "recover", idempotency_key, request_hash, response)
    return response


async def recover_action_and_reconcile(
    store: ActionStore,
    *,
    action_id: str,
    adapter: ActionAdapter | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Recover one action and close its parent run if recovery is terminal."""
    response = await recover_action(
        store,
        action_id=action_id,
        adapter=adapter,
        idempotency_key=idempotency_key,
    )
    await _reconcile_parent_run_if_terminal(store, response)
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
        approved_matches_submission = _normalize_value(expected, field=field) == _normalize_value(
            submitted, field=field
        )
        remote_matches = _normalize_value(expected, field=field) == _normalize_value(
            actual, field=field
        )
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
    *,
    require_side_effect_confirmations: bool = True,
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
    declared_adapter = (capability.get("action_adapters") or {}).get(action_type)
    if declared_adapter is not None and (
        action.get("adapter_identity") != declared_adapter
        or str(action.get("connector_type") or "").casefold()
        != str(declared_adapter.get("connector_type") or "").casefold()
    ):
        return "action_adapter_identity_mismatch"
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
    if require_side_effect_confirmations and _side_effect_confirmation_error(
        action, dict(action.get("side_effect_confirmations") or {})
    ):
        return "side_effect_confirmation_required"
    return None


def _side_effect_confirmation_error(
    action: dict[str, Any], confirmations: dict[str, bool]
) -> str | None:
    capability = action.get("capability_snapshot") or {}
    action_type = str(action.get("action_type") or "")
    side_effects = (capability.get("side_effects") or {}).get(action_type) or {}
    required = [
        key
        for key, enabled in side_effects.items()
        if key.startswith("requires_") and enabled
    ]
    missing = [key for key in required if confirmations.get(key) is not True]
    if missing:
        return "side-effect confirmation required: " + ", ".join(sorted(missing))
    unknown = set(confirmations) - set(required)
    if unknown:
        return "side-effect confirmation is not declared: " + ", ".join(
            sorted(unknown)
        )
    return None


def _target_identity(action: dict[str, Any]) -> dict[str, Any]:
    return {
        key: action.get(key)
        for key in (
            "business_id",
            "site_id",
            "page_type",
            "target_asset_id",
            "remote_object_id",
            "target_url",
            "connector_id",
            "connector_type",
            "action_type",
        )
    }


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


def _normalize_value(value: Any, *, field: str | None = None) -> Any:
    if value is None or value == "":
        return None
    if field == "body":
        plain = str(value)
        # WordPress stores the post title separately and removes the Markdown
        # H1 from the body. The title is compared as its own approved field, so
        # exclude a body H1 on either representation before semantic comparison.
        plain = re.sub(r"<h1\b[^>]*>.*?</h1>", " ", plain, flags=re.I | re.S)
        plain = re.sub(r"^#\s+.*(?:\r?\n|$)", " ", plain, count=1, flags=re.M)
        # Preserve boundaries between block, list, and table cells before tags
        # are removed; otherwise adjacent HTML cells collapse into one token.
        plain = re.sub(
            r"</(?:p|li|h[2-6]|td|th|tr|blockquote|figure|div|ul|ol|table)>",
            " ",
            plain,
            flags=re.I,
        )
        plain = re.sub(r"<[^>]+>", " ", plain)
        # Images and ALT are validated as separate approved fields. Excluding
        # them from body text keeps Markdown and connector-rendered HTML
        # semantically comparable.
        plain = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", plain)
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r" \1 ", plain)
        plain = re.sub(r"^#{1,6}\s*", "", plain, flags=re.M)
        plain = re.sub(r"^\s*\d+[.)]\s+", "", plain, flags=re.M)
        plain = re.sub(r"[*_`~>|-]+", " ", plain)
        return re.sub(r"\s+", " ", unescape(plain)).strip()
    if field == "images":
        items = value if isinstance(value, list) else []
        return sorted(_normalize_url(str(item)) for item in items)
    if field == "image_alts":
        items = value if isinstance(value, dict) else {}
        return {
            _normalize_url(str(src)): re.sub(r"\s+", " ", str(alt)).strip()
            for src, alt in sorted(items.items())
        }
    if field == "cover_image":
        cover = value if isinstance(value, dict) else {}
        if not cover:
            return None
        return {
            "image_id": str(cover.get("image_id") or "").strip(),
            "src": _normalize_url(str(cover.get("src") or "")),
            "alt": re.sub(
                r"\s+",
                " ",
                unescape(str(cover.get("alt") or "")),
            ).strip(),
        }
    if isinstance(value, str):
        normalized = re.sub(r">\s+<", "><", value.strip())
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = re.sub(r">\s+", ">", normalized)
        normalized = re.sub(r"\s+<", "<", normalized)
        return normalized
    if isinstance(value, dict):
        return {
            key: _normalize_value(child)
            for key, child in sorted(value.items())
        }
    if isinstance(value, list):
        return [_normalize_value(child) for child in value]
    return value


def _normalize_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        split = urlsplit(raw)
        return urlunsplit(
            (
                split.scheme.casefold(),
                split.netloc.casefold(),
                split.path,
                split.query,
                "",
            )
        )
    except ValueError:
        return raw


def _observation_for(action: dict[str, Any]) -> dict[str, Any]:
    strategy_decision = action.get("strategy_decision") or {}
    evidence = (
        strategy_decision.get("execution_evidence")
        or strategy_decision.get("evidence")
        or {}
    )
    return {
        "observation_id": str(uuid4()),
        "business_id": action.get("business_id"),
        "site_id": action.get("site_id"),
        "run_id": action.get("run_id"),
        "action_id": action.get("action_id"),
        "target_url": action.get("target_url"),
        "action_type": action.get("action_type"),
        "page_type": action.get("page_type"),
        "target_asset_id": action.get("target_asset_id"),
        "remote_object_id": action.get("remote_object_id"),
        "connector_id": action.get("connector_id"),
        "connector_type": action.get("connector_type"),
        "adapter_identity": action.get("adapter_identity"),
        "strategy": action.get("strategy"),
        "topic": action.get("topic"),
        "generation_mode": action.get("generation_mode"),
        "generation_provider": action.get("generation_provider"),
        "generation_model": action.get("generation_model"),
        "article_id": action.get("article_id"),
        "execution_task_id": action.get("execution_task_id"),
        "publish_task_id": action.get("publish_task_id"),
        "effect_id": action.get("effect_id"),
        "before_evidence_snapshot": action.get("before_snapshot") or {},
        "after_evidence_snapshot": action.get("readback") or {},
        "before_snapshot_hash": _hash(action.get("before_snapshot") or {}),
        "after_snapshot_hash": _hash(action.get("readback") or {}),
        "gsc_page_baseline": evidence.get("gsc_page_baseline")
        or evidence.get("gsc")
        or {},
        "ga4_landing_page_baseline": evidence.get("ga4_landing_page_baseline")
        or evidence.get("ga4")
        or {},
        "t0_required_checks": [
            "http_status",
            "title",
            "description",
            "canonical",
            "robots",
            "page_accessibility",
        ],
        "executed_at": action.get("executed_at"),
        "next_check_at": action.get("executed_at") or _now(),
        "checkpoints_days": [0, 7, 14, 28, 56, 90],
        "checkpoint_statuses": {
            "0": "pending",
            "7": "pending",
            "14": "pending",
            "28": "pending",
            "56": "pending",
            "90": "pending",
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


async def _validate_store_lineage(
    store: ActionStore, action: dict[str, Any]
) -> None:
    validator = getattr(store, "validate_lineage", None)
    if validator is not None:
        await validator(action)


async def _validate_store_wave(
    store: ActionStore, action: dict[str, Any]
) -> None:
    validator = getattr(store, "validate_wave", None)
    if validator is not None:
        await validator(action)


async def _reconcile_parent_run_if_terminal(
    store: ActionStore, action: dict[str, Any]
) -> None:
    if action.get("status") not in {"completed", "blocked", "failed", "canceled"}:
        return
    run_id = action.get("run_id")
    reconciler = getattr(store, "reconcile_parent_run", None)
    if run_id and reconciler is not None:
        await reconciler(str(run_id))


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
    "execute_action_and_reconcile",
    "get_action",
    "heartbeat_action",
    "preview_action",
    "recover_action",
    "recover_action_and_reconcile",
    "rollback_action",
    "rollback_preview",
]
