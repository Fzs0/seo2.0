from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.services import strategy_action_service as service
from app.services.strategy_exception_service import exception_fingerprint, sanitize_payload


class MemoryStore:
    def __init__(self, action: dict):
        self.action = deepcopy(action)
        self.exceptions: list[dict] = []
        self.observations: list[dict] = []
        self.reconciled_run_ids: list[str] = []
        self.reconciled_strategy_ids: list[str] = []
        self.resolved_corrective_parents: list[str] = []
        self.lock_held = False

    async def get(self, _action_id: str, *, lock: bool = False) -> dict:
        self.lock_held = lock
        return deepcopy(self.action)

    async def save(self, action: dict) -> None:
        self.action = deepcopy(action)
        self.lock_held = False

    async def record_exception(self, exception: dict) -> dict:
        self.exceptions.append(deepcopy(exception))
        return {"exception_id": f"exc-{len(self.exceptions)}", **exception}

    async def create_observation(self, observation: dict) -> dict:
        if not self.observations:
            self.observations.append(deepcopy(observation))
        return deepcopy(self.observations[0])

    async def reconcile_parent_run(self, run_id: str) -> None:
        self.reconciled_run_ids.append(run_id)

    async def reconcile_source_strategy(
        self, strategy_task_id: str, *, action_status: str
    ) -> None:
        assert action_status == "canceled"
        self.reconciled_strategy_ids.append(strategy_task_id)

    async def list_reconciliation_candidates(self, business_id: str) -> list[dict]:
        if self.action.get("business_id") != business_id:
            return []
        return [deepcopy(self.action)]

    async def resolve_corrective_parent(self, action: dict) -> None:
        self.resolved_corrective_parents.append(action["corrective_of_action_id"])


class Adapter:
    def __init__(self, *, mismatch: bool = False):
        self.writes = 0
        self.mismatch = mismatch

    async def preview(self, action: dict, patch: dict) -> dict:
        return {"before_snapshot": {"title": "Before"}, "proposed_patch": patch}

    async def execute(self, action: dict) -> dict:
        self.writes += 1
        return {
            "result": "updated",
            "remote_response": {"ok": True},
            "readback": {"title": "Wrong" if self.mismatch else "After"},
            "readback_matches": not self.mismatch,
            "observation_id": "obs-1",
        }

    async def recover(self, action: dict) -> dict:
        return {"recovery_status": "unknown"}


class FailingAdapter(Adapter):
    async def execute(self, action: dict) -> dict:
        self.writes += 1
        raise RuntimeError("connector token=secret-value failed")


def action() -> dict:
    return {
        "action_id": "action-1",
        "run_id": "run-1",
        "run_mode": "approval_execution",
        "business_id": "business-1",
        "site_id": "site-1",
        "action_type": "product_seo",
        "page_type": "product",
        "target_asset_id": "42",
        "connector_type": "oemapps",
        "target_url": "https://example.com/p/1",
        "status": "planned",
        "idempotency_key": "run-1:action-1",
        "risk_level": "medium",
        "approval_requirement": "approval_required",
        "capability_snapshot": {
            "capability_snapshot_hash": None,
            "supported_actions": {"product_seo": "approval_required"},
            "supported_fields": {
                "product_seo": ["title", "description", "meta_title"]
            },
            "connectors": {
                "products": {
                    "status": "available",
                    "write": True,
                    "checked_at": datetime.now(UTC).isoformat(),
                }
            },
            "configuration_issues": [],
            "side_effects": {},
        },
    }


@pytest.mark.asyncio
async def test_dry_run_action_never_calls_writer() -> None:
    dry_action = action()
    dry_action.update(
        {
            "run_mode": "dry_run",
            "status": "approved",
            "approved_snapshot_hash": service._hash({}),
            "approved_patch_hash": service._hash({"title": "After"}),
            "before_snapshot": {},
            "proposed_patch": {"title": "After"},
        }
    )
    store, adapter = MemoryStore(dry_action), Adapter()

    result = await service.execute_action(
        store,
        action_id="action-1",
        adapter=adapter,
    )

    assert result["result"] == "blocked"
    assert result["block_reason"] == "dry_run_actions_cannot_execute"
    assert adapter.writes == 0


@pytest.mark.asyncio
async def test_approval_binds_exact_snapshot_and_patch_hash() -> None:
    store, adapter = MemoryStore(action()), Adapter()
    preview = await service.preview_action(
        store, action_id="action-1", patch={"title": "After"}, adapter=adapter
    )
    approved = await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        generation_mode="manual",
    )
    assert approved["status"] == "approved"
    assert approved["generation_mode"] == "manual"
    assert approved["generation_provider"] is None
    assert approved["generation_model"] is None
    store.action["proposed_patch"]["title"] = "Changed after approval"
    result = await service.execute_action(store, action_id="action-1", adapter=adapter)
    assert result["result"] == "blocked"
    assert adapter.writes == 0


@pytest.mark.asyncio
async def test_model_provenance_must_be_exact() -> None:
    store, adapter = MemoryStore(action()), Adapter()
    preview = await service.preview_action(
        store, action_id="action-1", patch={"title": "After"}, adapter=adapter
    )
    with pytest.raises(ValueError, match="exact generation"):
        await service.approve_action(
            store,
            action_id="action-1",
            snapshot_hash=preview["snapshot_hash"],
            patch_hash=preview["patch_hash"],
            generation_mode="model",
            generation_provider="openai",
            generation_model="gpt-5",
        )


@pytest.mark.asyncio
async def test_preview_persists_exact_generation_provenance() -> None:
    store, adapter = MemoryStore(action()), Adapter()
    preview = await service.preview_action(
        store,
        action_id="action-1",
        patch={"title": "After"},
        adapter=adapter,
        generation_mode="model",
        generation_provider="openai",
        generation_model="not_exposed_by_runtime",
        generation_run_id="generation-1",
    )

    assert preview["generation_mode"] == "model"
    assert preview["generation_provider"] == "openai"
    assert preview["generation_model"] == "not_exposed_by_runtime"
    assert preview["generation_run_id"] == "generation-1"


@pytest.mark.asyncio
async def test_execute_is_idempotent_and_uses_fixed_result_semantics() -> None:
    store, adapter = MemoryStore(action()), Adapter()
    preview = await service.preview_action(
        store, action_id="action-1", patch={"title": "After"}, adapter=adapter
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        generation_mode="model",
        generation_provider="openai",
        generation_model="gpt-5.1-2026-01-15",
        generation_run_id="generation-1",
    )
    first = await service.execute_action(store, action_id="action-1", adapter=adapter)
    second = await service.execute_action(store, action_id="action-1", adapter=adapter)
    assert first["result"] == "updated"
    assert second["result"] == "already_applied"
    assert adapter.writes == 1
    assert store.action["execution_token"]
    assert store.action["execution_claimed_at"]
    assert store.action["generation_model"] == "gpt-5.1-2026-01-15"


@pytest.mark.asyncio
async def test_successful_corrective_action_resolves_parent_after_readback() -> None:
    corrective = action()
    corrective["corrective_of_action_id"] = "parent-action-1"
    store, adapter = MemoryStore(corrective), Adapter()
    preview = await service.preview_action(
        store, action_id="action-1", patch={"title": "After"}, adapter=adapter
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        generation_mode="manual",
    )

    result = await service.execute_action(
        store, action_id="action-1", adapter=adapter
    )

    assert result["status"] == "completed"
    assert store.resolved_corrective_parents == ["parent-action-1"]


@pytest.mark.asyncio
async def test_formal_execute_reconciles_parent_run_without_changing_action_response() -> None:
    store, adapter = MemoryStore(action()), Adapter()
    preview = await service.preview_action(
        store, action_id="action-1", patch={"title": "After"}, adapter=adapter
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        generation_mode="manual",
    )

    result = await service.execute_action_and_reconcile(
        store, action_id="action-1", adapter=adapter
    )

    assert result["status"] == "completed"
    assert result["result"] == "updated"
    assert store.reconciled_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_readback_mismatch_is_failed_exception_without_observation() -> None:
    store, adapter = MemoryStore(action()), Adapter(mismatch=True)
    preview = await service.preview_action(
        store, action_id="action-1", patch={"title": "After"}, adapter=adapter
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        generation_mode="manual",
    )
    result = await service.execute_action(store, action_id="action-1", adapter=adapter)
    assert result["result"] == "readback_mismatch"
    assert store.action["status"] == "failed"
    assert store.action.get("observation_id") is None
    assert store.exceptions[0]["type"] == "readback_mismatch"
    assert store.exceptions[0]["severity"] == "P1"
    assert store.exceptions[0]["remote_write_occurred"] is True
    assert store.action["field_differences"] == [
        {
            "field": "title",
            "expected": "After",
            "submitted": "After",
            "actual": "Wrong",
            "match": False,
        }
    ]
    assert store.observations == []


@pytest.mark.asyncio
async def test_platform_verifies_normalized_fields_and_creates_one_observation_plan() -> None:
    item = action()
    item["capability_snapshot"]["capability_snapshot_hash"] = "cap-v1"
    store, adapter = MemoryStore(item), Adapter()
    preview = await service.preview_action(
        store,
        action_id="action-1",
        patch={"title": " After ", "description": "<p>Hello</p>"},
        adapter=adapter,
        capability_snapshot_hash="cap-v1",
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        capability_snapshot_hash="cap-v1",
        generation_mode="manual",
    )

    class NormalizingAdapter(Adapter):
        async def execute(self, action: dict) -> dict:
            self.writes += 1
            return {
                "result": "updated",
                "submitted_patch": {
                    "title": " After ",
                    "description": "<p>Hello</p>",
                },
                "readback": {
                    "title": "After",
                    "description": "<p> Hello </p>",
                    "id": "remote-read-only",
                },
                "remote_response": {"ok": True},
                "read_only_fields": ["id"],
            }

    writer = NormalizingAdapter()
    first = await service.execute_action(
        store,
        action_id="action-1",
        adapter=writer,
        capability_snapshot_hash="cap-v1",
    )
    second = await service.execute_action(
        store,
        action_id="action-1",
        adapter=writer,
        capability_snapshot_hash="cap-v1",
    )
    assert first["status"] == "completed"
    assert second["result"] == "already_applied"
    assert all(item["match"] for item in first["field_differences"])
    assert len(store.observations) == 1
    assert store.observations[0]["checkpoints_days"] == [0, 7, 14, 28, 56, 90]
    assert store.observations[0]["checkpoint_statuses"]["0"] == "pending"
    assert store.observations[0]["page_type"] == "product"
    assert store.observations[0]["target_asset_id"] == "42"
    assert store.observations[0]["before_snapshot_hash"]
    assert store.observations[0]["after_snapshot_hash"]
    assert store.observations[0]["t0_required_checks"] == [
        "http_status",
        "title",
        "description",
        "canonical",
        "robots",
        "page_accessibility",
    ]
    assert store.observations[0]["next_check_at"]
    assert store.observations[0]["action_id"] == "action-1"


@pytest.mark.asyncio
async def test_capability_change_invalidates_approval_before_remote_write() -> None:
    item = action()
    item["capability_snapshot"]["capability_snapshot_hash"] = "cap-v1"
    store, adapter = MemoryStore(item), Adapter()
    preview = await service.preview_action(
        store,
        action_id="action-1",
        patch={"title": "After"},
        adapter=adapter,
        capability_snapshot_hash="cap-v1",
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        capability_snapshot_hash="cap-v1",
        generation_mode="manual",
    )
    result = await service.execute_action(
        store,
        action_id="action-1",
        adapter=adapter,
        capability_snapshot_hash="cap-v2",
    )
    assert result["result"] == "blocked"
    assert result["block_reason"] == "capability_snapshot_changed"
    assert store.action["status"] == "previewed"
    assert adapter.writes == 0


@pytest.mark.asyncio
async def test_preview_releases_claim_lock_before_external_call_and_idempotently_replays() -> None:
    store = MemoryStore(action())

    class LockCheckingAdapter(Adapter):
        async def preview(self, action: dict, patch: dict) -> dict:
            assert store.lock_held is False
            return await super().preview(action, patch)

    first = await service.preview_action(
        store,
        action_id="action-1",
        patch={"title": "After"},
        adapter=LockCheckingAdapter(),
        idempotency_key="preview-key",
    )
    replay = await service.preview_action(
        store,
        action_id="action-1",
        patch={"title": "After"},
        adapter=LockCheckingAdapter(),
        idempotency_key="preview-key",
    )
    assert replay == first
    with pytest.raises(ValueError, match="idempotency key"):
        await service.preview_action(
            store,
            action_id="action-1",
            patch={"title": "Different"},
            adapter=LockCheckingAdapter(),
            idempotency_key="preview-key",
        )


@pytest.mark.asyncio
async def test_heartbeat_requires_current_execution_token() -> None:
    active = action()
    active.update({"status": "executing", "execution_token": "current-token"})
    store = MemoryStore(active)
    updated = await service.heartbeat_action(
        store,
        action_id="action-1",
        execution_token="current-token",
        lease_seconds=120,
    )
    assert updated["last_heartbeat_at"]
    with pytest.raises(ValueError, match="stale execution token"):
        await service.heartbeat_action(
            store, action_id="action-1", execution_token="old-token"
        )


@pytest.mark.asyncio
async def test_persisted_capability_snapshot_blocks_undeclared_fields() -> None:
    item = action()
    item["capability_snapshot"] = {
        "capability_snapshot_hash": "capability-hash-v1",
        "supported_actions": {"product_seo": "approval_required"},
        "supported_fields": {"product_seo": ["meta_title"]},
        "connectors": {"products": {"status": "available", "write": True}},
        "configuration_issues": [],
        "side_effects": {},
    }
    store = MemoryStore(item)
    result = await service.preview_action(
        store,
        action_id="action-1",
        patch={"price": 12},
        adapter=Adapter(),
        capability_snapshot_hash="capability-hash-v1",
    )
    assert result["result"] == "blocked"
    assert result["block_reason"] == "field_not_allowed"


@pytest.mark.asyncio
async def test_preview_cannot_expand_beyond_formal_plan_fields() -> None:
    item = action()
    item["expected_fields"] = ["meta_title"]
    item["capability_snapshot"] = {
        "capability_snapshot_hash": "capability-hash-v1",
        "supported_actions": {"product_seo": "approval_required"},
        "supported_fields": {"product_seo": ["title", "meta_title"]},
        "connectors": {
            "products": {
                "status": "available",
                "write": True,
                "checked_at": datetime.now(UTC).isoformat(),
            }
        },
        "configuration_issues": [],
        "side_effects": {},
    }
    store = MemoryStore(item)

    result = await service.preview_action(
        store,
        action_id="action-1",
        patch={"title": "Unplanned title change"},
        adapter=Adapter(),
        capability_snapshot_hash="capability-hash-v1",
    )

    assert result["result"] == "blocked"
    assert result["block_reason"] == "field_not_in_formal_plan"


@pytest.mark.asyncio
async def test_side_effect_confirmation_is_bound_at_approval_not_preview() -> None:
    item = action()
    item["capability_snapshot"] = {
        "capability_snapshot_hash": "capability-hash-v1",
        "supported_actions": {"product_seo": "approval_required"},
        "supported_fields": {"product_seo": ["meta_title"]},
        "connectors": {
            "products": {
                "status": "available",
                "write": True,
                "checked_at": datetime.now(UTC).isoformat(),
            }
        },
        "configuration_issues": [],
        "side_effects": {
            "product_seo": {"requires_variant_confirmation": True}
        },
    }
    store = MemoryStore(item)
    preview = await service.preview_action(
        store,
        action_id="action-1",
        patch={"meta_title": "After"},
        adapter=Adapter(),
        capability_snapshot_hash="capability-hash-v1",
    )

    assert preview["status"] == "previewed"
    with pytest.raises(ValueError, match="side-effect confirmation"):
        await service.approve_action(
            store,
            action_id="action-1",
            snapshot_hash=preview["snapshot_hash"],
            patch_hash=preview["patch_hash"],
            capability_snapshot_hash="capability-hash-v1",
            generation_mode="manual",
        )

    approved = await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        capability_snapshot_hash="capability-hash-v1",
        generation_mode="manual",
        side_effect_confirmations={"requires_variant_confirmation": True},
    )

    assert approved["status"] == "approved"
    assert approved["side_effect_confirmations"] == {
        "requires_variant_confirmation": True
    }


@pytest.mark.asyncio
async def test_adapter_identity_is_bound_to_capability_and_approval() -> None:
    item = action()
    item["adapter_identity"] = {
        "adapter_id": "oemapps_on_page",
        "adapter_version": "1",
        "connector_type": "oemapps",
        "read": True,
        "write": True,
        "readback": True,
    }
    item["capability_snapshot"] = {
        "capability_snapshot_hash": "capability-hash-v1",
        "supported_actions": {"product_seo": "approval_required"},
        "supported_fields": {"product_seo": ["meta_title"]},
        "connectors": {
            "products": {
                "status": "available",
                "write": True,
                "checked_at": datetime.now(UTC).isoformat(),
            }
        },
        "configuration_issues": [],
        "side_effects": {},
        "action_adapters": {"product_seo": item["adapter_identity"]},
    }
    store, adapter = MemoryStore(item), Adapter()
    preview = await service.preview_action(
        store,
        action_id="action-1",
        patch={"meta_title": "After"},
        adapter=adapter,
        capability_snapshot_hash="capability-hash-v1",
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        capability_snapshot_hash="capability-hash-v1",
        generation_mode="manual",
    )
    store.action["adapter_identity"] = {
        **store.action["adapter_identity"],
        "adapter_version": "2",
    }

    result = await service.execute_action(
        store,
        action_id="action-1",
        adapter=adapter,
        capability_snapshot_hash="capability-hash-v1",
    )

    assert result["result"] == "blocked"
    assert result["block_reason"] == "adapter_identity_changed"
    assert adapter.writes == 0


@pytest.mark.asyncio
async def test_target_identity_change_invalidates_approval_before_write() -> None:
    store, adapter = MemoryStore(action()), Adapter()
    preview = await service.preview_action(
        store,
        action_id="action-1",
        patch={"title": "After"},
        adapter=adapter,
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        generation_mode="manual",
    )
    store.action["target_asset_id"] = "99"

    result = await service.execute_action(
        store, action_id="action-1", adapter=adapter
    )

    assert result["result"] == "blocked"
    assert result["block_reason"] == "target_identity_changed"
    assert adapter.writes == 0


@pytest.mark.asyncio
async def test_missing_capability_snapshot_is_blocked_by_default() -> None:
    item = action()
    item.pop("capability_snapshot")
    store, adapter = MemoryStore(item), Adapter()
    result = await service.preview_action(
        store,
        action_id="action-1",
        patch={"title": "After"},
        adapter=adapter,
        capability_snapshot_hash="missing-snapshot",
    )
    assert result["result"] == "blocked"
    assert result["block_reason"] == "capability_snapshot_missing"
    assert store.action["status"] == "planned"


@pytest.mark.asyncio
async def test_expired_execution_lease_recovery_classifies_remote_state_and_rejects_old_token() -> None:
    stale = action()
    stale.update(
        {
            "status": "executing",
            "execution_token": "old-token",
            "execution_claimed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "execution_lease_expires_at": (datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
            "execution_attempt": 1,
            "proposed_patch": {"title": "After"},
            "approved_patch": {"title": "After"},
        }
    )
    store = MemoryStore(stale)

    class NotAppliedAdapter(Adapter):
        async def recover(self, action: dict) -> dict:
            assert store.lock_held is False
            return {"recovery_status": "confirmed_not_applied"}

    recovered = await service.recover_action(
        store, action_id="action-1", adapter=NotAppliedAdapter()
    )
    assert recovered["status"] == "planned"
    assert recovered["approved_at"] is None
    assert recovered["approved_patch_hash"] is None
    assert recovered["execution_token"] is None
    with pytest.raises(ValueError, match="stale execution token"):
        await service.complete_execution(
            store,
            action_id="action-1",
            execution_token="old-token",
            result={"result": "updated", "readback": {"title": "After"}},
        )


@pytest.mark.asyncio
async def test_failed_action_can_be_reconciled_from_exact_readback_without_second_write() -> None:
    failed = action()
    failed.update(
        {
            "status": "failed",
            "result": "readback_mismatch",
            "execution_token": "original-write-token",
            "execution_claimed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "execution_lease_expires_at": (datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
            "execution_attempt": 1,
            "proposed_patch": {"title": "After"},
            "approved_patch": {"title": "After"},
            "submitted_patch": {"title": "After"},
            "remote_response": {"raw": {"code": 0, "data": True}},
            "article_id": "article-1",
            "publish_task_id": "publish-1",
            "effect_id": "effect-1",
        }
    )
    store = MemoryStore(failed)

    class ReadbackOnlyAdapter(Adapter):
        async def recover(self, action: dict) -> dict:
            assert action["status"] == "recovering"
            assert self.writes == 0
            return {
                "recovery_status": "confirmed_applied",
                "result": "updated",
                "submitted_patch": {"title": "After"},
                "readback": {"title": "After"},
                "remote_response": {"remote_id": "2588676"},
                "article_id": "article-1",
                "publish_task_id": "publish-1",
                "effect_id": "effect-1",
                "target_url": "https://example.com/p/1",
            }

    adapter = ReadbackOnlyAdapter()
    first = await service.recover_action(
        store,
        action_id="action-1",
        adapter=adapter,
        idempotency_key="reconcile-confirmed-write",
    )
    second = await service.recover_action(
        store,
        action_id="action-1",
        adapter=adapter,
        idempotency_key="reconcile-confirmed-write",
    )

    assert first["status"] == "completed"
    assert first["result"] == "updated"
    assert second == first
    assert adapter.writes == 0
    assert len(store.observations) == 1
    assert store.action["article_id"] == "article-1"
    assert store.action["publish_task_id"] == "publish-1"
    assert store.action["effect_id"] == "effect-1"


@pytest.mark.asyncio
async def test_success_activates_effect_only_after_action_and_observation_are_complete() -> None:
    executing = action()
    executing.update(
        {
            "status": "executing",
            "execution_token": "execution-token",
            "proposed_patch": {"title": "After"},
            "approved_patch": {"title": "After"},
            "effect_id": "effect-1",
            "execution_task_id": "execution-1",
        }
    )

    class OrderedStore(MemoryStore):
        def __init__(self, current: dict):
            super().__init__(current)
            self.events: list[str] = []

        async def save(self, current: dict) -> None:
            self.events.append(f"save:{current['status']}")
            await super().save(current)

        async def create_observation(self, observation: dict) -> dict:
            self.events.append("create_observation")
            return await super().create_observation(observation)

        async def activate_effect_observation(self, current: dict) -> None:
            assert current["status"] == "completed"
            assert current["observation_id"]
            self.events.append("activate_effect")

    store = OrderedStore(executing)
    result = await service.complete_execution(
        store,
        action_id="action-1",
        execution_token="execution-token",
        result={
            "result": "updated",
            "submitted_patch": {"title": "After"},
            "readback": {"title": "After"},
            "effect_id": "effect-1",
            "execution_task_id": "execution-1",
        },
    )

    assert result["status"] == "completed"
    assert store.events[-3:] == [
        "create_observation",
        "activate_effect",
        "save:completed",
    ]


@pytest.mark.asyncio
async def test_failed_action_confirmed_absent_stays_blocked_and_never_becomes_rewritable() -> None:
    failed = action()
    failed.update(
        {
            "status": "failed",
            "result": "readback_mismatch",
            "execution_token": "original-write-token",
            "submitted_patch": {"title": "After"},
            "remote_response": {"raw": {"code": 0, "data": True}},
        }
    )
    store = MemoryStore(failed)

    class AbsentAdapter(Adapter):
        async def recover(self, action: dict) -> dict:
            return {"recovery_status": "confirmed_absent"}

    adapter = AbsentAdapter()
    result = await service.recover_action(
        store,
        action_id="action-1",
        adapter=adapter,
        idempotency_key="reconcile-confirmed-absent",
    )

    assert result["status"] == "blocked"
    assert result["result"] == "blocked"
    assert store.action["recovery_status"] == "confirmed_absent"
    assert adapter.writes == 0
    assert store.observations == []


@pytest.mark.asyncio
async def test_rollback_is_capability_blocked_and_never_calls_writer() -> None:
    store, adapter = MemoryStore(action()), Adapter()
    preview = await service.rollback_preview(store, action_id="action-1")
    result = await service.rollback_action(store, action_id="action-1", confirm=True)
    assert preview["result"] == result["result"] == "blocked"
    assert preview["block_reason"] == "rollback_capability_not_declared"
    assert adapter.writes == 0


@pytest.mark.asyncio
async def test_adapter_failure_becomes_auditable_unknown_remote_state() -> None:
    store, adapter = MemoryStore(action()), FailingAdapter()
    preview = await service.preview_action(
        store, action_id="action-1", patch={"title": "After"}, adapter=adapter
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        generation_mode="manual",
    )
    result = await service.execute_action(store, action_id="action-1", adapter=adapter)
    assert result["result"] == "blocked"
    assert store.action["status"] == "blocked"
    assert store.action["recovery_status"] == "unknown_remote_state"
    assert store.exceptions[0]["type"] == "connector_error"
    assert store.exceptions[0]["retryable"] is False
    assert store.exceptions[0]["remote_write_occurred"] is None
    assert store.action["submitted_patch"] == {"title": "After"}
    assert "secret-value" not in str(store.action["remote_response"])

    class ReadOnlyRecovery(Adapter):
        async def recover(self, _action):
            return {
                "recovery_status": "confirmed_not_applied",
                "submitted_patch": {"title": "After"},
                "remote_response": {
                    "remote_outcome": "confirmed_not_applied",
                    "reconciled_without_remote_write": True,
                },
                "readback": {},
            }

    recovered = await service.recover_action(
        store, action_id="action-1", adapter=ReadOnlyRecovery()
    )
    assert recovered["recovery_status"] == "confirmed_not_applied"
    assert recovered["status"] == "blocked"
    assert recovered["remote_response"]["remote_outcome"] == "confirmed_not_applied"
    assert recovered["remote_response"]["reconciled_without_remote_write"] is True


@pytest.mark.asyncio
async def test_remote_outcome_blocked_result_creates_p1_exception_without_observation() -> None:
    class UnknownAdapter(Adapter):
        async def execute(self, _action):
            return {
                "result": "blocked",
                "remote_outcome": "unknown_remote_state",
                "remote_response": {"retry_policy": "manual_readback_required"},
            }

    store, adapter = MemoryStore(action()), UnknownAdapter()
    preview = await service.preview_action(
        store, action_id="action-1", patch={"title": "After"}, adapter=adapter
    )
    await service.approve_action(
        store,
        action_id="action-1",
        snapshot_hash=preview["snapshot_hash"],
        patch_hash=preview["patch_hash"],
        generation_mode="manual",
    )

    result = await service.execute_action(store, action_id="action-1", adapter=adapter)

    assert result["result"] == "blocked"
    assert store.action["status"] == "blocked"
    assert store.action["recovery_status"] == "unknown_remote_state"
    assert store.action["observation_id"] is None
    assert store.exceptions[0]["severity"] == "P1"
    assert store.exceptions[0]["retryable"] is False


def test_exception_redaction_is_recursive_and_fingerprint_aggregates_root_cause() -> None:
    sanitized = sanitize_payload(
        {
            "headers": {
                "Authorization": "Bearer abc",
                "Cookie": "session=abc",
                "safe": "ok",
            },
            "url": "https://example.com/path?api_key=abc&lang=en",
            "nested": [{"password": "pw", "message": "Bearer xyz"}],
        }
    )
    assert sanitized["headers"]["Authorization"] == "[REDACTED]"
    assert sanitized["headers"]["Cookie"] == "[REDACTED]"
    assert sanitized["headers"]["safe"] == "ok"
    assert sanitized["url"] == "https://example.com/path?api_key=[REDACTED]&lang=en"
    assert sanitized["nested"][0]["password"] == "[REDACTED]"
    assert "xyz" not in sanitized["nested"][0]["message"]
    base = {
        "business_id": "business-1",
        "site_id": "site-1",
        "type": "connector_error",
        "stage": "executing",
        "error_code": "CONNECTOR_TIMEOUT",
    }
    assert exception_fingerprint({**base, "action_id": "a-1"}) == exception_fingerprint(
        {**base, "action_id": "a-2"}
    )


def test_exception_redacts_credentials_from_multiple_urls_in_free_text() -> None:
    sanitized = sanitize_payload(
        "first https://a.test/x?token=one&q=safe then "
        "https://b.test/y?api_key=two&lang=en Authorization: Bearer abc"
    )
    assert "one" not in sanitized
    assert "two" not in sanitized
    assert "abc" not in sanitized
    assert "q=safe" in sanitized
    assert "lang=en" in sanitized


@pytest.mark.asyncio
async def test_expired_unexecuted_action_is_canceled_and_parent_run_reconciled() -> None:
    stale = action()
    stale["source_strategy_task_id"] = "strategy-1"
    stale["unexecuted_expires_at"] = (
        datetime.now(UTC) - timedelta(minutes=1)
    ).isoformat()
    store = MemoryStore(stale)

    result = await service.expire_unexecuted_action(
        store,
        action_id="action-1",
        now=datetime.now(UTC),
    )

    assert result["status"] == "canceled"
    assert result["closure_reason"] == "approval_expired_unexecuted"
    assert result["remote_write_occurred"] is False
    assert store.reconciled_strategy_ids == ["strategy-1"]
    assert store.reconciled_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_already_canceled_action_backfills_source_strategy_reconciliation() -> None:
    canceled = action()
    canceled.update(
        {
            "status": "canceled",
            "source_strategy_task_id": "strategy-1",
            "closure_reason": "approval_expired_unexecuted",
            "remote_write_occurred": False,
        }
    )
    store = MemoryStore(canceled)

    result = await service.expire_unexecuted_action(
        store,
        action_id="action-1",
        now=datetime.now(UTC),
    )

    assert result["closure_result"] == "already_terminal"
    assert store.reconciled_strategy_ids == ["strategy-1"]
    assert store.reconciled_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_expired_action_with_execution_evidence_is_never_locally_canceled() -> None:
    stale = action()
    stale.update(
        {
            "status": "approved",
            "unexecuted_expires_at": (
                datetime.now(UTC) - timedelta(minutes=1)
            ).isoformat(),
            "execution_token": "possible-remote-write",
        }
    )
    store = MemoryStore(stale)

    result = await service.expire_unexecuted_action(
        store,
        action_id="action-1",
        now=datetime.now(UTC),
    )

    assert result["status"] == "approved"
    assert result["closure_result"] == "remote_recovery_required"
    assert store.action["status"] == "approved"
    assert store.reconciled_run_ids == []


@pytest.mark.asyncio
async def test_stale_reconciliation_recovers_then_closes_confirmed_unapplied_action() -> None:
    stale = action()
    stale.update(
        {
            "status": "executing",
            "unexecuted_expires_at": (
                datetime.now(UTC) - timedelta(hours=2)
            ).isoformat(),
            "execution_token": "old-token",
            "execution_claimed_at": (
                datetime.now(UTC) - timedelta(hours=1)
            ).isoformat(),
            "execution_lease_expires_at": (
                datetime.now(UTC) - timedelta(minutes=30)
            ).isoformat(),
            "execution_attempt": 1,
            "proposed_patch": {"title": "After"},
            "approved_patch": {"title": "After"},
        }
    )
    store = MemoryStore(stale)

    class ConfirmedUnappliedAdapter(Adapter):
        async def recover(self, _action: dict) -> dict:
            return {"recovery_status": "confirmed_not_applied"}

    result = await service.reconcile_stale_actions(
        store,
        business_id="business-1",
        adapter=ConfirmedUnappliedAdapter(),
        now=datetime.now(UTC),
    )

    assert result["canceled"] == 1
    assert result["uncertain"] == 0
    assert store.action["status"] == "canceled"
    assert store.action["recovery_status"] == "confirmed_not_applied"
    assert store.reconciled_run_ids[-1] == "run-1"


@pytest.mark.asyncio
async def test_active_execution_lease_is_not_reported_as_stale_or_uncertain() -> None:
    active = action()
    active.update(
        {
            "status": "executing",
            "execution_token": "active-token",
            "execution_lease_expires_at": (
                datetime.now(UTC) + timedelta(minutes=10)
            ).isoformat(),
        }
    )
    store = MemoryStore(active)

    result = await service.reconcile_stale_actions(
        store,
        business_id="business-1",
        adapter=Adapter(),
        now=datetime.now(UTC),
    )

    assert result["uncertain"] == 0
    assert result["details"][0]["closure_result"] == "not_stale"
    assert store.action["status"] == "executing"


@pytest.mark.asyncio
async def test_expired_recovery_lease_can_be_reclaimed_by_read_only_recovery() -> None:
    recovering = action()
    recovering.update(
        {
            "status": "recovering",
            "recovery_from_status": "executing",
            "recovery_token": "dead-worker-token",
            "recovery_lease_expires_at": (
                datetime.now(UTC) - timedelta(minutes=5)
            ).isoformat(),
            "execution_token": "old-execution-token",
            "execution_lease_expires_at": (
                datetime.now(UTC) - timedelta(minutes=30)
            ).isoformat(),
            "unexecuted_expires_at": (
                datetime.now(UTC) - timedelta(hours=2)
            ).isoformat(),
            "proposed_patch": {"title": "After"},
            "approved_patch": {"title": "After"},
        }
    )
    store = MemoryStore(recovering)

    class ConfirmedUnappliedAdapter(Adapter):
        async def recover(self, claimed: dict) -> dict:
            assert claimed["recovery_token"] != "dead-worker-token"
            return {"recovery_status": "confirmed_not_applied"}

    result = await service.reconcile_stale_actions(
        store,
        business_id="business-1",
        adapter=ConfirmedUnappliedAdapter(),
        now=datetime.now(UTC),
    )

    assert result["canceled"] == 1
    assert store.action["status"] == "canceled"
    assert store.action["recovery_status"] == "confirmed_not_applied"
