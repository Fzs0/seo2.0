"""SerpApi 客户端：抓 Google SERP Top 10。

缺失 SERPAPI_KEY 时所有调用返回 configured=False，由调用方降级。
"""
from __future__ import annotations

import structlog

from app.clients.http_client import ExternalCallError, request_json
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
_settings = get_settings()
_TERMINAL_ERROR_TYPES = {"quota_exhausted", "authentication_failed"}


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
    try:
        data = await request_json(
            "GET",
            "https://serpapi.com/search.json",
            client_label="serpapi",
            params=params,
            timeout=30,
        )
        return {
            "configured": True,
            "keyword": keyword,
            "organic_results": data.get("organic_results", []),
            "related_questions": data.get("related_questions", []),
            "related_searches": data.get("related_searches", []),
            "status": "success",
        }
    except ExternalCallError as e:
        error_type, retry_after = await _classify_failure(e)
        logger.warning(
            "serpapi_failed",
            keyword=keyword,
            error_type=error_type,
            http_status=e.status_code,
            retryable=bool(e.retryable),
        )
        return {
            "configured": True,
            "keyword": keyword,
            "organic_results": [],
            "related_questions": [],
            "related_searches": [],
            "status": "fetch-failed",
            "error_type": error_type,
            "http_status": e.status_code,
            "retryable": False if error_type in _TERMINAL_ERROR_TYPES else bool(e.retryable),
            "retry_after": retry_after,
        }


async def _classify_failure(error: ExternalCallError) -> tuple[str, str | None]:
    if error.status_code in {401, 403}:
        return "authentication_failed", None
    if error.status_code == 429:
        try:
            account = await request_json(
                "GET",
                "https://serpapi.com/account.json",
                client_label="serpapi_account",
                params={"api_key": _settings.serpapi_key},
                timeout=15,
                max_attempts=1,
            )
        except ExternalCallError:
            return "rate_limited", None
        if int(account.get("plan_searches_left") or 0) <= 0 and int(
            account.get("total_searches_left") or 0
        ) <= 0:
            return "quota_exhausted", str(
                account.get("plan_renewal_date")
                or account.get("next_billing_date")
                or account.get("plan_reset_date")
                or ""
            ) or None
        return "rate_limited", None
    if error.status_code and error.status_code >= 500:
        return "provider_unavailable", None
    return "request_failed", None


def is_usable_serp_result(value: dict | None) -> bool:
    if not value:
        return False
    if value.get("status") == "fetch-failed" or value.get("error_type"):
        return False
    return bool(value.get("organic_results"))


def is_terminal_serp_failure(value: dict | None) -> bool:
    return bool(value and value.get("error_type") in _TERMINAL_ERROR_TYPES | {"rate_limited"})


__all__ = [
    "fetch_google_serp",
    "is_terminal_serp_failure",
    "is_usable_serp_result",
]
