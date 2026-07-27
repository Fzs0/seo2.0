"""Real PostgreSQL concurrency tests for the Hold coordination state machine."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.strategy_hold_service import (
    claim_hold_evidence_refresh,
    complete_hold_evidence_refresh,
    finalize_hold_run,
    wait_for_hold_evidence_refresh,
)


def _dsn() -> str:
    dsn = os.getenv("SEO_HOLD_TEST_POSTGRES_DSN", "")
    if not dsn:
        pytest.skip("set SEO_HOLD_TEST_POSTGRES_DSN to a disposable PostgreSQL database")
    database = urlparse(dsn).path.lstrip("/").lower()
    if not any(marker in database for marker in ("test", "temp", "tmp")):
        pytest.fail("SEO_HOLD_TEST_POSTGRES_DSN must name a disposable test database")
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_concurrent_hold_runs_refresh_once_count_every_run_and_emit_one_stagnation():
    engine = create_async_engine(_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("DROP SCHEMA IF EXISTS seo_agent CASCADE")
        await connection.exec_driver_sql("CREATE SCHEMA seo_agent")
        await connection.exec_driver_sql(
            """
            CREATE TABLE seo_agent.tasks (
              id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
              task_type text NOT NULL,
              status text NOT NULL,
              priority text NOT NULL,
              title text NOT NULL,
              payload jsonb NOT NULL DEFAULT '{}'::jsonb,
              decision jsonb NOT NULL DEFAULT '{}'::jsonb,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )

    business_id = str(uuid4())
    run_ids = [str(uuid4()), str(uuid4()), str(uuid4())]

    async def claim(run_id: str):
        async with factory() as session:
            return await claim_hold_evidence_refresh(
                session, business_id=business_id, run_id=run_id
            )

    claims = list(await __import__("asyncio").gather(*(claim(run) for run in run_ids)))
    refresh_claims = [item for item in claims if item["should_refresh"]]
    assert len(refresh_claims) == 1
    wait_claims = [item for item in claims if item["wait_for_refresh"]]
    assert len(wait_claims) == 1
    refresh_token = refresh_claims[0]["refresh_token"]
    assert refresh_token

    async def wait_for_refresh():
        async with factory() as session:
            return await wait_for_hold_evidence_refresh(
                session,
                business_id=business_id,
                refresh_token=refresh_token,
                timeout_seconds=5,
            )

    waiter = __import__("asyncio").create_task(wait_for_refresh())
    async with factory() as session:
        assert await complete_hold_evidence_refresh(
            session,
            business_id=business_id,
            refresh_token=refresh_token,
            refresh_results=[{"source": "gsc", "status": "fresh", "snapshot_id": "gsc-1"}],
        )
    waited_results = await waiter
    assert waited_results[0]["snapshot_id"] == "gsc-1"
    async with factory() as session:
        assert not await complete_hold_evidence_refresh(
            session,
            business_id=business_id,
            refresh_token=refresh_token,
            refresh_results=[],
        )

    async def finalize(run_id: str):
        async with factory() as session:
            return await finalize_hold_run(
                session,
                business_id=business_id,
                run_id=run_id,
                all_hold=True,
                site_hold_flags={"site-a": True},
            )

    results = await __import__("asyncio").gather(*(finalize(run) for run in run_ids))
    assert sorted(item["business_consecutive_hold_count"] for item in results) == [1, 2, 3]
    assert sum(bool(item["emit_strategy_stagnation"]) for item in results) == 1

    # Retrying an already registered run is idempotent: no count or anomaly duplication.
    async with factory() as session:
        retry = await finalize_hold_run(
            session,
            business_id=business_id,
            run_id=run_ids[2],
            all_hold=True,
            site_hold_flags={"site-a": True},
        )
    assert retry["business_consecutive_hold_count"] == 3
    assert retry["emit_strategy_stagnation"] is False

    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                """
                SELECT decision FROM seo_agent.tasks
                WHERE payload->>'kind' = 'strategy_hold_coordination'
                  AND payload->>'business_id' = :business_id
                """
                ),
                {"business_id": business_id},
            )
        ).first()
        state = row[0]
        assert state["business_consecutive_hold_count"] == 3
        assert state["site_consecutive_hold_counts"]["site-a"] == 3
        assert set(state["registered_run_ids"]) == set(run_ids)
        assert state["refresh"]["status"] == "completed"
        assert state["refresh"]["results"][0]["snapshot_id"] == "gsc-1"
        assert len(state["stagnation_emitted_for_counts"]) == 1
        stagnation_count = (
            await connection.execute(
                text(
                    """
                    SELECT count(*) FROM seo_agent.tasks
                     WHERE payload->>'kind' = 'strategy_stagnation'
                       AND payload->>'business_id' = :business_id
                    """
                ),
                {"business_id": business_id},
            )
        ).scalar_one()
        assert stagnation_count == 1

    # A worker that dies after claiming a refresh cannot leave the business
    # permanently pending. Once the lease expires, exactly one later run
    # reclaims ordinal two with a new token; the stale token stays invalid.
    reset_run_id = str(uuid4())
    async with factory() as session:
        reset = await finalize_hold_run(
            session,
            business_id=business_id,
            run_id=reset_run_id,
            all_hold=False,
            site_hold_flags={"site-a": False},
        )
    assert reset["business_consecutive_hold_count"] == 0

    lease_start = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    first_lease_run = str(uuid4())
    stale_refresh_run = str(uuid4())
    recovery_run = str(uuid4())
    async with factory() as session:
        first_claim = await claim_hold_evidence_refresh(
            session,
            business_id=business_id,
            run_id=first_lease_run,
            now=lease_start,
            refresh_lease_seconds=1,
        )
    assert first_claim["should_refresh"] is False
    async with factory() as session:
        stale_claim = await claim_hold_evidence_refresh(
            session,
            business_id=business_id,
            run_id=stale_refresh_run,
            now=lease_start,
            refresh_lease_seconds=1,
        )
    assert stale_claim["should_refresh"] is True
    stale_token = stale_claim["refresh_token"]

    async with factory() as session:
        recovered_claim = await claim_hold_evidence_refresh(
            session,
            business_id=business_id,
            run_id=recovery_run,
            now=lease_start + timedelta(seconds=2),
            refresh_lease_seconds=1,
        )
    assert recovered_claim["should_refresh"] is True
    assert recovered_claim["refresh_token"] != stale_token
    async with factory() as session:
        assert not await complete_hold_evidence_refresh(
            session,
            business_id=business_id,
            refresh_token=stale_token,
            refresh_results=[],
        )
        assert await complete_hold_evidence_refresh(
            session,
            business_id=business_id,
            refresh_token=recovered_claim["refresh_token"],
            refresh_results=[
                {"source": "serp", "status": "fresh", "snapshot_id": "serp-recovery"}
            ],
        )

    async with engine.begin() as connection:
        await connection.exec_driver_sql("DROP SCHEMA IF EXISTS seo_agent CASCADE")
    await engine.dispose()
