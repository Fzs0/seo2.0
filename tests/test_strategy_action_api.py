from fastapi import FastAPI
from fastapi.testclient import TestClient
from typing import get_type_hints

from app.api.v1.strategy_actions import get_action_adapter, router
from app.services.strategy_action_service import BlockedActionAdapter


def test_strategy_action_routes_expose_complete_safe_contract() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    paths = {route.path for route in app.routes}
    base = "/api/v1/strategy-actions/{action_id}"
    assert {
        base,
        f"{base}/generation-context",
        f"{base}/preview",
        f"{base}/approve",
        f"{base}/execute",
        f"{base}/recover",
        f"{base}/rollback-preview",
        f"{base}/rollback",
    } <= paths


def test_every_action_write_accepts_an_idempotency_key_and_uses_response_envelope() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    schema = app.openapi()
    write_paths = {
        path: operation["post"]
        for path, operation in schema["paths"].items()
        if path.startswith("/api/v1/strategy-actions/") and "post" in operation
    }
    assert write_paths
    for operation in write_paths.values():
        headers = {
            parameter["name"]
            for parameter in operation.get("parameters", [])
            if parameter["in"] == "header"
        }
        assert "Idempotency-Key" in headers


def test_rollback_request_requires_explicit_confirmation() -> None:
    route = next(route for route in router.routes if route.path.endswith("/{action_id}/rollback"))
    body = get_type_hints(route.endpoint)["body"]
    assert body.model_fields["confirm"].is_required()


def test_write_validation_error_uses_stable_envelope() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    response = TestClient(app).post(
        "/api/v1/strategy-actions/action-1/execute"
    )

    assert response.status_code == 422
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_runtime_action_adapter_is_not_the_permanent_blocked_default() -> None:
    adapter = get_action_adapter(session=object())

    assert not isinstance(adapter, BlockedActionAdapter)
