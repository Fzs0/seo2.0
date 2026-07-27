"""PostgreSQL 17 release-gate tests.

Normal offline suites may skip this module. The release command sets
``SEO_PG17_GATE_REQUIRED=1`` so a missing disposable PG17 DSN is a hard failure.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.strategy_action_service import SQLActionStore, create_action


ROOT = Path(__file__).resolve().parents[1]


def _dsn() -> str:
    dsn = os.getenv("SEO_PG17_TEST_DSN", "")
    if not dsn:
        if os.getenv("SEO_PG17_GATE_REQUIRED") == "1":
            pytest.fail("PG17 release gate requires SEO_PG17_TEST_DSN")
        pytest.skip("set SEO_PG17_TEST_DSN to a disposable PostgreSQL 17 database")
    database = urlparse(dsn).path.lstrip("/").lower()
    if not any(marker in database for marker in ("test", "temp", "tmp")):
        pytest.fail("SEO_PG17_TEST_DSN must name a disposable test/temp/tmp database")
    return dsn


@pytest.mark.asyncio
async def test_pg17_full_migrations_and_repair_migrations_are_idempotent():
    connection = await asyncpg.connect(_dsn())
    try:
        version_num = int(await connection.fetchval("SHOW server_version_num"))
        assert version_num // 10000 == 17
        await connection.execute("DROP SCHEMA IF EXISTS social CASCADE")
        await connection.execute("DROP SCHEMA IF EXISTS seo_agent CASCADE")
        migrations = sorted((ROOT / "db" / "migrations").glob("*.sql"))
        for migration in migrations:
            await connection.execute(migration.read_text(encoding="utf-8"))
        for migration_name in (
            "031_invalidate_late_strategy_effect_baselines.sql",
            "032_classify_legacy_new_article_zero_baselines.sql",
        ):
            sql = (ROOT / "db" / "migrations" / migration_name).read_text(encoding="utf-8")
            await connection.execute(sql)
            await connection.execute(sql)
        assert await connection.fetchval("SELECT to_regclass('seo_agent.tasks')") is not None
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_same_scoped_idempotency_key_concurrently_creates_one_action():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    site_id = str(uuid4())
    business_id = "pg17-idempotency-test"
    run_id = str(uuid4())
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.sites
              (id, site_key, name, site_type, business_id, strategy_enabled)
            VALUES ($1, $2, 'PG17 test', 'main', $3, true)
            ON CONFLICT (site_key) DO NOTHING
            """,
            (uuid4(), f"pg17-{site_id}", business_id),
        )
        actual_site_id = (
            await connection.exec_driver_sql(
                "SELECT id FROM seo_agent.sites WHERE site_key=$1",
                (f"pg17-{site_id}",),
            )
        ).scalar_one()

    async def create_once():
        async with factory() as session:
            return await create_action(
                SQLActionStore(session),
                run_id=run_id,
                business_id=business_id,
                site_id=str(actual_site_id),
                action_type="update_article",
                idempotency_key="same-key",
            )

    first, second = await asyncio.gather(create_once(), create_once())
    assert first["action_id"] == second["action_id"]
    async with engine.begin() as connection:
        count = (
            await connection.exec_driver_sql(
                """
                SELECT count(*) FROM seo_agent.tasks
                 WHERE payload->>'kind'='strategy_action'
                   AND payload->>'business_id'=$1
                   AND payload->>'site_id'=$2
                   AND payload->>'idempotency_key'='same-key'
                """,
                (business_id, str(actual_site_id)),
            )
        ).scalar_one()
    assert count == 1
    await engine.dispose()
