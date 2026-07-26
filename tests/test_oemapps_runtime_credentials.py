from __future__ import annotations

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
