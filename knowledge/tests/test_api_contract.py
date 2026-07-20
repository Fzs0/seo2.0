from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from knowledge.backend.app import api
from knowledge.backend.app.database import get_knowledge_session
from knowledge.backend.app.main import app


async def dummy_session() -> AsyncIterator[object]:
    yield object()


def test_required_route_contract_is_present() -> None:
    route_methods = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    assert {
        ("GET", "/api/v1/knowledge/health"),
        ("GET", "/api/v1/knowledge/overview"),
        ("GET", "/api/v1/knowledge/sources"),
        ("GET", "/api/v1/knowledge/documents"),
        ("GET", "/api/v1/knowledge/claims"),
        ("POST", "/api/v1/knowledge/signals/import"),
        ("GET", "/api/v1/knowledge/signals"),
        ("GET", "/api/v1/knowledge/signals/overview"),
        ("POST", "/api/v1/knowledge/signals/crawl/preview"),
        ("POST", "/api/v1/knowledge/signals/crawl/jobs"),
        ("GET", "/api/v1/knowledge/signals/crawl/jobs"),
        ("POST", "/api/v1/knowledge/signals/crawl/jobs/{job_id}/run"),
        ("GET", "/api/v1/knowledge/signals/crawl/runs/{run_id}"),
        ("POST", "/api/v1/knowledge/documents/import"),
        ("POST", "/api/v1/knowledge/documents/import-url"),
        ("POST", "/api/v1/knowledge/batch/preview"),
        ("POST", "/api/v1/knowledge/batch/runs"),
        ("GET", "/api/v1/knowledge/batch/runs/{run_id}"),
        ("GET", "/api/v1/knowledge/batch/runs/{run_id}/items"),
        ("POST", "/api/v1/knowledge/batch/runs/{run_id}/cancel"),
        ("POST", "/api/v1/knowledge/quality/runs"),
        ("GET", "/api/v1/knowledge/quality/runs/latest"),
        ("GET", "/api/v1/knowledge/quality/runs/{run_id}"),
        ("GET", "/api/v1/knowledge/quality/runs/{run_id}/items"),
        ("POST", "/api/v1/knowledge/quality/runs/{run_id}/apply"),
        ("POST", "/api/v1/knowledge/quality/runs/{run_id}/cancel"),
        ("POST", "/api/v1/knowledge/claims/{claim_id}/quality/restore"),
        ("POST", "/api/v1/knowledge/claims/{claim_id}/review"),
        ("POST", "/api/v1/knowledge/retrieve"),
    }.issubset(route_methods)


def test_invalid_import_url_uses_400_semantics() -> None:
    app.dependency_overrides[get_knowledge_session] = dummy_session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge/documents/import",
                json={
                    "source_name": "Example",
                    "canonical_url": "file:///etc/passwd",
                    "title": "Title",
                    "raw_content": "Licensed body",
                    "rights_confirmed": True,
                },
            )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_unconfirmed_rights_use_400_semantics() -> None:
    app.dependency_overrides[get_knowledge_session] = dummy_session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge/documents/import",
                json={
                    "source_name": "Example",
                    "title": "Title",
                    "raw_content": "Body",
                    "rights_confirmed": False,
                },
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "rights_confirmed must be true"
    finally:
        app.dependency_overrides.clear()


def test_url_import_blocks_local_targets_with_400_semantics() -> None:
    app.dependency_overrides[get_knowledge_session] = dummy_session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge/documents/import-url",
                json={
                    "url": "http://127.0.0.1/private",
                    "rights_confirmed": True,
                },
            )
        assert response.status_code == 400
        assert "不能抓取" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_signal_import_requires_confirmed_rights() -> None:
    app.dependency_overrides[get_knowledge_session] = dummy_session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge/signals/import",
                json={
                    "source_name": "Reddit export",
                    "platform": "reddit",
                    "signals": [{"content": "A user need"}],
                    "rights_confirmed": False,
                },
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "rights_confirmed must be true"
    finally:
        app.dependency_overrides.clear()


def test_batch_preview_requires_confirmed_rights_before_discovery() -> None:
    app.dependency_overrides[get_knowledge_session] = dummy_session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge/batch/preview",
                json={
                    "seed_url": "https://blog.example.test/",
                    "source_name": "Example blog",
                    "rights_confirmed": False,
                },
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "rights_confirmed must be true"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_requires_quality_gate_schema_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def scalar_one(self):
            return True

    class Connection:
        statement = ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            self.statement = str(statement)
            return Result()

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def connect(self):
            return self.connection

    engine = Engine()
    monkeypatch.setattr(api, "get_engine", lambda: engine)

    result = await api.health()

    assert result == {
        "status": "ok",
        "database": "reachable",
        "schema": "initialized",
    }
    assert "knowledge.ingestion_sources" in engine.connection.statement
    assert "knowledge.sync_runs" in engine.connection.statement
    assert "knowledge.crawl_items" in engine.connection.statement
    assert "knowledge.claim_quality_reviews" in engine.connection.statement
    assert "knowledge.quality_review_runs" in engine.connection.statement
    assert "knowledge.quality_review_items" in engine.connection.statement
    assert "002_batch_ingestion" in engine.connection.statement
    assert "003_claim_quality_gate" in engine.connection.statement
    assert "005_signal_crawl_jobs" in engine.connection.statement
