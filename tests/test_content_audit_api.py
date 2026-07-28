from fastapi.testclient import TestClient

import app.api.v1.endpoints as endpoints
from app.core.database import get_db
from app.main import app


class FakeSession:
    pass


def _client() -> TestClient:
    app.dependency_overrides[get_db] = lambda: FakeSession()
    return TestClient(app)


def test_content_audit_scan_returns_pollable_batch_without_waiting(monkeypatch):
    async def fake_start(session, **kwargs):
        assert kwargs["business_id"] == "exdivo"
        return {
            "batch_id": "batch-1",
            "business_id": "exdivo",
            "status": "queued",
            "reused": False,
            "poll_url": "/api/v1/workflow/content-audit/scans/batch-1",
        }

    monkeypatch.setattr(endpoints, "start_content_audit", fake_start)
    response = _client().post(
        "/api/v1/workflow/content-audit/scan",
        json={"businessId": "exdivo"},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["batch_id"] == "batch-1"
    assert response.json()["status"] == "queued"


def test_completed_content_audit_batch_can_be_polled(monkeypatch):
    async def fake_get(session, batch_id):
        assert batch_id == "batch-1"
        return {
            "batch_id": batch_id,
            "business_id": "exdivo",
            "status": "completed",
            "reused": False,
            "poll_url": f"/api/v1/workflow/content-audit/scans/{batch_id}",
            "result": {"summary": {"articles": 62}},
        }

    monkeypatch.setattr(endpoints, "get_content_audit_batch", fake_get)
    response = _client().get("/api/v1/workflow/content-audit/scans/batch-1")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["result"]["summary"]["articles"] == 62
