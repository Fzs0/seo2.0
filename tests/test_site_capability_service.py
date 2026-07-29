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
    assert item["supported_actions"]["delete_content"] == "forbidden"
    assert item["side_effects"]["product_seo"]["variant_recreation_possible"] is True
    assert item["side_effects"]["category_seo"]["membership_reset_possible"] is True
    assert item["connectors"]["images"]["status"] == "available"
    assert item["connectors"]["images"]["upload"] is True
    assert {"images", "image_alts", "cover_image"} <= set(item["supported_fields"]["articles"])
    assert "api_config" not in item
    assert "token" not in str(item)


def test_custom_blog_does_not_claim_unimplemented_image_upload() -> None:
    item = build_site_capability(
        _site(
            site_type="blog",
            is_main=False,
            api_base_url="https://blog-api.example.com/api/open/v1",
            api_config={
                "connector_type": "custom_openapi",
                "openApiKey": "configured",
                "imageUploadPath": "/media/upload",
            },
        ),
        connectors=[],
        generated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert item["connectors"]["images"]["status"] == "unavailable"
    assert item["connectors"]["images"]["upload"] is False
    assert "images" not in item["supported_fields"]["articles"]
    assert "image_alts" not in item["supported_fields"]["articles"]


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
