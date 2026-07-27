from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import router


def test_openapi_exposes_guarded_on_page_strategy_routes():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    paths = app.openapi()["paths"]

    assert "/api/v1/workflow/strategies/{task_id}/on-page/preview" in paths
    assert "/api/v1/workflow/strategies/{task_id}/on-page/execute" in paths


def test_on_page_execute_requires_snapshot_and_confirmation():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/workflow/strategies/task-1/on-page/execute",
        json={"patch": {"meta_title": "New title"}},
    )

    assert response.status_code == 422


def test_on_page_api_exposes_generation_provenance_fields():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    schema = app.openapi()["components"]["schemas"]["StrategyOnPagePreviewBody"]

    assert "generationMode" in schema["properties"]
    assert "generationProvider" in schema["properties"]
    assert "generationModel" in schema["properties"]
    assert "generationRunId" in schema["properties"]
    assert "generationMode" in schema["required"]
