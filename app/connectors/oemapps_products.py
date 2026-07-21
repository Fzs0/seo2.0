"""OEMApps self-hosted product adapter.

All OEMApps sites share this implementation. Callers provide only the encrypted
site token; list mapping, detail reads, and full-product PUT semantics stay here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.connectors.custom_data import ConnectorConfig


OEMAPPS_API_BASE = "https://openapi.oemapps.com"
_PRODUCT_FIELDS = (
    "product_type",
    "handle",
    "spu",
    "title",
    "subtitle",
    "vendor",
    "meta_title",
    "meta_descript",
    "meta_keywords",
    "inner_title",
    "inventory_tracking",
    "spec_mode",
    "mini_detail",
    "free_shipping",
    "inventory_policy",
    "taxable",
    "virtual_sale_count",
    "body_html",
    "status",
)
_VARIANT_FIELDS = (
    "id",
    "option1_title",
    "option2_title",
    "option3_title",
    "option1_value_title",
    "option2_value_title",
    "option3_value_title",
    "image_id",
    "src",
    "title",
    "barcode",
    "sku",
    "inventory_quantity",
    "price",
    "compare_at_price",
    "weight",
)


class OemAppsProductError(RuntimeError):
    """Safe error raised for invalid OEMApps product reads or writes."""


@dataclass(frozen=True, slots=True)
class PreparedSeoUpdate:
    body: dict[str, Any]
    changes: list[dict[str, Any]]
    snapshot_hash: str


def build_oemapps_connector_config() -> ConnectorConfig:
    """Return the invariant list contract shared by every OEMApps site."""
    return ConnectorConfig.model_validate(
        {
            "name": "OEMApps products",
            "adapter": "oemapps",
            "capability": "products.list",
            "request": {
                "method": "GET",
                "base_url": OEMAPPS_API_BASE,
                "path": "/products/list",
                "allowed_hosts": ["openapi.oemapps.com"],
                "headers": {"token": "${secret:token}"},
                "params": {},
                "pagination": {
                    "mode": "page",
                    "page_param": "page",
                    "page_size_param": "limit",
                    "page_size": 100,
                    "start_page": 1,
                    "current_path": "$.data.paginate.current",
                    "total_pages_path": "$.data.paginate.pageTotal",
                    "max_pages": 50,
                    "max_items": 10000,
                },
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
                "url": {"path": "$.detail_url", "transform": "url"},
                "canonical_url": {"path": "$.detail_url", "transform": "url"},
                "description": {"path": "$.mini_detail", "transform": "string"},
                "meta_title": {"path": "$.meta_title", "transform": "string"},
                "meta_description": {"path": "$.meta_descript", "transform": "string"},
                "meta_keywords": {"path": "$.meta_keywords", "transform": "string_list"},
                "status": {
                    "path": "$.status",
                    "transform": "status",
                    "values": {"0": "draft", "1": "active"},
                },
                "available": {"path": "$.available", "transform": "boolean"},
                "price": {"path": "$.variant_price_min", "transform": "decimal"},
                "price_min": {"path": "$.variant_price_min", "transform": "decimal"},
                "price_max": {"path": "$.variant_price_max", "transform": "decimal"},
                "inventory_quantity": {"path": "$.inventory_quantity", "transform": "integer"},
                "images": {"path": "$.images", "transform": "images"},
                "variants": {"path": "$.variants", "transform": "json"},
                "collections": {"path": "$.collections", "transform": "json"},
                "tags": {"path": "$.tags", "transform": "string_list"},
                "product_type": {"path": "$.product_type", "transform": "string"},
                "published_at": {"path": "$.published_at", "transform": "datetime"},
                "source_updated_at": {"path": "$.updated_at", "transform": "datetime"},
            },
        }
    )


def build_editable_product(product: dict[str, Any]) -> dict[str, Any]:
    """Convert product detail output to the complete body required by OEMApps PUT."""
    if not product.get("id") or not product.get("title"):
        raise OemAppsProductError("OEMApps product detail is missing id or title")
    body = {field: product.get(field) for field in _PRODUCT_FIELDS}
    body["variants"] = [
        {field: variant.get(field, "") for field in _VARIANT_FIELDS}
        for variant in product.get("variants") or []
    ]
    body["images"] = [
        {field: image.get(field, "") for field in ("image_id", "src", "alt")}
        for image in product.get("images") or []
    ]
    body["options"] = []
    for option in product.get("options") or []:
        body["options"].append(
            {
                "id": option.get("id"),
                "option_name": option.get("option_name"),
                "values": [
                    {
                        "id": value.get("id"),
                        "option_id": value.get("option_id"),
                        "option_value": value.get("option_value"),
                    }
                    for value in option.get("values") or []
                ],
            }
        )
    body["tags"] = list(product.get("tags") or [])
    body["collections"] = [
        {"collection_id": collection.get("collection_id")}
        for collection in product.get("collections") or []
        if collection.get("collection_id") is not None
    ]
    return body


def snapshot_hash(product: dict[str, Any]) -> str:
    canonical = json.dumps(
        build_editable_product(product),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare_seo_update(product: dict[str, Any], patch: dict[str, Any]) -> PreparedSeoUpdate:
    allowed = {"meta_title", "meta_description", "meta_keywords", "image_alts"}
    unknown = set(patch) - allowed
    if unknown:
        raise OemAppsProductError(f"unsupported SEO update fields: {sorted(unknown)}")
    body = build_editable_product(product)
    changes: list[dict[str, Any]] = []
    remote_names = {
        "meta_title": "meta_title",
        "meta_description": "meta_descript",
        "meta_keywords": "meta_keywords",
    }
    for public_name, remote_name in remote_names.items():
        if public_name not in patch:
            continue
        before = body.get(remote_name)
        after = patch[public_name]
        if before != after:
            changes.append({"field": public_name, "before": before, "after": after})
            body[remote_name] = after
    images_by_id = {str(image.get("image_id")): image for image in body["images"]}
    for image_id, alt in (patch.get("image_alts") or {}).items():
        image = images_by_id.get(str(image_id))
        if image is None:
            raise OemAppsProductError(f"image_id {image_id} does not belong to this product")
        before = image.get("alt") or ""
        if before != alt:
            changes.append(
                {"field": f"images.{image_id}.alt", "before": before, "after": alt}
            )
            image["alt"] = alt
    return PreparedSeoUpdate(body=body, changes=changes, snapshot_hash=snapshot_hash(product))


class OemAppsProducts:
    """Deep adapter for the fixed OEMApps product detail/update protocol."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        if not token.strip():
            raise OemAppsProductError("OEMApps token is required")
        self._token = token.strip()
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._timeout = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_product(self, product_id: str | int) -> dict[str, Any]:
        payload = await self._request("GET", product_id)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OemAppsProductError("OEMApps product detail did not return an object")
        return data

    async def update_product(
        self, product_id: str | int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request("PUT", product_id, body=body)

    async def _request(
        self,
        method: str,
        product_id: str | int,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        product_id_text = str(product_id)
        if not product_id_text.isdigit():
            raise OemAppsProductError("OEMApps product_id must be an integer product ID")
        try:
            response = await self._client.request(
                method,
                f"{OEMAPPS_API_BASE}/products/{product_id_text}",
                headers={"token": self._token, "Accept": "application/json"},
                json=body,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise OemAppsProductError(
                f"OEMApps product request failed: {type(error).__name__}"
            ) from error
        if len(response.content) > 5 * 1024 * 1024:
            raise OemAppsProductError("OEMApps product response exceeds 5 MB")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise OemAppsProductError("OEMApps returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise OemAppsProductError("OEMApps response root must be an object")
        if payload.get("code") != 0:
            message = str(payload.get("msg") or "business request failed")[:300]
            raise OemAppsProductError(f"OEMApps rejected the product request: {message}")
        return payload


__all__ = [
    "OEMAPPS_API_BASE",
    "OemAppsProductError",
    "OemAppsProducts",
    "PreparedSeoUpdate",
    "build_editable_product",
    "build_oemapps_connector_config",
    "prepare_seo_update",
    "snapshot_hash",
]
