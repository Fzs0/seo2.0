import os
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services import google_sync


ROOT = Path(__file__).resolve().parents[1]


def _dsn() -> str:
    dsn = os.getenv("SEO_PG17_TEST_DSN", "")
    if not dsn:
        pytest.skip("set SEO_PG17_TEST_DSN to a disposable PostgreSQL 17 database")
    database = urlparse(dsn).path.lstrip("/").lower()
    if not any(marker in database for marker in ("test", "temp", "tmp")):
        pytest.fail("SEO_PG17_TEST_DSN must name a disposable test database")
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_google_sync_trigger_migration_is_idempotent_on_pg17():
    dsn = _dsn().replace("postgresql+asyncpg://", "postgresql://", 1)
    migration = (
        ROOT / "db" / "migrations" / "033_google_sync_trigger_contract.sql"
    ).read_text(encoding="utf-8")
    connection = await asyncpg.connect(dsn)
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute("CREATE SCHEMA IF NOT EXISTS seo_agent")
        await connection.execute(
            """
                CREATE TABLE IF NOT EXISTS seo_agent.google_sync_log (
                  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                  source_id uuid NOT NULL,
                  source_type varchar(8) NOT NULL,
                  range_start date NOT NULL,
                  range_end date NOT NULL,
                  trigger varchar(16) NOT NULL DEFAULT 'manual',
                  CONSTRAINT google_sync_log_trigger_chk
                    CHECK (trigger IN ('manual', 'scheduled', 'retry'))
                )
                """
        )
        await connection.execute(migration)
        await connection.execute(migration)
        await connection.execute(
            "INSERT INTO seo_agent.google_sync_log "
                "(source_id, source_type, range_start, range_end, trigger) "
                "VALUES ('00000000-0000-0000-0000-000000000001', "
                "'gsc', DATE '2026-07-01', DATE '2026-07-02', "
                "'strategy_hold_refresh')"
        )
        data_type = await connection.fetchval(
            """
                SELECT data_type
                  FROM information_schema.columns
                 WHERE table_schema='seo_agent'
                   AND table_name='google_sync_log'
                   AND column_name='trigger'
                """
        )
        assert data_type == "text"
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "INSERT INTO seo_agent.google_sync_log "
                    "(source_id, source_type, range_start, range_end, trigger) "
                    "VALUES ('00000000-0000-0000-0000-000000000001', "
                    "'gsc', DATE '2026-07-01', DATE '2026-07-02', "
                    "'unknown_trigger')"
            )
    finally:
        await transaction.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_failed_gsc_transaction_does_not_block_ga4_on_pg17(monkeypatch):
    engine = create_async_engine(_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    calls = []

    class Source:
        id = "source-1"
        default_start_date = None
        default_end_date = None

        def gsc_host(self):
            return "example.com"

    class Store:
        def get_by_id(self, source_id):
            return Source()

    async def resolve_site_id(session, source):
        return "00000000-0000-0000-0000-000000000001"

    async def fail_gsc(session, *args, **kwargs):
        calls.append("gsc")
        await session.execute(text("SELECT 1 / 0"))

    async def pass_ga4(session, *args, **kwargs):
        calls.append("ga4")
        assert await session.scalar(text("SELECT 1")) == 1
        return {"ok": True, "type": "ga4"}

    monkeypatch.setattr(google_sync, "get_store", lambda: Store())
    monkeypatch.setattr(google_sync, "_resolve_site_id_by_domain", resolve_site_id)
    monkeypatch.setattr(google_sync, "_sync_gsc", fail_gsc)
    monkeypatch.setattr(google_sync, "_sync_ga4", pass_ga4)

    async with factory() as session:
        result = await google_sync.sync_source(
            session,
            "source-1",
            trigger=google_sync.GOOGLE_SYNC_TRIGGER_STRATEGY_HOLD_REFRESH,
        )
        assert await session.scalar(text("SELECT 1")) == 1

    assert calls == ["gsc", "ga4"]
    assert result["results"][0]["ok"] is False
    assert result["results"][1]["ok"] is True
    await engine.dispose()
