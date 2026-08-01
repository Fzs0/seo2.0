from __future__ import annotations

import base64

import pytest

from app.services import shopify_connection_service


class _MappingsResult:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self) -> "_MappingsResult":
        return self

    def first(self) -> dict | None:
        return self._row


class _Session:
    async def execute(self, statement: object, params: dict) -> _MappingsResult:
        assert "custom_connector_secrets" in str(statement)
        assert params == {"site_id": "site-id"}
        return _MappingsResult({"encrypted_value": b"encrypted-product-token"})


@pytest.mark.asyncio
async def test_oemapps_article_runtime_reuses_active_product_connector_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cipher:
        def decrypt(self, value: bytes) -> dict[str, str]:
            assert value == b"encrypted-product-token"
            return {"token": "shared-oemapps-token"}

    monkeypatch.setattr(shopify_connection_service, "_cipher", lambda: _Cipher())

    publisher = await shopify_connection_service.publisher_for_site_runtime(
        _Session(),  # type: ignore[arg-type]
        {
            "id": "site-id",
            "site_key": "trendprairie-main",
            "site_type": "main",
            "base_url": "https://trendprairie.com",
            "api_base_url": "https://openapi.oemapps.com",
            "api_config": {},
        },
        dry_run=True,
        require_active=True,
    )

    assert publisher._auth_headers() == {"token": "shared-oemapps-token"}


@pytest.mark.asyncio
async def test_oemapps_identity_wins_over_generic_custom_openapi_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cipher:
        def decrypt(self, value: bytes) -> dict[str, str]:
            assert value == b"encrypted-product-token"
            return {"token": "shared-oemapps-token"}

    monkeypatch.setattr(shopify_connection_service, "_cipher", lambda: _Cipher())
    publisher = await shopify_connection_service.publisher_for_site_runtime(
        _Session(),  # type: ignore[arg-type]
        {
            "id": "site-id",
            "site_key": "avinoti",
            "site_type": "main",
            "base_url": "https://avinoti.shop",
            "api_base_url": "https://openapi.oemapps.com",
            "api_config": {"connector_type": "custom_openapi"},
        },
        dry_run=True,
        require_active=True,
    )

    assert publisher._auth_headers() == {"token": "shared-oemapps-token"}


@pytest.mark.asyncio
async def test_wordpress_runtime_loads_local_credentials_without_persisting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def load_runtime_config(site: dict) -> dict[str, str]:
        assert site["site_key"] == "vapes2000"
        return {
            "username": "runtime-user",
            "applicationPassword": "runtime-password",
        }

    monkeypatch.setattr(
        shopify_connection_service,
        "load_wordpress_runtime_api_config",
        load_runtime_config,
        raising=False,
    )
    stored_site = {
        "id": "site-id",
        "site_key": "vapes2000",
        "site_type": "wp",
        "domain": "vapes2000.com",
        "base_url": "https://vapes2000.com",
        "api_config": {},
        "raw": {},
    }

    publisher = await shopify_connection_service.publisher_for_site_runtime(
        object(),  # type: ignore[arg-type]
        stored_site,
        dry_run=True,
        require_active=False,
    )

    expected = base64.b64encode(
        b"runtime-user:runtime-password"
    ).decode("ascii")
    assert publisher._auth_headers() == {
        "Authorization": f"Basic {expected}"
    }
    assert stored_site["api_config"] == {}


@pytest.mark.asyncio
async def test_custom_openapi_runtime_loads_local_credentials_without_persisting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def load_runtime_config(site: dict) -> dict[str, str]:
        assert site["site_key"] == "vapes1999"
        return {
            "connector_type": "custom_openapi",
            "articlesPath": "/posts",
            "openApiKey": "runtime-api-key",
        }

    monkeypatch.setattr(
        shopify_connection_service,
        "load_openapi_runtime_api_config",
        load_runtime_config,
        raising=False,
    )
    stored_site = {
        "id": "site-id",
        "site_key": "vapes1999",
        "site_type": "blog",
        "domain": "vapes1999.com",
        "base_url": "https://vapes1999.com",
        "api_base_url": "https://vapes1999.com/api/open/v1",
        "api_config": {
            "connector_type": "custom_openapi",
            "articlesPath": "/posts",
        },
        "raw": {},
    }

    publisher = await shopify_connection_service.publisher_for_site_runtime(
        object(),  # type: ignore[arg-type]
        stored_site,
        dry_run=True,
        require_active=False,
    )

    assert publisher._auth_headers() == {
        "X-API-Key": "runtime-api-key",
        "Host": "vapes1999.com",
    }
    assert "openApiKey" not in stored_site["api_config"]
