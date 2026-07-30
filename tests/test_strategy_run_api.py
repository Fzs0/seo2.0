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
    assert "/api/v1/strategy-runs/{run_id}/run-local-options" in paths
    assert "/api/v1/strategy-runs/{run_id}/cancel" in paths
    assert "/api/v1/strategy-runs/{run_id}/retry" in paths
    assert "/api/v1/strategy-runs/{run_id}/start" in paths
    assert "/api/v1/strategy-runs/{run_id}/reconcile" in paths


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


def test_run_local_options_accept_keywordless_research_decision(monkeypatch):
    async def fake_submit(_session, **values):
        assert values["run_id"] == "run-1"
        assert values["requested_by"] == "codex"
        assert values["idempotency_key"] == "research-run-1"
        option = values["options"][0]
        assert option["action"] == "new_article"
        assert option["candidate_id"] is None
        assert option["keyword_id"] is None
        return {
            "run_id": "run-1",
            "status": "queued",
            "run_local_options": values["options"],
            "run_local_option_count": 1,
            "idempotency_replayed": False,
        }

    monkeypatch.setattr(
        strategy_runs, "submit_strategy_run_local_options", fake_submit
    )
    response = _client().post(
        "/api/v1/strategy-runs/run-1/run-local-options",
        headers={"Idempotency-Key": "research-run-1"},
        json={
            "requested_by": "codex",
            "options": [
                {
                    "site_id": "11111111-1111-1111-1111-111111111111",
                    "action": "new_article",
                    "schedule_class": "execute_now",
                    "topic": "how to choose a titanium travel cup",
                    "title": "Titanium Travel Cup Buying Guide",
                    "reason": "Current product facts and live SERP intent support it.",
                    "user_intent": "Compare materials before buying a travel cup.",
                    "evidence": {
                        "product_api": ["product-123"],
                        "live_serp": ["serp-observation-1"],
                    },
                    "hypothesis": "A comparison guide can earn non-brand discovery.",
                    "success_metrics": ["GSC impressions", "organic sessions"],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["run_local_option_count"] == 1


def test_run_local_options_reject_update_without_target_identity():
    response = _client().post(
        "/api/v1/strategy-runs/run-1/run-local-options",
        headers={"Idempotency-Key": "research-run-1"},
        json={
            "options": [
                {
                    "site_id": "11111111-1111-1111-1111-111111111111",
                    "action": "update_article",
                    "schedule_class": "execute_now",
                    "topic": "existing guide refresh",
                    "title": "Existing Guide Refresh",
                    "reason": "The current article is stale.",
                    "user_intent": "Find current guidance.",
                    "evidence": {"gsc": ["page-impressions"]},
                }
            ]
        },
    )

    assert response.status_code == 422


def test_run_local_options_accept_concrete_on_page_target_identity(monkeypatch):
    async def fake_submit(_session, **values):
        option = values["options"][0]
        assert option["action"] == "on_page_fix"
        assert option["action_type"] == "product_seo"
        assert option["page_type"] == "product"
        assert option["target_asset_id"] == "42"
        assert option["remote_object_id"] == "gid://shopify/Product/42"
        assert option["connector_type"] == "shopify"
        assert option["expected_fields"] == ["meta_title", "meta_description"]
        return {
            "run_id": "run-1",
            "status": "queued",
            "run_local_options": values["options"],
            "run_local_option_count": 1,
        }

    monkeypatch.setattr(
        strategy_runs, "submit_strategy_run_local_options", fake_submit
    )
    response = _client().post(
        "/api/v1/strategy-runs/run-1/run-local-options",
        headers={"Idempotency-Key": "on-page-product-1"},
        json={
            "options": [
                {
                    "site_id": "11111111-1111-1111-1111-111111111111",
                    "action": "on_page_fix",
                    "action_type": "product_seo",
                    "page_type": "product",
                    "target_asset_id": "42",
                    "remote_object_id": "gid://shopify/Product/42",
                    "target_url": "https://example.com/products/example",
                    "connector_type": "shopify",
                    "reason": "The product metadata does not own its commercial intent.",
                    "user_intent": "Evaluate this exact product before purchase.",
                    "evidence": {"product_api": {"id": "gid://shopify/Product/42"}},
                    "schedule_class": "execute_now",
                    "expected_fields": ["meta_title", "meta_description"],
                }
            ]
        },
    )

    assert response.status_code == 200


def test_run_local_options_reject_mismatched_on_page_type():
    response = _client().post(
        "/api/v1/strategy-runs/run-1/run-local-options",
        headers={"Idempotency-Key": "on-page-mismatch-1"},
        json={
            "options": [
                {
                    "site_id": "11111111-1111-1111-1111-111111111111",
                    "action": "on_page_fix",
                    "action_type": "category_seo",
                    "page_type": "product",
                    "target_asset_id": "42",
                    "remote_object_id": "collection-42",
                    "target_url": "https://example.com/products/example",
                    "reason": "Metadata is missing.",
                    "user_intent": "Browse a product category.",
                    "evidence": {"api": {"id": "collection-42"}},
                    "schedule_class": "execute_now",
                    "expected_fields": ["meta_title"],
                }
            ]
        },
    )

    assert response.status_code == 422
