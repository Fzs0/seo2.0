from __future__ import annotations

import httpx
import pytest

from app.connectors.oemapps_products import (
    OemAppsProductError,
    OemAppsProducts,
    build_editable_product,
    build_oemapps_connector_config,
    prepare_seo_update,
    snapshot_hash,
)


PRODUCT = {
    "id": 13576711,
    "title": "Titanium Frying Pan",
    "handle": "titanium-frying-pan",
    "product_type": "",
    "spu": "SPU-1",
    "subtitle": "",
    "vendor": "Avinoti",
    "meta_title": "",
    "meta_descript": "",
    "meta_keywords": [],
    "inner_title": "",
    "inventory_tracking": 0,
    "spec_mode": 2,
    "mini_detail": "Short description",
    "free_shipping": 1,
    "inventory_policy": 1,
    "taxable": 0,
    "virtual_sale_count": 0,
    "body_html": "<p>Body</p>",
    "status": 1,
    "variants": [
        {
            "id": 138660225,
            "option1_title": "Size",
            "option2_title": "Style",
            "option1_value_title": "26cm",
            "option2_value_title": "Steel",
            "image_id": 61414231,
            "src": "https://cdn.example.com/one.png",
            "title": "Titanium Frying Pan 26cm/Steel",
            "barcode": "",
            "sku": "SKU-1",
            "inventory_quantity": 7,
            "price": "119.99",
            "compare_at_price": "149.99",
            "weight": "0.0000",
        }
    ],
    "images": [
        {"image_id": 61414231, "src": "https://cdn.example.com/one.png", "alt": ""}
    ],
    "options": [
        {
            "id": 16003455,
            "option_name": "Size",
            "values": [
                {"id": 66097295, "option_id": 16003455, "option_value": "26cm"}
            ],
        }
    ],
    "tags": ["pan"],
    "collections": [{"id": 99, "collection_id": 506543, "title": "Pans"}],
    "updated_at": 1779099216,
}


def test_oemapps_preset_hides_everything_except_site_token() -> None:
    config = build_oemapps_connector_config()

    assert config.adapter == "oemapps"
    assert config.request.base_url == "https://openapi.oemapps.com"
    assert config.request.headers == {"token": "${secret:token}"}
    assert config.request.pagination.page_param == "page"
    assert config.request.pagination.page_size_param == "limit"
    assert config.response.items_path == "$.data.list"
    assert config.fields["meta_description"].path == "$.meta_descript"
    assert config.fields["price"].path == "$.variant_price_min"


def test_prepare_seo_update_preserves_commerce_data_and_changes_only_approved_seo() -> None:
    before = build_editable_product(PRODUCT)
    prepared = prepare_seo_update(
        PRODUCT,
        {
            "meta_title": "Titanium Frying Pan | Avinoti",
            "meta_description": "A lightweight pan for everyday cooking.",
            "meta_keywords": ["titanium pan", "frying pan"],
            "image_alts": {"61414231": "Titanium frying pan with steel handle"},
        },
    )

    assert before["variants"][0]["inventory_quantity"] == 7
    assert prepared.body["variants"] == before["variants"]
    assert prepared.body["meta_title"] == "Titanium Frying Pan | Avinoti"
    assert prepared.body["meta_descript"].startswith("A lightweight")
    assert prepared.body["images"][0]["alt"] == "Titanium frying pan with steel handle"
    assert PRODUCT["meta_title"] == ""
    assert {change["field"] for change in prepared.changes} == {
        "meta_title",
        "meta_description",
        "meta_keywords",
        "images.61414231.alt",
    }


def test_prepare_seo_update_rejects_unknown_image() -> None:
    with pytest.raises(OemAppsProductError, match="image_id 999"):
        prepare_seo_update(PRODUCT, {"image_alts": {"999": "Unknown"}})


def test_snapshot_hash_is_stable_but_detects_commerce_changes() -> None:
    first = snapshot_hash(PRODUCT)
    assert first == snapshot_hash(dict(PRODUCT))
    changed = {**PRODUCT, "variants": [{**PRODUCT["variants"][0], "price": "129.99"}]}
    assert first != snapshot_hash(changed)


@pytest.mark.asyncio
async def test_client_uses_product_id_token_and_full_put_body() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "msg": "success", "data": PRODUCT})
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {"id": 13576711}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OemAppsProducts("secret-token", client=client)
    product = await adapter.get_product("13576711")
    body = build_editable_product(product)
    await adapter.update_product("13576711", body)
    await client.aclose()

    assert [request.method for request in requests] == ["GET", "PUT"]
    assert str(requests[1].url) == "https://openapi.oemapps.com/products/13576711"
    assert requests[1].headers["token"] == "secret-token"
    assert httpx.Response(200, content=requests[1].content).json()["variants"][0]["sku"] == "SKU-1"


@pytest.mark.asyncio
async def test_client_rejects_business_error_even_on_http_200() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 1001, "msg": "data not found", "data": None})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OemAppsProducts("secret-token", client=client)
    with pytest.raises(OemAppsProductError, match="data not found"):
        await adapter.get_product("13576711")
    await client.aclose()
