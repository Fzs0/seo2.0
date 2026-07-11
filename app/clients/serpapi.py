"""SerpApi 客户端：抓 Google SERP Top 10。

缺失 SERPAPI_KEY 时所有调用返回 configured=False，由调用方降级。
"""
from __future__ import annotations

import urllib.parse

import structlog

from app.clients.http_client import ExternalCallError, request_json
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
_settings = get_settings()


async def fetch_google_serp(
    keyword: str,
    *,
    gl: str = "us",
    hl: str = "en",
) -> dict:
    """返回 SerpApi Google organic_results / paa / related。失败时返回空结构。"""
    if not _settings.serpapi_key:
        return {"configured": False, "keyword": keyword, "organic_results": [], "related_questions": [], "related_searches": []}
    params = {"q": keyword, "gl": gl, "hl": hl, "api_key": _settings.serpapi_key, "num": 10}
    url = f"https://serpapi.com/search.json?{urllib.parse.urlencode(params)}"
    try:
        data = await request_json("GET", url, client_label="serpapi", timeout=30)
        return {
            "configured": True,
            "keyword": keyword,
            "organic_results": data.get("organic_results", []),
            "related_questions": data.get("related_questions", []),
            "related_searches": data.get("related_searches", []),
        }
    except ExternalCallError as e:
        logger.warning("serpapi_failed", keyword=keyword, error=str(e))
        return {"configured": True, "keyword": keyword, "organic_results": [], "related_questions": [], "related_searches": [], "status": "fetch-failed"}