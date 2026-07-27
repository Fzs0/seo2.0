from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import strategy_runs


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(strategy_runs.router, prefix="/api/v1")
    return TestClient(app)


def test_openapi_exposes_strategy_run_contract():
    paths = _client().app.openapi()["paths"]
    assert "/api/v1/strategy-runs" in paths
    assert "/api/v1/strategy-runs/{run_id}" in paths
    assert "/api/v1/strategy-runs/{run_id}/events" in paths
    assert "/api/v1/strategy-runs/{run_id}/cancel" in paths
    assert "/api/v1/strategy-runs/{run_id}/retry" in paths
    assert "/api/v1/strategy-runs/{run_id}/start" in paths


def test_create_rejects_non_dry_run_mode():
    response = _client().post(
        "/api/v1/strategy-runs",
        json={
            "business_id": "exdivo",
            "scope": "all_sites",
            "mode": "execute",
            "requested_by": "codex",
            "idempotency_key": "daily-2026-07-27",
        },
    )
    assert response.status_code == 422


def test_create_returns_structured_request_id(monkeypatch):
    async def fake_create(_session, **values):
        assert values["mode"] == "dry_run"
        return {
            "ok": True,
            "run_id": "run-1",
            "status": "queued",
            "business_id": values["business_id"],
            "mode": values["mode"],
            "idempotency_key": values["idempotency_key"],
            "created_at": "2026-07-27T00:00:00+00:00",
            "next_action": "poll",
        }

    monkeypatch.setattr(strategy_runs, "create_strategy_run", fake_create)
    response = _client().post(
        "/api/v1/strategy-runs",
        headers={"X-Request-ID": "request-123"},
        json={
            "business_id": "exdivo",
            "scope": "all_sites",
            "mode": "dry_run",
            "requested_by": "codex",
            "idempotency_key": "daily-2026-07-27",
        },
    )
    assert response.status_code == 201
    assert response.json()["request_id"] == "request-123"
    assert response.json()["error"] is None
    assert response.json()["data"]["status"] == "queued"


def test_start_requires_idempotency_key_and_uses_stable_error_contract(monkeypatch):
    response = _client().post("/api/v1/strategy-runs/run-1/start")
    assert response.status_code == 422

    async def rejected(_session, *, run_id, idempotency_key):
        assert idempotency_key == "start-run-1"
        raise ValueError("concurrent state transition")

    monkeypatch.setattr(strategy_runs, "run_strategy_run", rejected)
    response = _client().post(
        "/api/v1/strategy-runs/run-1/start",
        headers={"Idempotency-Key": "start-run-1"},
    )
    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert response.json()["data"] is None
    assert response.json()["error"]["code"] == "STRATEGY_RUN_START_REJECTED"
    assert response.json()["error"]["retryable"] is False
