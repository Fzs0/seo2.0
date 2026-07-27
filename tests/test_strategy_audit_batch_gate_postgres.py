"""Real PostgreSQL acceptance test for refreshed audit-batch approval identity."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.strategy_execution import _validate_current_strategy


def _dsn() -> str:
    dsn = os.getenv("SEO_HOLD_TEST_POSTGRES_DSN", "")
    if not dsn:
        pytest.skip("set SEO_HOLD_TEST_POSTGRES_DSN to a disposable PostgreSQL database")
    database = urlparse(dsn).path.lstrip("/").lower()
    if not any(marker in database for marker in ("test", "temp", "tmp")):
        pytest.fail("SEO_HOLD_TEST_POSTGRES_DSN must name a disposable test database")
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_only_strategy_referencing_latest_refreshed_audit_batch_can_pass_gate():
    engine = create_async_engine(_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("DROP SCHEMA IF EXISTS seo_agent CASCADE")
        await connection.exec_driver_sql("CREATE SCHEMA seo_agent")
        for ddl in (
            """
            CREATE TABLE seo_agent.tasks (
              id uuid PRIMARY KEY,
              task_type text NOT NULL,
              status text NOT NULL,
              priority text NOT NULL DEFAULT 'P2',
              title text NOT NULL DEFAULT '',
              payload jsonb NOT NULL DEFAULT '{}'::jsonb,
              decision jsonb NOT NULL DEFAULT '{}'::jsonb,
              keyword_id uuid,
              post_id uuid,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE seo_agent.keywords (
              id uuid PRIMARY KEY, business_id text, assigned_site_id uuid
            )
            """,
            "CREATE TABLE seo_agent.posts (id uuid PRIMARY KEY, site_id uuid)",
            """
            CREATE TABLE seo_agent.post_analyses (
              id uuid PRIMARY KEY, post_id uuid, analyzed_at timestamptz
            )
            """,
        ):
            await connection.exec_driver_sql(ddl)

    business_id = str(uuid4())
    site_id = str(uuid4())
    audit_a, audit_b = str(uuid4()), str(uuid4())
    analysis_a, analysis_b = str(uuid4()), str(uuid4())
    candidate_a, candidate_b = str(uuid4()), str(uuid4())
    plan_a, plan_b = str(uuid4()), str(uuid4())
    scanned_a = datetime.now(UTC) - timedelta(days=1)
    scanned_b = datetime.now(UTC)

    async with engine.begin() as connection:
        for audit_id, scanned_at, created_offset in (
            (audit_a, scanned_a, -4),
            (audit_b, scanned_b, -3),
        ):
            await connection.exec_driver_sql(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, payload, created_at)
                VALUES ($1, 'review', 'done',
                        jsonb_build_object(
                          'kind', 'content_audit_batch',
                          'business_id', $2::text,
                          'scanned_at', $3::text
                        ),
                        now() + make_interval(secs => $4::int))
                """,
                (UUID(audit_id), business_id, scanned_at.isoformat(), created_offset),
            )
        for analysis_id, audit_id, scanned_at, created_offset in (
            (analysis_a, audit_a, scanned_a, -2),
            (analysis_b, audit_b, scanned_b, -1),
        ):
            await connection.exec_driver_sql(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, payload, created_at)
                VALUES ($1, 'review', 'done',
                        jsonb_build_object(
                          'kind', 'strategy_analysis_batch',
                          'business_id', $2::text,
                          'source_audit_batch_id', $3::text,
                          'source_audit_scanned_at', $4::text
                        ),
                        now() + make_interval(secs => $5::int))
                """,
                (
                    UUID(analysis_id),
                    business_id,
                    audit_id,
                    scanned_at.isoformat(),
                    created_offset,
                ),
            )
        for candidate_id, analysis_id, fingerprint in (
            (candidate_a, analysis_a, "strategy-a"),
            (candidate_b, analysis_b, "strategy-b"),
        ):
            await connection.exec_driver_sql(
                """
                INSERT INTO seo_agent.tasks (id, task_type, status, payload)
                VALUES ($1, 'review', 'done',
                        jsonb_build_object(
                          'kind', 'strategy_candidate',
                          'business_id', $2::text,
                          'analysis_batch_id', $3::text,
                          'strategy_fingerprint', $4::text,
                          'evidence_fingerprint', $5::text
                        ))
                """,
                (
                    UUID(candidate_id),
                    business_id,
                    analysis_id,
                    fingerprint,
                    f"evidence-{fingerprint}",
                ),
            )
        for plan_id, analysis_id, candidate_id, created_offset in (
            (plan_a, analysis_a, candidate_a, -1),
            (plan_b, analysis_b, candidate_b, 0),
        ):
            await connection.exec_driver_sql(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, payload, decision, created_at)
                VALUES (
                  $1, 'review', 'queued',
                  jsonb_build_object(
                    'kind', 'strategy_plan',
                    'business_id', $2::text,
                    'analysis_batch_id', $3::text
                  ),
                  jsonb_build_object(
                    'plan_date', (now() AT TIME ZONE 'Asia/Shanghai')::date::text,
                    'selected_candidate_ids', jsonb_build_array($4::text)
                  ),
                  now() + make_interval(secs => $5::int)
                )
                """,
                (
                    UUID(plan_id),
                    business_id,
                    analysis_id,
                    candidate_id,
                    created_offset,
                ),
            )

    def row(candidate_id: str, plan_id: str, analysis_id: str) -> dict:
        return {
            "site_status": "active",
            "strategy_enabled": True,
            "business_id": business_id,
            "task_business_id": business_id,
            "candidate_id": candidate_id,
            "plan_id": plan_id,
            "analysis_batch_id": analysis_id,
            "keyword_id": None,
            "site_id": site_id,
            "post_id": None,
        }

    async with factory() as session:
        with pytest.raises(ValueError):
            await _validate_current_strategy(
                session,
                row=row(candidate_a, plan_a, analysis_a),
                decision={
                    "strategy_type": "new_article",
                    "scope_key": "scope-a",
                    "strategy_fingerprint": "strategy-a",
                    "evidence_fingerprint": "evidence-strategy-a",
                },
            )
        await session.rollback()
        await _validate_current_strategy(
            session,
            row=row(candidate_b, plan_b, analysis_b),
            decision={
                "strategy_type": "new_article",
                "scope_key": "scope-b",
                "strategy_fingerprint": "strategy-b",
                "evidence_fingerprint": "evidence-strategy-b",
            },
        )

    async with engine.begin() as connection:
        await connection.exec_driver_sql("DROP SCHEMA IF EXISTS seo_agent CASCADE")
    await engine.dispose()
