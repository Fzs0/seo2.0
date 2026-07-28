from __future__ import annotations

from datetime import date

import pytest

from app.clients.google_analytics import GA4Client, GA4ClientError
from app.core.google_config import GoogleSource, ServiceAccount
from app.services import google_sync


def _source(*, hostnames: tuple[str, ...] = ()) -> GoogleSource:
    return GoogleSource(
        id="source-id",
        name="Exdivo",
        gsc_site_url="https://exdivo.com/",
        ga4_property_id="123456",
        google_proxy_url="",
        default_start_date="",
        default_end_date="",
        updated_at="",
        service_account=ServiceAccount(
            type="service_account",
            project_id="project",
            private_key_id="key-id",
            private_key="private-key",
            client_email="service@example.com",
        ),
        ga4_hostnames=hostnames,
    )


def test_ga4_hostname_allowlist_defaults_to_gsc_apex_and_www() -> None:
    assert _source().ga4_hosts() == ("exdivo.com", "www.exdivo.com")


def test_ga4_hostname_allowlist_normalizes_explicit_values() -> None:
    source = _source(
        hostnames=("WWW.Exdivo.com.", "exdivo.com", "exdivo.jcysaas.cn")
    )

    assert source.ga4_hosts() == (
        "www.exdivo.com",
        "exdivo.com",
        "exdivo.jcysaas.cn",
    )


@pytest.mark.asyncio
async def test_ga4_request_is_always_filtered_to_source_hostnames() -> None:
    captured: dict = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict:
            return {"rows": []}

    class HttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url, **kwargs):
            captured.update(kwargs["json"])
            return Response()

    client = GA4Client(_source())

    async def token() -> str:
        return "token"

    client.get_access_token = token  # type: ignore[method-assign]
    client._httpx = lambda timeout=30.0: HttpClient()  # type: ignore[method-assign]

    await client.run_report("2026-07-01", "2026-07-26")

    assert captured["dimensionFilter"] == {
        "filter": {
            "fieldName": "hostName",
            "inListFilter": {
                "values": ["exdivo.com", "www.exdivo.com"],
                "caseSensitive": False,
            },
        }
    }


@pytest.mark.asyncio
async def test_ga4_rejects_truncated_reports() -> None:
    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict:
            return {"rowCount": 100001, "rows": []}

    class HttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url, **_kwargs):
            return Response()

    client = GA4Client(_source())

    async def token() -> str:
        return "token"

    client.get_access_token = token  # type: ignore[method-assign]
    client._httpx = lambda timeout=30.0: HttpClient()  # type: ignore[method-assign]

    with pytest.raises(GA4ClientError, match="row limit"):
        await client.run_report("2026-07-01", "2026-07-26")


class _Result:
    def scalar_one(self) -> int:
        return 1


class _Session:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_ga4_sync_replaces_existing_rows_in_the_filtered_date_range(
    monkeypatch,
) -> None:
    calls: list[dict] = []

    class Client:
        def __init__(self, source):
            assert source.ga4_hosts() == ("exdivo.com", "www.exdivo.com")

        async def run_report(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("landing_page_breakdown"):
                return [
                    {
                        "date": "2026-07-26",
                        "landing_page": "/",
                        "sessions": 3,
                        "total_users": 2,
                        "pageviews": 4,
                    }
                ]
            return [
                {
                    "date": "2026-07-26",
                    "channel": "organic" if kwargs.get("channel_breakdown") else "all",
                    "sessions": 3,
                    "total_users": 2,
                    "pageviews": 4,
                }
            ]

    monkeypatch.setattr(google_sync, "GA4Client", Client)
    session = _Session()

    result = await google_sync._sync_ga4(
        session,  # type: ignore[arg-type]
        _source(),
        "00000000-0000-0000-0000-000000000001",
        start_date="2026-05-01",
        end_date="2026-07-26",
        trigger="test",
    )

    sql = "\n".join(statement for statement, _ in session.statements)
    assert result["ok"] is True
    assert len(calls) == 3
    assert "DELETE FROM seo_agent.ga4_session_daily" in sql
    assert "DELETE FROM seo_agent.ga4_landing_page_daily" in sql
    assert sql.index("DELETE FROM seo_agent.ga4_session_daily") < sql.index(
        "INSERT INTO seo_agent.ga4_session_daily"
    )
    assert sql.index("DELETE FROM seo_agent.ga4_landing_page_daily") < sql.index(
        "INSERT INTO seo_agent.ga4_landing_page_daily"
    )
    delete_params = next(
        params
        for statement, params in session.statements
        if "DELETE FROM seo_agent.ga4_session_daily" in statement
    )
    assert delete_params["start_date"] == date(2026, 5, 1)
    assert delete_params["end_date"] == date(2026, 7, 26)


@pytest.mark.asyncio
async def test_ga4_landing_failure_preserves_existing_landing_rows(monkeypatch) -> None:
    class Client:
        def __init__(self, _source):
            pass

        async def run_report(self, **kwargs):
            if kwargs.get("landing_page_breakdown"):
                raise GA4ClientError("landing report unavailable")
            return []

    monkeypatch.setattr(google_sync, "GA4Client", Client)
    session = _Session()

    result = await google_sync._sync_ga4(
        session,  # type: ignore[arg-type]
        _source(),
        "00000000-0000-0000-0000-000000000001",
        start_date="2026-05-01",
        end_date="2026-07-26",
        trigger="test",
    )

    sql = "\n".join(statement for statement, _ in session.statements)
    assert result["ok"] is False
    assert result["error"] == "landing report unavailable"
    assert "DELETE FROM seo_agent.ga4_session_daily" not in sql
    assert "DELETE FROM seo_agent.ga4_landing_page_daily" not in sql


@pytest.mark.asyncio
async def test_ga4_nonempty_overview_with_empty_landing_report_is_rejected(
    monkeypatch,
) -> None:
    class Client:
        def __init__(self, _source):
            pass

        async def run_report(self, **kwargs):
            if kwargs.get("landing_page_breakdown"):
                return []
            return [{"date": "2026-07-26", "channel": "all", "sessions": 3}]

    monkeypatch.setattr(google_sync, "GA4Client", Client)
    session = _Session()

    result = await google_sync._sync_ga4(
        session,  # type: ignore[arg-type]
        _source(),
        "00000000-0000-0000-0000-000000000001",
        start_date="2026-05-01",
        end_date="2026-07-26",
        trigger="test",
    )

    sql = "\n".join(statement for statement, _ in session.statements)
    assert result["ok"] is False
    assert result["error"] == "GA4 landing report is empty while overview has data"
    assert "DELETE FROM seo_agent.ga4_session_daily" not in sql
    assert "DELETE FROM seo_agent.ga4_landing_page_daily" not in sql
