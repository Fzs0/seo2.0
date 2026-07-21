from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.connectors import router
from tests.test_custom_data_connector import EXDIVO_RESPONSE, exdivo_config


def test_preview_endpoint_maps_sample_without_exposing_secret_fields() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/connectors/preview",
        json={
            "config": exdivo_config().model_dump(mode="json"),
            "response": EXDIVO_RESPONSE,
            "limit": 10,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mapped_items"] == 1
    assert payload["items"][0]["seo_audit"]["images_missing_alt"] == 1
    assert "raw" not in payload["items"][0]
    assert "secrets" not in str(payload).lower()


def test_openapi_exposes_connector_management_routes() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    paths = app.openapi()["paths"]

    assert "/api/v1/connectors/preview" in paths
    assert "/api/v1/connectors/test-request" in paths
    assert "/api/v1/connectors/oemapps" in paths
    assert "/api/v1/connectors/{connector_id}/sync-products" in paths
    assert "/api/v1/connectors/{connector_id}/products/{product_id}/seo-update/preview" in paths
    assert "/api/v1/connectors/{connector_id}/products/{product_id}/seo-update/execute" in paths


def test_oemapps_execute_requires_seo_patch_and_explicit_confirmation_field() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/connectors/connector-id/products/13576711/seo-update/execute",
        json={
            "expected_snapshot_hash": "a" * 64,
            "confirm_variant_recreation": True,
        },
    )

    assert response.status_code == 422
