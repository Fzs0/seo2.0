"""Disposable-PostgreSQL integration tests for strategy-effect repair migrations.

Set SEO_MIGRATION_TEST_POSTGRES_DSN to a PostgreSQL 17 database created solely
for tests. The fixture recreates the ``seo_agent`` schema and must never target
a development or production database.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_031 = ROOT / "db/migrations/031_invalidate_late_strategy_effect_baselines.sql"
MIGRATION_032 = ROOT / "db/migrations/032_classify_legacy_new_article_zero_baselines.sql"


def _configured_postgres_dsns() -> list[str | None]:
    raw_many = os.getenv("SEO_MIGRATION_TEST_POSTGRES_DSNS", "")
    if raw_many:
        parsed = (
            json.loads(raw_many)
            if raw_many.lstrip().startswith('["')
            else [item for item in raw_many.split(";") if item]
        )
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("SEO_MIGRATION_TEST_POSTGRES_DSNS must be a JSON string array")
        return parsed
    return [os.getenv("SEO_MIGRATION_TEST_POSTGRES_DSN") or None]


def _disposable_postgres_dsn(dsn: str | None) -> str:
    if not dsn:
        pytest.skip(
            "set SEO_MIGRATION_TEST_POSTGRES_DSN or "
            "SEO_MIGRATION_TEST_POSTGRES_DSNS to disposable PostgreSQL databases"
        )
    database = urlparse(dsn).path.lstrip("/").lower()
    if not any(marker in database for marker in ("test", "temp", "tmp")):
        pytest.fail(
            "SEO_MIGRATION_TEST_POSTGRES_DSN must name a disposable database "
            "containing test, temp, or tmp"
        )
    return dsn


@pytest.mark.asyncio
async def test_acceptance_database_is_postgresql_17():
    configured = _configured_postgres_dsns()
    if configured == [None]:
        pytest.skip("set a disposable PostgreSQL 17 DSN")
    majors: set[int] = set()
    for raw_dsn in configured:
        connection = await asyncpg.connect(_disposable_postgres_dsn(raw_dsn))
        try:
            majors.add(int(await connection.fetchval("SHOW server_version_num")) // 10000)
        finally:
            await connection.close()
    assert majors == {17}


@pytest_asyncio.fixture(params=_configured_postgres_dsns())
async def postgres(request):
    connection = await asyncpg.connect(_disposable_postgres_dsn(request.param))
    version = int(await connection.fetchval("SHOW server_version_num"))
    if version // 10000 != 17:
        await connection.close()
        pytest.fail(f"migration acceptance requires PostgreSQL 17, got {version}")
    await connection.execute("DROP SCHEMA IF EXISTS seo_agent CASCADE")
    await connection.execute("CREATE SCHEMA seo_agent")
    await connection.execute(
        """
        CREATE TABLE seo_agent.tasks (
          id uuid PRIMARY KEY,
          task_type text NOT NULL,
          target_url text,
          payload jsonb NOT NULL DEFAULT '{}'::jsonb,
          decision jsonb,
          updated_at timestamptz
        )
        """
    )
    try:
        yield connection
    finally:
        await connection.execute("DROP SCHEMA IF EXISTS seo_agent CASCADE")
        await connection.close()


async def _insert_task(connection, *, action: str, payload: dict, target_url: str | None):
    task_id = uuid4()
    full_payload = {"kind": "strategy_effect", "action": action, **payload}
    await connection.execute(
        """
        INSERT INTO seo_agent.tasks (id, task_type, target_url, payload, decision, updated_at)
        VALUES ($1, 'review', $2, $3::jsonb, '{"original": true}', '2026-07-01T00:00:00Z')
        """,
        task_id,
        target_url,
        json.dumps(full_payload),
    )
    return task_id


async def _create_legacy_backup_table(connection):
    await connection.execute(
        """
        CREATE TABLE seo_agent.strategy_effect_baseline_repair_backup (
          effect_task_id uuid PRIMARY KEY,
          original_target_url text,
          original_payload jsonb NOT NULL,
          original_decision jsonb,
          original_updated_at timestamptz,
          backed_up_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT legacy_task_archive_link
            FOREIGN KEY (effect_task_id)
            REFERENCES seo_agent.tasks(id)
            ON DELETE RESTRICT
        )
        """
    )


@pytest.mark.asyncio
async def test_031_skips_null_and_invalid_times_and_repairs_valid_late_baseline(postgres):
    await _create_legacy_backup_table(postgres)
    null_id = await _insert_task(
        postgres,
        action="update_article",
        target_url="https://example.test/null",
        payload={"published_at": None, "baseline": {"captured_at": None}},
    )
    invalid_id = await _insert_task(
        postgres,
        action="update_article",
        target_url="https://example.test/invalid",
        payload={
            "published_at": "not-a-time",
            "baseline": {"captured_at": "2026-99-99T25:61:00Z"},
        },
    )
    empty_id = await _insert_task(
        postgres,
        action="update_article",
        target_url="https://example.test/empty",
        payload={"published_at": "", "baseline": {"captured_at": ""}},
    )
    out_of_range_id = await _insert_task(
        postgres,
        action="update_article",
        target_url="https://example.test/out-of-range",
        payload={
            "published_at": "999999999999-01-01T00:00:00Z",
            "baseline": {"captured_at": "999999999999-01-02T00:00:00Z"},
        },
    )
    valid_id = await _insert_task(
        postgres,
        action="update_article",
        target_url="https://example.test/valid",
        payload={
            "published_at": "2026-07-10T00:00:00Z",
            "baseline": {"captured_at": "2026-07-11T00:00:00Z"},
        },
    )

    sql = MIGRATION_031.read_text(encoding="utf-8")
    await postgres.execute(sql)
    await postgres.execute(sql)

    rows = {
        row["id"]: {
            "payload": json.loads(row["payload_text"]),
            "decision": json.loads(row["decision_text"]),
        }
        for row in await postgres.fetch(
            "SELECT id, payload::text AS payload_text, "
            "decision::text AS decision_text FROM seo_agent.tasks ORDER BY id"
        )
    }
    assert "baseline_valid" not in rows[null_id]["payload"]
    assert "baseline_valid" not in rows[invalid_id]["payload"]
    assert "baseline_valid" not in rows[empty_id]["payload"]
    assert "baseline_valid" not in rows[out_of_range_id]["payload"]
    assert rows[valid_id]["payload"]["baseline_valid"] is False
    assert rows[valid_id]["payload"]["outcome"] == "inconclusive"
    assert rows[valid_id]["decision"]["reason"] == "late_baseline_capture"

    backups = await postgres.fetch(
        "SELECT effect_task_id, original_payload::text AS original_payload FROM "
        "seo_agent.strategy_effect_baseline_repair_backup"
    )
    assert len(backups) == 1
    assert backups[0]["effect_task_id"] == valid_id
    assert json.loads(backups[0]["original_payload"]).get("baseline_valid") is not False

    # A repair archive must not prevent later operational task cleanup.
    await postgres.execute("DELETE FROM seo_agent.tasks WHERE id = $1", valid_id)
    assert await postgres.fetchval(
        "SELECT count(*) FROM seo_agent.strategy_effect_baseline_repair_backup "
        "WHERE effect_task_id = $1",
        valid_id,
    ) == 1


@pytest.mark.asyncio
async def test_032_requires_published_at_backs_up_original_and_is_idempotent(postgres):
    await _create_legacy_backup_table(postgres)
    missing_time_id = await _insert_task(
        postgres,
        action="new_article",
        target_url="https://example.test/missing-time",
        payload={
            "published_at": None,
            "baseline_note": "New canonical URL did not exist before publication.",
            "baseline": {
                "metric_scope": {"gsc": "page", "ga4": "landing_page"},
                "gsc": {"clicks": 0, "impressions": 0, "avg_position": 0},
                "ga4": {"sessions": 0, "conversions": 0},
            },
        },
    )
    valid_id = await _insert_task(
        postgres,
        action="new_article",
        target_url="https://example.test/new",
        payload={
            "published_at": "2026-07-10T00:00:00Z",
            "baseline_note": "New canonical URL did not exist before publication.",
            "baseline": {
                "metric_scope": {"gsc": "page", "ga4": "landing_page"},
                "gsc": {"clicks": 0, "impressions": 0, "avg_position": 0},
                "ga4": {"sessions": 0, "conversions": 0},
            },
        },
    )

    # 032 is deliberately safe to run even when 031 has not run first.
    sql = MIGRATION_032.read_text(encoding="utf-8")
    await postgres.execute(sql)
    await postgres.execute(sql)

    missing_payload = json.loads(
        await postgres.fetchval(
            "SELECT payload::text FROM seo_agent.tasks WHERE id = $1", missing_time_id
        )
    )
    valid_payload = json.loads(
        await postgres.fetchval(
            "SELECT payload::text FROM seo_agent.tasks WHERE id = $1", valid_id
        )
    )
    assert "kind" not in missing_payload["baseline"]
    assert valid_payload["baseline"]["kind"] == "structural_zero"
    assert valid_payload["baseline"]["target_url"] == "https://example.test/new"
    assert valid_payload["baseline"]["effective_at"] == "2026-07-10T00:00:00Z"
    assert valid_payload["baseline_valid"] is True

    backups = await postgres.fetch(
        "SELECT effect_task_id, original_payload::text AS original_payload FROM "
        "seo_agent.strategy_effect_baseline_repair_backup"
    )
    assert len(backups) == 1
    assert backups[0]["effect_task_id"] == valid_id
    assert "kind" not in json.loads(backups[0]["original_payload"])["baseline"]

    # The shared historical archive must survive without blocking task cleanup.
    await postgres.execute("DELETE FROM seo_agent.tasks WHERE id = $1", valid_id)
    assert await postgres.fetchval(
        "SELECT count(*) FROM seo_agent.strategy_effect_baseline_repair_backup "
        "WHERE effect_task_id = $1",
        valid_id,
    ) == 1
