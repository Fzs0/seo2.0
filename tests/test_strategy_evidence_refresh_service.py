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
    async def execute(self, statement, params=None):
        return _Rows([{"id": "connector-1", "site_id": "site-1"}])


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
