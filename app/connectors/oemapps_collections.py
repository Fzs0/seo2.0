"""OEMApps self-hosted product collection adapter."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.connectors.oemapps_products import OEMAPPS_API_BASE


class OemAppsCollectionError(RuntimeError):
    """Safe error raised for invalid OEMApps collection reads or writes."""


@dataclass(frozen=True, slots=True)
class PreparedCollectionSeoUpdate:
    body: dict[str, Any]
    changes: list[dict[str, Any]]
    snapshot_hash: str


_EDITABLE_FIELDS = (
    "title",
    "top_descript",
    "bottom_descript",
    "handle",
    "meta_title",
    "meta_descript",
    "meta_keywords",
    "sort_order",
    "manual_mod_index",
    "src",
)


def derive_collection_members(
    collection: dict[str, Any], products: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    collection_id = str(collection.get("id") or "")
    members: list[dict[str, Any]] = []
    for product in products:
        belongs = any(
            str(item.get("id") or item.get("collection_id") or "") == collection_id
            for item in product.get("collections") or []
            if isinstance(item, dict)
        )
        if belongs and product.get("id") is not None:
            members.append({"id": product["id"], "is_top": 0})
    members.sort(key=lambda item: int(item["id"]))
    expected = int(collection.get("product_count") or 0)
    if len(members) != expected:
        raise OemAppsCollectionError(
            f"collection member count mismatch: detail={expected}, derived={len(members)}"
        )
    return members


def build_editable_collection(
    collection: dict[str, Any], members: list[dict[str, Any]]
) -> dict[str, Any]:
    if not collection.get("id") or not collection.get("title"):
        raise OemAppsCollectionError("OEMApps collection detail is missing id or title")
    body = {field: collection.get(field) for field in _EDITABLE_FIELDS}
    body["products"] = [
        {"id": item["id"], "is_top": int(item.get("is_top") or 0)} for item in members
    ]
    return body


def collection_snapshot_hash(
    collection: dict[str, Any], members: list[dict[str, Any]]
) -> str:
    encoded = json.dumps(
        build_editable_collection(collection, members),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def prepare_collection_seo_update(
    collection: dict[str, Any],
    members: list[dict[str, Any]],
    patch: dict[str, Any],
) -> PreparedCollectionSeoUpdate:
    allowed = {"meta_title", "meta_description", "meta_keywords"}
    unknown = set(patch) - allowed
    if unknown:
        raise OemAppsCollectionError(
            f"unsupported collection SEO fields: {sorted(unknown)}"
        )
    body = build_editable_collection(collection, members)
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
    return PreparedCollectionSeoUpdate(
        body=body,
        changes=changes,
        snapshot_hash=collection_snapshot_hash(collection, members),
    )


class OemAppsCollections:
    """Deep adapter for OEMApps collection list, detail and complete PUT semantics."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        if not token.strip():
            raise OemAppsCollectionError("OEMApps token is required")
        self._token = token.strip()
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._timeout = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_collections(self, *, page: int = 1, limit: int = 200) -> dict[str, Any]:
        payload = await self._request(
            "GET", "/collections/list", params={"page": page, "limit": limit}
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise OemAppsCollectionError("OEMApps collection list has an invalid shape")
        return data

    async def list_products(self, *, page: int = 1, limit: int = 200) -> dict[str, Any]:
        payload = await self._request(
            "GET", "/products/list", params={"page": page, "limit": limit}
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise OemAppsCollectionError("OEMApps product list has an invalid shape")
        return data

    async def list_all_collections(self) -> list[dict[str, Any]]:
        return await self._list_all(self.list_collections)

    async def list_all_products(self) -> list[dict[str, Any]]:
        return await self._list_all(self.list_products)

    async def get_collection(self, collection_id: str | int) -> dict[str, Any]:
        collection_id_text = _numeric_id(collection_id, "collection_id")
        payload = await self._request("GET", f"/collections/{collection_id_text}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OemAppsCollectionError("OEMApps collection detail did not return an object")
        return data

    async def update_collection(
        self, collection_id: str | int, body: dict[str, Any]
    ) -> dict[str, Any]:
        collection_id_text = _numeric_id(collection_id, "collection_id")
        return await self._request("PUT", f"/collections/{collection_id_text}", body=body)

    async def _list_all(self, loader: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, 51):
            data = await loader(page=page, limit=200)
            items.extend(item for item in data["list"] if isinstance(item, dict))
            paginate = data.get("paginate") or {}
            current = int(paginate.get("current") or page)
            total_pages = int(paginate.get("pageTotal") or current)
            if current >= total_pages:
                return items
        raise OemAppsCollectionError("OEMApps pagination exceeded 50 pages")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                f"{OEMAPPS_API_BASE}{path}",
                headers={"token": self._token, "Accept": "application/json"},
                params=params,
                json=body,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise OemAppsCollectionError(
                f"OEMApps collection request failed: {type(error).__name__}"
            ) from error
        if len(response.content) > 10 * 1024 * 1024:
            raise OemAppsCollectionError("OEMApps collection response exceeds 10 MB")
        try:
            payload = response.json()
        except ValueError as error:
            raise OemAppsCollectionError("OEMApps returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise OemAppsCollectionError("OEMApps response root must be an object")
        if payload.get("code") != 0:
            message = str(payload.get("msg") or "business request failed")[:300]
            raise OemAppsCollectionError(
                f"OEMApps rejected the collection request: {message}"
            )
        return payload


def _numeric_id(value: str | int, field: str) -> str:
    text = str(value)
    if not text.isdigit():
        raise OemAppsCollectionError(f"OEMApps {field} must be an integer ID")
    return text


__all__ = [
    "OemAppsCollectionError",
    "OemAppsCollections",
    "PreparedCollectionSeoUpdate",
    "build_editable_collection",
    "collection_snapshot_hash",
    "derive_collection_members",
    "prepare_collection_seo_update",
]
