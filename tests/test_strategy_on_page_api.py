from fastapi import FastAPI

from app.api.v1.endpoints import router


def test_legacy_on_page_write_routes_are_retired():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    paths = app.openapi()["paths"]

    assert "/api/v1/workflow/strategies/{task_id}/on-page/preview" not in paths
    assert "/api/v1/workflow/strategies/{task_id}/on-page/execute" not in paths
