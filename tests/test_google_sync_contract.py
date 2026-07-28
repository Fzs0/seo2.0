import pytest

from app.services import google_sync


class _Source:
    id = "source-1"
    default_start_date = None
    default_end_date = None

    def gsc_host(self):
        return "example.com"


class _Store:
    def get_by_id(self, source_id):
        return _Source() if source_id == "source-1" else None


class _Session:
    def __init__(self):
        self.poisoned = False
        self.rollbacks = 0

    async def rollback(self):
        self.poisoned = False
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_sync_source_records_gsc_failure_and_continues_ga4(monkeypatch):
    session = _Session()
    calls = []

    async def resolve_site_id(current_session, source):
        return "site-1"

    async def gsc(current_session, *args, **kwargs):
        calls.append("gsc")
        current_session.poisoned = True
        raise RuntimeError("value too long for type character varying(16)")

    async def ga4(current_session, *args, **kwargs):
        assert current_session.poisoned is False
        calls.append("ga4")
        return {"ok": True, "type": "ga4"}

    monkeypatch.setattr(google_sync, "get_store", lambda: _Store())
    monkeypatch.setattr(google_sync, "_resolve_site_id_by_domain", resolve_site_id)
    monkeypatch.setattr(google_sync, "_sync_gsc", gsc)
    monkeypatch.setattr(google_sync, "_sync_ga4", ga4)

    result = await google_sync.sync_source(
        session,
        "source-1",
        trigger="strategy_hold_refresh",
    )

    assert calls == ["gsc", "ga4"]
    assert session.rollbacks == 1
    assert result["results"][0]["type"] == "gsc"
    assert "character varying(16)" in result["results"][0]["error"]
    assert result["results"][1]["ok"] is True


@pytest.mark.asyncio
async def test_sync_source_rejects_unknown_trigger_before_database_work():
    with pytest.raises(ValueError, match="unsupported google sync trigger"):
        await google_sync.sync_source(
            _Session(),
            "source-1",
            trigger="not-a-real-trigger",
        )
