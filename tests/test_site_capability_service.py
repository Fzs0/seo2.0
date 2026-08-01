from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.services.site_capability_service import (
    build_site_capability,
    get_business_site_capabilities,
)


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)


class FakeSession:
    def __init__(self, results: list[list[dict[str, Any]]]) -> None:
        self.results = list(results)
        self.calls: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(str(statement))
        return _Result(self.results.pop(0))


def _site(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "site_key": "example",
        "name": "Example",
        "site_type": "main",
        "domain": "WWW.Example.com.",
        "base_url": "https://example.com/",
        "api_base_url": "https://admin.example.net/api",
        "market": "US",
        "language_code": "en",
        "content_role": "commerce",
        "content_scope": "vaping",
        "business_id": "example-business",
        "strategy_enabled": True,
        "is_main": True,
        "status": "active",
        "api_config": {},
        "raw": {},
        "updated_at": datetime(2026, 7, 27, tzinfo=timezone.utc),
        **overrides,
    }


def test_build_oemapps_capability_is_safe_and_machine_readable() -> None:
    item = build_site_capability(
        _site(api_base_url="https://openapi.oemapps.com"),
        connectors=[
            {
                "id": "connector",
                "status": "active",
                "adapter": "oemapps",
                "secret_names": ["token"],
                "verified_at": datetime(2026, 7, 26, tzinfo=timezone.utc),
                "last_success_at": datetime(2026, 7, 27, tzinfo=timezone.utc),
                "last_error": None,
            }
        ],
        generated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    assert len(item["capability_snapshot_hash"]) == 64

    assert item["canonical_hosts"] == ["example.com"]
    assert item["connectors"]["products"]["status"] == "available"
    assert item["supported_actions"]["product_seo"] == "approval_required"
    assert item["action_adapters"]["product_seo"] == {
        "adapter_id": "oemapps_on_page",
        "adapter_version": "1",
        "connector_type": "oemapps",
        "read": True,
        "write": True,
        "readback": True,
    }
    assert item["supported_actions"]["delete_content"] == "forbidden"
    assert item["side_effects"]["product_seo"]["variant_recreation_possible"] is True
    assert item["side_effects"]["category_seo"]["membership_reset_possible"] is True
    assert "price" in item["protected_fields"]["product_seo"]
    assert "category_membership" in item["protected_fields"]["category_seo"]
    assert item["connectors"]["images"]["status"] == "available"
    assert item["connectors"]["images"]["upload"] is True
    assert {"images", "image_alts", "cover_image"} <= set(item["supported_fields"]["articles"])
    assert "api_config" not in item
    assert "token" not in str(item)


def test_shopify_product_seo_capability_requires_active_product_scopes() -> None:
    item = build_site_capability(
        _site(
            site_type="shopify",
            api_config={"connector_type": "shopify"},
        ),
        connectors=[
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "site_id": "11111111-1111-1111-1111-111111111111",
                "status": "active",
                "adapter": "shopify",
                "scopes": ["read_products", "write_products"],
                "last_success_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
                "last_error": None,
            }
        ],
        generated_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    assert item["connectors"]["products"]["status"] == "available"
    assert item["supported_actions"]["product_seo"] == "approval_required"
    assert item["supported_actions"].get("category_seo") is None
    assert item["action_adapters"]["product_seo"]["adapter_id"] == (
        "shopify_product_seo"
    )
    assert item["action_adapters"]["product_seo"]["readback"] is True
    assert "inventory" in item["protected_fields"]["product_seo"]


def test_shopify_write_products_implies_read_products_for_product_seo() -> None:
    item = build_site_capability(
        _site(
            site_type="shopify",
            api_config={"connector_type": "shopify"},
        ),
        connectors=[
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "site_id": "11111111-1111-1111-1111-111111111111",
                "status": "active",
                "adapter": "shopify",
                "scopes": ["write_products"],
                "last_success_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
                "last_error": None,
            }
        ],
        generated_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    product_health = item["connectors"]["products"]
    assert product_health["status"] == "available"
    assert product_health["read"] is True
    assert product_health["write"] is True
    assert product_health["error_code"] is None
    assert product_health["error_summary"] is None
    assert product_health["unlock_condition"] is None
    assert product_health["granted_scopes"] == ["write_products"]
    assert product_health["effective_scopes"] == [
        "read_products",
        "write_products",
    ]
    assert item["supported_actions"]["product_seo"] == "approval_required"
    assert item["action_adapters"]["product_seo"] == {
        "adapter_id": "shopify_product_seo",
        "adapter_version": "1",
        "connector_type": "shopify",
        "read": True,
        "write": True,
        "readback": True,
    }


def test_custom_content_blog_declares_same_business_oemapps_media_route() -> None:
    item = build_site_capability(
        _site(
            site_type="blog",
            is_main=False,
            api_base_url="https://blog-api.example.com/api/open/v1",
            api_config={
                "connector_type": "custom_openapi",
                "openApiKey": "configured",
            },
        ),
        connectors=[],
        generated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        media_host_site=_site(
            id="22222222-2222-2222-2222-222222222222",
            site_key="example-main",
            site_type="main",
            is_main=True,
            api_base_url="https://openapi.oemapps.com",
            api_config={},
        ),
        media_host_connectors=[
            {
                "status": "active",
                "adapter": "oemapps",
                "secret_names": ["token"],
            }
        ],
    )

    assert item["connectors"]["images"]["status"] == "available"
    assert item["connectors"]["images"]["upload"] is True
    assert item["connectors"]["images"]["ingest"] is True
    assert item["connectors"]["images"]["transport"] == (
        "business_oemapps_upload_then_article_publish"
    )
    assert item["connectors"]["images"]["media_host_site_id"] == (
        "22222222-2222-2222-2222-222222222222"
    )
    assert item["connectors"]["images"]["media_host_business_id"] == (
        "example-business"
    )
    assert {
        "images",
        "image_alts",
        "cover_image",
    } <= set(item["supported_fields"]["articles"])


def test_wordpress_capability_declares_media_upload_and_cover_fields() -> None:
    item = build_site_capability(
        _site(
            site_type="wp",
            is_main=False,
            api_base_url=None,
            api_config={
                "username": "admin",
                "applicationPassword": "configured",
            },
        ),
        connectors=[],
        generated_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )

    assert item["connectors"]["images"]["status"] == "available"
    assert item["connectors"]["images"]["upload"] is True
    assert {
        "images",
        "image_alts",
        "cover_image",
    } <= set(item["supported_fields"]["articles"])
    assert item["supported_actions"]["new_article"] == "approval_required"
    assert item["supported_actions"]["update_article"] == "approval_required"


def test_missing_secret_is_misconfigured_and_never_writable() -> None:
    item = build_site_capability(
        _site(),
        connectors=[
            {
                "id": "connector",
                "status": "active",
                "adapter": "oemapps",
                "secret_names": [],
                "verified_at": None,
                "last_success_at": None,
                "last_error": None,
            }
        ],
        generated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert item["connectors"]["products"]["status"] == "misconfigured"
    assert item["connectors"]["products"]["write"] is False
    assert "connector_token_missing" in {
        issue["code"] for issue in item["configuration_issues"]
    }


def test_connector_error_summary_redacts_credentials() -> None:
    item = build_site_capability(
        _site(),
        connectors=[
            {
                "id": "connector",
                "status": "active",
                "adapter": "oemapps",
                "secret_names": ["token"],
                "last_success_at": None,
                "last_error": "request failed authorization: Bearer secret-value",
            }
        ],
        generated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    summary = item["connectors"]["products"]["error_summary"]
    assert "[REDACTED]" in summary
    assert "secret-value" not in summary


@pytest.mark.asyncio
async def test_business_query_returns_every_site_without_remote_probe() -> None:
    sites = [_site(), _site(id="22222222-2222-2222-2222-222222222222", is_main=False, site_type="blog")]
    session = FakeSession([sites, []])

    result = await get_business_site_capabilities(
        session, "example-business", now=datetime(2026, 7, 27, tzinfo=timezone.utc)
    )

    assert len(result["sites"]) == 2
    assert result["discovered_site_count"] == 2
    assert len(session.calls) == 2
    assert all("http" not in query.casefold() for query in session.calls)
