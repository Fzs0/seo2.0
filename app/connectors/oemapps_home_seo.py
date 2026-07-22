"""OEMApps self-hosted homepage SEO adapter."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.connectors.oemapps_products import OEMAPPS_API_BASE


class OemAppsHomeSeoError(RuntimeError):
    """Safe error raised for invalid OEMApps homepage SEO reads or writes."""


@dataclass(frozen=True, slots=True)
class PreparedHomeSeoUpdate:
    body: dict[str, Any]
    changes: list[dict[str, Any]]
    snapshot_hash: str


def normalize_home_seo(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "meta_title": str(value.get("meta_title") or ""),
        "meta_descript": str(value.get("meta_descript") or ""),
        "meta_keywords": _string_list(value.get("meta_keywords")),
    }


def home_seo_snapshot_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        normalize_home_seo(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def prepare_home_seo_update(
    current: dict[str, Any], patch: dict[str, Any]
) -> PreparedHomeSeoUpdate:
    allowed = {"meta_title", "meta_description", "meta_keywords"}
    unknown = set(patch) - allowed
    if unknown:
        raise OemAppsHomeSeoError(f"unsupported homepage SEO fields: {sorted(unknown)}")
    body = normalize_home_seo(current)
    changes: list[dict[str, Any]] = []
    remote_names = {
        "meta_title": "meta_title",
        "meta_description": "meta_descript",
        "meta_keywords": "meta_keywords",
    }
    for public_name, remote_name in remote_names.items():
        if public_name not in patch:
            continue
        before = body[remote_name]
        after = patch[public_name]
        if before != after:
            changes.append({"field": public_name, "before": before, "after": after})
            body[remote_name] = after
    return PreparedHomeSeoUpdate(
        body=body,
        changes=changes,
        snapshot_hash=home_seo_snapshot_hash(current),
    )


class OemAppsHomeSeo:
    """Deep adapter for the fixed OEMApps `/seoplans` interface."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        if not token.strip():
            raise OemAppsHomeSeoError("OEMApps token is required")
        self._token = token.strip()
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._timeout = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_home_seo(self) -> dict[str, Any]:
        payload = await self._request("GET")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OemAppsHomeSeoError("OEMApps homepage SEO did not return an object")
        return normalize_home_seo(data)

    async def update_home_seo(self, body: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_home_seo(body)
        payload = await self._request("PUT", body=normalized)
        if payload.get("data") is not True and not isinstance(payload.get("data"), dict):
            raise OemAppsHomeSeoError("OEMApps homepage SEO update was not acknowledged")
        return payload

    async def _request(
        self, method: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                f"{OEMAPPS_API_BASE}/seoplans",
                headers={"token": self._token, "Accept": "application/json"},
                json=body,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise OemAppsHomeSeoError(
                f"OEMApps homepage SEO request failed: {type(error).__name__}"
            ) from error
        if len(response.content) > 1024 * 1024:
            raise OemAppsHomeSeoError("OEMApps homepage SEO response exceeds 1 MB")
        try:
            payload = response.json()
        except ValueError as error:
            raise OemAppsHomeSeoError("OEMApps returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise OemAppsHomeSeoError("OEMApps response root must be an object")
        if payload.get("code") != 0:
            message = str(payload.get("msg") or "business request failed")[:300]
            raise OemAppsHomeSeoError(
                f"OEMApps rejected the homepage SEO request: {message}"
            )
        return payload


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise OemAppsHomeSeoError("meta_keywords JSON must be an array")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text.split(",") if item.strip()]


__all__ = [
    "OemAppsHomeSeo",
    "OemAppsHomeSeoError",
    "PreparedHomeSeoUpdate",
    "home_seo_snapshot_hash",
    "normalize_home_seo",
    "prepare_home_seo_update",
]
