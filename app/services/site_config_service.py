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
    market = _market(s.get("targetMarket"))
    language_code = _lang(s.get("targetLanguage"))
    return {
        "site_key": s.get("siteKey") or s.get("name"),
        "name": s.get("name"),
        "site_type": site_type,
        "domain": _domain(base_url),
        "base_url": base_url,
        "api_base_url": api_base_url,
        "market": market,
        "language_code": language_code,
        "google_gl": market.lower() if market and len(market) == 2 else None,
        "google_hl": language_code,
        "semrush_database": market.lower() if market and len(market) == 2 else None,
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
    config = {"openApiKey": s.get("openApiKey"), "tokenA": s.get("tokenA"), "tokenB": s.get("tokenB")}
    if site_type == "blog":
        config.update({"connector_type": "custom_openapi", "articlesPath": "/posts", "publishPath": "/posts"})
    if s.get("articleUrlPath"):
        config["articleUrlPath"] = s["articleUrlPath"]
    return config


def load_wordpress_runtime_api_config(site: dict[str, Any]) -> dict[str, Any]:
    """Merge local WordPress credentials into an in-memory site copy only.

    Older installations keep WordPress Application Passwords in the existing
    local config file while synchronized ``sites`` rows may intentionally omit
    them.  The runtime Publisher still needs those values for readback and
    publishing, but they must not be written back to the row or returned by a
    public site endpoint.
    """
    current = dict(site.get("api_config") or {})
    if current.get("username") and current.get("applicationPassword"):
        return current
    entry = _matching_local_site(site, "wp-sites.local.json")
    if entry:
        username = str(entry.get("username") or "").strip()
        application_password = str(
            entry.get("applicationPassword") or ""
        ).strip()
        if username and application_password:
            return {
                **current,
                "username": username,
                "applicationPassword": application_password,
            }
    return current


def load_openapi_runtime_api_config(site: dict[str, Any]) -> dict[str, Any]:
    """Merge a local custom-blog API key into an in-memory site copy only."""
    current = dict(site.get("api_config") or {})
    if any(current.get(key) for key in ("openApiKey", "tokenA", "tokenB")):
        return current
    entry = _matching_local_site(site, "blog-sites.local.json")
    if not entry:
        return current
    merged = dict(current)
    for key in (
        "openApiKey",
        "tokenA",
        "tokenB",
        "articlesPath",
        "publishPath",
        "articleUrlPath",
    ):
        value = entry.get(key)
        if value not in (None, ""):
            merged[key] = value
    merged.setdefault("connector_type", "custom_openapi")
    return merged


def _matching_local_site(
    site: dict[str, Any],
    filename: str,
) -> dict[str, Any] | None:
    path = CONFIG / filename
    if not path.is_file():
        return None
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("sites", [])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    identities = {
        str(value or "").strip().casefold()
        for value in (site.get("site_key"), site.get("name"))
        if str(value or "").strip()
    }
    hosts = {
        _domain(str(value or "")).casefold()
        for value in (site.get("domain"), site.get("base_url"))
        if _domain(str(value or ""))
    }
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        entry_identities = {
            str(value or "").strip().casefold()
            for value in (entry.get("siteKey"), entry.get("name"))
            if str(value or "").strip()
        }
        entry_host = _domain(
            str(entry.get("siteUrl") or entry.get("apiBaseUrl") or "")
        ).casefold()
        if identities & entry_identities or (entry_host and entry_host in hosts):
            return entry
    return None


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
    value = str(value or "").strip().lower()
    return {
        "english": "en",
        "german": "de",
        "french": "fr",
        "spanish": "es",
    }.get(value, value or None)
