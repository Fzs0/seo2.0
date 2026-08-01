from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.services.autonomous_strategy_orchestrator import StrategyContractError
from app.services import strategy_run_service as service
from app.services.strategy_run_service import (
    _attach_action_bindings,
    _restrict_plan_to_discovered_sites,
    create_strategy_run,
    derive_child_idempotency_key,
    run_strategy_run,
    validate_run_transition,
)


class _Mappings:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or ([] if row is None else [row])

    def first(self):
        return self._row

    def all(self):
        return self._rows

    def one(self):
        return self._row


class _Result:
    def __init__(self, row=None, rows=None, scalar=None):
        self._mappings = _Mappings(row, rows)
        self._scalar = scalar

    def mappings(self):
        return self._mappings

    def scalar_one(self):
        return self._scalar


class _CreateSession:
    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.commits = 0
        self.insert_count = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "payload->>'idempotency_key'" in sql and "LIMIT 1" in sql:
            return _Result(
                row=self.rows.get(
                    (params["business_id"], params["idempotency_key"])
                )
            )
        if "INSERT INTO seo_agent.tasks" in sql:
            self.insert_count += 1
            payload = json.loads(params["payload"])
            decision = json.loads(params["decision"])
            row = {
                "id": params["id"],
                "payload": payload,
                "decision": decision,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "started_at": None,
                "finished_at": None,
            }
            self.rows[
                (payload["business_id"], payload["idempotency_key"])
            ] = row
            return _Result(row=row)
        raise AssertionError(sql)

    async def commit(self):
        self.commits += 1


def _run_state(status: str = "queued") -> dict:
    return {
        "ok": True,
        "run_id": "11111111-1111-1111-1111-111111111111",
        "status": status,
        "current_stage": status,
        "business_id": "avinoti",
        "scope": "all_sites",
        "site_ids": None,
        "mode": "dry_run",
        "action_budget": 10,
        "site_quotas": {},
        "approval_policy": "use_site_capabilities",
        "discovered_sites": [],
        "stage_outputs": {},
        "research_portfolio": [],
        "proposed_actions": [],
    }


def test_run_coverage_rejects_planner_sites_outside_discovery_snapshot():
    discovered = [{"id": "enabled-a"}, {"id": "enabled-b"}]
    planned = {
        "site_scope": [*discovered, {"id": "outside"}],
        "coverage_matrix": {
            "decisions": [
                {"site_id": "enabled-a", "action": "hold"},
                {"site_id": "enabled-b", "action": "hold"},
                {"site_id": "outside", "action": "hold"},
            ],
        },
    }

    with pytest.raises(StrategyContractError) as error:
        _restrict_plan_to_discovered_sites(planned, discovered)

    assert error.value.code == "SITE_OUT_OF_SCOPE"
    assert "outside" in str(error.value)


def test_formal_decision_binds_only_by_ai_proposal_id():
    coverage = {
        "decisions": [
            {
                "site_id": "site-a",
                "action": "update_article",
                "selected_option_id": "proposal-a",
            }
        ]
    }
    plan = {
        "id": "plan-a",
        "items": [
            {
                "id": "strategy-a",
                "option_id": "proposal-a",
                "site_id": "site-a",
                "target_asset_id": "post-a",
                "strategy_type": "update_article",
                "schedule_class": "execute_now",
            }
        ],
    }

    bound = _attach_action_bindings(coverage, plan)

    assert bound["decisions"][0]["source_strategy_task_id"] == "strategy-a"
    assert bound["decisions"][0]["target_asset_id"] == "post-a"
    assert "candidate_id" not in bound["decisions"][0]


@pytest.mark.asyncio
async def test_create_run_replays_same_input_and_rejects_changed_input():
    session = _CreateSession()
    values = {
        "business_id": "avinoti",
        "site_ids": None,
        "scope": "all_sites",
        "mode": "dry_run",
        "requested_by": "codex",
        "idempotency_key": "run-key-123",
        "action_budget": 10,
        "site_quotas": {},
        "approval_policy": "use_site_capabilities",
    }

    first = await create_strategy_run(session, **values)
    replay = await create_strategy_run(session, **values)

    assert first["idempotency_replayed"] is False
    assert replay["idempotency_replayed"] is True
    assert first["run_id"] == replay["run_id"]
    assert session.insert_count == 1

    with pytest.raises(StrategyContractError) as error:
        await create_strategy_run(
            session,
            **{**values, "mode": "approval_execution"},
        )
    assert error.value.code == "DECISION_INPUT_CHANGED"


def test_fixed_state_machine_rejects_illegal_or_terminal_transition():
    validate_run_transition("queued", "discovering_sites")
    validate_run_transition("gathering_evidence", "ai_researching")
    validate_run_transition(
        "ai_researching", "proposed_actions_submitted"
    )
    validate_run_transition(
        "zero_action_reviewing", "research_revision_required"
    )
    with pytest.raises(ValueError, match="illegal strategy run transition"):
        validate_run_transition("queued", "planning")
    with pytest.raises(ValueError, match="terminal"):
        validate_run_transition("completed", "queued")


def test_child_idempotency_key_is_stable_and_exact_target_scoped():
    first = derive_child_idempotency_key(
        "run-a", "site-a", "product_seo", "product-1"
    )
    assert first == derive_child_idempotency_key(
        "run-a", "site-a", "product_seo", "product-1"
    )
    assert first != derive_child_idempotency_key(
        "run-a", "site-a", "product_seo", "product-2"
    )


@pytest.mark.asyncio
async def test_first_start_stops_at_ai_researching(monkeypatch):
    state = _run_state()
    site = {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "Avinoti",
        "strategy_enabled": True,
    }

    async def get_run(_session, *, run_id):
        return deepcopy(state)

    async def transition(
        _session,
        *,
        run_id,
        current_status,
        next_status,
        updates=None,
        commit=True,
    ):
        assert state["status"] == current_status
        state.update(updates or {})
        state["status"] = next_status
        state["current_stage"] = next_status
        return deepcopy(state)

    async def complete(
        _session, *, run_id, current, next_status, updates
    ):
        return await transition(
            _session,
            run_id=run_id,
            current_status=current,
            next_status=next_status,
            updates=updates,
        )

    async def discover(_session, **_kwargs):
        return [site]

    async def capabilities(_session, _business_id):
        return {
            "generated_at": "2026-07-31T00:00:00Z",
            "sites": [{"site_id": site["id"]}],
        }

    async def evidence(_session, **_kwargs):
        return {
            "snapshot_id": "snapshot-1",
            "captured_at": "2026-07-31T00:00:00Z",
        }

    async def stage_started(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "get_strategy_run", get_run)
    monkeypatch.setattr(service, "transition_strategy_run", transition)
    monkeypatch.setattr(service, "_complete_stage", complete)
    monkeypatch.setattr(service, "_stage_started", stage_started)

    result = await run_strategy_run(
        object(),
        run_id=state["run_id"],
        site_discoverer=discover,
        capability_loader=capabilities,
        evidence_gatherer=evidence,
    )

    assert result["status"] == "ai_researching"
    assert result["next_action"] == "capture_research"
    assert result["discovered_sites"] == [site]
    assert result["evidence_snapshot_id"] == "snapshot-1"


@pytest.mark.asyncio
async def test_second_start_builds_shadow_plan_without_remote_action(
    monkeypatch,
):
    site_id = "22222222-2222-2222-2222-222222222222"
    state = {
        **_run_state("proposed_actions_submitted"),
        "discovered_sites": [
            {
                "id": site_id,
                "name": "Avinoti",
                "strategy_enabled": True,
            }
        ],
        "research_portfolio": [{"site_id": site_id}],
        "research_portfolio_hash": "research-hash",
        "proposed_actions": [{"site_id": site_id}],
        "proposed_action_hash": "proposal-hash",
        "capability_snapshot": {"sites": [{"site_id": site_id}]},
    }

    async def get_run(_session, *, run_id):
        return deepcopy(state)

    async def transition(
        _session,
        *,
        run_id,
        current_status,
        next_status,
        updates=None,
        commit=True,
    ):
        assert state["status"] == current_status
        state.update(updates or {})
        state["status"] = next_status
        state["current_stage"] = next_status
        return deepcopy(state)

    async def complete(
        _session, *, run_id, current, next_status, updates
    ):
        return await transition(
            _session,
            run_id=run_id,
            current_status=current,
            next_status=next_status,
            updates=updates,
        )

    async def stage_started(*_args, **_kwargs):
        return None

    async def planner(_session, **kwargs):
        assert kwargs["proposed_actions"] == [{"site_id": site_id}]
        item = {
            "id": "strategy-1",
            "option_id": "proposal-1",
            "site_id": site_id,
            "strategy_type": "new_article",
            "action_type": "new_article",
            "editorial_action": "new_article",
            "schedule_class": "execute_now",
            "scope_key": "scope-1",
            "lock_key": "scope-1",
            "lock_scope": "intent",
            "query": "travel titanium cup care",
        }
        decision = {
            "site_id": site_id,
            "site_name": "Avinoti",
            "action": "new_article",
            "schedule_class": "execute_now",
            "selected_option_id": "proposal-1",
        }
        return {
            "site_scope": state["discovered_sites"],
            "analysis_batch_id": state["run_id"],
            "plan": {"id": "plan-1", "items": [item]},
            "items": [item],
            "reviewed_actions": [item],
            "coverage_matrix": {
                "complete": True,
                "discovered_site_count": 1,
                "decided_site_count": 1,
                "decisions": [decision],
                "options": [item],
            },
            "decisions": [decision],
            "executable_decisions": [item],
        }

    async def action_factory(*_args, **_kwargs):
        raise AssertionError("dry_run must not create a Unified Action")

    monkeypatch.setattr(service, "get_strategy_run", get_run)
    monkeypatch.setattr(service, "transition_strategy_run", transition)
    monkeypatch.setattr(service, "_complete_stage", complete)
    monkeypatch.setattr(service, "_stage_started", stage_started)

    result = await run_strategy_run(
        object(),
        run_id=state["run_id"],
        planner=planner,
        action_factory=action_factory,
    )

    assert result["status"] == "completed"
    assert result["shadow_run"] is True
    assert result["counts"]["execute_now"] == 1
    assert result["action_ids"] == []


@pytest.mark.asyncio
async def test_configuration_only_plan_completes_without_creating_action(
    monkeypatch,
):
    site_id = "22222222-2222-2222-2222-222222222222"
    state = {
        **_run_state("proposed_actions_submitted"),
        "mode": "approval_execution",
        "discovered_sites": [
            {
                "id": site_id,
                "name": "Fashionshopx",
                "strategy_enabled": False,
            }
        ],
        "research_portfolio": [{"site_id": site_id}],
        "research_portfolio_hash": "research-hash",
        "proposed_actions": [{"site_id": site_id}],
        "proposed_action_hash": "proposal-hash",
        "capability_snapshot": {"sites": [{"site_id": site_id}]},
    }

    async def get_run(_session, *, run_id):
        return deepcopy(state)

    async def transition(
        _session,
        *,
        run_id,
        current_status,
        next_status,
        updates=None,
        commit=True,
    ):
        assert state["status"] == current_status
        state.update(updates or {})
        state["status"] = next_status
        state["current_stage"] = next_status
        return deepcopy(state)

    async def complete(
        _session, *, run_id, current, next_status, updates
    ):
        return await transition(
            _session,
            run_id=run_id,
            current_status=current,
            next_status=next_status,
            updates=updates,
        )

    async def stage_started(*_args, **_kwargs):
        return None

    async def planner(_session, **_kwargs):
        item = {
            "id": "strategy-repair-1",
            "option_id": "proposal-repair-1",
            "site_id": site_id,
            "strategy_type": "configuration_repair",
            "action_type": "configuration_repair",
            "editorial_action": "configuration_repair",
            "schedule_class": "configuration_repair",
            "reason_code": "CAPABILITY_MISSING",
        }
        decision = {
            "site_id": site_id,
            "site_name": "Fashionshopx",
            "action": "configuration_repair",
            "schedule_class": "configuration_repair",
            "selected_option_id": "proposal-repair-1",
        }
        return {
            "site_scope": state["discovered_sites"],
            "analysis_batch_id": state["run_id"],
            "plan": {"id": "plan-repair-1", "items": [item]},
            "items": [item],
            "reviewed_actions": [item],
            "coverage_matrix": {
                "complete": True,
                "discovered_site_count": 1,
                "decided_site_count": 1,
                "decisions": [decision],
                "options": [item],
            },
            "decisions": [decision],
            "executable_decisions": [],
        }

    async def configuration_skipped(*_args, **_kwargs):
        return {
            "required": True,
            "result": "configuration_skipped",
            "reason_codes": ["CONFIGURATION_REPAIR_SKIPPED"],
            "missing_evidence": [],
        }

    async def action_factory(*_args, **_kwargs):
        raise AssertionError("configuration repair must not create an Action")

    monkeypatch.setattr(service, "get_strategy_run", get_run)
    monkeypatch.setattr(service, "transition_strategy_run", transition)
    monkeypatch.setattr(service, "_complete_stage", complete)
    monkeypatch.setattr(service, "_stage_started", stage_started)
    monkeypatch.setattr(
        "app.services.autonomous_strategy_orchestrator.evaluate_zero_action_review",
        configuration_skipped,
    )

    result = await run_strategy_run(
        object(),
        run_id=state["run_id"],
        planner=planner,
        action_factory=action_factory,
    )

    assert result["status"] == "completed"
    assert result["next_action"] == "none"
    assert result["counts"]["configuration_repair"] == 1
    assert result["zero_action_review"]["result"] == "configuration_skipped"


@pytest.mark.asyncio
async def test_expired_unexecuted_actions_cancel_parent_without_fake_execution_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transitions: list[tuple[str, str]] = []
    run = {
        "run_id": "run-1",
        "status": "awaiting_approval",
        "current_stage": "awaiting_approval",
        "stage_outputs": {},
    }

    async def actions(_session, *, run_id):
        return [
            {
                "action_id": "action-1",
                "status": "canceled",
                "closure_reason": "approval_expired_unexecuted",
            }
        ]

    async def complete(_session, *, run_id, current, next_status, updates):
        transitions.append((current, next_status))
        return {
            **run,
            **updates,
            "status": next_status,
            "current_stage": next_status,
        }

    monkeypatch.setattr(service, "_complete_stage", complete)

    result = await service._reconcile_run_actions(
        object(),  # type: ignore[arg-type]
        run=run,
        action_loader=actions,
    )

    assert transitions == [("awaiting_approval", "canceled")]
    assert result["status"] == "canceled"
    assert result["next_action"] == "none"


@pytest.mark.asyncio
async def test_partial_stale_cleanup_does_not_start_parent_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = {
        "run_id": "run-1",
        "status": "awaiting_approval",
        "current_stage": "awaiting_approval",
        "stage_outputs": {},
    }

    async def actions(_session, *, run_id):
        return [
            {
                "action_id": "action-1",
                "status": "canceled",
                "closure_reason": "approval_expired_unexecuted",
            },
            {"action_id": "action-2", "status": "planned"},
        ]

    async def must_not_transition(*_args, **_kwargs):
        raise AssertionError("unexecuted cancellation must not start execution")

    monkeypatch.setattr(service, "_complete_stage", must_not_transition)

    result = await service._reconcile_run_actions(
        object(),  # type: ignore[arg-type]
        run=run,
        action_loader=actions,
    )

    assert result["status"] == "awaiting_approval"
    assert result["next_action"] == "complete_actions"


@pytest.mark.asyncio
async def test_completed_action_refreshes_parent_run_summary_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = {
        "run_id": "run-1",
        "status": "awaiting_approval",
        "current_stage": "awaiting_approval",
        "stage_outputs": {},
        "counts": {
            "executed": 0,
            "awaiting_approval": 1,
            "execute_now": 1,
            "deferred": 0,
            "hold": 6,
            "configuration_repair": 0,
            "failed": 0,
        },
    }

    async def actions(_session, *, run_id):
        return [
            {
                "action_id": "action-1",
                "status": "completed",
                "observation_id": "observation-1",
            }
        ]

    async def complete(_session, *, run_id, current, next_status, updates):
        return {
            **run,
            **updates,
            "status": next_status,
            "current_stage": next_status,
        }

    async def stage_started(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_complete_stage", complete)
    monkeypatch.setattr(service, "_stage_started", stage_started)

    result = await service._reconcile_run_actions(
        object(),  # type: ignore[arg-type]
        run=run,
        action_loader=actions,
    )

    assert result["status"] == "completed"
    assert result["counts"] == {
        "executed": 1,
        "awaiting_approval": 0,
        "execute_now": 1,
        "deferred": 0,
        "hold": 6,
        "configuration_repair": 0,
        "failed": 0,
    }


@pytest.mark.asyncio
async def test_terminal_run_reconcile_backfills_stale_summary_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = {
        "run_id": "run-1",
        "status": "completed",
        "current_stage": "completed",
        "counts": {
            "executed": 0,
            "awaiting_approval": 1,
            "execute_now": 1,
            "deferred": 0,
            "hold": 6,
            "configuration_repair": 0,
            "failed": 0,
        },
        "action_outcome_counts": {"completed": 1, "blocked": 0, "failed": 0},
        "observation_ids": [],
    }

    async def get_run(_session, *, run_id):
        return dict(run)

    async def actions(_session, *, run_id):
        return [
            {
                "action_id": "action-1",
                "status": "completed",
                "observation_id": "observation-1",
            }
        ]

    async def refresh(_session, *, run, actions):
        return {
            **run,
            "counts": {**run["counts"], "executed": 1, "awaiting_approval": 0},
            "observation_ids": ["observation-1"],
            "reconcile_replayed": False,
        }

    monkeypatch.setattr(service, "get_strategy_run", get_run)
    monkeypatch.setattr(service, "_refresh_terminal_run_summary", refresh, raising=False)

    result = await service.reconcile_strategy_run(
        object(),  # type: ignore[arg-type]
        run_id="run-1",
        action_loader=actions,
    )

    assert result["counts"]["executed"] == 1
    assert result["counts"]["awaiting_approval"] == 0
    assert result["observation_ids"] == ["observation-1"]
    assert result["reconcile_replayed"] is False


@pytest.mark.asyncio
async def test_failed_run_reconciles_to_completed_after_all_actions_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = {
        "run_id": "run-1",
        "status": "failed",
        "current_stage": "failed",
        "counts": {"executed": 0, "failed": 1},
    }

    async def get_run(_session, *, run_id):
        return dict(run)

    async def actions(_session, *, run_id):
        return [
            {
                "action_id": "action-1",
                "status": "completed",
                "observation_id": "observation-1",
            }
        ]

    async def reconcile_terminal(
        _session, *, run_id, current_status, next_status, updates
    ):
        assert current_status == "failed"
        assert next_status == "completed"
        return {
            **run,
            **updates,
            "status": "completed",
            "current_stage": "completed",
        }

    async def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("a fully recovered failed Run must be completed")

    monkeypatch.setattr(service, "get_strategy_run", get_run)
    monkeypatch.setattr(service, "_reconcile_terminal_run", reconcile_terminal)
    monkeypatch.setattr(service, "_refresh_terminal_run_summary", unexpected_refresh)

    result = await service.reconcile_strategy_run(
        object(),  # type: ignore[arg-type]
        run_id="run-1",
        action_loader=actions,
    )

    assert result["status"] == "completed"
    assert result["observation_ids"] == ["observation-1"]
    assert result["next_action"] == "none"
