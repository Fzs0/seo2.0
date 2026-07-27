from datetime import datetime, timezone

import asyncio

import pytest

from app.services.strategy_hold_service import (
    acquire_hold_state_lock,
    evolve_hold_state,
    refresh_hold_evidence,
)


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _holds():
    return [
        {"site_id": "main", "action": "hold", "reason": "insufficient evidence"},
        {"site_id": "blog", "action": "hold", "reason": "missing facts"},
    ]


def test_second_all_site_hold_expands_evidence_without_bypassing_safety() -> None:
    result = evolve_hold_state(
        _holds(),
        previous_site_counts={"main": 1, "blog": 1},
        previous_business_count=1,
        now=NOW,
    )
    assert result["business_consecutive_hold_count"] == 2
    assert result["expand_evidence"] is True
    assert result["evidence_sources"] == [
        "gsc", "ga4", "live_serp", "products", "collections", "articles", "on_page",
        "publishing_frequency", "content_gap",
    ]
    assert result["safety_gates_may_be_bypassed"] is False
    assert result["anomalies"] == []
    for decision in result["decisions"]:
        assert decision["consecutive_hold_count"] == 2
        assert decision["block_reason"]
        assert decision["unlock_condition"]
        assert decision["responsibility_type"]
        assert decision["review_by"] == "2026-08-03"
        assert decision["alternative_evidence"]


def test_third_all_site_hold_creates_strategy_stagnation() -> None:
    result = evolve_hold_state(
        _holds(),
        previous_site_counts={"main": 2, "blog": 2},
        previous_business_count=2,
        now=NOW,
    )
    assert result["business_consecutive_hold_count"] == 3
    assert result["anomalies"] == [{
        "kind": "strategy_stagnation",
        "severity": "P2",
        "consecutive_hold_count": 3,
    }]
    repeated = evolve_hold_state(
        _holds(),
        previous_site_counts={"main": 3, "blog": 3},
        previous_business_count=3,
        now=NOW,
    )
    assert repeated["anomalies"] == []


def test_any_real_action_resets_business_and_site_hold_counter() -> None:
    decisions = _holds()
    decisions[1] = {"site_id": "blog", "action": "on_page_fix"}
    result = evolve_hold_state(
        decisions,
        previous_site_counts={"main": 2, "blog": 4},
        previous_business_count=2,
        now=NOW,
    )
    assert result["business_consecutive_hold_count"] == 0
    assert result["site_consecutive_hold_counts"] == {"main": 3, "blog": 0}
    assert result["expand_evidence"] is False
    assert result["anomalies"] == []


@pytest.mark.asyncio
async def test_second_hold_actually_refreshes_every_evidence_boundary_and_records_failures() -> None:
    called = []

    async def ok(*, site_id):
        called.append(site_id)
        return {"snapshot_id": f"snap-{site_id}", "status": "fresh"}

    async def failed(*, site_id):
        raise RuntimeError(f"SERP unavailable for {site_id}")

    collectors = {
        source: ok
        for source in (
            "gsc", "ga4", "products", "collections", "articles", "on_page",
            "publishing_frequency", "content_gap",
        )
    }
    collectors["live_serp"] = failed
    result = await refresh_hold_evidence(
        site_id="main", collectors=collectors, now=NOW
    )
    assert set(result["sources"]) == set(collectors)
    assert result["sources"]["gsc"]["snapshot_id"] == "snap-main"
    assert result["sources"]["gsc"]["refreshed_at"] == NOW.isoformat()
    assert result["sources"]["live_serp"]["status"] == "failed"
    assert "SERP unavailable" in result["sources"]["live_serp"]["error"]
    assert result["degraded"] is True
    assert len(called) == 8


@pytest.mark.asyncio
async def test_hold_counter_uses_transaction_advisory_lock() -> None:
    calls = []

    class Session:
        async def execute(self, statement, params):
            calls.append((str(statement), params))

    await asyncio.gather(
        acquire_hold_state_lock(Session(), business_id="business"),
        acquire_hold_state_lock(Session(), business_id="business"),
    )
    assert len(calls) == 2
    assert all("pg_advisory_xact_lock" in sql for sql, _ in calls)
    assert calls[0][1] == calls[1][1] == {"key": "strategy-hold:business"}
