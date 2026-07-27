"""Pure hold-streak policy; persistence uses existing task JSON payloads."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import json
from typing import Any, Awaitable, Callable
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


EXPANDED_EVIDENCE_SOURCES = [
    "gsc", "ga4", "live_serp", "products", "collections", "articles", "on_page",
    "publishing_frequency", "content_gap",
]

EvidenceCollector = Callable[..., Awaitable[dict[str, Any]]]

_COORDINATION_KIND = "strategy_hold_coordination"
_REFRESH_LEASE_SECONDS = 300


async def acquire_hold_state_lock(
    session: AsyncSession, *, business_id: str
) -> None:
    """Serialize hold-streak reads and writes inside the caller transaction."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"strategy-hold:{business_id}"},
    )


async def _load_or_create_coordination_state(
    session: AsyncSession, *, business_id: str
) -> tuple[str, dict[str, Any]]:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, decision
                  FROM seo_agent.tasks
                 WHERE task_type = 'review'
                   AND payload->>'kind' = :kind
                   AND payload->>'business_id' = :business_id
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            ),
            {"kind": _COORDINATION_KIND, "business_id": business_id},
        )
    ).mappings().first()
    if row:
        return str(row["id"]), dict(row.get("decision") or {})
    task_id = str(uuid4())
    state = {
        "business_consecutive_hold_count": 0,
        "site_consecutive_hold_counts": {},
        "registered_run_ids": [],
        "run_claim_ordinals": {},
        "refresh_claimed_for_counts": [],
        "stagnation_emitted_for_counts": [],
    }
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks
              (id, task_type, status, priority, title, payload, decision)
            VALUES
              (CAST(:id AS uuid), 'review', 'done', 'P2', :title,
               CAST(:payload AS jsonb), CAST(:decision AS jsonb))
            """
        ),
        {
            "id": task_id,
            "title": f"Strategy Hold coordination: {business_id}",
            "payload": json.dumps(
                {"kind": _COORDINATION_KIND, "business_id": business_id}
            ),
            "decision": json.dumps(state),
        },
    )
    return task_id, state


async def _save_coordination_state(
    session: AsyncSession, *, task_id: str, state: dict[str, Any]
) -> None:
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET decision = CAST(:decision AS jsonb), updated_at = now()
             WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": task_id, "decision": json.dumps(state, ensure_ascii=False)},
    )


async def claim_hold_evidence_refresh(
    session: AsyncSession,
    *,
    business_id: str,
    run_id: str,
    now: datetime | None = None,
    refresh_lease_seconds: int = _REFRESH_LEASE_SECONDS,
) -> dict[str, Any]:
    """Atomically claim the one evidence refresh due before Hold run number two.

    This deliberately commits before returning: the advisory transaction lock is
    never held while external evidence collectors run. ``refresh_token`` is the
    idempotency key used by :func:`complete_hold_evidence_refresh`.
    """
    await acquire_hold_state_lock(session, business_id=business_id)
    task_id, state = await _load_or_create_coordination_state(
        session, business_id=business_id
    )
    claim_now = now or datetime.now(timezone.utc)
    pending_refresh = dict(state.get("refresh") or {})
    if _refresh_lease_expired(
        pending_refresh,
        now=claim_now,
        lease_seconds=refresh_lease_seconds,
    ):
        trigger_count = int(pending_refresh.get("trigger_hold_count") or 0)
        claimed_counts = {
            int(value) for value in state.get("refresh_claimed_for_counts") or []
        }
        claimed_counts.discard(trigger_count)
        state["refresh_claimed_for_counts"] = sorted(claimed_counts)
        run_claims = dict(state.get("run_claim_ordinals") or {})
        run_claims.pop(str(pending_refresh.get("claimed_by_run_id") or ""), None)
        state["run_claim_ordinals"] = run_claims
        pending_refresh.update(
            {
                "status": "failed",
                "completed_at": claim_now.isoformat(),
                "error": "refresh lease expired before completion",
                "decision_impact": (
                    "no refreshed evidence was used; a new token may safely retry "
                    "without bypassing quality or safety gates"
                ),
            }
        )
        state["refresh"] = pending_refresh
    current_count = int(state.get("business_consecutive_hold_count") or 0)
    run_claims = {
        str(key): int(value)
        for key, value in dict(state.get("run_claim_ordinals") or {}).items()
    }
    if run_id not in run_claims:
        run_claims[run_id] = current_count + len(run_claims) + 1
        state["run_claim_ordinals"] = run_claims
    target_count = run_claims[run_id]
    claimed_counts = {
        int(value) for value in state.get("refresh_claimed_for_counts") or []
    }
    # Refresh is an edge-triggered escalation for the second consecutive Hold,
    # not a level-triggered action repeated by every later request.
    should_refresh = target_count == 2 and target_count not in claimed_counts
    pending_refresh = dict(state.get("refresh") or {})
    should_wait = (
        not should_refresh
        and target_count >= 2
        and pending_refresh.get("status") == "pending"
    )
    refresh_token: str | None = None
    if should_refresh:
        refresh_token = str(uuid4())
        claimed_counts.add(target_count)
        state["refresh_claimed_for_counts"] = sorted(claimed_counts)
        state["refresh"] = {
            "status": "pending",
            "refresh_token": refresh_token,
            "claimed_by_run_id": run_id,
            "trigger_hold_count": target_count,
            "claimed_at": claim_now.isoformat(),
            "expires_at": (
                claim_now + timedelta(seconds=max(1, refresh_lease_seconds))
            ).isoformat(),
            "results": [],
        }
        await _save_coordination_state(session, task_id=task_id, state=state)
    elif run_id not in (state.get("registered_run_ids") or []):
        # Persist the run reservation even when it did not own the refresh.
        await _save_coordination_state(session, task_id=task_id, state=state)
    await session.commit()
    return {
        "should_refresh": should_refresh,
        "refresh_token": refresh_token,
        "wait_for_refresh": should_wait,
        "pending_refresh_token": (
            pending_refresh.get("refresh_token") if should_wait else None
        ),
        "business_consecutive_hold_count": current_count,
        "site_consecutive_hold_counts": dict(
            state.get("site_consecutive_hold_counts") or {}
        ),
        "trigger_hold_count": target_count if should_refresh else None,
    }


async def register_hold_decision_and_claim_refresh(
    session: AsyncSession,
    *,
    business_id: str,
    run_id: str,
    all_hold: bool,
    site_hold_flags: dict[str, bool],
    now: datetime | None = None,
    refresh_lease_seconds: int = _REFRESH_LEASE_SECONDS,
) -> dict[str, Any]:
    """Register a *decided* run and claim refresh only on the real 1→2 Hold edge.

    Unlike the legacy preflight claimant, this transition never infers a Hold
    from request order.  The caller must first finish the site coverage matrix.
    """
    await acquire_hold_state_lock(session, business_id=business_id)
    task_id, state = await _load_or_create_coordination_state(
        session, business_id=business_id
    )
    registered = list(state.get("registered_run_ids") or [])
    if run_id in registered:
        await session.commit()
        return {
            **state,
            "should_refresh": False,
            "refresh_token": None,
            "already_registered": True,
            "emit_strategy_stagnation": False,
        }

    transition_now = now or datetime.now(timezone.utc)
    refresh = dict(state.get("refresh") or {})
    if _refresh_lease_expired(
        refresh, now=transition_now, lease_seconds=refresh_lease_seconds
    ):
        refresh.update(
            {
                "status": "failed",
                "completed_at": transition_now.isoformat(),
                "error": "refresh lease expired before completion",
                "decision_impact": "expired evidence was not used; a later confirmed Hold may retry",
            }
        )
        state["refresh"] = refresh
    elif (
        all_hold
        and refresh.get("status") == "pending"
        and refresh.get("claimed_by_run_id") != run_id
    ):
        await session.commit()
        return {
            **state,
            "should_refresh": False,
            "refresh_token": None,
            "wait_for_refresh": True,
            "pending_refresh_token": refresh.get("refresh_token"),
            "already_registered": False,
            "emit_strategy_stagnation": False,
        }

    previous_count = int(state.get("business_consecutive_hold_count") or 0)
    next_count = previous_count + 1 if all_hold else 0
    previous_sites = dict(state.get("site_consecutive_hold_counts") or {})
    state["site_consecutive_hold_counts"] = {
        site: int(previous_sites.get(site, 0)) + 1 if is_hold else 0
        for site, is_hold in site_hold_flags.items()
    }
    state["business_consecutive_hold_count"] = next_count
    state["registered_run_ids"] = [*registered, run_id][-100:]
    state["last_registered_at"] = transition_now.isoformat()

    should_refresh = all_hold and previous_count == 1 and next_count == 2
    refresh_token: str | None = None
    if should_refresh:
        refresh_token = str(uuid4())
        state["refresh"] = {
            "status": "pending",
            "refresh_token": refresh_token,
            "trigger_hold_transition": "1_to_2",
            "claimed_by_run_id": run_id,
            "claimed_at": transition_now.isoformat(),
            "expires_at": (
                transition_now + timedelta(seconds=max(1, refresh_lease_seconds))
            ).isoformat(),
            "degraded": False,
            "results": [],
        }
    if not all_hold:
        state["refresh_claimed_for_counts"] = []

    emitted = {
        int(value) for value in state.get("stagnation_emitted_for_counts") or []
    }
    emit_stagnation = next_count == 3 and 3 not in emitted
    if emit_stagnation:
        emitted.add(3)
        await _insert_stagnation_task(
            session, business_id=business_id, run_id=run_id, count=next_count
        )
    state["stagnation_emitted_for_counts"] = sorted(emitted)
    await _save_coordination_state(session, task_id=task_id, state=state)
    await session.commit()
    return {
        **state,
        "should_refresh": should_refresh,
        "refresh_token": refresh_token,
        "already_registered": False,
        "emit_strategy_stagnation": emit_stagnation,
    }


async def reconcile_refreshed_hold_decision(
    session: AsyncSession,
    *,
    business_id: str,
    run_id: str,
    all_hold: bool,
    site_hold_flags: dict[str, bool],
) -> dict[str, Any]:
    """Replace the provisional second-Hold result with the refreshed decision."""
    await acquire_hold_state_lock(session, business_id=business_id)
    task_id, state = await _load_or_create_coordination_state(
        session, business_id=business_id
    )
    refresh = dict(state.get("refresh") or {})
    if (
        refresh.get("claimed_by_run_id") != run_id
        or refresh.get("status") != "completed"
    ):
        await session.commit()
        raise ValueError("completed Hold refresh is required before reconciliation")
    if not all_hold:
        state["business_consecutive_hold_count"] = 0
        state["site_consecutive_hold_counts"] = {
            site: 0 if not is_hold else int(
                dict(state.get("site_consecutive_hold_counts") or {}).get(site, 0)
            )
            for site, is_hold in site_hold_flags.items()
        }
        state["refresh_claimed_for_counts"] = []
    state["refreshed_decision"] = {
        "run_id": run_id,
        "all_hold": all_hold,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    await _save_coordination_state(session, task_id=task_id, state=state)
    await session.commit()
    return state


async def _insert_stagnation_task(
    session: AsyncSession, *, business_id: str, run_id: str, count: int
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks
              (task_type, status, priority, title, payload, decision)
            VALUES ('review', 'done', 'P2', :title,
                    CAST(:payload AS jsonb), CAST(:decision AS jsonb))
            """
        ),
        {
            "title": f"Strategy stagnation: {business_id}",
            "payload": json.dumps(
                {
                    "kind": "strategy_stagnation",
                    "business_id": business_id,
                    "strategy_run_id": run_id,
                }
            ),
            "decision": json.dumps(
                {
                    "kind": "strategy_stagnation",
                    "severity": "P2",
                    "consecutive_hold_count": count,
                }
            ),
        },
    )


def _refresh_lease_expired(
    refresh: dict[str, Any], *, now: datetime, lease_seconds: int
) -> bool:
    if refresh.get("status") != "pending":
        return False
    raw_expires = refresh.get("expires_at")
    try:
        if raw_expires:
            expires_at = datetime.fromisoformat(str(raw_expires))
        else:
            claimed_at = datetime.fromisoformat(str(refresh.get("claimed_at") or ""))
            expires_at = claimed_at + timedelta(seconds=max(1, lease_seconds))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    comparison_now = now
    if comparison_now.tzinfo is None:
        comparison_now = comparison_now.replace(tzinfo=timezone.utc)
    return comparison_now >= expires_at


async def wait_for_hold_evidence_refresh(
    session: AsyncSession,
    *,
    business_id: str,
    refresh_token: str,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Wait for another run's claimed refresh without holding a DB lock."""
    deadline = asyncio.get_running_loop().time() + max(0.1, timeout_seconds)
    while True:
        row = (
            await session.execute(
                text(
                    """
                    SELECT decision->'refresh' AS refresh
                      FROM seo_agent.tasks
                     WHERE task_type = 'review'
                       AND payload->>'kind' = :kind
                       AND payload->>'business_id' = :business_id
                     ORDER BY created_at DESC
                     LIMIT 1
                    """
                ),
                {"kind": _COORDINATION_KIND, "business_id": business_id},
            )
        ).mappings().first()
        await session.commit()
        refresh = dict((row or {}).get("refresh") or {})
        if (
            refresh.get("refresh_token") == refresh_token
            and refresh.get("status") == "completed"
        ):
            return list(refresh.get("results") or [])
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("timed out waiting for the claimed Hold evidence refresh")
        await asyncio.sleep(0.05)


async def complete_hold_evidence_refresh(
    session: AsyncSession,
    *,
    business_id: str,
    refresh_token: str,
    refresh_results: list[dict[str, Any]],
) -> bool:
    """Persist one refresh result iff ``refresh_token`` still owns the claim."""
    await acquire_hold_state_lock(session, business_id=business_id)
    task_id, state = await _load_or_create_coordination_state(
        session, business_id=business_id
    )
    refresh = dict(state.get("refresh") or {})
    if (
        refresh.get("refresh_token") != refresh_token
        or refresh.get("status") != "pending"
    ):
        await session.commit()
        return False
    normalized_results = _with_decision_impact(refresh_results)
    refresh.update(
        {
            "status": "completed",
            "completed_at": datetime.now().astimezone().isoformat(),
            "results": normalized_results,
            "degraded": _refresh_results_degraded(normalized_results),
        }
    )
    state["refresh"] = refresh
    await _save_coordination_state(session, task_id=task_id, state=state)
    await session.commit()
    return True


async def finalize_hold_run(
    session: AsyncSession,
    *,
    business_id: str,
    run_id: str,
    all_hold: bool,
    site_hold_counts: dict[str, int] | None = None,
    site_hold_flags: dict[str, bool] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Atomically register one completed run and advance its Hold streak once."""
    await acquire_hold_state_lock(session, business_id=business_id)
    task_id, state = await _load_or_create_coordination_state(
        session, business_id=business_id
    )
    registered = list(state.get("registered_run_ids") or [])
    if run_id in registered:
        await session.commit()
        return {
            **state,
            "emit_strategy_stagnation": False,
            "already_registered": True,
        }
    previous_count = int(state.get("business_consecutive_hold_count") or 0)
    count = previous_count + 1 if all_hold else 0
    state["business_consecutive_hold_count"] = count
    if site_hold_flags is not None:
        previous_site = dict(state.get("site_consecutive_hold_counts") or {})
        next_site = {
            site_id: int(previous_site.get(site_id, 0)) + 1 if is_hold else 0
            for site_id, is_hold in site_hold_flags.items()
        }
    else:
        next_site = dict(site_hold_counts or {})
    state["site_consecutive_hold_counts"] = next_site
    # Bound the idempotency history while retaining ample overlap for retries.
    state["registered_run_ids"] = [*registered, run_id][-100:]
    run_claims = dict(state.get("run_claim_ordinals") or {})
    run_claims.pop(run_id, None)
    state["run_claim_ordinals"] = run_claims
    if not all_hold:
        # A successful action ends the streak; a future second Hold may claim a
        # new refresh without being blocked by the prior streak's token.
        state["refresh_claimed_for_counts"] = []
    emitted = {
        int(value) for value in state.get("stagnation_emitted_for_counts") or []
    }
    emit_stagnation = count == 3 and 3 not in emitted
    if emit_stagnation:
        emitted.add(3)
    state["stagnation_emitted_for_counts"] = sorted(emitted)
    state["last_registered_at"] = datetime.now().astimezone().isoformat()
    await _save_coordination_state(session, task_id=task_id, state=state)
    if emit_stagnation:
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, title, payload, decision)
                VALUES
                  ('review', 'done', 'P2', :title,
                   CAST(:payload AS jsonb), CAST(:decision AS jsonb))
                """
            ),
            {
                "title": f"Strategy stagnation: {business_id}",
                "payload": json.dumps(
                    {
                        "kind": "strategy_stagnation",
                        "business_id": business_id,
                        "strategy_run_id": run_id,
                    }
                ),
                "decision": json.dumps(
                    {
                        "kind": "strategy_stagnation",
                        "severity": "P2",
                        "consecutive_hold_count": count,
                    }
                ),
            },
        )
    if commit:
        await session.commit()
    return {
        **state,
        "emit_strategy_stagnation": emit_stagnation,
        "already_registered": False,
    }


def _with_decision_impact(
    refresh_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source_result in refresh_results:
        item = dict(source_result)
        sources = {}
        for source, raw in dict(item.get("sources") or {}).items():
            detail = dict(raw or {})
            detail["decision_impact"] = (
                "fresh evidence included in candidate recalculation"
                if detail.get("status") in {"fresh", "success", "empty"}
                else "candidate confidence may be reduced; source failure cannot bypass safety gates"
            )
            sources[source] = detail
        item["sources"] = sources
        normalized.append(item)
    return normalized


def _refresh_results_degraded(results: list[dict[str, Any]]) -> bool:
    for item in results:
        sources = dict(item.get("sources") or {})
        if sources:
            if any(
                dict(source or {}).get("status") not in {"fresh", "success", "empty"}
                for source in sources.values()
            ):
                return True
        elif item.get("status") not in {"fresh", "success", "empty"}:
            return True
    return False


async def refresh_hold_evidence(
    *,
    site_id: str,
    collectors: dict[str, EvidenceCollector],
    now: datetime,
) -> dict[str, Any]:
    """Refresh every supplied read-only evidence source with failure isolation."""
    sources: dict[str, dict[str, Any]] = {}
    for source in EXPANDED_EVIDENCE_SOURCES:
        collector = collectors.get(source)
        if collector is None:
            sources[source] = {
                "status": "unavailable",
                "refreshed_at": now.isoformat(),
                "snapshot_id": None,
                "error": "collector is not configured",
                "decision_impact": "candidate confidence may be reduced; source unavailable cannot bypass safety gates",
            }
            continue
        try:
            item = dict(await collector(site_id=site_id) or {})
            sources[source] = {
                **item,
                "status": item.get("status") or "fresh",
                "snapshot_id": item.get("snapshot_id"),
                "refreshed_at": now.isoformat(),
                "decision_impact": "fresh evidence included in candidate recalculation",
            }
        except Exception as exc:  # connector isolation is intentional
            sources[source] = {
                "status": "failed",
                "snapshot_id": None,
                "refreshed_at": now.isoformat(),
                "error": str(exc),
                "decision_impact": "candidate confidence may be reduced; source failure cannot bypass safety gates",
            }
    return {
        "site_id": site_id,
        "refreshed_at": now.isoformat(),
        "sources": sources,
        "degraded": any(item["status"] != "fresh" for item in sources.values()),
    }


def evolve_hold_state(
    decisions: list[dict[str, Any]],
    *,
    previous_site_counts: dict[str, int] | None = None,
    previous_business_count: int = 0,
    now: datetime,
    review_after_days: int = 7,
) -> dict[str, Any]:
    previous_site_counts = previous_site_counts or {}
    enriched: list[dict[str, Any]] = []
    site_counts: dict[str, int] = {}
    all_hold = bool(decisions) and all(item.get("action") == "hold" for item in decisions)
    for source in decisions:
        decision = dict(source)
        site_id = str(decision.get("site_id") or "")
        is_hold = decision.get("action") == "hold"
        count = int(previous_site_counts.get(site_id, 0)) + 1 if is_hold else 0
        site_counts[site_id] = count
        if is_hold:
            reason = str(decision.get("block_reason") or decision.get("reason") or "insufficient evidence")
            decision.update({
                "block_reason": reason,
                "unlock_condition": decision.get("unlock_condition")
                or "Refresh missing evidence and pass every quality and safety gate.",
                "responsibility_type": decision.get("responsibility_type")
                or "strategy_configuration_or_evidence",
                "review_by": (now + timedelta(days=review_after_days)).date().isoformat(),
                "alternative_evidence": decision.get("alternative_evidence")
                or ["verified product facts", "authoritative public sources", "manual content audit"],
                "consecutive_hold_count": count,
            })
        enriched.append(decision)
    business_count = int(previous_business_count) + 1 if all_hold else 0
    anomalies = []
    if business_count == 3 and int(previous_business_count) < 3:
        anomalies.append({
            "kind": "strategy_stagnation",
            "severity": "P2",
            "consecutive_hold_count": business_count,
        })
    return {
        "decisions": enriched,
        "site_consecutive_hold_counts": site_counts,
        "business_consecutive_hold_count": business_count,
        "expand_evidence": business_count >= 2,
        "evidence_sources": EXPANDED_EVIDENCE_SOURCES if business_count >= 2 else [],
        "safety_gates_may_be_bypassed": False,
        "anomalies": anomalies,
    }
