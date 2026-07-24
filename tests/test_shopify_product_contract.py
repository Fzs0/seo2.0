from pathlib import Path
from datetime import datetime, timezone

import pytest

from app.services.shopify_product_service import (
    ShopifyProductError,
    _map_product,
    _iso_timestamp,
    _preview_token,
)


def _site(site_id: str = "00000000-0000-0000-0000-000000000001") -> dict:
    return {
        "id": site_id,
        "domain": "https://ladiesstreetx.shop",
        "base_url": "https://ladiesstreetx.shop",
    }


def _remote() -> dict:
    return {
        "id": "gid://shopify/Product/123",
        "title": "Dress",
        "handle": "dress",
        "descriptionHtml": "<p>Dress</p>",
        "status": "ACTIVE",
        "productType": "Dress",
        "updatedAt": "2026-07-24T00:00:00Z",
        "seo": {"title": "", "description": ""},
        "featuredMedia": {"id": "gid://shopify/MediaImage/1", "alt": "", "image": {"url": "https://cdn/image.jpg"}},
        "media": {"nodes": [{"id": "gid://shopify/MediaImage/1", "alt": "", "image": {"url": "https://cdn/image.jpg"}}]},
        "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/1", "sku": "SKU", "price": "9.99", "inventoryQuantity": 3}]},
    }


def test_shopify_product_mapping_preserves_commerce_snapshot_and_flags_seo() -> None:
    mapped = _map_product(_site(), _remote())

    assert mapped["external_id"] == "gid://shopify/Product/123"
    assert mapped["variants"][0] == {
        "id": "gid://shopify/ProductVariant/1",
        "sku": "SKU",
        "price": "9.99",
        "inventoryQuantity": 3,
    }
    assert mapped["seo_audit"] == {
        "missing_meta_title": True,
        "missing_meta_description": True,
        "images_missing_alt": 1,
    }
    assert mapped["url"] == "https://ladiesstreetx.shop/products/dress"


@pytest.mark.parametrize("bad_id", ["123", "gid://shopify/Product/x", "gid://shopify/Product/0", "gid://shopify/Product/1/extra"])
def test_shopify_product_mapping_rejects_invalid_gid(bad_id: str) -> None:
    remote = _remote()
    remote["id"] = bad_id
    with pytest.raises(ShopifyProductError, match="invalid product id"):
        _map_product(_site(), remote)


def test_preview_token_binds_site_product_version_and_exact_patch(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.shopify_product_service.get_settings",
        lambda: type("Settings", (), {"connector_secret_key": "local-secret"})(),
    )
    patch = {"meta_title": "Title", "meta_description": "Description"}
    token = _preview_token("site-a", 1, "v1", patch)

    assert token == _preview_token("site-a", 1, "v1", patch)
    assert token != _preview_token("site-b", 1, "v1", patch)
    assert token != _preview_token("site-a", 1, "v2", patch)
    assert token != _preview_token("site-a", 1, "v1", {**patch, "meta_title": "Other"})


def test_shopify_timestamps_use_one_canonical_utc_representation() -> None:
    expected = "2026-07-24T00:00:00Z"
    assert _iso_timestamp("2026-07-24T00:00:00.000Z") == expected
    assert _iso_timestamp("2026-07-24T08:00:00+08:00") == expected
    assert _iso_timestamp(datetime(2026, 7, 24, tzinfo=timezone.utc)) == expected


def test_shopify_product_migration_scopes_ids_and_audits_uncertain_writes() -> None:
    sql = Path("db/migrations/027_shopify_product_seo.sql").read_text(encoding="utf-8")

    assert "products_site_external_id_uk" in sql
    assert "ON seo_agent.products (site_id, external_id)" in sql
    assert "request_id uuid UNIQUE" in sql
    assert "'running'" in sql and "'unknown'" in sql and "'stale'" in sql
    assert "requested_patch" in sql and "before_snapshot" in sql and "after_snapshot" in sql
