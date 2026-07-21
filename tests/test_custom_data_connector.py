from __future__ import annotations

import pytest

from app.connectors.custom_data import (
    ConnectorConfig,
    ConnectorMappingError,
    ConnectorSecurityError,
    CustomDataConnector,
    extract_json_path,
)
from app.services.custom_connector_service import _as_datetime, _schema_fingerprint


EXDIVO_RESPONSE = {
    "code": 0,
    "msg": "success",
    "data": {
        "list": [
            {
                "id": 13281541,
                "handle": "exdivo-bright-mirror-35k",
                "title": "ExDivo BRIGHT MIRROR 35K",
                "detail_url": "/products/exdivo-bright-mirror-35k",
                "meta_title": "ExDivo BRIGHT MIRROR 35K Replaceable Vape Kit",
                "meta_descript": "A replaceable vape kit with multiple flavor options.",
                "meta_keywords": '["bright mirror", "vape kit"]',
                "mini_detail": "Replaceable kit for adult users.",
                "available": 1,
                "status": 0,
                "variant_price_min": 23.99,
                "variant_price_max": 189.9,
                "inventory_quantity": 1098,
                "image": {"src": "https://cdn.example.com/product.png", "alt": ""},
                "images": [
                    {"src": "https://cdn.example.com/product.png", "alt": ""},
                    {"src": "https://cdn.example.com/detail.png", "alt": "Front view"},
                ],
                "variants": [{"id": 1, "sku": "EXD-1", "price": 23.99}],
                "collections": [{"id": 7, "title": "Replaceable Kits"}],
                "tags": [],
                "product_type": "vape-kit",
                "published_at": "2026-05-11T15:03:07+08:00",
                "updated_at": "2026-07-10T21:52:50+08:00",
            }
        ],
        "paginate": {"page": 1, "page_size": 20, "total": 104},
    },
}


def exdivo_config() -> ConnectorConfig:
    return ConnectorConfig.model_validate(
        {
            "name": "ExDivo products",
            "capability": "products.list",
            "request": {
                "method": "GET",
                "base_url": "https://api.exdivo.example",
                "path": "/v1/products",
                "allowed_hosts": ["api.exdivo.example"],
            },
            "response": {
                "success_path": "$.code",
                "success_value": 0,
                "items_path": "$.data.list",
                "pagination_path": "$.data.paginate",
            },
            "fields": {
                "external_id": {"path": "$.id", "required": True, "transform": "string"},
                "title": {"path": "$.title", "required": True, "transform": "string"},
                "handle": {"path": "$.handle", "transform": "string"},
                "url": {"path": "$.detail_url", "transform": "url", "base": "https://exdivo.com"},
                "meta_title": {"path": "$.meta_title", "transform": "string"},
                "meta_description": {"path": "$.meta_descript", "transform": "string"},
                "meta_keywords": {"path": "$.meta_keywords", "transform": "string_list"},
                "description": {"path": "$.mini_detail", "transform": "string"},
                "available": {"path": "$.available", "transform": "boolean"},
                "status": {"path": "$.status", "transform": "status", "values": {"0": "active"}},
                "price_min": {"path": "$.variant_price_min", "transform": "decimal"},
                "price_max": {"path": "$.variant_price_max", "transform": "decimal"},
                "inventory_quantity": {"path": "$.inventory_quantity", "transform": "integer"},
                "images": {"path": "$.images", "transform": "images"},
                "variants": {"path": "$.variants", "transform": "json"},
                "collections": {"path": "$.collections", "transform": "json"},
                "published_at": {"path": "$.published_at", "transform": "datetime"},
                "source_updated_at": {"path": "$.updated_at", "transform": "datetime"},
            },
        }
    )


def test_json_path_extracts_nested_values_and_array_items() -> None:
    assert extract_json_path(EXDIVO_RESPONSE, "$.data.list[0].title") == "ExDivo BRIGHT MIRROR 35K"
    assert extract_json_path(EXDIVO_RESPONSE, "$.data.list[*].id") == [13281541]


def test_schema_fingerprint_tracks_envelope_not_optional_item_fields() -> None:
    first_page = {"code": 0, "data": {"list": [{"id": 1, "optional": "value"}]}}
    last_page = {"code": 0, "data": {"list": [{"id": 2}]}}

    assert _schema_fingerprint(first_page) == _schema_fingerprint(last_page)


def test_database_datetime_conversion_accepts_connector_iso_value() -> None:
    parsed = _as_datetime("2026-07-10T08:52:50-05:00")

    assert parsed is not None
    assert parsed.isoformat() == "2026-07-10T08:52:50-05:00"


def test_preview_maps_exdivo_product_and_reports_missing_image_alt() -> None:
    preview = CustomDataConnector(exdivo_config()).preview(EXDIVO_RESPONSE)

    assert preview.total_items == 1
    assert preview.mapped_items == 1
    assert preview.mapping_errors == 0
    product = preview.items[0]
    assert product["external_id"] == "13281541"
    assert product["url"] == "https://exdivo.com/products/exdivo-bright-mirror-35k"
    assert product["meta_description"].startswith("A replaceable vape kit")
    assert product["status"] == "active"
    assert product["meta_keywords"] == ["bright mirror", "vape kit"]
    assert product["source_updated_at"] == "2026-07-10T21:52:50+08:00"
    assert product["images"][0]["alt"] == ""
    assert product["seo_audit"]["missing_tdk"] == []
    assert product["seo_audit"]["images_missing_alt"] == 1


def test_preview_rejects_business_error_response() -> None:
    payload = {"code": 401, "msg": "invalid token", "data": {"list": []}}
    with pytest.raises(ConnectorMappingError, match="success condition"):
        CustomDataConnector(exdivo_config()).preview(payload)


def test_required_mapping_failure_is_reported_without_dropping_other_items() -> None:
    payload = {**EXDIVO_RESPONSE, "data": {**EXDIVO_RESPONSE["data"], "list": [{"id": 1}]}}
    preview = CustomDataConnector(exdivo_config()).preview(payload)

    assert preview.total_items == 1
    assert preview.mapped_items == 0
    assert preview.mapping_errors == 1
    assert preview.errors[0]["field"] == "title"


def test_optional_blank_datetime_maps_to_none() -> None:
    payload = {**EXDIVO_RESPONSE, "data": {**EXDIVO_RESPONSE["data"], "list": [{**EXDIVO_RESPONSE["data"]["list"][0], "published_at": ""}]}}
    preview = CustomDataConnector(exdivo_config()).preview(payload)

    assert preview.mapping_errors == 0
    assert preview.items[0]["published_at"] is None


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.com",
        "https://127.0.0.1:8000",
        "https://localhost/private",
        "https://10.0.0.4/products",
        "file:///etc/passwd",
    ],
)
def test_connector_config_rejects_unsafe_base_urls(base_url: str) -> None:
    payload = exdivo_config().model_dump(mode="json")
    payload["request"]["base_url"] = base_url
    with pytest.raises((ValueError, ConnectorSecurityError)):
        ConnectorConfig.model_validate(payload)


def test_public_config_never_returns_secret_values() -> None:
    connector = CustomDataConnector(exdivo_config(), secrets={"api_token": "super-secret"})
    public = connector.public_config()

    assert "super-secret" not in str(public)
    assert public["configured_secret_names"] == ["api_token"]


def test_page_pagination_stops_when_remote_next_does_not_advance() -> None:
    payload = exdivo_config().model_dump(mode="json")
    payload["request"]["pagination"] = {
        "mode": "page",
        "page_param": "current",
        "page_size_param": "pagesize",
        "page_size": 200,
        "start_page": 1,
        "next_path": "$.data.paginate.next",
        "current_path": "$.data.paginate.current",
        "total_pages_path": "$.data.paginate.pageTotal",
        "max_pages": 10,
        "max_items": 1000,
    }
    connector = CustomDataConnector(ConnectorConfig.model_validate(payload))

    request = connector.build_request(page=1)
    assert request["params"]["current"] == 1
    assert request["params"]["pagesize"] == 200
    assert connector.next_page(
        {"data": {"paginate": {"current": 1, "next": 1, "pageTotal": 1}}},
        current_page=1,
    ) is None
