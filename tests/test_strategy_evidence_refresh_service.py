import pytest

from app.services.strategy_evidence_refresh_service import (
    refresh_default_strategy_evidence,
)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self):
        self.poisoned = False
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        if self.poisoned:
            raise RuntimeError("current transaction is aborted")
        return _Rows([{"id": "connector-1", "site_id": "site-1"}])

    async def rollback(self):
        self.poisoned = False
        self.rollbacks += 1


class _Source:
    id = "google-1"

    def gsc_host(self):
        return "example.com"


class _Store:
    def list_all(self):
        return [_Source()]


@pytest.mark.asyncio
async def test_default_second_hold_refreshes_real_read_boundaries(monkeypatch):
    calls = []

    async def google(session, source_id, **kwargs):
        calls.append(("google", source_id))
        return {
            "ok": True,
            "results": [
                {"ok": True, "type": "gsc", "log_id": "gsc-log"},
                {"ok": True, "type": "ga4", "log_id": "ga4-log"},
            ],
        }

    async def content(session, **kwargs):
        calls.append(("content", kwargs["business_id"]))
        return {
            "scanned_at": "2026-07-27T00:00:00+00:00",
            "sync": {"ok": True},
            "data_sources": {"serp": {"usable": 1}},
            "items": [
                {
                    "site_id": "site-1",
                    "action": "new_article",
                }
            ],
        }

    async def product(session, connector_id):
        calls.append(("products", connector_id))
        return {"ok": True, "items_upserted": 2}

    async def collections(session, connector_id):
        calls.append(("collections", connector_id))
        return {"ok": True, "collections_upserted": 1}

    async def home(session, connector_id):
        calls.append(("home", connector_id))
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.get_store", lambda: _Store()
    )
    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.sync_source", google
    )
    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.scan_content", content
    )
    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.sync_connector_products",
        product,
    )
    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.sync_oemapps_collections",
        collections,
    )
    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.sync_oemapps_home_seo", home
    )

    result = await refresh_default_strategy_evidence(
        _Session(),
        business_id="business-1",
        sites=[
            {
                "id": "site-1",
                "domain": "www.example.com",
                "site_type": "oemapps",
            }
        ],
    )

    sources = result[0]["sources"]
    assert sources["gsc"]["snapshot_id"] == "gsc-log"
    assert sources["ga4"]["snapshot_id"] == "ga4-log"
    assert sources["products"]["status"] == "fresh"
    assert sources["collections"]["status"] == "fresh"
    assert sources["articles"]["status"] == "fresh"
    assert sources["live_serp"]["status"] == "fresh"
    assert sources["on_page"]["status"] == "fresh"
    assert sources["content_gap"]["detail"]["new_article_candidates"] == 1
    assert {name for name, _ in calls} == {
        "google",
        "content",
        "products",
        "collections",
        "home",
    }


@pytest.mark.asyncio
async def test_google_failure_rolls_back_before_later_sources_and_sites(monkeypatch):
    calls = []
    session = _Session()
    failed_once = False

    async def google(current_session, source_id, **kwargs):
        nonlocal failed_once
        calls.append(("google", source_id))
        if source_id == "google-1" and not failed_once:
            failed_once = True
            current_session.poisoned = True
            raise RuntimeError("value too long for type character varying(16)")
        return {
            "ok": True,
            "results": [
                {"ok": True, "type": "gsc", "log_id": "gsc-2"},
                {"ok": True, "type": "ga4", "log_id": "ga4-2"},
            ],
        }

    async def content(current_session, **kwargs):
        await current_session.execute("SELECT 1")
        calls.append(("content", kwargs["business_id"]))
        return {"scanned_at": "2026-07-27T00:00:00+00:00", "items": []}

    class TwoSources:
        def list_all(self):
            first = _Source()
            class SecondSource(_Source):
                id = "google-2"

                def gsc_host(self):
                    return "other.example"

            second = SecondSource()
            return [first, second]

    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.get_store", lambda: TwoSources()
    )
    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.sync_source", google
    )
    monkeypatch.setattr(
        "app.services.strategy_evidence_refresh_service.scan_content", content
    )

    result = await refresh_default_strategy_evidence(
        session,
        business_id="business-1",
        sites=[
            {"id": "site-1", "domain": "example.com", "site_type": "other"},
            {"id": "site-2", "domain": "other.example", "site_type": "other"},
        ],
    )

    assert session.rollbacks == 1
    assert calls.count(("google", "google-2")) == 1
    assert ("content", "business-1") in calls
    assert "character varying(16)" in result[0]["sources"]["gsc"]["error"]
    assert result[1]["sources"]["ga4"]["status"] == "fresh"
