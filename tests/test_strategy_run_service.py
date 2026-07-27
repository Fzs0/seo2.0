from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.services.strategy_run_service import (
    TERMINAL_RUN_STATUSES,
    create_strategy_run,
    derive_child_idempotency_key,
    run_strategy_run,
    transition_strategy_run,
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


class _Result:
    def __init__(self, row=None, rows=None, scalar=None):
        self._mappings = _Mappings(row, rows)
        self._scalar = scalar

    def mappings(self):
        return self._mappings

    def scalar_one(self):
        return self._scalar


async def _evidence(_session, **_kwargs):
    return {"snapshot_id": "audit-1"}


async def _action(_session, **kwargs):
    return {"action_id": kwargs["idempotency_key"]}


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
            return _Result(row=self.rows.get((params["business_id"], params["idempotency_key"])))
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
            self.rows[(payload["business_id"], payload["idempotency_key"])] = row
            return _Result(row=row)
        raise AssertionError(sql)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_create_is_idempotent_and_never_starts_execution():
    session = _CreateSession()
    request = {
        "business_id": "exdivo",
        "site_ids": None,
        "scope": "all_sites",
        "mode": "dry_run",
        "requested_by": "codex",
        "idempotency_key": "daily-2026-07-27",
        "action_budget": 4,
        "site_quotas": {},
        "approval_policy": "use_site_capabilities",
    }

    first = await create_strategy_run(session, **request)
    second = await create_strategy_run(session, **request)

    UUID(first["run_id"])
    assert second["run_id"] == first["run_id"]
    assert first["status"] == "queued"
    assert second["idempotency_replayed"] is True
    assert session.insert_count == 1
    assert "started_at" not in session.rows[("exdivo", "daily-2026-07-27")]["payload"]


def test_fixed_state_machine_rejects_illegal_or_terminal_transition():
    validate_run_transition("queued", "discovering_sites")
    validate_run_transition("planning", "awaiting_approval")
    assert "completed" in TERMINAL_RUN_STATUSES

    with pytest.raises(ValueError, match="illegal strategy run transition"):
        validate_run_transition("planning", "completed")
    with pytest.raises(ValueError, match="illegal strategy run transition"):
        validate_run_transition("queued", "executing")
    with pytest.raises(ValueError, match="terminal"):
        validate_run_transition("completed", "planning")


def test_child_idempotency_key_is_stable_and_scoped():
    first = derive_child_idempotency_key("run-1", "site-1", "product_seo", "url-lock")
    assert first == derive_child_idempotency_key("run-1", "site-1", "product_seo", "url-lock")
    assert first != derive_child_idempotency_key("run-1", "site-2", "product_seo", "url-lock")


@pytest.mark.asyncio
async def test_dry_run_worker_uses_injected_planner_and_rejects_incomplete_coverage(monkeypatch):
    states = [{"status": "queued", "business_id": "exdivo", "site_ids": None, "scope": "all_sites",
               "action_budget": 4, "site_quotas": {}, "current_stage": "queued",
               "cancel_requested": False}]
    planner_calls = 0

    async def get_run(_session, *, run_id):
        return dict(states[-1])

    async def transition(_session, *, run_id, current_status, next_status, updates=None, commit=True):
        states.append({**states[-1], **(updates or {}), "status": next_status, "current_stage": next_status})
        return dict(states[-1])

    async def event(*_args, **_kwargs):
        return {}

    async def planner(_session, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return {"site_scope": [{"id": "a"}, {"id": "b"}], "coverage_matrix": {"decisions": [{"site_id": "a", "action": "hold"}]}}

    async def discoverer(*_args, **_kwargs):
        return [{"id": "a"}, {"id": "b"}]

    async def capabilities(_session, business_id):
        return {"business_id": business_id, "sites": [{"site_id": "a"}, {"site_id": "b"}]}

    monkeypatch.setattr("app.services.strategy_run_service.get_strategy_run", get_run)
    monkeypatch.setattr("app.services.strategy_run_service.transition_strategy_run", transition)
    monkeypatch.setattr("app.services.strategy_run_service.append_strategy_run_event", event)
    result = await run_strategy_run(
        object(), run_id="run-1", planner=planner,
        site_discoverer=discoverer, capability_loader=capabilities,
        evidence_gatherer=_evidence, action_factory=_action,
    )
    assert planner_calls == 1
    assert result["status"] == "failed"
    assert result["exceptions"][0]["type"] == "RuntimeError"

    replay = await run_strategy_run(object(), run_id="run-1", planner=planner)
    assert replay["start_replayed"] is True
    assert planner_calls == 1


@pytest.mark.asyncio
async def test_selected_sites_all_receive_capability_snapshot_and_independent_decision(monkeypatch):
    states = [{
        "status": "queued", "business_id": "exdivo", "site_ids": ["a", "b"],
        "scope": "selected_sites", "action_budget": 4, "site_quotas": {},
        "current_stage": "queued", "cancel_requested": False, "stage_outputs": {},
    }]
    planner_sites = []
    capability_calls = 0
    created_actions = []
    gathered = []

    async def get_run(_session, *, run_id):
        return dict(states[-1])

    async def transition(_session, *, run_id, current_status, next_status, updates=None, commit=True):
        states.append({**states[-1], **(updates or {}), "status": next_status, "current_stage": next_status})
        return dict(states[-1])

    async def event(*_args, **_kwargs):
        return {}

    async def discoverer(_session, *, business_id, scope, site_ids):
        assert scope == "selected_sites"
        return [{"id": item, "name": item} for item in site_ids]

    async def capabilities(_session, business_id):
        nonlocal capability_calls
        capability_calls += 1
        return {
            "business_id": business_id,
            "generated_at": "2026-07-27T00:00:00+00:00",
            "sites": [
                {"site_id": "a", "supported_actions": {"new_article": "approval_required"}},
                {"site_id": "b", "supported_actions": {"homepage_seo": "forbidden"}},
            ],
        }

    async def planner(_session, **kwargs):
        planner_sites.append(kwargs["site_id"])
        return {
            "site_scope": [{"id": kwargs["site_id"]}],
            "coverage_matrix": {
                "discovered_site_count": 1,
                "decided_site_count": 1,
                "decisions": [{"site_id": kwargs["site_id"], "action": "hold"
                    if kwargs["site_id"] == "b" else "new_article"}],
            },
        }

    async def evidence(_session, **kwargs):
        gathered.append([site["id"] for site in kwargs["sites"]])
        return {"snapshot_id": "audit-b"}

    async def action_factory(_session, **kwargs):
        assert kwargs["capability_snapshot"]["site_id"] == "a"
        created_actions.append(kwargs["decision"]["site_id"])
        return {"action_id": "action-a"}

    monkeypatch.setattr("app.services.strategy_run_service.get_strategy_run", get_run)
    monkeypatch.setattr("app.services.strategy_run_service.transition_strategy_run", transition)
    monkeypatch.setattr("app.services.strategy_run_service.append_strategy_run_event", event)
    result = await run_strategy_run(
        object(), run_id="run-1", planner=planner,
        site_discoverer=discoverer, capability_loader=capabilities,
        evidence_gatherer=evidence, action_factory=action_factory,
    )

    assert planner_sites == ["a", "b"]
    assert capability_calls == 1
    assert result["status"] == "awaiting_approval", result
    assert result["discovered_site_count"] == result["decided_site_count"] == 2
    assert [item["site_id"] for item in result["capability_snapshot"]["sites"]] == ["a", "b"]
    assert gathered == [["a", "b"]]
    assert created_actions == ["a"]
    assert result["action_ids"] == ["action-a"]
    assert "completed" not in [state["status"] for state in states]


@pytest.mark.asyncio
async def test_run_resume_does_not_repeat_completed_discovery(monkeypatch):
    states = [{
        "status": "checking_capabilities", "business_id": "exdivo", "site_ids": None,
        "scope": "all_sites", "action_budget": 4, "site_quotas": {},
        "current_stage": "checking_capabilities", "cancel_requested": False,
        "discovered_sites": [{"id": "a"}],
        "stage_outputs": {"discovering_sites": {"site_count": 1}},
    }]
    discovery_calls = 0

    async def get_run(_session, *, run_id):
        return dict(states[-1])

    async def transition(_session, *, run_id, current_status, next_status, updates=None, commit=True):
        states.append({**states[-1], **(updates or {}), "status": next_status, "current_stage": next_status})
        return dict(states[-1])

    async def event(*_args, **_kwargs):
        return {}

    async def discoverer(*_args, **_kwargs):
        nonlocal discovery_calls
        discovery_calls += 1
        return [{"id": "a"}]

    async def capabilities(_session, business_id):
        return {"business_id": business_id, "generated_at": "now", "sites": [{"site_id": "a"}]}

    async def planner(_session, **kwargs):
        return {"site_scope": [{"id": "a"}], "coverage_matrix": {
            "discovered_site_count": 1, "decided_site_count": 1,
            "decisions": [{"site_id": "a", "action": "hold"}]}}

    monkeypatch.setattr("app.services.strategy_run_service.get_strategy_run", get_run)
    monkeypatch.setattr("app.services.strategy_run_service.transition_strategy_run", transition)
    monkeypatch.setattr("app.services.strategy_run_service.append_strategy_run_event", event)
    result = await run_strategy_run(
        object(), run_id="run-1", planner=planner,
        site_discoverer=discoverer, capability_loader=capabilities,
        evidence_gatherer=_evidence, action_factory=_action,
    )
    assert discovery_calls == 0
    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_refreshing_evidence_rebuilds_the_candidate_plan(monkeypatch):
    states = [{
        "status": "planning", "business_id": "exdivo", "site_ids": None,
        "scope": "all_sites", "action_budget": 4, "site_quotas": {},
        "current_stage": "planning", "cancel_requested": False,
        "discovered_sites": [{"id": "a"}],
        "capability_snapshot": {"sites": [{"site_id": "a"}]},
        "stage_outputs": {},
    }]
    planner_calls = 0
    refresh_calls = 0

    async def get_run(_session, *, run_id):
        return dict(states[-1])

    async def transition(
        _session, *, run_id, current_status, next_status, updates=None, commit=True
    ):
        states.append({
            **states[-1], **(updates or {}), "status": next_status,
            "current_stage": next_status,
        })
        return dict(states[-1])

    async def event(*_args, **_kwargs):
        return {}

    async def planner(_session, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return {
            "replanned_after_hold_refresh": planner_calls == 1,
            "site_scope": [{"id": "a"}],
            "coverage_matrix": {
                "discovered_site_count": 1,
                "decided_site_count": 1,
                "decisions": [{"site_id": "a", "action": "hold"}],
            },
        }

    async def refresher(*_args, **_kwargs):
        nonlocal refresh_calls
        refresh_calls += 1
        return [{"source": "gsc", "status": "succeeded", "snapshot_id": "gsc-b"}]

    monkeypatch.setattr(
        "app.services.strategy_run_service.get_strategy_run", get_run
    )
    monkeypatch.setattr(
        "app.services.strategy_run_service.transition_strategy_run", transition
    )
    monkeypatch.setattr(
        "app.services.strategy_run_service.append_strategy_run_event", event
    )

    result = await run_strategy_run(
        object(),
        run_id="run-1",
        planner=planner,
        evidence_refresher=refresher,
        action_factory=_action,
    )

    assert refresh_calls == 1
    assert planner_calls == 2
    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_approved_actions_drive_verification_observation_and_completion(monkeypatch):
    states = [{
        "status": "awaiting_approval", "current_stage": "awaiting_approval",
        "business_id": "exdivo", "scope": "all_sites", "site_ids": None,
        "action_budget": 2, "site_quotas": {}, "cancel_requested": False,
        "stage_outputs": {},
    }]

    async def get_run(_session, *, run_id):
        return dict(states[-1])

    async def transition(_session, *, run_id, current_status, next_status, updates=None, commit=True):
        states.append({**states[-1], **(updates or {}), "status": next_status, "current_stage": next_status})
        return dict(states[-1])

    async def event(*_args, **_kwargs):
        return {}

    async def actions(_session, *, run_id):
        return [
            {"action_id": "x", "status": "completed", "observation_id": "o1"},
            {"action_id": "y", "status": "completed", "observation_id": "o2"},
        ]

    monkeypatch.setattr("app.services.strategy_run_service.get_strategy_run", get_run)
    monkeypatch.setattr("app.services.strategy_run_service.transition_strategy_run", transition)
    monkeypatch.setattr("app.services.strategy_run_service.append_strategy_run_event", event)
    result = await run_strategy_run(object(), run_id="run-1", action_loader=actions)

    assert [item["status"] for item in states] == [
        "awaiting_approval", "executing", "verifying", "observing", "completed"
    ]
    assert result["observation_ids"] == ["o1", "o2"]


@pytest.mark.asyncio
async def test_transition_requires_compare_and_set(monkeypatch):
    calls = []

    class Session:
        async def execute(self, statement, params=None):
            calls.append((str(statement), params))
            if "RETURNING" in str(statement):
                return _Result(
                    row={
                        "id": params["run_id"],
                        "payload": {"kind": "strategy_run", "business_id": "exdivo"},
                        "decision": {"status": params["next_status"], "current_stage": params["next_status"]},
                        "created_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                        "started_at": datetime.now(timezone.utc),
                        "finished_at": None,
                    }
                )
            return _Result()

        async def commit(self):
            return None

    result = await transition_strategy_run(
        Session(),
        run_id="6f680884-ff18-4c84-b17f-0a9e4e450807",
        current_status="queued",
        next_status="discovering_sites",
    )

    update_sql, update_params = next(item for item in calls if "UPDATE seo_agent.tasks" in item[0])
    assert "decision->>'status' = :current_status" in update_sql
    assert update_params["current_status"] == "queued"
    assert result["status"] == "discovering_sites"
