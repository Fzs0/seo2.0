from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.site_service import upsert_site


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"


async def sync_sites_from_config(session: AsyncSession) -> dict[str, Any]:
    sites = [
        *(_load("main-sites.local.json", "main")),
        *(_load("blog-sites.local.json", "blog")),
        *(_load("wp-sites.local.json", "wp")),
    ]
    saved = [await upsert_site(session, s) for s in sites]
    return {"saved": len(saved), "items": saved}


def _load(filename: str, site_type: str) -> list[dict[str, Any]]:
    path = CONFIG / filename
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [_normalize(s, site_type) for s in data.get("sites", [])]


def _normalize(s: dict[str, Any], site_type: str) -> dict[str, Any]:
    base_url = s.get("siteUrl") or _base_from_api(s.get("apiBaseUrl")) or f"https://{s.get('name')}.com"
    api_base_url = s.get("apiBaseUrl") or s.get("siteUrl")
    return {
        "site_key": s.get("siteKey") or s.get("name"),
        "name": s.get("name"),
        "site_type": site_type,
        "domain": _domain(base_url),
        "base_url": base_url,
        "api_base_url": api_base_url,
        "market": _market(s.get("targetMarket")),
        "language_code": _lang(s.get("targetLanguage")),
        "google_gl": "us" if "US" in str(s.get("targetMarket") or "") else None,
        "google_hl": "en" if "English" in str(s.get("targetLanguage") or "") else None,
        "semrush_database": "us" if "US" in str(s.get("targetMarket") or "") else None,
        "content_role": s.get("contentRole"),
        "content_scope": s.get("contentScope"),
        "is_main": site_type == "main",
        "status": "active",
        "api_config": _api_config(s, site_type),
        "publish_config": _publish_config(s),
        "raw": s,
    }


def _api_config(s: dict[str, Any], site_type: str) -> dict[str, Any]:
    if site_type == "wp":
        return {"username": s.get("username"), "applicationPassword": s.get("applicationPassword")}
    return {"openApiKey": s.get("openApiKey"), "tokenA": s.get("tokenA"), "tokenB": s.get("tokenB")}


def _publish_config(s: dict[str, Any]) -> dict[str, Any]:
    keys = ("defaultAuthor", "defaultStatus", "defaultCategoryId", "defaultCoverUrl", "defaultSrcPrefix", "defaultImageId")
    return {k: s.get(k) for k in keys if s.get(k) not in (None, "")}


def _base_from_api(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None


def _domain(value: str | None) -> str | None:
    return urlparse(value or "").netloc or None


def _market(value: str | None) -> str | None:
    return str(value).split("/", 1)[0].strip() if value else None


def _lang(value: str | None) -> str | None:
    return "en" if str(value or "").lower().startswith("english") else value
