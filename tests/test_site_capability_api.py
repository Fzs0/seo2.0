from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.site_capabilities import router
from app.core.database import get_db


class FakeSession:
    async def execute(self, statement, params):
        class Mappings:
            def all(self):
                return []

        class Result:
            def mappings(self):
                return Mappings()

        return Result()


def test_business_capabilities_endpoint_is_read_only() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: FakeSession()
    client = TestClient(app)

    response = client.get("/api/v1/businesses/exdivo/sites/capabilities")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["error"] is None
    assert response.json()["request_id"]
    assert response.json()["data"]["business_id"] == "exdivo"
    assert response.json()["data"]["sites"] == []


def test_missing_site_uses_stable_error_envelope() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: FakeSession()
    client = TestClient(app)

    response = client.get("/api/v1/sites/missing/capabilities")

    assert response.status_code == 404
    assert response.json()["ok"] is False
    assert response.json()["data"] is None
    assert response.json()["error"] == {
        "code": "SITE_NOT_FOUND",
        "message": "Site capability record was not found.",
        "retryable": False,
        "details": {"site_id": "missing"},
    }
