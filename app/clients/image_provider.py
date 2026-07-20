"""Image Provider 客户端：Pexels / Unsplash / Pixabay。

缺失 API key 时返回空列表并标记 configured=False。
"""
from __future__ import annotations

import urllib.parse
from typing import Any

import structlog

from app.clients.http_client import ExternalCallError, request_json
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
_settings = get_settings()


def _key_for(provider: str) -> str:
    return {
        "pexels": _settings.image_pexels_key,
        "unsplash": _settings.image_unsplash_key,
        "pixabay": _settings.image_pixabay_key,
    }.get(provider.lower(), "")


async def search_images(
    *,
    provider: str,
    query: str,
    per_page: int = 10,
    page: int = 1,
) -> dict[str, Any]:
    """按 provider 调对应 API。"""
    key = _key_for(provider)
    if not key:
        return {"provider": provider, "configured": False, "items": [], "query": query}

    provider_l = provider.lower()
    if provider_l == "pexels":
        params = {"query": query, "per_page": str(per_page), "page": str(page)}
        url = f"https://api.pexels.com/v1/search?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": key}
    elif provider_l == "unsplash":
        params = {"query": query, "per_page": str(per_page), "page": str(page)}
        url = f"https://api.unsplash.com/search/photos?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": f"Client-ID {key}"}
    elif provider_l == "pixabay":
        params = {"q": query, "per_page": str(per_page), "page": str(page), "key": key}
        url = f"https://pixabay.com/api/?{urllib.parse.urlencode(params)}"
        headers = {}
    else:
        return {"provider": provider, "configured": False, "items": [], "query": query, "error": "unsupported provider"}

    try:
        data = await request_json("GET", url, client_label=f"image_{provider_l}", headers=headers, timeout=20)
    except ExternalCallError as e:
        logger.warning("image_search_failed", provider=provider, query=query, error=str(e))
        return {"provider": provider, "configured": True, "items": [], "query": query, "status": "fetch-failed"}

    items = _normalize_items(provider_l, data)
    return {"provider": provider, "configured": True, "items": items, "query": query}


def _normalize_items(provider: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    if provider == "pexels":
        return [
            {
                "id": str(p.get("id")),
                "url": (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large") or (p.get("src") or {}).get("medium"),
                "thumbnail": (p.get("src") or {}).get("medium"),
                "photographer": p.get("photographer"),
                "alt": p.get("alt"),
            }
            for p in (data.get("photos") or [])
        ]
    if provider == "unsplash":
        return [
            {
                "id": str(p.get("id")),
                "url": (p.get("urls") or {}).get("regular"),
                "thumbnail": (p.get("urls") or {}).get("thumb"),
                "photographer": (p.get("user") or {}).get("name"),
                "alt": (p.get("alt_description") or p.get("description") or ""),
            }
            for p in (data.get("results") or [])
        ]
    if provider == "pixabay":
        return [
            {
                "id": str(p.get("id")),
                "url": p.get("largeImageURL") or p.get("webformatURL"),
                "thumbnail": p.get("previewURL"),
                "photographer": p.get("user"),
                "alt": p.get("tags"),
            }
            for p in (data.get("hits") or [])
        ]
    return []
