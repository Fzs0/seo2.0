from pathlib import Path

import pytest

from app.services.shopify_connection_service import _normalize_shop_domain, _safe_error


@pytest.mark.parametrize(
    "value",
    [
        "example.com",
        "example.myshopify.com.evil.test",
        "user@example.myshopify.com",
        "example.myshopify.com:443",
        "example.myshopify.com/path",
    ],
)
def test_shopify_domain_rejects_noncanonical_targets(value: str) -> None:
    with pytest.raises(ValueError, match="myshopify"):
        _normalize_shop_domain(value)


def test_shopify_domain_normalizes_https_url() -> None:
    assert _normalize_shop_domain("https://Example.myshopify.com/") == "example.myshopify.com"


def test_shopify_errors_are_redacted() -> None:
    message = _safe_error("authorization=Bearer-secret client_secret=top-secret access_token=shpat_x")

    assert "Bearer-secret" not in str(message)
    assert "top-secret" not in str(message)
    assert "shpat_x" not in str(message)


def test_shopify_migration_keeps_credentials_out_of_public_table() -> None:
    sql = Path("db/migrations/026_shopify_connections.sql").read_text(encoding="utf-8")

    public_table = sql.split("CREATE TABLE IF NOT EXISTS seo_agent.shopify_connection_secrets", 1)[0]
    assert "client_secret" not in public_table
    assert "encrypted_value bytea NOT NULL" in sql
    assert "REFERENCES seo_agent.sites(id) ON DELETE CASCADE" in sql
    assert "shopify_connection_rollbacks" in sql
