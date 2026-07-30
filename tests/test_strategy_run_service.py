from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.services import strategy_run_service as service
from app.services.strategy_run_service import (
    TERMINAL_RUN_STATUSES,
    _attach_action_bindings,
    _normalize_submitted_run_local_options,
    _plan_all_sites,
    _restrict_plan_to_discovered_sites,
    create_strategy_run,
    derive_child_idempotency_key,
    reconcile_strategy_run,
    run_strategy_run,
    submit_strategy_run_local_options,
    transition_strategy_run,
    validate_run_transition,
)


def test_run_coverage_excludes_planner_sites_outside_enabled_discovery_snapshot():
    discovered = [{"id": "enabled-a"}, {"id": "enabled-b"}]
    planned = {
        "site_scope": [*discovered, {"id": "disabled-main"}],
        "coverage_matrix": {
            "discovered_site_count": 3,
            "decided_site_count": 3,
            "decisions": [
                {"site_id": "enabled-a", "action": "hold"},
                {"site_id": "enabled-b", "action": "hold"},
                {"site_id": "disabled-main", "action": "configuration_repair"},
            ],
        },
    }

    restricted = _restrict_plan_to_discovered_sites(planned, discovered)

    assert restricted["coverage_matrix"]["discovered_site_count"] == 2
    assert restricted["coverage_matrix"]["decided_site_count"] == 2
    assert {item["site_id"] for item in restricted["coverage_matrix"]["decisions"]} == {
        "enabled-a",
        "enabled-b",
    }


def test_selected_coverage_decision_binds_the_exact_persisted_strategy_task():
    coverage = {
        "decisions": [
            {
                "site_id": "site-a",
                "action": "update_article",
                "selected_candidate_id": "candidate-a",
            }
        ]
    }
    plan = {
        "id": "plan-a",
        "items": [
            {
                "id": "strategy-a",
                "candidate_id": "candidate-a",
                "site_id": "site-a",
                "post_id": "post-a",
                "strategy_type": "update_article",
            }
        ],
    }

    bound = _attach_action_bindings(coverage, plan)

    assert bound["decisions"][0]["source_strategy_task_id"] == "strategy-a"
    assert bound["decisions"][0]["target_asset_id"] == "post-a"


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


class _OptionSession:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.row = {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "payload": {
                "kind": "strategy_run",
                "business_id": "avinoti",
                "site_ids": ["11111111-1111-1111-1111-111111111111"],
                "scope": "selected_sites",
                "mode": "approval_execution",
                "requested_by": "codex",
                "idempotency_key": "run-create-1",
                "action_budget": 200,
                "site_quotas": {},
                "approval_policy": "use_site_capabilities",
            },
            "decision": {
                "status": "queued",
                "current_stage": "queued",
                "next_action": "poll",
            },
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "error_message": None,
        }
        self.commits = 0
        self.updates = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "FROM seo_agent.tasks" in sql and "FOR UPDATE" in sql:
            return _Result(row=dict(self.row))
        if "FROM seo_agent.sites" in sql:
            return _Result(
                rows=[
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "name": "Avinoti",
                        "site_type": "main",
                        "strategy_enabled": True,
                        "market": "US",
                        "language_code": "en",
                        "domain": "avinoti.shop",
                        "base_url": "https://avinoti.shop",
                        "api_base_url": "https://api.example.test",
                    }
                ]
            )
        if "UPDATE seo_agent.tasks" in sql:
            self.updates += 1
            self.row["decision"] = {
                **self.row["decision"],
                **json.loads(params["patch"]),
            }
            return _Result(row=dict(self.row))
        raise AssertionError(sql)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_run_local_option_submission_is_persisted_and_idempotent():
    session = _OptionSession()
    options = [
        {
            "site_id": "11111111-1111-1111-1111-111111111111",
            "action": "new_article",
            "schedule_class": "execute_now",
            "topic": "how to choose a titanium travel cup",
            "title": "Titanium Travel Cup Buying Guide",
            "reason": "Live SERP and product facts support it.",
            "user_intent": "Compare travel cups before buying.",
            "evidence": {"product_api": ["product-123"]},
            "candidate_id": None,
            "keyword_id": None,
        }
    ]

    first = await submit_strategy_run_local_options(
        session,
        run_id=session.row["id"],
        requested_by="codex",
        idempotency_key="research-run-1",
        options=options,
    )
    replay = await submit_strategy_run_local_options(
        session,
        run_id=session.row["id"],
        requested_by="codex",
        idempotency_key="research-run-1",
        options=options,
    )

    assert first["run_local_option_count"] == 1
    assert first["run_local_options"][0]["option_origin"] == "codex_research"
    assert first["run_local_options"][0]["candidate_id"] is None
    assert first["run_local_options"][0]["keyword_id"] is None
    assert replay["idempotency_replayed"] is True
    assert session.updates == 1


@pytest.mark.asyncio
async def test_run_local_option_idempotency_key_rejects_changed_research():
    session = _OptionSession()
    base = {
        "site_id": "11111111-1111-1111-1111-111111111111",
        "action": "new_article",
        "schedule_class": "execute_now",
        "topic": "first topic",
        "title": "First Topic",
        "reason": "Current evidence supports it.",
        "user_intent": "Learn about the product.",
        "evidence": {"product_api": ["product-123"]},
    }
    await submit_strategy_run_local_options(
        session,
        run_id=session.row["id"],
        requested_by="codex",
        idempotency_key="research-run-1",
        options=[base],
    )

    with pytest.raises(ValueError, match="different run-local options"):
        await submit_strategy_run_local_options(
            session,
            run_id=session.row["id"],
            requested_by="codex",
            idempotency_key="research-run-1",
            options=[{**base, "topic": "changed topic"}],
        )


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
async def test_run_retry_rejects_unresolved_remote_uncertainty(monkeypatch):
    class Result:
        def scalar_one_or_none(self):
            return "unknown_remote_state"

    class Session:
        async def execute(self, statement, params=None):
            assert "remote_outcome" in str(statement)
            assert params["root_run_id"] == "root-run"
            return Result()

    async def get_run(_session, *, run_id):
        return {
            "run_id": run_id,
            "root_run_id": "root-run",
            "business_id": "business",
            "status": "partial",
            "site_ids": None,
            "scope": "all_sites",
            "mode": "execute",
            "action_budget": 1,
            "site_quotas": {},
            "approval_policy": "manual",
            "attempt": 1,
        }

    async def create(*_args, **_kwargs):
        raise AssertionError("an unresolved remote outcome must not create a retry run")

    monkeypatch.setattr(service, "get_strategy_run", get_run)
    monkeypatch.setattr(service, "create_strategy_run", create)

    with pytest.raises(ValueError, match="REMOTE_STATE_REQUIRES_MANUAL_READBACK"):
        await service.retry_strategy_run(
            Session(),
            run_id="run-1",
            requested_by="human",
            idempotency_key="retry-1",
        )


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
    planner_site_batches = []
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
        planner_site_batches.append(kwargs["site_ids"])
        return {
            "site_scope": [{"id": site_id} for site_id in kwargs["site_ids"]],
            "coverage_matrix": {
                "discovered_site_count": len(kwargs["site_ids"]),
                "decided_site_count": len(kwargs["site_ids"]),
                "decisions": [
                    {
                        "site_id": site_id,
                        "action": "hold" if site_id == "b" else "new_article",
                        "schedule_class": "hold" if site_id == "b" else "execute_now",
                    }
                    for site_id in kwargs["site_ids"]
                ],
            },
            "plan": {
                "id": "plan-1",
                "items": [{
                    "id": "strategy-a",
                    "option_id": "option-a",
                    "site_id": "a",
                    "strategy_type": "new_article",
                    "schedule_class": "execute_now",
                    "plan_id": "plan-1",
                }],
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

    assert planner_site_batches == [["a", "b"]]
    assert capability_calls == 1
    assert result["status"] == "awaiting_approval", result
    assert result["discovered_site_count"] == result["decided_site_count"] == 2
    assert [item["site_id"] for item in result["capability_snapshot"]["sites"]] == ["a", "b"]
    assert gathered == [["a", "b"]]
    assert created_actions == ["a"]
    assert result["action_ids"] == ["action-a"]
    assert "completed" not in [state["status"] for state in states]


@pytest.mark.asyncio
async def test_selected_sites_are_compiled_by_one_planner_call():
    calls = []

    async def planner(_session, **kwargs):
        calls.append(kwargs)
        return {
            "site_scope": [{"id": item} for item in kwargs["site_ids"]],
            "coverage_matrix": {
                "discovered_site_count": len(kwargs["site_ids"]),
                "decided_site_count": len(kwargs["site_ids"]),
                "decisions": [
                    {"site_id": item, "action": "hold", "schedule_class": "hold"}
                    for item in kwargs["site_ids"]
                ],
            },
        }

    result = await _plan_all_sites(
        object(),
        run={
            "run_id": "run-1",
            "business_id": "business-1",
            "scope": "selected_sites",
            "discovered_sites": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            "action_budget": 6,
            "site_quotas": {},
        },
        planner=planner,
    )

    assert len(calls) == 1
    assert calls[0]["site_ids"] == ["a", "b", "c"]
    assert result["coverage_matrix"]["decided_site_count"] == 3


@pytest.mark.asyncio
async def test_run_local_research_options_are_passed_to_the_formal_planner():
    calls = []
    submitted = [
        {
            "site_id": "a",
            "action": "new_article",
            "schedule_class": "execute_now",
            "topic": "independently researched topic",
        }
    ]

    async def planner(_session, **kwargs):
        calls.append(kwargs)
        return {
            "site_scope": [{"id": "a"}],
            "coverage_matrix": {
                "discovered_site_count": 1,
                "decided_site_count": 1,
                "decisions": [
                    {
                        "site_id": "a",
                        "action": "new_article",
                        "schedule_class": "execute_now",
                    }
                ],
            },
        }

    await _plan_all_sites(
        object(),
        run={
            "run_id": "run-1",
            "business_id": "business-1",
            "scope": "selected_sites",
            "discovered_sites": [{"id": "a"}],
            "run_local_options": submitted,
            "action_budget": 200,
            "site_quotas": {},
        },
        planner=planner,
    )

    assert calls[0]["run_local_options"] == submitted


def test_submitted_research_options_are_keywordless_and_authoritative():
    site = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Avinoti",
        "strategy_enabled": True,
        "market": "US",
        "language_code": "en",
        "base_url": "https://avinoti.shop",
        "domain": "avinoti.shop",
    }
    normalized = _normalize_submitted_run_local_options(
        business_id="avinoti",
        sites=[site],
        options=[
            {
                "site_id": site["id"],
                "action": "new_article",
                "schedule_class": "execute_now",
                "topic": "how to choose a titanium travel cup",
                "title": "Titanium Travel Cup Buying Guide",
                "reason": "Live SERP and current product facts support this direction.",
                "user_intent": "Compare options before buying.",
                "evidence": {
                    "product_api": ["product-123"],
                    "live_serp": ["serp-observation-1"],
                },
                "candidate_id": None,
                "keyword_id": None,
                "hypothesis": "Useful comparison content can earn discovery traffic.",
                "success_metrics": ["GSC impressions"],
            }
        ],
    )

    assert len(normalized) == 1
    option = normalized[0]
    assert option["option_origin"] == "codex_research"
    assert option["candidate_id"] is None
    assert option["keyword_id"] is None
    assert option["requested_schedule_class"] == "execute_now"
    assert option["strategy_type"] == "new_article"
    assert option["query"] == "how to choose a titanium travel cup"
    assert option["strategy_fingerprint"]


def test_submitted_update_requires_matching_target_identity():
    site = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Avinoti",
        "strategy_enabled": True,
        "market": "US",
        "language_code": "en",
        "base_url": "https://avinoti.shop",
        "domain": "avinoti.shop",
    }

    with pytest.raises(ValueError, match="target identity"):
        _normalize_submitted_run_local_options(
            business_id="avinoti",
            sites=[site],
            options=[
                {
                    "site_id": site["id"],
                    "action": "update_article",
                    "schedule_class": "execute_now",
                    "topic": "refresh an existing guide",
                    "title": "Refresh Existing Guide",
                    "reason": "The existing article is stale.",
                    "user_intent": "Find current guidance.",
                    "evidence": {"gsc": ["page-impressions"]},
                }
            ],
        )


def test_submitted_on_page_editorial_decision_becomes_concrete_formal_action():
    site = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "HealthyOxy",
        "strategy_enabled": True,
        "market": "US",
        "language_code": "en",
        "base_url": "https://healthyoxy.com",
        "domain": "healthyoxy.com",
    }

    normalized = _normalize_submitted_run_local_options(
        business_id="healthyoxy",
        sites=[site],
        options=[
            {
                "site_id": site["id"],
                "action": "on_page_fix",
                "action_type": "product_seo",
                "page_type": "product",
                "target_asset_id": "42",
                "remote_object_id": "gid://shopify/Product/42",
                "target_url": "https://healthyoxy.com/products/example",
                "connector_type": "shopify",
                "reason": "The product metadata misses the commercial intent.",
                "user_intent": "Evaluate this product before purchase.",
                "evidence": {"product_api": {"id": "gid://shopify/Product/42"}},
                "schedule_class": "execute_now",
                "expected_fields": ["meta_title", "meta_description"],
            }
        ],
    )

    decision = normalized[0]
    assert decision["editorial_action"] == "on_page_fix"
    assert decision["strategy_type"] == "product_seo"
    assert decision["action_type"] == "product_seo"
    assert decision["page_type"] == "product"
    assert decision["target_asset_id"] == "42"
    assert decision["remote_object_id"] == "gid://shopify/Product/42"
    assert decision["connector_type"] == "shopify"
    assert decision["expected_fields"] == ["meta_title", "meta_description"]


def test_formal_plan_binding_keeps_all_on_page_target_identity_fields():
    source = {
        "id": "strategy-1",
        "option_id": "option-1",
        "plan_id": "plan-1",
        "strategy_run_id": "run-1",
        "site_id": "site-1",
        "strategy_type": "product_seo",
        "action_type": "product_seo",
        "editorial_action": "on_page_fix",
        "page_type": "product",
        "target_asset_id": "42",
        "remote_object_id": "gid://shopify/Product/42",
        "connector_id": None,
        "connector_type": "shopify",
        "target_url": "https://example.com/products/example",
        "expected_fields": ["meta_title", "meta_description"],
        "schedule_class": "execute_now",
    }
    bound = service._bind_planned_actions(
        {
            "plan": {"id": "plan-1", "items": [source]},
            "coverage_matrix": {
                "decisions": [
                    {
                        "site_id": "site-1",
                        "selected_option_id": "option-1",
                    }
                ]
            },
        }
    )

    decision = bound["coverage_matrix"]["decisions"][0]
    for field in (
        "action_type",
        "editorial_action",
        "page_type",
        "target_asset_id",
        "remote_object_id",
        "connector_id",
        "connector_type",
        "target_url",
        "expected_fields",
    ):
        assert decision[field] == source[field]


def test_capability_gate_removes_forbidden_formal_action_from_execution_batch():
    planned = {
        "coverage_matrix": {
            "decisions": [
                {
                    "site_id": "site-a",
                    "action": "new_article",
                    "schedule_class": "execute_now",
                }
            ]
        },
        "executable_decisions": [
            {
                "site_id": "site-a",
                "action": "new_article",
                "schedule_class": "execute_now",
                "source_strategy_task_id": "strategy-a",
            }
        ],
    }
    snapshot = {
        "sites": [
            {
                "site_id": "site-a",
                "supported_actions": {"new_article": "forbidden"},
            }
        ]
    }

    gated = service._apply_capability_gate(planned, snapshot)

    assert gated["executable_decisions"] == []
    assert gated["coverage_matrix"]["decisions"][0]["action"] == "hold"
    assert (
        gated["coverage_matrix"]["decisions"][0]["block_reason"]
        == "action_capability_forbidden"
    )


def test_capability_gate_holds_action_without_unified_runtime_adapter():
    planned = {
        "coverage_matrix": {
            "decisions": [
                {
                    "site_id": "site-a",
                    "action": "on_page_fix",
                    "schedule_class": "execute_now",
                }
            ]
        },
        "executable_decisions": [
            {
                "site_id": "site-a",
                "action": "on_page_fix",
                "schedule_class": "execute_now",
                "source_strategy_task_id": "strategy-a",
            }
        ],
    }
    snapshot = {
        "sites": [
            {
                "site_id": "site-a",
                "supported_actions": {"on_page_fix": "approval_required"},
            }
        ]
    }

    gated = service._apply_capability_gate(planned, snapshot)

    assert gated["executable_decisions"] == []
    decision = gated["coverage_matrix"]["decisions"][0]
    assert decision["action"] == "hold"
    assert decision["block_reason"] == "unified_action_adapter_unavailable"


def test_capability_gate_allows_registered_concrete_on_page_action():
    decision = {
        "site_id": "site-a",
        "action": "product_seo",
        "strategy_type": "product_seo",
        "schedule_class": "execute_now",
        "source_strategy_task_id": "strategy-a",
    }
    planned = {
        "coverage_matrix": {"decisions": [decision]},
        "executable_decisions": [decision],
    }
    snapshot = {
        "sites": [
            {
                "site_id": "site-a",
                "supported_actions": {"product_seo": "approval_required"},
                "action_adapters": {
                    "product_seo": {
                        "adapter_id": "shopify_product_seo",
                        "adapter_version": "1",
                        "connector_type": "shopify",
                        "read": True,
                        "write": True,
                        "readback": True,
                    }
                },
            }
        ]
    }
    decision["connector_type"] = "shopify"

    gated = service._apply_capability_gate(planned, snapshot)

    assert gated["executable_decisions"] == [decision]
    assert gated["coverage_matrix"]["decisions"][0]["action"] == "product_seo"


def test_capability_gate_holds_concrete_on_page_action_without_adapter():
    decision = {
        "site_id": "site-a",
        "action": "product_seo",
        "strategy_type": "product_seo",
        "connector_type": "custom_openapi",
        "schedule_class": "execute_now",
        "source_strategy_task_id": "strategy-a",
    }
    planned = {
        "coverage_matrix": {"decisions": [decision]},
        "executable_decisions": [decision],
    }
    snapshot = {
        "sites": [
            {
                "site_id": "site-a",
                "supported_actions": {"product_seo": "approval_required"},
                "action_adapters": {},
            }
        ]
    }

    gated = service._apply_capability_gate(planned, snapshot)

    assert gated["executable_decisions"] == []
    assert gated["coverage_matrix"]["decisions"][0]["action"] == "hold"
    assert (
        gated["coverage_matrix"]["decisions"][0]["block_reason"]
        == "unified_action_adapter_unavailable"
    )


@pytest.mark.asyncio
async def test_formal_on_page_action_preserves_platform_target_identity(monkeypatch):
    captured = {}

    async def fake_create(_store, **values):
        captured.update(values)
        return values

    monkeypatch.setattr(
        "app.services.strategy_action_service.create_action", fake_create
    )
    capability = {
        "capability_snapshot_hash": "capability-hash",
        "supported_actions": {"product_seo": "approval_required"},
    }

    await service._create_unified_action(
        object(),
        run={
            "run_id": "run-1",
            "mode": "approval_execution",
            "business_id": "healthyoxy",
            "evidence_snapshot_id": "evidence-1",
        },
        decision={
            "site_id": "11111111-1111-1111-1111-111111111111",
            "plan_id": "plan-1",
            "source_strategy_task_id": "strategy-1",
            "action": "product_seo",
            "page_type": "product",
            "target_asset_id": "42",
            "remote_object_id": "gid://shopify/Product/42",
            "target_url": "https://healthyoxy.com/products/example",
            "connector_id": "22222222-2222-2222-2222-222222222222",
            "connector_type": "shopify",
            "expected_fields": ["meta_title", "meta_description"],
            "strategy_fingerprint": "strategy-fingerprint",
            "evidence_fingerprint": "evidence-fingerprint",
            "schedule_class": "execute_now",
        },
        idempotency_key="action-key",
        capability_snapshot=capability,
    )

    assert captured["action_type"] == "product_seo"
    assert captured["page_type"] == "product"
    assert captured["target_asset_id"] == "42"
    assert captured["remote_object_id"] == "gid://shopify/Product/42"
    assert captured["connector_type"] == "shopify"
    assert captured["expected_fields"] == ["meta_title", "meta_description"]


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
    result = await reconcile_strategy_run(
        object(), run_id="run-1", action_loader=actions
    )

    assert [item["status"] for item in states] == [
        "awaiting_approval", "executing", "verifying", "observing", "completed"
    ]
    assert result["observation_ids"] == ["o1", "o2"]


@pytest.mark.asyncio
async def test_reconcile_marks_run_executing_while_some_actions_are_still_open(monkeypatch):
    states = [{
        "run_id": "run-1",
        "status": "awaiting_approval",
        "current_stage": "awaiting_approval",
        "business_id": "exdivo",
        "stage_outputs": {},
    }]

    async def get_run(_session, *, run_id):
        return dict(states[-1])

    async def transition(
        _session, *, run_id, current_status, next_status, updates=None, commit=True
    ):
        states.append({
            **states[-1],
            **(updates or {}),
            "status": next_status,
            "current_stage": next_status,
        })
        return dict(states[-1])

    async def event(*_args, **_kwargs):
        return {}

    async def actions(_session, *, run_id):
        return [
            {"action_id": "x", "status": "completed", "observation_id": "o1"},
            {"action_id": "y", "status": "approved"},
        ]

    monkeypatch.setattr("app.services.strategy_run_service.get_strategy_run", get_run)
    monkeypatch.setattr(
        "app.services.strategy_run_service.transition_strategy_run", transition
    )
    monkeypatch.setattr(
        "app.services.strategy_run_service.append_strategy_run_event", event
    )

    result = await reconcile_strategy_run(
        object(), run_id="run-1", action_loader=actions
    )

    assert result["status"] == "executing"
    assert result["next_action"] == "complete_actions"


@pytest.mark.asyncio
async def test_reconcile_closes_partial_run_after_last_blocked_action_recovers(
    monkeypatch,
):
    states = [{
        "run_id": "run-1",
        "status": "partial",
        "current_stage": "partial",
        "business_id": "ladiesstreetx",
        "stage_outputs": {},
        "observation_ids": ["o1"],
        "finished_at": "2026-07-30T13:00:00+00:00",
    }]

    async def get_run(_session, *, run_id):
        return dict(states[-1])

    async def reconcile_terminal(
        _session, *, run_id, current_status, next_status, updates=None
    ):
        states.append({
            **states[-1],
            **(updates or {}),
            "status": next_status,
            "current_stage": next_status,
        })
        return dict(states[-1])

    async def actions(_session, *, run_id):
        return [
            {"action_id": "x", "status": "completed", "observation_id": "o1"},
            {"action_id": "y", "status": "completed", "observation_id": "o2"},
        ]

    monkeypatch.setattr(
        "app.services.strategy_run_service.get_strategy_run", get_run
    )
    monkeypatch.setattr(
        "app.services.strategy_run_service._reconcile_terminal_run",
        reconcile_terminal,
        raising=False,
    )

    result = await reconcile_strategy_run(
        object(), run_id="run-1", action_loader=actions
    )

    assert result["status"] == "completed"
    assert result["observation_ids"] == ["o1", "o2"]
    assert result["action_outcome_counts"] == {
        "completed": 2,
        "blocked": 0,
        "failed": 0,
    }


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
