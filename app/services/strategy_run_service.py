"""Persistent orchestration contract for autonomous SEO strategy runs.

This module deliberately stores runs and events in ``seo_agent.tasks`` JSON so
the public run API can be introduced without a schema migration. It only owns
coordination metadata; creating a run never starts an external operation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.autonomous_strategy_orchestrator import StrategyContractError

StrategyPlanner = Callable[..., Awaitable[dict[str, Any]]]
SiteDiscoverer = Callable[..., Awaitable[list[dict[str, Any]]]]
CapabilityLoader = Callable[..., Awaitable[dict[str, Any]]]
ActionLoader = Callable[..., Awaitable[list[dict[str, Any]]]]
ActionFactory = Callable[..., Awaitable[dict[str, Any]]]
EvidenceGatherer = Callable[..., Awaitable[dict[str, Any]]]


RUN_KIND = "strategy_run"
EVENT_KIND = "strategy_run_event"
# Persisted V2 rows may still carry these retired automatic-refresh stages.
# They remain readable only so an explicit resume can move them into the
# current AI-led research revision path without deleting audit history.
_LEGACY_EVIDENCE_REFRESH_STATUSES = frozenset({"refreshing_evidence", "replanning"})
RUN_STATUSES = frozenset(
    {
        "queued",
        "discovering_sites",
        "checking_capabilities",
        "gathering_evidence",
        "ai_researching",
        "proposed_actions_submitted",
        "safety_reviewing",
        "planning",
        "zero_action_reviewing",
        "research_revision_required",
        "refreshing_evidence",
        "replanning",
        "awaiting_approval",
        "executing",
        "verifying",
        "observing",
        "completed",
        "partial",
        "blocked",
        "failed",
        "canceled",
    }
)
TERMINAL_RUN_STATUSES = frozenset({"completed", "partial", "blocked", "failed", "canceled"})
_DB_STATUS = {
    "queued": "queued",
    "discovering_sites": "running",
    "checking_capabilities": "running",
    "gathering_evidence": "running",
    "ai_researching": "blocked",
    "proposed_actions_submitted": "queued",
    "safety_reviewing": "running",
    "planning": "running",
    "zero_action_reviewing": "running",
    "research_revision_required": "blocked",
    "refreshing_evidence": "running",
    "replanning": "running",
    "awaiting_approval": "blocked",
    "executing": "running",
    "verifying": "running",
    "observing": "running",
    "completed": "done",
    "partial": "done",
    "blocked": "blocked",
    "failed": "failed",
    "canceled": "canceled",
}
_TRANSITIONS = {
    "queued": {"discovering_sites", "canceled", "failed"},
    "discovering_sites": {"checking_capabilities", "blocked", "failed", "canceled"},
    "checking_capabilities": {"gathering_evidence", "blocked", "failed", "canceled"},
    "gathering_evidence": {
        "ai_researching",
        "blocked",
        "failed",
        "canceled",
    },
    "ai_researching": {
        "proposed_actions_submitted",
        "blocked",
        "failed",
        "canceled",
    },
    "proposed_actions_submitted": {
        "safety_reviewing",
        "failed",
        "canceled",
    },
    "safety_reviewing": {"planning", "blocked", "failed", "canceled"},
    "planning": {
        "zero_action_reviewing",
        "awaiting_approval",
        "executing",
        "observing",
        "completed",
        "partial",
        "blocked",
        "failed",
        "canceled",
    },
    "refreshing_evidence": {
        "research_revision_required",
        "blocked",
        "failed",
        "canceled",
    },
    "replanning": {
        "research_revision_required",
        "blocked",
        "failed",
        "canceled",
    },
    "zero_action_reviewing": {
        "research_revision_required",
        "completed",
        "blocked",
        "failed",
        "canceled",
    },
    "research_revision_required": {
        "ai_researching",
        "blocked",
        "failed",
        "canceled",
    },
    "awaiting_approval": {"executing", "blocked", "failed", "canceled"},
    "executing": {"verifying", "partial", "failed", "canceled"},
    "verifying": {"observing", "completed", "partial", "failed", "canceled"},
    "observing": {"completed", "partial", "failed", "canceled"},
}


def validate_run_transition(current_status: str, next_status: str) -> None:
    if current_status not in RUN_STATUSES or next_status not in RUN_STATUSES:
        raise ValueError("unknown strategy run status")
    if current_status in TERMINAL_RUN_STATUSES:
        raise ValueError(f"strategy run status {current_status} is terminal")
    if next_status not in _TRANSITIONS.get(current_status, set()):
        raise ValueError(
            f"illegal strategy run transition: {current_status} -> {next_status}"
        )


def derive_child_idempotency_key(
    run_id: str, site_id: str, action_type: str, target_identity: str
) -> str:
    material = "\x1f".join((run_id, site_id, action_type, target_identity))
    return "strategy-action:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


async def create_strategy_run(
    session: AsyncSession,
    *,
    business_id: str,
    site_ids: list[str] | None,
    scope: str,
    mode: str,
    requested_by: str,
    idempotency_key: str,
    action_budget: int,
    site_quotas: dict[str, int],
    approval_policy: str,
    root_run_id: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    if mode not in {"dry_run", "approval_execution"}:
        raise ValueError("mode must be dry_run or approval_execution")
    if scope not in {"all_sites", "selected_sites"}:
        raise ValueError("scope must be all_sites or selected_sites")
    normalized_site_ids = sorted(
        {str(site_id).strip() for site_id in site_ids or [] if str(site_id).strip()}
    )
    if scope == "selected_sites" and not normalized_site_ids:
        raise ValueError("selected_sites scope requires site_ids")
    if not 0 <= int(action_budget) <= 200:
        raise ValueError("action_budget must be between 0 and 200")
    normalized_quotas = {
        str(site_id): max(0, min(int(value), 200))
        for site_id, value in site_quotas.items()
    }
    request_contract = {
        "business_id": business_id,
        "site_ids": normalized_site_ids or None,
        "scope": scope,
        "mode": mode,
        "requested_by": requested_by,
        "action_budget": int(action_budget),
        "site_quotas": normalized_quotas,
        "approval_policy": approval_policy,
        "root_run_id": root_run_id,
        "attempt": attempt,
    }
    request_hash = _stable_json_hash(request_contract)
    lock_key = f"strategy-run:{business_id}:{idempotency_key}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": lock_key},
    )
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, decision, created_at, updated_at,
                       started_at, finished_at
                  FROM seo_agent.tasks
                 WHERE task_type = 'review'
                   AND payload->>'kind' = :kind
                   AND payload->>'business_id' = :business_id
                   AND payload->>'idempotency_key' = :idempotency_key
                 ORDER BY created_at ASC
                 LIMIT 1
                """
            ),
            {
                "kind": RUN_KIND,
                "business_id": business_id,
                "idempotency_key": idempotency_key,
            },
        )
    ).mappings().first()
    if existing:
        existing_payload = dict(existing.get("payload") or {})
        existing_hash = existing_payload.get("request_hash")
        if not existing_hash:
            existing_hash = _stable_json_hash(
                {
                    "business_id": existing_payload.get("business_id"),
                    "site_ids": sorted(existing_payload.get("site_ids") or [])
                    or None,
                    "scope": existing_payload.get("scope"),
                    "mode": existing_payload.get("mode"),
                    "requested_by": existing_payload.get("requested_by"),
                    "action_budget": int(
                        existing_payload.get("action_budget") or 0
                    ),
                    "site_quotas": existing_payload.get("site_quotas") or {},
                    "approval_policy": existing_payload.get(
                        "approval_policy"
                    ),
                    "root_run_id": existing_payload.get("root_run_id"),
                    "attempt": int(existing_payload.get("attempt") or 1),
                }
            )
        if existing_hash != request_hash:
            from app.services.autonomous_strategy_orchestrator import (
                StrategyContractError,
            )

            raise StrategyContractError(
                "DECISION_INPUT_CHANGED",
                "idempotency key is already bound to a different Run request",
            )
        await session.commit()
        result = _serialize_run(dict(existing))
        result["idempotency_replayed"] = True
        return result

    run_id = str(uuid4())
    payload = {
        "kind": RUN_KIND,
        "business_id": business_id,
        "site_ids": normalized_site_ids or None,
        "scope": scope,
        "mode": mode,
        "requested_by": requested_by,
        "idempotency_key": idempotency_key,
        "action_budget": int(action_budget),
        "site_quotas": normalized_quotas,
        "approval_policy": approval_policy,
        "root_run_id": root_run_id or run_id,
        "attempt": attempt,
        "request_hash": request_hash,
    }
    decision = {
        "status": "queued",
        "current_stage": "queued",
        "counts": {
            "executed": 0,
            "awaiting_approval": 0,
            "execute_now": 0,
            "deferred": 0,
            "hold": 0,
            "configuration_repair": 0,
            "failed": 0,
        },
        "site_results": [],
        "exceptions": [],
        "observation_ids": [],
        "next_action": "poll",
        "cancel_requested": False,
    }
    inserted = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, priority, title, payload, decision)
                VALUES
                  (CAST(:id AS uuid), 'review', 'queued', 'P1', :title,
                   CAST(:payload AS jsonb), CAST(:decision AS jsonb))
                RETURNING id::text AS id, payload, decision, created_at, updated_at,
                          started_at, finished_at
                """
            ),
            {
                "id": run_id,
                "title": f"SEO strategy run: {business_id}",
                "payload": json.dumps(payload, ensure_ascii=False),
                "decision": json.dumps(decision, ensure_ascii=False),
            },
        )
    ).mappings().first()
    await session.commit()
    result = _serialize_run(dict(inserted))
    result["idempotency_replayed"] = False
    return result


def _stable_json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


async def get_strategy_run(
    session: AsyncSession, *, run_id: str
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, decision, created_at, updated_at,
                       started_at, finished_at, error_message
                  FROM seo_agent.tasks
                 WHERE id = CAST(:run_id AS uuid) AND task_type = 'review'
                   AND payload->>'kind' = :kind
                """
            ),
            {"run_id": run_id, "kind": RUN_KIND},
        )
    ).mappings().first()
    return _serialize_run(dict(row)) if row else None


async def list_strategy_runs(
    session: AsyncSession,
    *,
    business_id: str | None = None,
    status: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if status is not None and status not in RUN_STATUSES:
        raise ValueError("unknown strategy run status")
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, decision, created_at, updated_at,
                       started_at, finished_at, error_message
                 FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = :kind
                   AND (
                     CAST(:business_id AS text) IS NULL
                     OR payload->>'business_id' = CAST(:business_id AS text)
                   )
                   AND (
                     CAST(:status AS text) IS NULL
                     OR decision->>'status' = CAST(:status AS text)
                   )
                   AND (CAST(:from_at AS timestamptz) IS NULL OR created_at >= CAST(:from_at AS timestamptz))
                   AND (CAST(:to_at AS timestamptz) IS NULL OR created_at <= CAST(:to_at AS timestamptz))
                 ORDER BY created_at DESC
                 LIMIT :limit
                """
            ),
            {
                "kind": RUN_KIND,
                "business_id": business_id,
                "status": status,
                "from_at": from_at,
                "to_at": to_at,
                "limit": max(1, min(limit, 500)),
            },
        )
    ).mappings().all()
    return [_serialize_run(dict(row)) for row in rows]


async def transition_strategy_run(
    session: AsyncSession,
    *,
    run_id: str,
    current_status: str,
    next_status: str,
    updates: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    validate_run_transition(current_status, next_status)
    patch = {"status": next_status, "current_stage": next_status, **(updates or {})}
    row = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status = :db_status,
                       decision = decision || CAST(:patch AS jsonb),
                       started_at = CASE WHEN :mark_started THEN COALESCE(started_at, now()) ELSE started_at END,
                       finished_at = CASE WHEN :mark_finished THEN now() ELSE finished_at END,
                       updated_at = now()
                 WHERE id = CAST(:run_id AS uuid) AND task_type = 'review'
                   AND payload->>'kind' = :kind
                   AND decision->>'status' = :current_status
                RETURNING id::text AS id, payload, decision, created_at, updated_at,
                          started_at, finished_at, error_message
                """
            ),
            {
                "run_id": run_id,
                "kind": RUN_KIND,
                "current_status": current_status,
                "next_status": next_status,
                "db_status": _DB_STATUS[next_status],
                "patch": json.dumps(patch, ensure_ascii=False),
                "mark_started": next_status not in {"queued"},
                "mark_finished": next_status in TERMINAL_RUN_STATUSES,
            },
        )
    ).mappings().first()
    if not row:
        if commit:
            await session.commit()
        raise ValueError("strategy run changed concurrently or was not found")
    if commit:
        await session.commit()
    return _serialize_run(dict(row))


async def append_strategy_run_event(
    session: AsyncSession,
    *,
    run_id: str,
    stage: str,
    event_type: str,
    message: str,
    site_id: str | None = None,
    data: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    if event_type not in {"stage_started", "stage_completed", "warning", "error", "action"}:
        raise ValueError("unknown strategy run event type")
    event_id = str(uuid4())
    event_key = (
        f"{run_id}:{stage}:{event_type}"
        if event_type in {"stage_started", "stage_completed"}
        else f"{run_id}:{stage}:{event_type}:{event_id}"
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:event_key))"),
        {"event_key": f"strategy-run-event:{event_key}"},
    )
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, created_at
                  FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = :kind
                   AND payload->>'event_key' = :event_key
                 ORDER BY created_at ASC LIMIT 1
                """
            ),
            {"kind": EVENT_KIND, "event_key": event_key},
        )
    ).mappings().first()
    if existing:
        if commit:
            await session.commit()
        return _serialize_event(dict(existing))
    payload = {
        "kind": EVENT_KIND,
        "event_key": event_key,
        "run_id": run_id,
        "stage": stage,
        "event_type": event_type,
        "site_id": site_id,
        "message": message,
        "data": data or {},
    }
    row = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, priority, title, payload, decision, finished_at)
                SELECT CAST(:id AS uuid), 'review', 'done', 'P3', :title,
                       CAST(:payload AS jsonb), '{}'::jsonb, now()
                 WHERE EXISTS (
                   SELECT 1 FROM seo_agent.tasks
                    WHERE id = CAST(:run_id AS uuid) AND payload->>'kind' = :run_kind
                 )
                RETURNING id::text AS id, payload, created_at
                """
            ),
            {
                "id": event_id,
                "run_id": run_id,
                "run_kind": RUN_KIND,
                "title": f"Strategy run event: {event_type}",
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )
    ).mappings().first()
    if not row:
        raise ValueError("strategy run not found")
    if commit:
        await session.commit()
    return _serialize_event(dict(row))


async def list_strategy_run_events(
    session: AsyncSession, *, run_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, created_at
                  FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = :kind
                   AND payload->>'run_id' = :run_id
                 ORDER BY created_at ASC
                 LIMIT :limit
                """
            ),
            {"kind": EVENT_KIND, "run_id": run_id, "limit": max(1, min(limit, 1000))},
        )
    ).mappings().all()
    return [_serialize_event(dict(row)) for row in rows]


async def cancel_strategy_run(
    session: AsyncSession, *, run_id: str, requested_by: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if idempotency_key and not await _claim_control_request(
        session, run_id=run_id, operation="cancel", key=idempotency_key
    ):
        replay = await get_strategy_run(session, run_id=run_id)
        return {**replay, "idempotency_replayed": True} if replay else None
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        raise ValueError("strategy run not found")
    if run["status"] in TERMINAL_RUN_STATUSES:
        if run["status"] == "canceled":
            await _cancel_unexecuted_run_items(session, run_id=run_id)
            refreshed = await get_strategy_run(session, run_id=run_id)
            return refreshed or run
        return run
    # Cancellation is cooperative: running actions are not rolled back or
    # misrepresented. The orchestrator must check this flag before each stage.
    canceled = await transition_strategy_run(
        session,
        run_id=run_id,
        current_status=run["status"],
        next_status="canceled",
        updates={"cancel_requested": True, "cancel_requested_by": requested_by},
    )
    await _cancel_unexecuted_run_items(session, run_id=run_id)
    return (await get_strategy_run(session, run_id=run_id)) or canceled


async def _cancel_unexecuted_run_items(
    session: AsyncSession, *, run_id: str
) -> None:
    """Retire only work that never entered a remote-write attempt."""
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET status='canceled',
                   payload=payload || jsonb_build_object(
                     'status','canceled',
                     'result','canceled',
                     'canceled_at',now()::text
                   ),
                   finished_at=COALESCE(finished_at, now()),
                   updated_at=now()
             WHERE task_type='review'
               AND payload->>'kind'='strategy_action'
               AND payload->>'run_id'=CAST(:run_id AS text)
               AND payload->>'status' IN ('planned','previewed','approved')
            """
        ),
        {"run_id": run_id},
    )
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET status='canceled', finished_at=COALESCE(finished_at, now()),
                   updated_at=now()
             WHERE task_type='review'
               AND payload->>'kind' IN ('seo_strategy','strategy_plan')
               AND payload->>'strategy_run_id'=CAST(:run_id AS text)
               AND status IN ('queued','running')
            """
        ),
        {"run_id": run_id},
    )
    await session.commit()


async def retry_strategy_run(
    session: AsyncSession,
    *,
    run_id: str,
    requested_by: str,
    idempotency_key: str,
) -> dict[str, Any]:
    original = await get_strategy_run(session, run_id=run_id)
    if not original:
        raise ValueError("strategy run not found")
    if original["status"] not in {"partial", "blocked", "failed", "canceled"}:
        raise ValueError("only partial, blocked, failed, or canceled runs can be retried")
    root_run_id = original.get("root_run_id") or original["run_id"]
    unresolved = (
        await session.execute(
            text(
                """
                WITH lineage AS (
                    SELECT id::text AS run_id
                      FROM seo_agent.tasks
                     WHERE payload->>'kind' = :run_kind
                       AND (
                         id::text = :root_run_id
                         OR payload->>'root_run_id' = :root_run_id
                       )
                )
                SELECT COALESCE(
                    payload->>'remote_outcome',
                    payload->>'recovery_status',
                    decision->>'remote_outcome'
                )
                  FROM seo_agent.tasks
                 WHERE COALESCE(payload->>'run_id', payload->>'strategy_run_id')
                       IN (SELECT run_id FROM lineage)
                   AND COALESCE(
                         payload->>'remote_outcome',
                         payload->>'recovery_status',
                         decision->>'remote_outcome'
                       ) IN (
                         'partially_applied',
                         'unknown_remote_state',
                         'identity_conflict'
                       )
                   AND status IN ('queued', 'running', 'blocked', 'failed')
                 LIMIT 1
                """
            ),
            {"root_run_id": root_run_id, "run_kind": RUN_KIND},
        )
    ).scalar_one_or_none()
    if unresolved:
        raise ValueError(
            "REMOTE_STATE_REQUIRES_MANUAL_READBACK: resolve the uncertain remote outcome before retrying"
        )
    return await create_strategy_run(
        session,
        business_id=original["business_id"],
        site_ids=original.get("site_ids"),
        scope=original["scope"],
        mode=original["mode"],
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        action_budget=original["action_budget"],
        site_quotas=original["site_quotas"],
        approval_policy=original["approval_policy"],
        root_run_id=root_run_id,
        attempt=int(original.get("attempt") or 1) + 1,
    )


async def run_strategy_run(
    session: AsyncSession,
    *,
    run_id: str,
    planner: StrategyPlanner | None = None,
    site_discoverer: SiteDiscoverer | None = None,
    capability_loader: CapabilityLoader | None = None,
    action_loader: ActionLoader | None = None,
    action_factory: ActionFactory | None = None,
    evidence_gatherer: EvidenceGatherer | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Run or resume the safe pre-approval stages.

    Stage outputs are persisted on the run before advancing, so a process can
    resume at the current stage without repeating completed work.  This worker
    intentionally stops at ``awaiting_approval`` or ``blocked``; it never
    performs an unapproved remote write.
    """
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        raise ValueError("strategy run not found")
    if idempotency_key and not await _claim_control_request(
        session, run_id=run_id, operation="start", key=idempotency_key
    ):
        return {**run, "idempotency_replayed": True}
    if run["status"] in TERMINAL_RUN_STATUSES:
        return {**run, "start_replayed": True}
    try:
        if run["status"] in _LEGACY_EVIDENCE_REFRESH_STATUSES:
            legacy_status = run["status"]
            await _stage_started(session, run_id, legacy_status)
            run = await _complete_stage(
                session,
                run_id=run_id,
                current=legacy_status,
                next_status="research_revision_required",
                updates={
                    "next_action": "capture_research",
                    "stage_outputs": _merge_stage_output(
                        run,
                        legacy_status,
                        {
                            "legacy_state_retired": True,
                            "replacement": "ai_research_revision",
                        },
                    ),
                },
            )
            return {
                **run,
                "legacy_state_recovered": True,
                "next_action": "capture_research",
            }
        if run["status"] in {
            "awaiting_approval",
            "executing",
            "verifying",
            "observing",
        }:
            return await reconcile_strategy_run(
                session,
                run_id=run_id,
                action_loader=action_loader or _load_run_actions,
            )
        if run["status"] == "queued":
            try:
                run = await transition_strategy_run(
                    session, run_id=run_id, current_status="queued",
                    next_status="discovering_sites",
                )
            except ValueError:
                current = await get_strategy_run(session, run_id=run_id)
                if current:
                    return {**current, "start_replayed": True}
                raise

        if site_discoverer is None:
            site_discoverer = _discover_run_sites
        if capability_loader is None:
            from app.services.site_capability_service import (
                get_business_site_capabilities,
            )
            capability_loader = get_business_site_capabilities
        evidence_gatherer = evidence_gatherer or _gather_run_evidence
        action_factory = action_factory or _create_unified_action
        if run["status"] == "discovering_sites":
            await _stage_started(session, run_id, "discovering_sites")
            sites = await site_discoverer(
                session, business_id=run["business_id"], scope=run["scope"],
                site_ids=run.get("site_ids"),
            )
            if not sites:
                raise RuntimeError("strategy run discovered no enabled sites")
            run = await _complete_stage(
                session, run_id=run_id, current="discovering_sites",
                next_status="checking_capabilities",
                updates={
                    "discovered_sites": sites,
                    "discovered_site_count": len(sites),
                    "stage_outputs": _merge_stage_output(
                        run, "discovering_sites",
                        {"site_count": len(sites), "site_ids": [str(s["id"]) for s in sites]},
                    ),
                },
            )

        if run["status"] == "checking_capabilities":
            await _stage_started(session, run_id, "checking_capabilities")
            capability_snapshot = await capability_loader(session, run["business_id"])
            wanted = {str(site["id"]) for site in run.get("discovered_sites") or []}
            snapshot_sites = [
                item for item in capability_snapshot.get("sites", [])
                if str(item.get("site_id")) in wanted
            ]
            capability_snapshot = {**capability_snapshot, "sites": snapshot_sites}
            if len(snapshot_sites) != len(wanted):
                raise RuntimeError("capability snapshot does not cover every discovered site")
            run = await _complete_stage(
                session, run_id=run_id, current="checking_capabilities",
                next_status="gathering_evidence",
                updates={
                    "capability_snapshot": capability_snapshot,
                    "stage_outputs": _merge_stage_output(
                        run, "checking_capabilities",
                        {"generated_at": capability_snapshot.get("generated_at"),
                         "site_count": len(snapshot_sites)},
                    ),
                },
            )

        if run["status"] == "gathering_evidence":
            await _stage_started(session, run_id, "gathering_evidence")
            evidence_snapshot = await evidence_gatherer(
                session, business_id=run["business_id"],
                sites=run.get("discovered_sites") or [],
                run=run,
            )
            run = await _complete_stage(
                session, run_id=run_id, current="gathering_evidence",
                next_status="ai_researching",
                updates={
                    "evidence_snapshot": evidence_snapshot,
                    "evidence_snapshot_id": evidence_snapshot.get("snapshot_id"),
                    "stage_outputs": _merge_stage_output(
                        run, "gathering_evidence",
                        {"capability_snapshot_generated_at":
                         (run.get("capability_snapshot") or {}).get("generated_at"),
                          "site_ids": [str(s["id"]) for s in run.get("discovered_sites") or []],
                          "snapshot_id": evidence_snapshot.get("snapshot_id")},
                    ),
                    "next_action": "capture_research",
                },
            )

        if run["status"] in {"ai_researching", "research_revision_required"}:
            return {
                **run,
                "start_replayed": True,
                "next_action": (
                    "capture_research"
                    if not run.get("research_portfolio")
                    or run["status"] == "research_revision_required"
                    else "submit_proposed_actions"
                ),
            }

        if run["status"] == "proposed_actions_submitted":
            await _stage_started(session, run_id, "proposed_actions_submitted")
            if not run.get("research_portfolio") or not run.get("proposed_actions"):
                raise RuntimeError(
                    "formal safety review requires Research Portfolio and Proposed Actions"
                )
            run = await _complete_stage(
                session,
                run_id=run_id,
                current="proposed_actions_submitted",
                next_status="safety_reviewing",
                updates={
                    "stage_outputs": _merge_stage_output(
                        run,
                        "proposed_actions_submitted",
                        {
                            "research_portfolio_hash": run.get(
                                "research_portfolio_hash"
                            ),
                            "proposed_action_hash": run.get(
                                "proposed_action_hash"
                            ),
                            "proposed_action_count": len(
                                run.get("proposed_actions") or []
                            ),
                        },
                    ),
                    "next_action": "safety_review",
                },
            )

        if run["status"] == "safety_reviewing":
            await _stage_started(session, run_id, "safety_reviewing")
            run = await _complete_stage(
                session,
                run_id=run_id,
                current="safety_reviewing",
                next_status="planning",
                updates={
                    "stage_outputs": _merge_stage_output(
                        run,
                        "safety_reviewing",
                        {
                            "capability_snapshot_generated_at": (
                                run.get("capability_snapshot") or {}
                            ).get("generated_at"),
                            "proposed_action_hash": run.get(
                                "proposed_action_hash"
                            ),
                        },
                    ),
                    "next_action": "build_formal_plan",
                },
            )

        if run["status"] != "planning":
            return {**run, "start_replayed": True}
        stage = run["status"]
        await _stage_started(session, run_id, stage)
        planned = await _plan_all_sites(session, run=run, planner=planner)
        planned = _bind_planned_actions(planned)
        planned = _restrict_plan_to_discovered_sites(
            planned,
            run.get("discovered_sites") or [],
        )
        summary = _summarize_plan(planned)
        if summary["discovered_site_count"] != summary["decided_site_count"]:
            raise RuntimeError(
                "strategy coverage incomplete: discovered_site_count must equal decided_site_count"
            )
        discovered_ids = {str(site["id"]) for site in run.get("discovered_sites") or []}
        decided_ids = {str(item.get("site_id")) for item in summary["site_results"]}
        if discovered_ids != decided_ids:
            raise RuntimeError(
                "strategy coverage incomplete: every discovered site must have exactly one decision"
            )
        reviewed_actions = list(
            planned.get("reviewed_actions")
            or (planned.get("coverage_matrix") or {}).get("options")
            or []
        )
        from app.services.autonomous_strategy_orchestrator import (
            evaluate_zero_action_review,
        )

        zero_action_review = await evaluate_zero_action_review(
            session,
            run=run,
            reviewed_actions=reviewed_actions,
        )
        if zero_action_review["required"]:
            run = await _complete_stage(
                session,
                run_id=run_id,
                current=stage,
                next_status="zero_action_reviewing",
                updates={
                    **summary,
                    "reviewed_actions": reviewed_actions,
                    "zero_action_review": zero_action_review,
                    "stage_outputs": _merge_stage_output(
                        run,
                        stage,
                        {
                            "discovered_site_count": summary[
                                "discovered_site_count"
                            ],
                            "decided_site_count": summary["decided_site_count"],
                            "zero_action_review_required": True,
                        },
                    ),
                    "next_action": "zero_action_review",
                },
            )
            zero_result = str(zero_action_review["result"])
            if zero_result == "research_revision_required":
                return await _complete_stage(
                    session,
                    run_id=run_id,
                    current="zero_action_reviewing",
                    next_status="research_revision_required",
                    updates={
                        "zero_action_review": zero_action_review,
                        "next_action": "capture_research",
                    },
                )
            if zero_result == "hard_blocked":
                return await _complete_stage(
                    session,
                    run_id=run_id,
                    current="zero_action_reviewing",
                    next_status="blocked",
                    updates={
                        "zero_action_review": zero_action_review,
                        "next_action": "resolve_hard_blocks",
                    },
                )
            return await _complete_stage(
                session,
                run_id=run_id,
                current="zero_action_reviewing",
                next_status="completed",
                updates={
                    "zero_action_review": zero_action_review,
                    "next_action": "none",
                },
            )

        if summary["counts"]["execute_now"] == 0:
            return await _complete_stage(
                session,
                run_id=run_id,
                current=stage,
                next_status="completed",
                updates={
                    **summary,
                    "reviewed_actions": reviewed_actions,
                    "zero_action_review": zero_action_review,
                    "next_action": "wait_for_deferred_reevaluation",
                },
            )

        if run.get("mode") == "dry_run":
            return await _complete_stage(
                session,
                run_id=run_id,
                current=stage,
                next_status="completed",
                updates={
                    **summary,
                    "reviewed_actions": reviewed_actions,
                    "zero_action_review": zero_action_review,
                    "shadow_run": True,
                    "action_ids": [],
                    "next_action": "none",
                },
            )

        next_status = (
            "awaiting_approval"
            if summary["counts"]["awaiting_approval"] > 0
            else "blocked"
        )
        created_actions = []
        if next_status == "awaiting_approval":
            for decision in summary["executable_decisions"]:
                action = str(
                    decision.get("action") or decision.get("strategy_type") or ""
                )
                created_actions.append(
                    await action_factory(
                        session, run=run, decision=decision,
                        capability_snapshot=next(
                            (
                                item for item in
                                (run.get("capability_snapshot") or {}).get("sites", [])
                                if str(item.get("site_id")) == str(decision["site_id"])
                            ),
                            None,
                        ),
                        idempotency_key=derive_child_idempotency_key(
                            run_id, str(decision["site_id"]), action,
                            str(
                                decision.get("source_strategy_task_id")
                                or decision.get("target_url")
                                or decision.get("canonical_url")
                                or action
                            ),
                        ),
                    )
                )
            if not created_actions:
                raise RuntimeError("approval run has no unified strategy actions")
        return await _complete_stage(
            session, run_id=run_id, current=stage, next_status=next_status,
            updates={
                **summary,
                "reviewed_actions": reviewed_actions,
                "zero_action_review": zero_action_review,
                "action_ids": [item.get("action_id") for item in created_actions],
                "stage_outputs": _merge_stage_output(
                    run, stage,
                    {"discovered_site_count": summary["discovered_site_count"],
                     "decided_site_count": summary["decided_site_count"],
                     "source_audit_batch_id": summary.get("source_audit_batch_id")},
                ),
                "next_action": "approve_actions" if next_status == "awaiting_approval"
                else "resolve_blocks",
            },
        )
    except Exception as error:
        error_code = str(getattr(error, "code", "") or "STRATEGY_RUN_FAILED")
        if hasattr(session, "rollback"):
            await session.rollback()
        if idempotency_key:
            await _release_control_request(
                session,
                run_id=run_id,
                operation="start",
                key=idempotency_key,
            )
        current = await get_strategy_run(session, run_id=run_id)
        if current and current["status"] not in TERMINAL_RUN_STATUSES:
            await append_strategy_run_event(
                session,
                run_id=run_id,
                stage=current["current_stage"],
                event_type="error",
                message="Strategy run failed",
                data={
                    "error_code": error_code,
                    "error": str(error)[:1000],
                },
            )
            return await transition_strategy_run(
                session,
                run_id=run_id,
                current_status=current["status"],
                next_status="failed",
                updates={
                    "exceptions": [
                        {
                            "type": type(error).__name__,
                            "error_code": error_code,
                            "message": str(error)[:1000],
                        }
                    ],
                    "next_action": "retry",
                },
            )
        raise


async def _discover_run_sites(
    session: AsyncSession, *, business_id: str, scope: str,
    site_ids: list[str] | None,
) -> list[dict[str, Any]]:
    if scope == "selected_sites" and not site_ids:
        raise ValueError("selected_sites scope requires site_ids")
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, name, site_type, is_main,
                       strategy_enabled, market, language_code, content_role,
                       content_scope, domain, base_url, api_base_url
                  FROM seo_agent.sites
                 WHERE business_id = :business_id
                    AND status = 'active'
                    AND (:all_sites OR id = ANY(CAST(:site_ids AS uuid[])))
                 ORDER BY is_main DESC, name ASC
                """
            ),
            {
                "business_id": business_id,
                "all_sites": scope == "all_sites",
                "site_ids": site_ids or [],
            },
        )
    ).mappings().all()
    sites = [dict(row) for row in rows]
    if scope == "selected_sites":
        found = {str(site["id"]) for site in sites}
        missing = [item for item in site_ids or [] if str(item) not in found]
        if missing:
            raise ValueError("selected sites are missing, inactive, or outside the business")
    return sites


async def _plan_all_sites(
    session: AsyncSession,
    *,
    run: dict[str, Any],
    planner: StrategyPlanner | None,
) -> dict[str, Any]:
    if planner is None:
        from app.services.autonomous_strategy_orchestrator import (
            build_reviewed_plan,
        )

        return await build_reviewed_plan(session, run=run)
    sites = list(run.get("discovered_sites") or [])
    values = {
        "business_id": run["business_id"],
        "site_ids": (
            [str(site["id"]) for site in sites]
            if run["scope"] == "selected_sites"
            else None
        ),
        "action_budget": run["action_budget"],
        "site_quotas": run["site_quotas"],
        "_strategy_run_id": run.get("run_id"),
    }
    if run.get("proposed_actions"):
        values["proposed_actions"] = list(run["proposed_actions"])
    if run.get("research_portfolio"):
        values["research_portfolio"] = list(run["research_portfolio"])
    return await planner(session, **values)


async def _stage_started(session: AsyncSession, run_id: str, stage: str) -> None:
    await append_strategy_run_event(
        session, run_id=run_id, stage=stage, event_type="stage_started",
        message=f"Stage {stage} started",
    )


async def _complete_stage(
    session: AsyncSession, *, run_id: str, current: str, next_status: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    advanced = await transition_strategy_run(
        session, run_id=run_id, current_status=current,
        next_status=next_status, updates=updates, commit=False,
    )
    await append_strategy_run_event(
        session, run_id=run_id, stage=current, event_type="stage_completed",
        message=f"Stage {current} completed",
        data=updates.get("stage_outputs", {}).get(current, {}), commit=False,
    )
    if hasattr(session, "commit"):
        await session.commit()
    return advanced


def _merge_stage_output(
    run: dict[str, Any], stage: str, output: dict[str, Any]
) -> dict[str, Any]:
    return {**(run.get("stage_outputs") or {}), stage: output}


async def _gather_run_evidence(
    session: AsyncSession,
    *,
    business_id: str,
    sites: list[dict[str, Any]],
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS snapshot_id, payload->>'scanned_at' AS scanned_at
                  FROM seo_agent.tasks
                 WHERE task_type='review'
                   AND payload->>'kind'='content_audit_batch'
                   AND payload->>'business_id'=:business_id
                 ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    if not row:
        captured_at = datetime.now().astimezone().isoformat()
        snapshot_id = "run-evidence-" + _stable_json_hash(
            {
                "run_id": (run or {}).get("run_id"),
                "business_id": business_id,
                "site_ids": [str(site["id"]) for site in sites],
                "captured_at": captured_at,
            }
        )[:32]
        return {
            "snapshot_id": snapshot_id,
            "captured_at": captured_at,
            "business_id": business_id,
            "site_ids": [str(site["id"]) for site in sites],
            "source": "run_evidence_manifest",
            "sources": [
                {
                    "source_type": "content_audit",
                    "collection_status": "empty",
                    "captured_at": captured_at,
                    "limitations": [
                        "No current content audit batch was available."
                    ],
                    "decision_use": (
                        "AI must use current site/API/analytics/SERP evidence "
                        "before submitting Proposed Actions."
                    ),
                }
            ],
        }
    return {
        **dict(row),
        "captured_at": row.get("scanned_at"),
        "business_id": business_id,
        "site_ids": [str(site["id"]) for site in sites],
        "source": "content_audit_batch",
    }


async def _create_unified_action(
    session: AsyncSession, *, run: dict[str, Any], decision: dict[str, Any],
    idempotency_key: str, capability_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    from app.services.strategy_action_service import SQLActionStore, create_action
    action_type = str(
        decision.get("action_type")
        or decision.get("action")
        or decision.get("strategy_type")
    )
    return await create_action(
        SQLActionStore(session),
        run_id=run["run_id"],
        run_mode=run.get("mode"),
        business_id=run["business_id"],
        site_id=str(decision["site_id"]),
        plan_id=decision.get("plan_id"),
        action_type=action_type,
        editorial_action=decision.get("editorial_action"),
        page_type=decision.get("page_type"),
        target_url=decision.get("target_url") or decision.get("canonical_url"),
        target_asset_id=decision.get("target_asset_id"),
        remote_object_id=decision.get("remote_object_id"),
        corrective_of_action_id=decision.get("corrective_of_action_id"),
        corrective_action_validated=decision.get("corrective_action_validated") is True,
        corrective_reason=decision.get("corrective_reason"),
        connector_id=decision.get("connector_id"),
        connector_type=decision.get("connector_type"),
        expected_fields=list(decision.get("expected_fields") or []),
        adapter_identity=(
            (capability_snapshot or {}).get("action_adapters", {}).get(action_type)
        ),
        source_strategy_task_id=decision.get("source_strategy_task_id"),
        evidence_snapshot_id=run.get("evidence_snapshot_id"),
        scope_key=decision.get("scope_key"),
        lock_scope=decision.get("lock_scope"),
        lock_key=decision.get("lock_key"),
        strategy_fingerprint=decision.get("strategy_fingerprint"),
        evidence_fingerprint=decision.get("evidence_fingerprint"),
        topic=decision.get("query"),
        schedule_class=decision.get("schedule_class"),
        wave_number=decision.get("wave_number") or 1,
        idempotency_key=idempotency_key,
        risk_level=decision.get("risk_level") or "medium",
        approval_requirement="approval_required",
        capability_snapshot=capability_snapshot,
        strategy_decision=decision.get("strategy_decision") or decision,
    )


async def _claim_control_request(
    session: AsyncSession, *, run_id: str, operation: str, key: str
) -> bool:
    lock_key = f"strategy-run-control:{run_id}:{key}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key}
    )
    row = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET decision=jsonb_set(
                       decision, '{control_idempotency}',
                       COALESCE(decision->'control_idempotency','{}'::jsonb)
                         || jsonb_build_object(
                              CAST(:control_key AS text),
                              CAST(:operation AS text)
                            ),
                       true),
                       updated_at=now()
                 WHERE id=CAST(:run_id AS uuid)
                   AND payload->>'kind'=:kind
                   AND NOT COALESCE(decision->'control_idempotency','{}'::jsonb)
                           ? CAST(:control_key AS text)
                RETURNING id
                """
            ),
            {
                "run_id": run_id, "kind": RUN_KIND,
                "control_key": key, "operation": operation,
            },
        )
    ).first()
    if row:
        await session.commit()
        return True

    existing = (
        await session.execute(
            text(
                """
                SELECT decision->'control_idempotency'->>CAST(:control_key AS text)
                  FROM seo_agent.tasks
                 WHERE id=CAST(:run_id AS uuid)
                   AND payload->>'kind'=:kind
                """
            ),
            {"run_id": run_id, "kind": RUN_KIND, "control_key": key},
        )
    ).scalar_one_or_none()
    await session.commit()
    if existing == operation:
        return False
    if existing:
        raise ValueError(
            f"idempotency key is already bound to strategy run operation {existing}"
        )
    raise ValueError("strategy run not found")


async def _release_control_request(
    session: AsyncSession, *, run_id: str, operation: str, key: str
) -> None:
    """Remove a control receipt when the claimed operation fails before completion."""
    lock_key = f"strategy-run-control:{run_id}:{key}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key}
    )
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET decision=jsonb_set(
                   decision,
                   '{control_idempotency}',
                   COALESCE(decision->'control_idempotency','{}'::jsonb)
                     - CAST(:control_key AS text),
                   true
               ),
                   updated_at=now()
             WHERE id=CAST(:run_id AS uuid)
               AND payload->>'kind'=:kind
               AND decision->'control_idempotency'->>CAST(:control_key AS text)
                     = CAST(:operation AS text)
            """
        ),
        {
            "run_id": run_id,
            "kind": RUN_KIND,
            "control_key": key,
            "operation": operation,
        },
    )
    await session.commit()


async def _load_run_actions(
    session: AsyncSession, *, run_id: str
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT payload
                  FROM seo_agent.tasks
                 WHERE task_type = 'review'
                   AND payload->>'kind' = 'strategy_action'
                   AND payload->>'run_id' = :run_id
                 ORDER BY created_at ASC
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().all()
    return [dict(row["payload"] or {}) for row in rows]


async def _reconcile_terminal_run(
    session: AsyncSession,
    *,
    run_id: str,
    current_status: str,
    next_status: str,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Close a recoverable terminal Run after its persisted Actions converge.

    A Run can become ``partial`` or ``failed`` before a later read-only recovery
    proves every persisted Action was applied. This narrow compare-and-set
    permits only that recovered terminal state to become ``completed``; normal
    terminal-state transitions remain forbidden.
    """
    if current_status not in {"partial", "failed"} or next_status != "completed":
        raise ValueError("unsupported terminal strategy run reconciliation")
    patch = {
        "status": next_status,
        "current_stage": next_status,
        **(updates or {}),
    }
    row = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status = :db_status,
                       decision = decision || CAST(:patch AS jsonb),
                       finished_at = now(),
                       updated_at = now()
                 WHERE id = CAST(:run_id AS uuid)
                   AND task_type = 'review'
                   AND payload->>'kind' = :kind
                   AND decision->>'status' = :current_status
                RETURNING id::text AS id, payload, decision, created_at, updated_at,
                          started_at, finished_at, error_message
                """
            ),
            {
                "run_id": run_id,
                "kind": RUN_KIND,
                "current_status": current_status,
                "db_status": _DB_STATUS[next_status],
                "patch": json.dumps(patch, ensure_ascii=False),
            },
        )
    ).mappings().first()
    if not row:
        await session.commit()
        raise ValueError("strategy run changed concurrently or was not found")
    await session.commit()
    return _serialize_run(dict(row))


async def reconcile_strategy_run(
    session: AsyncSession,
    *,
    run_id: str,
    action_loader: ActionLoader | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Advance a post-approval run from persisted Action outcomes.

    This is the single coordination seam used by Action execution and by the
    public recovery endpoint. Compare-and-set races are retried from the latest
    persisted state, so concurrent terminal Actions cannot strand their Run.
    """
    claimed_control = False
    if idempotency_key:
        claimed_control = await _claim_control_request(
            session, run_id=run_id, operation="reconcile", key=idempotency_key
        )
        if not claimed_control:
            replay = await get_strategy_run(session, run_id=run_id)
            if not replay:
                raise ValueError("strategy run not found")
            return {**replay, "idempotency_replayed": True}

    try:
        loader = action_loader or _load_run_actions
        for _attempt in range(5):
            run = await get_strategy_run(session, run_id=run_id)
            if not run:
                raise ValueError("strategy run not found")
            if run["status"] in {"partial", "failed"}:
                actions = await loader(session, run_id=run_id)
                if actions and all(
                    action.get("status") == "completed" for action in actions
                ):
                    observations = [
                        str(action["observation_id"])
                        for action in actions
                        if action.get("observation_id")
                    ]
                    return await _reconcile_terminal_run(
                        session,
                        run_id=run_id,
                        current_status=str(run["status"]),
                        next_status="completed",
                        updates={
                            "action_outcome_counts": {
                                "completed": len(actions),
                                "blocked": 0,
                                "failed": 0,
                            },
                            "observation_ids": observations,
                            "next_action": "none",
                        },
                    )
            if run["status"] in TERMINAL_RUN_STATUSES:
                actions = await loader(session, run_id=run_id)
                if actions:
                    return await _refresh_terminal_run_summary(
                        session, run=run, actions=actions
                    )
                return {**run, "reconcile_replayed": True}
            if run["status"] not in {
                "awaiting_approval",
                "executing",
                "verifying",
                "observing",
            }:
                return {**run, "reconcile_replayed": True, "next_action": "start"}
            try:
                return await _reconcile_run_actions(
                    session, run=run, action_loader=loader
                )
            except ValueError as error:
                if str(error) != "strategy run changed concurrently or was not found":
                    raise
                if hasattr(session, "rollback"):
                    await session.rollback()
        raise ValueError("strategy run reconciliation remained busy after 5 attempts")
    except Exception:
        if claimed_control:
            if hasattr(session, "rollback"):
                await session.rollback()
            await _release_control_request(
                session,
                run_id=run_id,
                operation="reconcile",
                key=str(idempotency_key),
            )
        raise


async def _reconcile_run_actions(
    session: AsyncSession, *, run: dict[str, Any], action_loader: ActionLoader
) -> dict[str, Any]:
    """Advance post-approval stages from persisted unified action outcomes."""
    run_id = run.get("run_id")
    actions = await action_loader(session, run_id=run_id)
    if not actions:
        return {**run, "start_replayed": True, "next_action": "approve_actions"}
    statuses = {str(action.get("status") or "") for action in actions}
    terminal = {"completed", "blocked", "failed", "canceled"}
    counts, summary_counts, observations = _summarize_action_lifecycle(run, actions)
    if (
        run["status"] == "awaiting_approval"
        and statuses == {"canceled"}
        and all(
            action.get("closure_reason") == "approval_expired_unexecuted"
            for action in actions
        )
    ):
        return await _complete_stage(
            session,
            run_id=run_id,
            current="awaiting_approval",
            next_status="canceled",
            updates={
                "action_outcome_counts": counts,
                "counts": summary_counts,
                "closure_reason": "all_actions_expired_unexecuted",
                "next_action": "none",
            },
        )
    post_approval = {
        str(action.get("status") or "")
        for action in actions
        if str(action.get("status") or "")
        not in {"planned", "previewing", "previewed"}
        and not (
            action.get("status") == "canceled"
            and action.get("closure_reason") == "approval_expired_unexecuted"
        )
    }
    if run["status"] == "awaiting_approval" and post_approval:
        run = await _complete_stage(
            session, run_id=run_id, current="awaiting_approval",
            next_status="executing",
            updates={"action_outcome_counts": counts, "counts": summary_counts},
        )
        await _stage_started(session, run_id, "executing")
    if not statuses.issubset(terminal):
        return {**run, "start_replayed": True, "next_action": "complete_actions"}
    if run["status"] == "executing":
        run = await _complete_stage(
            session, run_id=run_id, current="executing", next_status="verifying",
            updates={"action_outcome_counts": counts, "counts": summary_counts},
        )
        await _stage_started(session, run_id, "verifying")
    if run["status"] == "verifying":
        if counts["completed"] == 0:
            return await _complete_stage(
                session, run_id=run_id, current="verifying",
                next_status="failed" if counts["failed"] else "partial",
                updates={
                    "action_outcome_counts": counts,
                    "counts": summary_counts,
                    "next_action": "resolve_action_failures",
                },
            )
        run = await _complete_stage(
            session, run_id=run_id, current="verifying", next_status="observing",
            updates={
                "action_outcome_counts": counts,
                "counts": summary_counts,
                "observation_ids": observations,
            },
        )
        await _stage_started(session, run_id, "observing")
    if run["status"] == "observing":
        final = "completed" if counts["completed"] == len(actions) else "partial"
        return await _complete_stage(
            session, run_id=run_id, current="observing", next_status=final,
            updates={
                "action_outcome_counts": counts,
                "counts": summary_counts,
                "observation_ids": observations,
                "next_action": "none" if final == "completed" else "resolve_action_failures",
            },
        )
    return run


def _summarize_action_lifecycle(
    run: dict[str, Any], actions: list[dict[str, Any]]
) -> tuple[dict[str, int], dict[str, int], list[str]]:
    outcome_counts = {
        "completed": sum(a.get("status") == "completed" for a in actions),
        "blocked": sum(a.get("status") == "blocked" for a in actions),
        "failed": sum(a.get("status") in {"failed", "canceled"} for a in actions),
    }
    summary_counts = dict(run.get("counts") or {})
    summary_counts.update(
        {
            "executed": outcome_counts["completed"],
            "awaiting_approval": sum(
                str(action.get("status") or "")
                in {"planned", "previewed", "approved"}
                for action in actions
            ),
            "failed": sum(action.get("status") == "failed" for action in actions),
        }
    )
    observations = [
        str(action["observation_id"])
        for action in actions
        if action.get("status") == "completed" and action.get("observation_id")
    ]
    return outcome_counts, summary_counts, observations


async def _refresh_terminal_run_summary(
    session: AsyncSession,
    *,
    run: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Backfill lifecycle counters for terminal Runs without changing outcome."""
    outcome_counts, summary_counts, observations = _summarize_action_lifecycle(
        run, actions
    )
    if (
        run.get("counts") == summary_counts
        and run.get("action_outcome_counts") == outcome_counts
        and list(run.get("observation_ids") or []) == observations
    ):
        return {**run, "reconcile_replayed": True}
    patch = {
        "counts": summary_counts,
        "action_outcome_counts": outcome_counts,
        "observation_ids": observations,
    }
    row = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET decision = decision || CAST(:patch AS jsonb),
                       updated_at = now()
                 WHERE id = CAST(:run_id AS uuid)
                   AND task_type = 'review'
                   AND payload->>'kind' = :kind
                   AND decision->>'status' = :status
                RETURNING id::text AS id, payload, decision, created_at, updated_at,
                          started_at, finished_at, error_message
                """
            ),
            {
                "run_id": run["run_id"],
                "kind": RUN_KIND,
                "status": run["status"],
                "patch": json.dumps(patch, ensure_ascii=False),
            },
        )
    ).mappings().first()
    if not row:
        await session.commit()
        raise ValueError("strategy run changed concurrently or was not found")
    await session.commit()
    return {**_serialize_run(dict(row)), "reconcile_replayed": False}


def _summarize_plan(planned: dict[str, Any]) -> dict[str, Any]:
    coverage = dict(planned.get("coverage_matrix") or {})
    site_scope = list(planned.get("site_scope") or [])
    decisions = list(coverage.get("decisions") or planned.get("decisions") or [])
    discovered = int(coverage.get("discovered_site_count") or len(site_scope))
    decided = int(coverage.get("decided_site_count") or len(decisions))
    counts = {
        "executed": 0,
        "awaiting_approval": 0,
        "execute_now": 0,
        "deferred": 0,
        "hold": 0,
        "configuration_repair": 0,
        "failed": 0,
    }
    for decision in decisions:
        action = str(decision.get("action") or decision.get("strategy_type") or "")
        schedule_class = str(decision.get("schedule_class") or "")
        if action == "hold" or schedule_class == "hold":
            counts["hold"] += 1
        elif action == "configuration_repair":
            counts["configuration_repair"] += 1
        elif action == "failed":
            counts["failed"] += 1
    executable_decisions = list(planned.get("executable_decisions") or [])
    plan_items = list((planned.get("plan") or {}).get("items") or [])
    counts["execute_now"] = len(executable_decisions)
    counts["awaiting_approval"] = len(executable_decisions)
    counts["deferred"] = sum(
        item.get("schedule_class") == "deferred" for item in plan_items
    )
    plan = dict(planned.get("plan") or {})
    return {
        "discovered_site_count": discovered,
        "decided_site_count": decided,
        "source_audit_batch_id": plan.get("source_audit_batch_id")
        or planned.get("source_audit_batch_id"),
        "analysis_batch_id": planned.get("analysis_batch_id"),
        "plan_id": plan.get("id"),
        "counts": counts,
        "stale_action_reconciliation": dict(
            planned.get("stale_action_reconciliation") or {}
        ),
        "site_results": decisions,
        "executable_decisions": executable_decisions,
    }


def _bind_planned_actions(planned: dict[str, Any]) -> dict[str, Any]:
    coverage = _attach_action_bindings(
        dict(planned.get("coverage_matrix") or {}),
        dict(planned.get("plan") or {}),
    )
    return {
        **planned,
        "decisions": list(coverage.get("decisions") or []),
        "coverage_matrix": coverage,
        "executable_decisions": [
            _strategy_item_action(item)
            for item in (planned.get("plan") or {}).get("items") or []
            if item.get("schedule_class") == "execute_now"
        ],
    }


def _attach_action_bindings(
    coverage: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Bind each executable AI proposal to its exact persisted Strategy row."""
    by_option = {
        str(item.get("option_id")): item
        for item in plan.get("items") or []
        if item.get("option_id") and item.get("id")
    }
    decisions: list[dict[str, Any]] = []
    for raw in coverage.get("decisions") or []:
        decision = dict(raw)
        option_id = str(decision.get("selected_option_id") or "")
        source = by_option.get(option_id)
        if source:
            action_type = str(
                source.get("action_type")
                or decision.get("action")
                or source.get("strategy_type")
                or ""
            )
            decision.update(
                {
                    "action": action_type,
                    "source_strategy_task_id": str(source["id"]),
                    "plan_id": source.get("plan_id") or plan.get("id"),
                    "strategy_run_id": source.get("strategy_run_id")
                    or plan.get("strategy_run_id"),
                    "schedule_class": source.get("schedule_class")
                    or decision.get("schedule_class"),
                    "target_asset_id": (
                        source.get("target_asset_id")
                        or source.get("post_id")
                        or source.get("article_id")
                    ),
                    "remote_object_id": source.get("remote_object_id"),
                    "connector_id": source.get("connector_id"),
                    "connector_type": source.get("connector_type"),
                    "page_type": source.get("page_type"),
                    "expected_fields": list(source.get("expected_fields") or []),
                    "editorial_action": source.get("editorial_action"),
                    "action_type": action_type,
                    "target_url": source.get("target_url")
                    or source.get("canonical_url")
                    or ((source.get("evidence") or {}).get("site_content") or {}).get(
                        "published_url"
                    ),
                    "strategy_fingerprint": source.get("strategy_fingerprint"),
                    "evidence_fingerprint": source.get("evidence_fingerprint"),
                    "query": source.get("query"),
                    "strategy_type": source.get("strategy_type"),
                    "strategy_decision": {
                        key: value
                        for key, value in source.items()
                        if key
                        not in {
                            "id",
                            "plan_id",
                            "status",
                            "created_at",
                            "updated_at",
                        }
                    },
                }
            )
        decisions.append(decision)
    return {**coverage, "decisions": decisions}


def _strategy_item_action(item: dict[str, Any]) -> dict[str, Any]:
    action_type = str(item.get("action_type") or item.get("strategy_type") or "")
    return {
        **item,
        "action": action_type,
        "source_strategy_task_id": str(item["id"]),
        "target_asset_id": (
            item.get("target_asset_id")
            or item.get("post_id")
            or item.get("article_id")
        ),
        "strategy_decision": {
            key: value
            for key, value in item.items()
            if key
            not in {
                "id",
                "plan_id",
                "status",
                "created_at",
                "updated_at",
            }
        },
    }


def _restrict_plan_to_discovered_sites(
    planned: dict[str, Any],
    discovered_sites: list[dict[str, Any]],
) -> dict[str, Any]:
    """Require planner coverage to match the run's exact discovery snapshot."""
    discovered_ids = {str(site["id"]) for site in discovered_sites}
    coverage = dict(planned.get("coverage_matrix") or {})
    decisions = list(coverage.get("decisions") or planned.get("decisions") or [])
    planned_ids = {str(decision.get("site_id")) for decision in decisions}
    outside_scope = sorted(planned_ids - discovered_ids)
    if outside_scope:
        raise StrategyContractError(
            "SITE_OUT_OF_SCOPE",
            "Planner returned site IDs outside the discovered Run scope: "
            + ", ".join(outside_scope),
        )
    return {
        **planned,
        "site_scope": discovered_sites,
        "decisions": decisions,
        "coverage_matrix": {
            **coverage,
            "discovered_site_count": len(discovered_sites),
            "decided_site_count": len(decisions),
            "decisions": decisions,
            "complete": len(decisions) == len(discovered_sites),
        },
    }


def _serialize_run(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("payload") or {})
    decision = dict(row.get("decision") or {})
    return {
        "ok": True,
        "run_id": str(row["id"]),
        "status": decision.get("status", "queued"),
        "current_stage": decision.get("current_stage", decision.get("status", "queued")),
        "business_id": payload.get("business_id"),
        "site_ids": payload.get("site_ids"),
        "scope": payload.get("scope"),
        "mode": payload.get("mode"),
        "requested_by": payload.get("requested_by"),
        "idempotency_key": payload.get("idempotency_key"),
        "action_budget": payload.get("action_budget"),
        "site_quotas": payload.get("site_quotas") or {},
        "approval_policy": payload.get("approval_policy"),
        "root_run_id": payload.get("root_run_id"),
        "attempt": payload.get("attempt", 1),
        "discovered_site_count": decision.get("discovered_site_count", 0),
        "decided_site_count": decision.get("decided_site_count", 0),
        "discovered_sites": decision.get("discovered_sites") or [],
        "capability_snapshot": decision.get("capability_snapshot"),
        "stage_outputs": decision.get("stage_outputs") or {},
        "evidence_snapshot_id": decision.get("evidence_snapshot_id"),
        "evidence_snapshot": decision.get("evidence_snapshot"),
        "evidence_refreshes": decision.get("evidence_refreshes") or [],
        "research_portfolio": decision.get("research_portfolio") or [],
        "research_portfolio_hash": decision.get("research_portfolio_hash"),
        "research_revision": int(decision.get("research_revision") or 0),
        "research_receipt": decision.get("research_receipt"),
        "proposed_actions": decision.get("proposed_actions") or [],
        "proposed_action_count": int(
            decision.get("proposed_action_count") or 0
        ),
        "proposed_action_hash": decision.get("proposed_action_hash"),
        "proposed_action_receipt": decision.get("proposed_action_receipt"),
        "reviewed_actions": decision.get("reviewed_actions") or [],
        "zero_action_review": decision.get("zero_action_review"),
        "action_ids": decision.get("action_ids") or [],
        "source_audit_batch_id": decision.get("source_audit_batch_id"),
        "analysis_batch_id": decision.get("analysis_batch_id"),
        "plan_id": decision.get("plan_id"),
        "counts": decision.get("counts") or {},
        "site_results": decision.get("site_results") or [],
        "exceptions": decision.get("exceptions") or [],
        "observation_ids": decision.get("observation_ids") or [],
        "cancel_requested": bool(decision.get("cancel_requested")),
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "updated_at": _iso(row.get("updated_at")),
        "next_action": decision.get("next_action", "poll"),
        "error": row.get("error_message"),
    }


def _serialize_event(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("payload") or {})
    return {
        "event_id": str(row["id"]),
        "run_id": payload.get("run_id"),
        "stage": payload.get("stage"),
        "event_type": payload.get("event_type"),
        "site_id": payload.get("site_id"),
        "message": payload.get("message"),
        "data": payload.get("data") or {},
        "created_at": _iso(row.get("created_at")),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value
