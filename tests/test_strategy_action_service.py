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
        "business_id": "business-1",
        "site_id": "site-1",
        "action_type": "product_seo",
        "target_url": "https://example.com/p/1",
        "status": "planned",
        "idempotency_key": "run-1:action-1",
        "risk_level": "medium",
        "approval_requirement": "approval_required",
        "legacy_capability_compatibility": True,
    }


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
    store, adapter = MemoryStore(action()), Adapter()
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
    assert store.observations[0]["checkpoints_days"] == [7, 14, 28, 56]
    assert store.observations[0]["action_id"] == "action-1"


@pytest.mark.asyncio
async def test_capability_change_invalidates_approval_before_remote_write() -> None:
    store, adapter = MemoryStore(action()), Adapter()
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
async def test_missing_capability_snapshot_is_blocked_by_default() -> None:
    item = action()
    item.pop("legacy_capability_compatibility")
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
    assert recovered["status"] == "approved"
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
