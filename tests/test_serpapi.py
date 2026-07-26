from __future__ import annotations

import pytest

from app.clients import serpapi
from app.clients.http_client import ExternalCallError, safe_log_url


@pytest.mark.asyncio
async def test_serpapi_classifies_exhausted_quota_without_putting_key_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    async def fake_request_json(method: str, url: str, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/search.json"):
            raise ExternalCallError(
                "serpapi status 429",
                status_code=429,
                retryable=False,
            )
        return {
            "plan_searches_left": 0,
            "total_searches_left": 0,
            "account_rate_limit_per_hour": 250,
            "this_hour_searches": 0,
            "plan_renewal_date": "2026-08-04",
        }

    monkeypatch.setattr(serpapi._settings, "serpapi_key", "super-secret-key")
    monkeypatch.setattr(serpapi, "request_json", fake_request_json)

    result = await serpapi.fetch_google_serp("safe test query")

    assert result["status"] == "fetch-failed"
    assert result["error_type"] == "quota_exhausted"
    assert result["http_status"] == 429
    assert result["retryable"] is False
    assert result["retry_after"] == "2026-08-04"
    assert calls[0][0] == "https://serpapi.com/search.json"
    assert calls[0][1]["params"]["api_key"] == "super-secret-key"
    assert "super-secret-key" not in calls[0][0]


def test_safe_log_url_removes_sensitive_query_values() -> None:
    value = safe_log_url(
        "https://example.test/search?q=guide&api_key=secret&access_token=also-secret&token=third-secret"
    )

    assert value == (
        "https://example.test/search?q=guide&api_key=%5BREDACTED%5D"
        "&access_token=%5BREDACTED%5D&token=%5BREDACTED%5D"
    )
    assert "secret" not in value
