"""OEMApps custom page adapter with guarded full-page PUT semantics."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.connectors.oemapps_products import OEMAPPS_API_BASE


class OemAppsPageError(RuntimeError):
    """Safe error raised for invalid OEMApps custom-page reads or writes."""


@dataclass(frozen=True, slots=True)
class PreparedPageUpdate:
    body: dict[str, Any]
    changes: list[dict[str, Any]]
    snapshot_hash: str


_EDITABLE_FIELDS = (
    "handle",
    "title",
    "meta_title",
    "meta_descript",
    "meta_keywords",
    "is_default",
    "from_id",
    "from_name",
    "content",
)
_PUBLIC_TO_REMOTE = {
    "handle": "handle",
    "title": "title",
    "meta_title": "meta_title",
    "meta_description": "meta_descript",
    "meta_keywords": "meta_keywords",
    "is_default": "is_default",
    "from_id": "from_id",
    "from_name": "from_name",
    "content": "content",
}


def build_editable_page(page: dict[str, Any]) -> dict[str, Any]:
    """Build the complete body documented by OEMApps `PUT /pages/{id}`."""
    if page.get("id") in (None, ""):
        raise OemAppsPageError("OEMApps custom page is missing id")
    if not str(page.get("title") or "").strip():
        raise OemAppsPageError("OEMApps custom page is missing title")
    missing = [field for field in _EDITABLE_FIELDS if field not in page]
    if missing:
        raise OemAppsPageError(
            "OEMApps page list does not contain the complete PUT fields: "
            f"{missing}"
        )
    return {
        "handle": str(page.get("handle") or ""),
        "title": str(page.get("title") or ""),
        "meta_title": str(page.get("meta_title") or ""),
        "meta_descript": str(page.get("meta_descript") or ""),
        "meta_keywords": _string_list(page.get("meta_keywords")),
        "is_default": _integer(page.get("is_default"), default=0),
        "from_id": _integer(page.get("from_id"), default=0),
        "from_name": str(page.get("from_name") or ""),
        "content": str(page.get("content") or ""),
    }


def page_snapshot_hash(page: dict[str, Any]) -> str:
    encoded = json.dumps(
        build_editable_page(page),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def prepare_page_update(
    page: dict[str, Any], patch: dict[str, Any]
) -> PreparedPageUpdate:
    unknown = set(patch) - set(_PUBLIC_TO_REMOTE)
    if unknown:
        raise OemAppsPageError(f"unsupported custom page fields: {sorted(unknown)}")
    body = build_editable_page(page)
    changes: list[dict[str, Any]] = []
    for public_name, remote_name in _PUBLIC_TO_REMOTE.items():
        if public_name not in patch:
            continue
        before = body[remote_name]
        after = patch[public_name]
        if public_name == "meta_keywords":
            after = _string_list(after)
        elif public_name in {"is_default", "from_id"}:
            after = _integer(after, default=0)
        elif after is None:
            after = ""
        if before != after:
            changes.append({"field": public_name, "before": before, "after": after})
            body[remote_name] = after
    return PreparedPageUpdate(
        body=body,
        changes=changes,
        snapshot_hash=page_snapshot_hash(page),
    )


class OemAppsPages:
    """Adapter for OEMApps `GET /pages` and `PUT /pages/{id}`."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        if not token.strip():
            raise OemAppsPageError("OEMApps token is required")
        self._token = token.strip()
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._timeout = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_pages(self) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        for page_number in range(1, 51):
            payload = await self._request(
                "GET",
                "/pages",
                params={"page": page_number, "pagesize": 200},
            )
            items, paginate = _page_items(payload)
            pages.extend(items)
            if not paginate:
                return pages
            current = _integer(paginate.get("current"), default=page_number)
            total_pages = _integer(paginate.get("pageTotal"), default=current)
            if current >= total_pages:
                return pages
        raise OemAppsPageError("OEMApps custom page pagination exceeded 50 pages")

    async def get_page(self, page_id: str | int) -> dict[str, Any]:
        page_id_text = _numeric_id(page_id)
        pages = await self.list_pages()
        for page in pages:
            if str(page.get("id") or "") == page_id_text:
                return page
        raise OemAppsPageError(f"OEMApps custom page {page_id_text} was not found")

    async def update_page(
        self, page_id: str | int, body: dict[str, Any]
    ) -> dict[str, Any]:
        page_id_text = _numeric_id(page_id)
        complete_body = build_editable_page({"id": page_id_text, **body})
        return await self._request(
            "PUT", f"/pages/{page_id_text}", body=complete_body
        )

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
            raise OemAppsPageError(
                f"OEMApps custom page request failed: {type(error).__name__}"
            ) from error
        if len(response.content) > 10 * 1024 * 1024:
            raise OemAppsPageError("OEMApps custom page response exceeds 10 MB")
        try:
            payload = response.json()
        except ValueError as error:
            raise OemAppsPageError("OEMApps returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise OemAppsPageError("OEMApps response root must be an object")
        if payload.get("code") != 0:
            message = str(payload.get("msg") or "business request failed")[:300]
            raise OemAppsPageError(
                f"OEMApps rejected the custom page request: {message}"
            )
        return payload


def _page_items(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    paginate: dict[str, Any] | None = None
    data = payload.get("data")
    if isinstance(data, dict):
        items = data.get("list")
        if isinstance(data.get("paginate"), dict):
            paginate = data["paginate"]
    elif isinstance(data, list):
        items = data
    else:
        items = payload.get("list")
    if not isinstance(items, list):
        raise OemAppsPageError("OEMApps custom page list has an invalid shape")
    pages = [item for item in items if isinstance(item, dict)]
    if len(pages) != len(items):
        raise OemAppsPageError("OEMApps custom page list contains invalid items")
    return pages, paginate


def _numeric_id(value: str | int) -> str:
    text = str(value)
    if not text.isdigit():
        raise OemAppsPageError("OEMApps page_id must be an integer ID")
    return text


def _integer(value: Any, *, default: int) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise OemAppsPageError("OEMApps custom page integer field is invalid") from error


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise OemAppsPageError("meta_keywords JSON must be an array") from error
        if not isinstance(parsed, list):
            raise OemAppsPageError("meta_keywords JSON must be an array")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text.split(",") if item.strip()]


__all__ = [
    "OemAppsPageError",
    "OemAppsPages",
    "PreparedPageUpdate",
    "build_editable_page",
    "page_snapshot_hash",
    "prepare_page_update",
]
