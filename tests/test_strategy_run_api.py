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
    assert "/api/v1/strategy-runs/{run_id}/research-portfolio" in paths
    assert "/api/v1/strategy-runs/{run_id}/proposed-actions" in paths
    assert "/api/v1/strategy-runs/{run_id}/zero-action-review" in paths
    assert "/api/v1/strategy-runs/{run_id}/run-local-options" not in paths
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


def test_research_portfolio_accepts_auditable_keywordless_sources(monkeypatch):
    async def fake_capture(_session, **values):
        assert values["run_id"] == "run-1"
        assert values["requested_by"] == "codex"
        assert values["idempotency_key"] == "research-run-1"
        research = values["portfolio"][0]
        assert research["actions_considered"] == [
            "new_article",
            "update_article",
            "on_page_fix",
            "hold",
            "configuration_repair",
        ]
        assert research["evidence_sources"][0]["source_type"] == "site_api"
        return {
            "run_id": "run-1",
            "status": "ai_researching",
            "research_portfolio": values["portfolio"],
            "idempotency_replayed": False,
        }

    monkeypatch.setattr(strategy_runs, "capture_research_portfolio", fake_capture)
    response = _client().post(
        "/api/v1/strategy-runs/run-1/research-portfolio",
        headers={"Idempotency-Key": "research-run-1"},
        json={
            "requested_by": "codex",
            "evidence_snapshot_id": "snapshot-1",
            "portfolio": [
                {
                    "site_id": "11111111-1111-1111-1111-111111111111",
                    "evidence_snapshot_id": "snapshot-1",
                    "research_questions": ["What does the buyer need?"],
                    "actions_considered": [
                        "new_article",
                        "update_article",
                        "on_page_fix",
                        "hold",
                        "configuration_repair",
                    ],
                        "material_options": [
                            {
                                "option_id": "compare-materials",
                                "action": "new_article",
                                "target_identity": {
                                    "intent_key": "compare materials before buying",
                                    "topic_cluster": "material comparisons",
                                },
                                "user_intent": "Compare materials before buying.",
                                "evidence_refs": ["product-api-1"],
                                "outcome": "qualified",
                                "reason": "The product evidence supports this exact comparison.",
                            }
                        ],
                        "opportunity_exhaustion": {
                            "surfaces_checked": [
                                "existing_articles",
                                "new_topics",
                                "product_pages",
                                "category_pages",
                                "on_page",
                            ],
                            "evaluated_option_ids": ["compare-materials"],
                            "conclusion": "Existing pages and new topics were evaluated.",
                        },
                    "action_assessments": {
                        action: {
                            "outcome": "considered",
                            "reason": f"Assessed {action}.",
                            "evidence_refs": ["product-api-1"],
                        }
                        for action in [
                            "new_article",
                            "update_article",
                            "on_page_fix",
                            "hold",
                            "configuration_repair",
                        ]
                    },
                    "sources_attempted": ["site API"],
                    "evidence_sources": [
                        {
                            "source_type": "site_api",
                            "source_name": "Current products",
                            "captured_at": "2026-07-31T10:00:00+08:00",
                            "collection_status": "success",
                            "fact_scope": "product_fact",
                            "decision_use": "Validate current product fit.",
                        }
                    ],
                    "research_conclusion": "A distinct buying intent exists.",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(response.json()["data"]["research_portfolio"]) == 1


def test_proposed_actions_reject_candidate_and_keyword_decision_fields():
    response = _client().post(
        "/api/v1/strategy-runs/run-1/proposed-actions",
        headers={"Idempotency-Key": "proposal-run-1"},
        json={
            "actions": [
                {
                    "site_id": "11111111-1111-1111-1111-111111111111",
                    "action": "new_article",
                    "schedule_request": "execute_now",
                    "topic": "titanium travel cup guide",
                    "user_intent": "Compare travel cups.",
                    "decision_reason": "Current product and intent evidence agree.",
                    "evidence_refs": ["site-api-1", "serp-1"],
                    "candidate_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "keyword_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                }
            ]
        },
    )

    assert response.status_code == 422


def test_proposed_actions_accept_concrete_on_page_target_identity(monkeypatch):
    async def fake_submit(_session, **values):
        option = values["actions"][0]
        assert option["action"] == "on_page_fix"
        assert option["action_type"] == "product_seo"
        assert option["page_type"] == "product"
        assert option["target_asset_id"] == "42"
        assert (
            option["target_identity"]["remote_object_id"]
            == "gid://shopify/Product/42"
        )
        assert option["connector_type"] == "shopify"
        assert option["expected_fields"] == ["meta_title", "meta_description"]
        return {
            "run_id": "run-1",
            "status": "proposed_actions_submitted",
            "proposed_actions": values["actions"],
            "proposed_action_count": 1,
        }

    monkeypatch.setattr(strategy_runs, "submit_proposed_actions", fake_submit)
    response = _client().post(
        "/api/v1/strategy-runs/run-1/proposed-actions",
        headers={"Idempotency-Key": "on-page-product-1"},
        json={
            "actions": [
                {
                    "site_id": "11111111-1111-1111-1111-111111111111",
                    "action": "on_page_fix",
                    "action_type": "product_seo",
                    "page_type": "product",
                    "target_asset_id": "42",
                    "target_identity": {
                        "remote_object_id": "gid://shopify/Product/42",
                        "target_url": "https://example.com/products/example",
                    },
                    "connector_type": "shopify",
                    "user_intent": "Evaluate this exact product before purchase.",
                    "decision_reason": "The product metadata misses the intent.",
                    "evidence_refs": ["product-api-42"],
                    "schedule_request": "execute_now",
                    "expected_fields": ["meta_title", "meta_description"],
                }
            ]
        },
    )

    assert response.status_code == 200


def test_proposed_actions_accept_corrective_action_lineage(monkeypatch):
    parent_action_id = "39cc4c3a-8f5f-4add-b3ca-383f30f0cabf"

    async def fake_submit(_session, **values):
        option = values["actions"][0]
        assert option["action"] == "update_article"
        assert option["corrective_of_action_id"] == parent_action_id
        return {
            "run_id": "run-1",
            "status": "proposed_actions_submitted",
            "proposed_actions": values["actions"],
            "proposed_action_count": 1,
        }

    monkeypatch.setattr(strategy_runs, "submit_proposed_actions", fake_submit)
    response = _client().post(
        "/api/v1/strategy-runs/run-1/proposed-actions",
        headers={"Idempotency-Key": "corrective-article-1"},
        json={
            "actions": [
                {
                    "site_id": "11111111-1111-1111-1111-111111111111",
                    "action": "update_article",
                    "target_asset_id": "post-1",
                    "target_identity": {
                        "target_url": "https://example.com/blog/broken-article",
                        "remote_object_id": "10",
                        "local_object_id": "post-1",
                    },
                    "corrective_of_action_id": parent_action_id,
                    "user_intent": "Read the complete guide without lost paragraphs.",
                    "decision_reason": "Repair a confirmed partial write.",
                    "evidence_refs": ["readback-mismatch-1"],
                    "schedule_request": "execute_now",
                    "priority": "P1",
                }
            ]
        },
    )

    assert response.status_code == 200


def test_retired_run_local_options_is_not_a_runtime_entry():
    response = _client().post(
        "/api/v1/strategy-runs/run-1/run-local-options",
        json={"options": []},
    )

    assert response.status_code == 404
