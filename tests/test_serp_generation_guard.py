from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import article_generation_service


class _Result:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict | None:
        return self._row


class _Session:
    def __init__(self) -> None:
        self.calls = 0
        self.commits = 0

    async def execute(self, statement: object, _params: dict) -> _Result:
        self.calls += 1
        if str(statement).lstrip().startswith("SELECT"):
            return _Result(
                {
                    "id": "failed-snapshot",
                    "organic_results": [],
                    "related_questions": [],
                    "related_searches": [],
                    "requested_at": datetime(2026, 7, 26, tzinfo=timezone.utc),
                    "raw": {
                        "status": "fetch-failed",
                        "error_type": "quota_exhausted",
                    },
                }
            )
        return _Result({"id": "new-failure-snapshot"})

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_failed_serp_snapshot_does_not_block_a_future_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetches = 0

    async def fetch(_keyword: str, *, gl: str, hl: str) -> dict:
        nonlocal fetches
        fetches += 1
        assert (gl, hl) == ("us", "en")
        return {
            "configured": True,
            "status": "fetch-failed",
            "error_type": "quota_exhausted",
            "organic_results": [],
            "related_questions": [],
            "related_searches": [],
        }

    monkeypatch.setattr(article_generation_service, "fetch_google_serp", fetch)
    session = _Session()

    result = await article_generation_service._latest_or_fetch_serp(
        session,  # type: ignore[arg-type]
        {"id": "keyword-id", "keyword": "test query", "google_gl": "us", "google_hl": "en"},
    )

    assert fetches == 1
    assert result["source"] == "serpapi"
    assert result["status"] == "fetch-failed"
    assert result["error_type"] == "quota_exhausted"
