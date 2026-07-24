from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import serp_snapshot_service


class _MappingsResult:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self) -> "_MappingsResult":
        return self

    def first(self) -> dict | None:
        return self._row


class _Session:
    def __init__(self) -> None:
        self.executed: list[tuple[object, dict]] = []
        self.commits = 0

    async def execute(self, statement: object, params: dict) -> _MappingsResult:
        self.executed.append((statement, params))
        return _MappingsResult(
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "requested_at": datetime(2026, 7, 23, tzinfo=timezone.utc),
            }
        )

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_fetch_and_save_serp_snapshot_uses_existing_table_without_keyword_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(keyword: str, *, gl: str, hl: str) -> dict:
        assert (keyword, gl, hl) == ("titanium cookware care", "us", "en")
        return {
            "configured": True,
            "keyword": keyword,
            "organic_results": [{"position": 1, "link": "https://example.com"}],
            "related_questions": [{"question": "How do you clean titanium?"}],
            "related_searches": [{"query": "titanium pan care"}],
        }

    monkeypatch.setattr(serp_snapshot_service, "fetch_google_serp", fake_fetch)
    session = _Session()

    result = await serp_snapshot_service.fetch_and_save_serp_snapshot(
        session, keyword="  titanium   cookware care  ", gl="us", hl="en"
    )

    assert result["snapshot_id"] == "11111111-1111-1111-1111-111111111111"
    assert session.commits == 1
    assert len(session.executed) == 1
    statement, params = session.executed[0]
    assert "seo_agent.serp_snapshots" in str(statement)
    assert "keyword_id" not in str(statement)
    assert params["keyword"] == "titanium cookware care"
    assert params["count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "configured": False,
            "keyword": "query",
            "organic_results": [],
            "related_questions": [],
            "related_searches": [],
        },
        {
            "configured": True,
            "keyword": "query",
            "organic_results": [],
            "related_questions": [],
            "related_searches": [],
            "status": "fetch-failed",
        },
    ],
)
async def test_fetch_and_save_serp_snapshot_does_not_store_unavailable_calls(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
) -> None:
    async def fake_fetch(keyword: str, *, gl: str, hl: str) -> dict:
        return payload

    monkeypatch.setattr(serp_snapshot_service, "fetch_google_serp", fake_fetch)
    session = _Session()

    result = await serp_snapshot_service.fetch_and_save_serp_snapshot(
        session, keyword="query"
    )

    assert result["snapshot_id"] is None
    assert session.executed == []
    assert session.commits == 0
