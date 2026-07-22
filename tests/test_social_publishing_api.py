from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.social import router
from app.services.social_publishing_service import validate_platform
from app.services.social_platform_registry import validate_binding_payload, validate_package_payload


def test_social_openapi_exposes_reviewed_queue_foundation() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    paths = app.openapi()["paths"]
    assert "/api/v1/social/platforms" in paths
    assert "/api/v1/social/connections" in paths
    assert "/api/v1/social/connections/{connection_id}/test" in paths
    assert "/api/v1/social/decisions" in paths
    assert "/api/v1/social/packages" in paths
    assert "/api/v1/social/packages/{package_id}/versions" in paths
    assert "/api/v1/social/packages/{package_id}/submit-review" in paths
    assert "/api/v1/social/packages/{package_id}/review" in paths
    assert "/api/v1/social/bindings" in paths
    assert "/api/v1/social/bindings/{binding_id}/activate" in paths
    assert "/api/v1/social/publish-jobs" in paths
    assert "/api/v1/social/publish-jobs/{job_id}/cancel" in paths
    assert "/api/v1/social/publish-jobs/{job_id}/prepare" in paths
    assert "/api/v1/social/publish-jobs/{job_id}/confirm" in paths
    assert not any(path.endswith("/advance") for path in paths)


def test_decision_requires_targets_to_be_requested() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    response = TestClient(app).post(
        "/api/v1/social/decisions",
        json={
            "business_id": "exdivo", "topic": "new product discussion", "language_code": "en",
            "requested_platforms": ["reddit"],
            "decision": {
                "allow_publish": True, "target_platforms": ["x"],
                "recommended_content_type": "discussion_post",
                "recommended_topic": "new product discussion",
                "potential_score": 60, "risk_score": 70,
            },
        },
    )
    assert response.status_code == 422


def test_social_platforms_are_normalized_and_unknown_platforms_rejected() -> None:
    assert validate_platform(" Reddit ") == "reddit"
    with pytest.raises(ValueError, match="unsupported social platform"):
        validate_platform("myspace")


def test_x_and_reddit_have_distinct_delivery_adapters() -> None:
    validate_binding_payload({
        "platform": "x", "delivery_channel": "hubstudio_browser", "publish_adapter": "x_web_intent",
        "default_publish_url": "https://twitter.com/intent/tweet", "container_code": "1234",
        "enabled": False,
    })
    validate_binding_payload({
        "platform": "reddit", "delivery_channel": "hubstudio_browser",
        "publish_adapter": "reddit_browser_review",
        "default_publish_url": "https://www.reddit.com/submit", "container_code": "2669",
        "enabled": False,
    })
    with pytest.raises(ValueError, match="publish_adapter"):
        validate_binding_payload({
            "platform": "x", "delivery_channel": "hubstudio_browser",
            "publish_adapter": "x_api_v2", "default_publish_url": "https://twitter.com/intent/tweet",
            "container_code": "1234", "enabled": False,
        })


def test_platform_content_shape_is_validated() -> None:
    validate_package_payload("x", "short_post", {"title": "", "body": "hello", "metadata": {}})
    validate_package_payload(
        "reddit", "discussion_post",
        {"title": "Question", "body": "Discussion", "metadata": {"subreddit": "Vaping"}},
    )
    with pytest.raises(ValueError, match="280"):
        validate_package_payload("x", "short_post", {"title": "", "body": "x" * 281})
    with pytest.raises(ValueError, match="subreddit"):
        validate_package_payload("reddit", "discussion_post", {"title": "Question", "body": "Body"})
    with pytest.raises(ValueError, match="body content"):
        validate_package_payload(
            "reddit", "discussion_post",
            {"title": "Question", "body": "", "metadata": {"subreddit": "Vaping"}},
        )


def test_binding_rejects_non_https_publish_url_before_database_access() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    response = TestClient(app).post(
        "/api/v1/social/bindings",
        json={
            "business_id": "exdivo", "platform": "reddit", "display_name": "Reddit",
            "connection_id": "00000000-0000-0000-0000-000000000000",
            "container_code": "2669", "binding_name": "reddit-main",
            "default_publish_url": "http://reddit.example/submit", "publish_adapter": "reddit_browser",
        },
    )
    assert response.status_code == 422
