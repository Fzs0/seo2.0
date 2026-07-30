"""PostgreSQL 17 release-gate tests.

Normal offline suites may skip this module. The release command sets
``SEO_PG17_GATE_REQUIRED=1`` so a missing disposable PG17 DSN is a hard failure.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.content_audit_service as content_audit_service
import app.services.post_sync_service as post_sync_service
import app.services.publish_service as publish_service
from app.clients.publishers import PublishResult
from app.services.content_audit_service import start_content_audit
from app.core.time_values import require_aware_datetime
from app.services.strategy_action_service import (
    SQLActionStore,
    approve_action,
    create_action,
    execute_action,
    execute_action_and_reconcile,
    preview_action,
)
from app.services.post_sync_service import sync_site_posts
from app.services.strategy_run_service import (
    cancel_strategy_run,
    create_strategy_run,
    get_strategy_run,
    list_strategy_runs,
    retry_strategy_run,
    run_strategy_run,
    submit_strategy_run_local_options,
)
from app.services.strategy_service import _replace_strategy_plan


ROOT = Path(__file__).resolve().parents[1]


async def _seed_pg17_action_lineage(engine, *, action_type: str = "product_seo"):
    ids = {
        "business_id": f"pg17-action-{uuid4()}",
        "site_id": uuid4(),
        "run_id": uuid4(),
        "plan_id": uuid4(),
        "strategy_id": uuid4(),
    }
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.sites
              (id, site_key, name, site_type, business_id, strategy_enabled,
               status, domain, base_url, market, language_code)
            VALUES ($1::uuid, $2, 'PG17 Action', 'main', $3, true, 'active',
                    $4, $5, 'US', 'en')
            """,
            (
                ids["site_id"],
                f"pg17-action-{ids['site_id']}",
                ids["business_id"],
                f"{ids['site_id']}.test",
                f"https://{ids['site_id']}.test",
            ),
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.tasks
              (id, task_type, status, priority, site_id, title, payload, decision)
            VALUES
              ($1::uuid, 'review', 'running', 'P1', NULL, 'run',
               jsonb_build_object(
                 'kind','strategy_run','business_id',$4::text
               ),
               '{"status":"awaiting_approval","current_stage":"awaiting_approval"}'::jsonb),
              ($2::uuid, 'review', 'queued', 'P1', NULL, 'plan',
               jsonb_build_object(
                 'kind','strategy_plan','business_id',$4::text,
                 'strategy_run_id',$1::text
               ),
               jsonb_build_object(
                 'strategy_task_ids',jsonb_build_array($3::text),
                 'execute_now_strategy_ids',jsonb_build_array($3::text)
               )),
              ($3::uuid, 'review', 'queued', 'P1', $5::uuid, 'strategy',
               jsonb_build_object(
                 'kind','seo_strategy','business_id',$4::text,
                 'strategy_run_id',$1::text,'plan_id',$2::text,
                 'schedule_class','execute_now'
               ),
               jsonb_build_object('strategy_type',$6::text))
            """,
            (
                str(ids["run_id"]),
                str(ids["plan_id"]),
                str(ids["strategy_id"]),
                ids["business_id"],
                str(ids["site_id"]),
                action_type,
            ),
        )
    return ids


def _pg17_on_page_capability(capability_hash: str):
    adapter_identity = {
        "adapter_id": "oemapps_on_page",
        "adapter_version": "1",
        "connector_type": "oemapps",
        "read": True,
        "write": True,
        "readback": True,
    }
    capability = {
        "capability_snapshot_hash": capability_hash,
        "supported_actions": {"product_seo": "approval_required"},
        "supported_fields": {
            "product_seo": ["meta_title", "meta_description"]
        },
        "protected_fields": {"product_seo": ["price", "inventory", "variants"]},
        "connectors": {
            "products": {
                "status": "available",
                "read": True,
                "write": True,
                "checked_at": "2099-01-01T00:00:00+00:00",
            }
        },
        "configuration_issues": [],
        "side_effects": {},
        "action_adapters": {"product_seo": adapter_identity},
    }
    return capability, adapter_identity


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
            "034_clear_stale_strategy_effect_cancel_reason.sql",
        ):
            sql = (ROOT / "db" / "migrations" / migration_name).read_text(encoding="utf-8")
            await connection.execute(sql)
            await connection.execute(sql)
        assert await connection.fetchval("SELECT to_regclass('seo_agent.tasks')") is not None
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_replanning_one_run_does_not_supersede_another_run_plan():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    business_id = f"pg17-plan-isolation-{uuid4()}"
    site_id = uuid4()
    run_a, run_b = str(uuid4()), str(uuid4())
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.sites
              (id, site_key, name, site_type, business_id, strategy_enabled)
            VALUES ($1::uuid, $2, 'Plan isolation', 'main', $3, true)
            """,
            (site_id, f"plan-isolation-{site_id}", business_id),
        )

    def option(label: str) -> dict:
        return {
            "option_id": f"option-{label}",
            "option_origin": "run_local",
            "candidate_id": None,
            "keyword_id": None,
            "site_id": str(site_id),
            "strategy_type": "new_article",
            "schedule_class": "execute_now",
            "schedule_reason": "PG17 plan isolation test",
            "wave_number": 1,
            "priority": "P1",
            "score": 90,
            "title": f"Strategy {label}",
        }

    async def persist(run_id: str, label: str) -> dict:
        async with factory() as session:
            result = await _replace_strategy_plan(
                session,
                business_id=business_id,
                analysis_batch_id=str(uuid4()),
                source_audit_batch_id=str(uuid4()),
                source_audit_scanned_at="2026-07-30T00:00:00+00:00",
                action_budget=200,
                site_quotas={},
                candidates=[option(label)],
                strategy_run_id=run_id,
            )
            await session.commit()
            return result

    plan_a1 = await persist(run_a, "a1")
    plan_b = await persist(run_b, "b")
    plan_a2 = await persist(run_a, "a2")

    async with engine.begin() as connection:
        statuses = {
            str(row["id"]): row["status"]
            for row in (
                await connection.exec_driver_sql(
                    """
                    SELECT id, status
                      FROM seo_agent.tasks
                     WHERE id = ANY($1::uuid[])
                    """,
                    (
                        [
                            UUID(plan_a1["id"]),
                            UUID(plan_b["id"]),
                            UUID(plan_a2["id"]),
                        ],
                    ),
                )
            ).mappings()
        }
        run_b_queued = (
            await connection.exec_driver_sql(
                """
                SELECT count(*)
                  FROM seo_agent.tasks
                 WHERE payload->>'business_id'=$1
                   AND payload->>'strategy_run_id'=$2
                   AND payload->>'kind' IN ('strategy_plan','seo_strategy')
                   AND status='queued'
                """,
                (business_id, run_b),
            )
        ).scalar_one()

    assert statuses[plan_a1["id"]] == "canceled"
    assert statuses[plan_b["id"]] == "queued"
    assert statuses[plan_a2["id"]] == "queued"
    assert run_b_queued == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_pg17_strategy_run_list_accepts_null_optional_filters():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    run_id = uuid4()
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.tasks
              (id, task_type, status, priority, title, payload, decision)
            VALUES
              ($1::uuid, 'review', 'queued', 'P2', 'list filter regression',
               jsonb_build_object(
                 'kind', 'strategy_run',
                 'business_id', 'pg17-list',
                 'run_id', $1::text,
                 'root_run_id', $1::text
               ),
               '{"status":"queued","current_stage":"queued"}'::jsonb)
            """,
            (run_id,),
        )
    try:
        async with factory() as session:
            unfiltered = await list_strategy_runs(session, limit=1)
            scoped = await list_strategy_runs(
                session,
                business_id="pg17-list",
                limit=1,
            )
        assert unfiltered
        assert scoped[0]["run_id"] == str(run_id)
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "DELETE FROM seo_agent.tasks WHERE id=$1::uuid",
                (run_id,),
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_pg17_keywordless_run_local_options_persist_and_replay():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    business_id = f"pg17-research-options-{uuid4()}"
    site_id = uuid4()
    run_id = None
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.sites
              (id, site_key, name, site_type, business_id, strategy_enabled,
               status, market, language_code, domain, base_url)
            VALUES
              ($1::uuid, $2, 'Research option site', 'main', $3, true,
               'active', 'US', 'en', 'research.example.test',
               'https://research.example.test')
            """,
            (site_id, f"research-option-{site_id}", business_id),
        )
    try:
        async with factory() as session:
            run = await create_strategy_run(
                session,
                business_id=business_id,
                site_ids=[str(site_id)],
                scope="selected_sites",
                mode="approval_execution",
                requested_by="codex",
                idempotency_key=f"create-{uuid4()}",
                action_budget=200,
                site_quotas={},
                approval_policy="use_site_capabilities",
            )
            run_id = run["run_id"]
            options = [
                {
                    "site_id": str(site_id),
                    "action": "new_article",
                    "schedule_class": "execute_now",
                    "topic": "independently researched topic",
                    "title": "Independent Research Topic",
                    "reason": "Current product and SERP evidence support it.",
                    "user_intent": "Learn before choosing a product.",
                    "evidence": {"product_api": ["product-1"]},
                    "candidate_id": None,
                    "keyword_id": None,
                }
            ]
            first = await submit_strategy_run_local_options(
                session,
                run_id=run_id,
                requested_by="codex",
                idempotency_key="pg17-research-submit",
                options=options,
            )
            replay = await submit_strategy_run_local_options(
                session,
                run_id=run_id,
                requested_by="codex",
                idempotency_key="pg17-research-submit",
                options=options,
            )

            assert first["run_local_option_count"] == 1
            assert first["run_local_options"][0]["candidate_id"] is None
            assert first["run_local_options"][0]["keyword_id"] is None
            assert replay["idempotency_replayed"] is True
        async with engine.begin() as connection:
            saved = await connection.exec_driver_sql(
                """
                SELECT decision->'run_local_options'->0->>'option_origin'
                  FROM seo_agent.tasks
                 WHERE id=$1::uuid
                """,
                (UUID(run_id),),
            )
            assert saved.scalar_one() == "codex_research"
    finally:
        async with engine.begin() as connection:
            if run_id:
                await connection.exec_driver_sql(
                    "DELETE FROM seo_agent.tasks WHERE id=$1::uuid",
                    (UUID(run_id),),
                )
            await connection.exec_driver_sql(
                "DELETE FROM seo_agent.sites WHERE id=$1::uuid",
                (site_id,),
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_pg17_accepts_persisted_iso_timestamp_only_after_boundary_coercion():
    connection = await asyncpg.connect(_dsn())
    try:
        raw = "2026-07-27T13:54:12.774115Z"
        with pytest.raises(asyncpg.DataError):
            await connection.fetchval("SELECT CAST($1 AS timestamptz)", raw)
        parsed = require_aware_datetime(raw, field="source_audit_scanned_at")
        assert await connection.fetchval("SELECT CAST($1 AS timestamptz)", parsed) == parsed
    finally:
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("site_type", "domain", "api_base_url", "api_config", "old_url", "expected"),
    [
        (
            "main", "exdivo.com", "https://openapi.oemapps.com", {},
            "https://exdivo.com/blogs/detail/11", "https://exdivo.com/blogs/guide",
        ),
        (
            "main", "avinoti.shop", "https://openapi.oemapps.com", {},
            "https://avinoti.shop/blogs/detail/22", "https://avinoti.shop/blogs/guide",
        ),
        (
            "shopify", "healthyoxy.com", None,
            {"connector_type": "shopify", "blogHandle": "news"},
            "https://store.myshopify.com/blogs/news/guide",
            "https://healthyoxy.com/blogs/news/guide",
        ),
    ],
)
async def test_pg17_post_sync_entry_reconciles_four_persisted_article_urls(
    monkeypatch, site_type, domain, api_base_url, api_config, old_url, expected,
):
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    site_id, article_id = uuid4(), uuid4()
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.sites
              (id, site_key, name, site_type, domain, base_url, api_base_url, api_config)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            (site_id, f"url-{site_id}", domain, site_type, domain, f"https://{domain}", api_base_url, json.dumps(api_config)),
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.articles
              (id, site_id, title, slug, status, published_post_id, published_url, target_url)
            VALUES ($1, $2, 'Guide', 'guide', 'published', '22', $3, $3)
            """,
            (article_id, site_id, old_url),
        )
        for task_type, payload in (
            ("new_article", {"kind": "strategy_execution"}),
            ("publish", {}),
            ("review", {"kind": "strategy_effect", "target_url": old_url}),
        ):
            await connection.exec_driver_sql(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, site_id, article_id, target_url, payload)
                VALUES ($1, 'done', 'P1', $2, $3, $4, $5::jsonb)
                """,
                (task_type, site_id, article_id, old_url, json.dumps(payload)),
            )

    class Connector:
        connector_type = "shopify" if site_type == "shopify" else "openapi"

        async def read_articles(self, *, limit):
            item = {
                "id": "22",
                "title": "Guide",
                "handle": "guide",
                "slug": "guide",
                "isPublished": True,
                "status": "published",
                "url": old_url,
            }
            return [item]

    async def connector_for_site(*_args, **_kwargs):
        return Connector()

    async def no_html_enrichment(_post):
        return None

    monkeypatch.setattr(
        post_sync_service, "publisher_for_site_runtime", connector_for_site
    )
    monkeypatch.setattr(
        post_sync_service, "_enrich_from_public_html", no_html_enrichment
    )
    async with factory() as session:
        first = await sync_site_posts(session, site_id=str(site_id), limit=1)
        second = await sync_site_posts(session, site_id=str(site_id), limit=1)
        assert first["ok"] is True
        assert second["ok"] is True
    async with engine.begin() as connection:
        article_url = (
            await connection.exec_driver_sql(
                "SELECT published_url FROM seo_agent.articles WHERE id=$1", (article_id,)
            )
        ).scalar_one()
        task_urls = (
            await connection.exec_driver_sql(
                "SELECT target_url FROM seo_agent.tasks WHERE article_id=$1 ORDER BY task_type", (article_id,)
            )
        ).scalars().all()
    assert article_url == expected
    assert task_urls == [expected, expected, expected]
    await engine.dispose()


@pytest.mark.asyncio
async def test_same_scoped_idempotency_key_concurrently_creates_one_action():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    site_id = str(uuid4())
    business_id = "pg17-idempotency-test"
    run_id = str(uuid4())
    plan_id = str(uuid4())
    strategy_id = str(uuid4())
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
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.tasks
              (id, task_type, status, priority, site_id, title, payload, decision)
            VALUES
              ($1::uuid, 'review', 'running', 'P1', NULL, 'PG17 strategy run',
               jsonb_build_object('kind','strategy_run','business_id',$4::text),
               jsonb_build_object('status','planning')),
              ($2::uuid, 'review', 'queued', 'P2', NULL, 'PG17 strategy plan',
               jsonb_build_object(
                 'kind','strategy_plan','business_id',$4::text,
                 'strategy_run_id',($1::uuid)::text
               ),
               jsonb_build_object(
                 'strategy_task_ids',jsonb_build_array(($3::uuid)::text),
                 'execute_now_strategy_ids',jsonb_build_array(($3::uuid)::text)
               )),
              ($3::uuid, 'review', 'queued', 'P2', $5::uuid, 'PG17 strategy',
               jsonb_build_object(
                 'kind','seo_strategy','business_id',$4::text,
                 'strategy_run_id',($1::uuid)::text,
                 'plan_id',($2::uuid)::text,
                 'schedule_class','execute_now'
               ),
               jsonb_build_object('strategy_type','update_article'))
            """,
            (
                UUID(run_id),
                UUID(plan_id),
                UUID(strategy_id),
                business_id,
                actual_site_id,
            ),
        )

    async def create_once():
        async with factory() as session:
            return await create_action(
                SQLActionStore(session),
                run_id=run_id,
                plan_id=plan_id,
                source_strategy_task_id=strategy_id,
                business_id=business_id,
                site_id=str(actual_site_id),
                action_type="update_article",
                idempotency_key="same-key",
                capability_snapshot={
                    "supported_actions": {
                        "update_article": "approval_required"
                    }
                },
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


@pytest.mark.asyncio
async def test_strategy_run_start_control_idempotency_executes_on_pg17():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        run = await create_strategy_run(
            session,
            business_id="pg17-run-control-test",
            site_ids=None,
            scope="all_sites",
            mode="dry_run",
            requested_by="pytest",
            idempotency_key=f"create-{uuid4()}",
            action_budget=1,
            site_quotas={},
            approval_policy="use_site_capabilities",
        )

    site_id = str(uuid4())

    async def stable_discoverer(*_args, **_kwargs):
        return [{"id": site_id, "name": "PG17 site"}]

    async def capabilities(_session, business_id):
        return {
            "business_id": business_id,
            "generated_at": "2026-07-27T00:00:00+00:00",
            "sites": [{"site_id": site_id, "supported_actions": {}}],
        }

    async def evidence(*_args, **_kwargs):
        return {"snapshot_id": "pg17-evidence"}

    async def planner(*_args, **_kwargs):
        return {
            "site_scope": [{"id": site_id}],
            "coverage_matrix": {
                "discovered_site_count": 1,
                "decided_site_count": 1,
                "decisions": [{"site_id": site_id, "action": "hold"}],
            },
        }

    control_key = f"start-{uuid4()}"
    async with factory() as session:
        first = await run_strategy_run(
            session,
            run_id=run["run_id"],
            idempotency_key=control_key,
            site_discoverer=stable_discoverer,
            capability_loader=capabilities,
            evidence_gatherer=evidence,
            planner=planner,
        )
    assert first["status"] == "blocked"

    async with factory() as session:
        replay = await run_strategy_run(
            session, run_id=run["run_id"], idempotency_key=control_key
        )
    assert replay["idempotency_replayed"] is True

    async with factory() as session:
        with pytest.raises(ValueError, match="idempotency key"):
            await cancel_strategy_run(
                session,
                run_id=run["run_id"],
                requested_by="pytest",
                idempotency_key=control_key,
            )

    async with engine.begin() as connection:
        marker = (
            await connection.exec_driver_sql(
                """
                SELECT decision->'control_idempotency'->>$1
                  FROM seo_agent.tasks
                 WHERE id=$2
                """,
                (control_key, run["run_id"]),
            )
        ).scalar_one()
    assert marker == "start"

    async with factory() as session:
        failed_run = await create_strategy_run(
            session,
            business_id="pg17-run-control-failure-test",
            site_ids=None,
            scope="all_sites",
            mode="dry_run",
            requested_by="pytest",
            idempotency_key=f"create-{uuid4()}",
            action_budget=1,
            site_quotas={},
            approval_policy="use_site_capabilities",
        )

    async def failing_planner(*_args, **_kwargs):
        raise RuntimeError("planned test failure")

    failed_key = f"failed-start-{uuid4()}"
    async with factory() as session:
        failed = await run_strategy_run(
            session,
            run_id=failed_run["run_id"],
            idempotency_key=failed_key,
            site_discoverer=stable_discoverer,
            capability_loader=capabilities,
            evidence_gatherer=evidence,
            planner=failing_planner,
        )
    assert failed["status"] == "failed"

    async with engine.begin() as connection:
        failed_marker = (
            await connection.exec_driver_sql(
                """
                SELECT decision->'control_idempotency'->>$1
                  FROM seo_agent.tasks
                 WHERE id=$2
                """,
                (failed_key, failed_run["run_id"]),
            )
        ).scalar_one()
    assert failed_marker is None

    retry_key = f"retry-{uuid4()}"
    async with factory() as session:
        retried = await retry_strategy_run(
            session,
            run_id=failed_run["run_id"],
            requested_by="pytest-retry",
            idempotency_key=retry_key,
        )
    assert retried["root_run_id"] == failed_run["root_run_id"]
    assert retried["attempt"] == 2
    assert retried["business_id"] == failed_run["business_id"]
    assert retried["scope"] == failed_run["scope"]
    assert retried["mode"] == failed_run["mode"]
    assert retried["action_budget"] == failed_run["action_budget"]
    assert retried["site_quotas"] == failed_run["site_quotas"]
    assert retried["approval_policy"] == failed_run["approval_policy"]

    async with engine.begin() as connection:
        persisted_retry = (
            await connection.exec_driver_sql(
                """
                SELECT payload
                  FROM seo_agent.tasks
                 WHERE id=$1
                   AND payload->>'kind'='strategy_run'
                """,
                (retried["run_id"],),
            )
        ).scalar_one()
    assert persisted_retry["root_run_id"] == failed_run["root_run_id"]
    assert persisted_retry["attempt"] == 2
    assert persisted_retry["idempotency_key"] == retry_key
    await engine.dispose()


@pytest.mark.asyncio
async def test_formal_run_action_readback_observation_chain_closes_on_pg17():
    """One no-network formal run must close without a second /start call."""
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    business_id = f"pg17-formal-lifecycle-{uuid4()}"
    site_id = uuid4()
    capability_hash = f"pg17-capability-{uuid4()}"

    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.sites
              (id, site_key, name, site_type, business_id, strategy_enabled, status)
            VALUES ($1::uuid, $2, 'Formal lifecycle', 'main', $3, true, 'active')
            """,
            (site_id, f"formal-lifecycle-{site_id}", business_id),
        )

    async with factory() as session:
        created_run = await create_strategy_run(
            session,
            business_id=business_id,
            site_ids=[str(site_id)],
            scope="selected_sites",
            mode="approval_execution",
            requested_by="pytest",
            idempotency_key=f"formal-create-{uuid4()}",
            action_budget=10,
            site_quotas={},
            approval_policy="use_site_capabilities",
        )

    async def discoverer(*_args, **_kwargs):
        return [{"id": str(site_id), "name": "Formal lifecycle", "site_type": "main"}]

    async def capabilities(_session, requested_business_id):
        assert requested_business_id == business_id
        return {
            "business_id": business_id,
            "generated_at": "2099-01-01T00:00:00+00:00",
            "sites": [{
                "site_id": str(site_id),
                "capability_snapshot_hash": capability_hash,
                "supported_actions": {"update_article": "approval_required"},
                "supported_fields": {"articles": ["title"]},
                "connectors": {
                    "articles": {
                        "status": "available",
                        "write": True,
                        "checked_at": "2099-01-01T00:00:00+00:00",
                    }
                },
                "configuration_issues": [],
            }],
        }

    async def evidence(*_args, **_kwargs):
        return {"snapshot_id": f"evidence-{uuid4()}"}

    async def planner(session, *, business_id, _strategy_run_id, **_kwargs):
        option_id = f"formal-option-{uuid4()}"
        analysis_batch_id = str(uuid4())
        audit_batch_id = str(uuid4())
        plan = await _replace_strategy_plan(
            session,
            business_id=business_id,
            analysis_batch_id=analysis_batch_id,
            source_audit_batch_id=audit_batch_id,
            source_audit_scanned_at="2099-01-01T00:00:00+00:00",
            action_budget=10,
            site_quotas={},
            candidates=[{
                "option_id": option_id,
                "option_origin": "run_local",
                "candidate_id": None,
                "keyword_id": None,
                "site_id": str(site_id),
                "strategy_type": "update_article",
                "action_type": "update_article",
                "schedule_class": "execute_now",
                "schedule_reason": "PG17 formal lifecycle gate",
                "wave_number": 1,
                "priority": "P1",
                "score": 90,
                "title": "Formal lifecycle update",
                "target_url": "https://example.com/blogs/formal-lifecycle",
                "query": "formal lifecycle",
            }],
            strategy_run_id=_strategy_run_id,
        )
        return {
            "analysis_batch_id": analysis_batch_id,
            "site_scope": [{"id": str(site_id)}],
            "plan": plan,
            "coverage_matrix": {
                "discovered_site_count": 1,
                "decided_site_count": 1,
                "decisions": [{
                    "site_id": str(site_id),
                    "action": "update_article",
                    "selected_option_id": option_id,
                }],
            },
        }

    async with factory() as session:
        awaiting = await run_strategy_run(
            session,
            run_id=created_run["run_id"],
            idempotency_key=f"formal-start-{uuid4()}",
            site_discoverer=discoverer,
            capability_loader=capabilities,
            evidence_gatherer=evidence,
            planner=planner,
        )
    assert awaiting["status"] == "awaiting_approval"
    assert len(awaiting["action_ids"]) == 1
    action_id = awaiting["action_ids"][0]

    class NoNetworkAdapter:
        async def preview(self, _action, patch):
            return {
                "before_snapshot": {"title": "Before"},
                "proposed_patch": patch,
            }

        async def execute(self, action):
            patch = dict(action["proposed_patch"])
            return {
                "result": "updated",
                "submitted_patch": patch,
                "remote_response": {"ok": True, "source": "pg17-no-network-gate"},
                "readback": patch,
                "target_url": action["target_url"],
            }

        async def recover(self, _action):
            return {"recovery_status": "confirmed_not_applied"}

    adapter = NoNetworkAdapter()
    async with factory() as session:
        store = SQLActionStore(session)
        preview = await preview_action(
            store,
            action_id=action_id,
            patch={"title": "After"},
            adapter=adapter,
            capability_snapshot_hash=capability_hash,
            idempotency_key=f"formal-preview-{uuid4()}",
        )
        approved = await approve_action(
            store,
            action_id=action_id,
            snapshot_hash=preview["snapshot_hash"],
            patch_hash=preview["patch_hash"],
            generation_mode="manual",
            capability_snapshot_hash=capability_hash,
            idempotency_key=f"formal-approve-{uuid4()}",
        )
        assert approved["status"] == "approved"
        executed = await execute_action_and_reconcile(
            store,
            action_id=action_id,
            adapter=adapter,
            capability_snapshot_hash=capability_hash,
            idempotency_key=f"formal-execute-{uuid4()}",
        )
        closed_run = await get_strategy_run(session, run_id=created_run["run_id"])

    assert executed["status"] == "completed"
    assert executed["observation_id"]
    assert closed_run["status"] == "completed"
    assert closed_run["observation_ids"] == [executed["observation_id"]]

    async with engine.begin() as connection:
        observations = (
            await connection.exec_driver_sql(
                """
                SELECT count(*)
                  FROM seo_agent.tasks
                 WHERE payload->>'kind'='strategy_action_observation'
                   AND payload->>'action_id'=$1
                """,
                (action_id,),
            )
        ).scalar_one()
    assert observations == 1
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("connector_type", "remote_object_id"),
    [
        ("oemapps", "oem-product-42"),
        ("shopify", "gid://shopify/Product/42"),
    ],
)
async def test_on_page_product_run_local_to_observation_closes_on_pg17(
    connector_type: str,
    remote_object_id: str,
):
    """The real PG17 lineage must preserve platform identity end to end."""
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    business_id = f"pg17-on-page-{connector_type}-{uuid4()}"
    site_id = uuid4()
    connector_id = uuid4() if connector_type == "oemapps" else None
    target_url = f"https://{connector_type}-{site_id}.test/products/example"
    capability_hash = f"pg17-on-page-capability-{uuid4()}"

    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.sites
              (id, site_key, name, site_type, business_id, strategy_enabled,
               status, domain, base_url, market, language_code, api_config)
            VALUES
              ($1::uuid, $2, $3, $4, $5, true, 'active', $6, $7, 'US', 'en',
               CAST($8 AS jsonb))
            """,
            (
                site_id,
                f"on-page-{site_id}",
                f"On-page {connector_type}",
                "shopify" if connector_type == "shopify" else "main",
                business_id,
                f"{connector_type}-{site_id}.test",
                f"https://{connector_type}-{site_id}.test",
                json.dumps({"connector_type": connector_type}),
            ),
        )
        if connector_id:
            await connection.exec_driver_sql(
                """
                INSERT INTO seo_agent.custom_connectors
                  (id, site_id, name, capability, status, current_version,
                   active_version)
                VALUES ($1::uuid, $2::uuid, 'OEMApps PG17', 'products.list',
                        'active', 1, 1)
                """,
                (connector_id, site_id),
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO seo_agent.custom_connector_versions
                  (connector_id, version, config, verified_at)
                VALUES ($1::uuid, 1, '{"adapter":"oemapps"}'::jsonb, now())
                """,
                (connector_id,),
            )
        product_id = (
            await connection.exec_driver_sql(
                """
                INSERT INTO seo_agent.products
                  (external_id, site_id, title, url, source, source_connector_id,
                   meta_title, meta_description, source_updated_at)
                VALUES ($1, $2::uuid, 'PG17 product', $3, $4, $5::uuid,
                        'Before title', 'Before description', now())
                RETURNING id
                """,
                (
                    remote_object_id,
                    site_id,
                    target_url,
                    (
                        "shopify_admin_graphql"
                        if connector_type == "shopify"
                        else "oemapps"
                    ),
                    connector_id,
                ),
            )
        ).scalar_one()

    async with factory() as session:
        created_run = await create_strategy_run(
            session,
            business_id=business_id,
            site_ids=[str(site_id)],
            scope="selected_sites",
            mode="approval_execution",
            requested_by="pytest",
            idempotency_key=f"on-page-create-{uuid4()}",
            action_budget=10,
            site_quotas={},
            approval_policy="use_site_capabilities",
        )
        submitted = await submit_strategy_run_local_options(
            session,
            run_id=created_run["run_id"],
            requested_by="pytest",
            idempotency_key=f"on-page-options-{uuid4()}",
            options=[
                {
                    "site_id": str(site_id),
                    "action": "on_page_fix",
                    "action_type": "product_seo",
                    "page_type": "product",
                    "target_asset_id": str(product_id),
                    "remote_object_id": remote_object_id,
                    "target_url": target_url,
                    "connector_id": str(connector_id) if connector_id else None,
                    "connector_type": connector_type,
                    "reason": "PG17 on-page lifecycle evidence.",
                    "user_intent": "Evaluate this exact product before purchase.",
                    "evidence": {
                        "product_api": {"remote_object_id": remote_object_id}
                    },
                    "schedule_class": "execute_now",
                    "priority": "P1",
                    "risk_level": "medium",
                    "expected_fields": [
                        "meta_title",
                        "meta_description",
                    ],
                }
            ],
        )
    assert submitted["run_local_option_count"] == 1

    async def discoverer(*_args, **_kwargs):
        return [
            {
                "id": str(site_id),
                "name": f"On-page {connector_type}",
                "site_type": (
                    "shopify" if connector_type == "shopify" else "main"
                ),
            }
        ]

    adapter_identity = {
        "adapter_id": (
            "shopify_product_seo"
            if connector_type == "shopify"
            else "oemapps_on_page"
        ),
        "adapter_version": "1",
        "connector_type": connector_type,
        "read": True,
        "write": True,
        "readback": True,
    }

    async def capabilities(_session, requested_business_id):
        assert requested_business_id == business_id
        return {
            "business_id": business_id,
            "generated_at": "2099-01-01T00:00:00+00:00",
            "sites": [
                {
                    "site_id": str(site_id),
                    "capability_snapshot_hash": capability_hash,
                    "supported_actions": {
                        "product_seo": "approval_required"
                    },
                    "supported_fields": {
                        "product_seo": [
                            "meta_title",
                            "meta_description",
                        ]
                    },
                    "connectors": {
                        "products": {
                            "status": "available",
                            "read": True,
                            "write": True,
                            "checked_at": "2099-01-01T00:00:00+00:00",
                        }
                    },
                    "configuration_issues": [],
                    "side_effects": (
                        {
                            "product_seo": {
                                "requires_variant_confirmation": True
                            }
                        }
                        if connector_type == "oemapps"
                        else {}
                    ),
                    "action_adapters": {
                        "product_seo": adapter_identity
                    },
                }
            ],
        }

    async def evidence(*_args, **_kwargs):
        return {"snapshot_id": f"evidence-{uuid4()}"}

    async def planner(
        session,
        *,
        business_id,
        _strategy_run_id,
        run_local_options,
        **_kwargs,
    ):
        plan = await _replace_strategy_plan(
            session,
            business_id=business_id,
            analysis_batch_id=str(uuid4()),
            source_audit_batch_id=str(uuid4()),
            source_audit_scanned_at="2099-01-01T00:00:00+00:00",
            action_budget=10,
            site_quotas={},
            candidates=run_local_options,
            strategy_run_id=_strategy_run_id,
        )
        option = run_local_options[0]
        return {
            "analysis_batch_id": str(uuid4()),
            "site_scope": [{"id": str(site_id)}],
            "plan": plan,
            "coverage_matrix": {
                "discovered_site_count": 1,
                "decided_site_count": 1,
                "decisions": [
                    {
                        "site_id": str(site_id),
                        "action": "product_seo",
                        "selected_option_id": option["option_id"],
                    }
                ],
            },
        }

    async with factory() as session:
        awaiting = await run_strategy_run(
            session,
            run_id=created_run["run_id"],
            idempotency_key=f"on-page-start-{uuid4()}",
            site_discoverer=discoverer,
            capability_loader=capabilities,
            evidence_gatherer=evidence,
            planner=planner,
        )
    assert awaiting["status"] == "awaiting_approval"
    action_id = awaiting["action_ids"][0]

    class NoNetworkOnPageAdapter:
        writes = 0

        async def preview(self, action, patch):
            assert action["connector_type"] == connector_type
            assert action["remote_object_id"] == remote_object_id
            assert action["target_asset_id"] == str(product_id)
            return {
                "before_snapshot": {
                    "meta_title": "Before title",
                    "meta_description": "Before description",
                },
                "proposed_patch": patch,
            }

        async def execute(self, action):
            self.writes += 1
            patch = dict(action["approved_patch"])
            return {
                "result": "updated",
                "submitted_patch": patch,
                "remote_response": {
                    "ok": True,
                    "source": "pg17-no-network-on-page",
                },
                "readback": patch,
                "target_url": action["target_url"],
            }

        async def recover(self, _action):
            return {"recovery_status": "confirmed_not_applied"}

    adapter = NoNetworkOnPageAdapter()
    patch = {
        "meta_title": "After title",
        "meta_description": "After description",
    }
    async with factory() as session:
        store = SQLActionStore(session)
        preview = await preview_action(
            store,
            action_id=action_id,
            patch=patch,
            adapter=adapter,
            capability_snapshot_hash=capability_hash,
            generation_mode="model",
            generation_provider="openai",
            generation_model="not_exposed_by_runtime",
            generation_run_id=f"pg17-generation-{uuid4()}",
            idempotency_key=f"on-page-preview-{uuid4()}",
        )
        approved = await approve_action(
            store,
            action_id=action_id,
            snapshot_hash=preview["snapshot_hash"],
            patch_hash=preview["patch_hash"],
            generation_mode=preview["generation_mode"],
            generation_provider=preview["generation_provider"],
            generation_model=preview["generation_model"],
            generation_run_id=preview["generation_run_id"],
            side_effect_confirmations=(
                {"requires_variant_confirmation": True}
                if connector_type == "oemapps"
                else {}
            ),
            capability_snapshot_hash=capability_hash,
            idempotency_key=f"on-page-approve-{uuid4()}",
        )
        assert approved["status"] == "approved"
        executed = await execute_action_and_reconcile(
            store,
            action_id=action_id,
            adapter=adapter,
            capability_snapshot_hash=capability_hash,
            idempotency_key=f"on-page-execute-{uuid4()}",
        )
        closed_run = await get_strategy_run(
            session, run_id=created_run["run_id"]
        )

    assert adapter.writes == 1
    assert executed["status"] == "completed"
    assert executed["action_type"] == "product_seo"
    assert executed["connector_type"] == connector_type
    assert executed["observation_id"]
    assert closed_run["status"] == "completed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_on_page_capability_change_invalidates_pg17_approval_before_write():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed_pg17_action_lineage(engine)
    capability_hash = f"capability-{uuid4()}"
    capability, adapter_identity = _pg17_on_page_capability(capability_hash)

    async with factory() as session:
        action = await create_action(
            SQLActionStore(session),
            run_id=str(ids["run_id"]),
            run_mode="approval_execution",
            plan_id=str(ids["plan_id"]),
            source_strategy_task_id=str(ids["strategy_id"]),
            business_id=ids["business_id"],
            site_id=str(ids["site_id"]),
            action_type="product_seo",
            page_type="product",
            target_asset_id="42",
            remote_object_id="product-42",
            target_url=f"https://{ids['site_id']}.test/products/example",
            connector_type="oemapps",
            expected_fields=["meta_title"],
            adapter_identity=adapter_identity,
            idempotency_key=f"capability-action-{uuid4()}",
            capability_snapshot=capability,
        )

    class Adapter:
        writes = 0

        async def preview(self, _action, patch):
            return {
                "before_snapshot": {"meta_title": "Before"},
                "proposed_patch": patch,
            }

        async def execute(self, _action):
            self.writes += 1
            return {"result": "updated", "readback": {"meta_title": "After"}}

        async def recover(self, _action):
            return {"recovery_status": "unknown_remote_state"}

    adapter = Adapter()
    async with factory() as session:
        store = SQLActionStore(session)
        preview = await preview_action(
            store,
            action_id=action["action_id"],
            patch={"meta_title": "After"},
            adapter=adapter,
            capability_snapshot_hash=capability_hash,
        )
        await approve_action(
            store,
            action_id=action["action_id"],
            snapshot_hash=preview["snapshot_hash"],
            patch_hash=preview["patch_hash"],
            capability_snapshot_hash=capability_hash,
            generation_mode="manual",
        )
        result = await execute_action(
            store,
            action_id=action["action_id"],
            adapter=adapter,
            capability_snapshot_hash=f"changed-{uuid4()}",
        )

    assert result["block_reason"] == "capability_snapshot_changed"
    assert adapter.writes == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_on_page_readback_mismatch_creates_one_pg17_p1_and_no_observation():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed_pg17_action_lineage(engine)
    capability_hash = f"capability-{uuid4()}"
    capability, adapter_identity = _pg17_on_page_capability(capability_hash)

    async with factory() as session:
        action = await create_action(
            SQLActionStore(session),
            run_id=str(ids["run_id"]),
            run_mode="approval_execution",
            plan_id=str(ids["plan_id"]),
            source_strategy_task_id=str(ids["strategy_id"]),
            business_id=ids["business_id"],
            site_id=str(ids["site_id"]),
            action_type="product_seo",
            page_type="product",
            target_asset_id="42",
            remote_object_id="product-42",
            target_url=f"https://{ids['site_id']}.test/products/example",
            connector_type="oemapps",
            expected_fields=["meta_title"],
            adapter_identity=adapter_identity,
            idempotency_key=f"mismatch-action-{uuid4()}",
            capability_snapshot=capability,
        )

    class MismatchAdapter:
        async def preview(self, _action, patch):
            return {
                "before_snapshot": {"meta_title": "Before"},
                "proposed_patch": patch,
            }

        async def execute(self, _action):
            return {
                "result": "updated",
                "submitted_patch": {"meta_title": "After"},
                "remote_response": {"ok": True},
                "readback": {"meta_title": "Wrong"},
            }

        async def recover(self, _action):
            return {"recovery_status": "partially_applied"}

    async with factory() as session:
        store = SQLActionStore(session)
        preview = await preview_action(
            store,
            action_id=action["action_id"],
            patch={"meta_title": "After"},
            adapter=MismatchAdapter(),
            capability_snapshot_hash=capability_hash,
        )
        await approve_action(
            store,
            action_id=action["action_id"],
            snapshot_hash=preview["snapshot_hash"],
            patch_hash=preview["patch_hash"],
            capability_snapshot_hash=capability_hash,
            generation_mode="manual",
        )
        result = await execute_action(
            store,
            action_id=action["action_id"],
            adapter=MismatchAdapter(),
            capability_snapshot_hash=capability_hash,
        )

    assert result["result"] == "readback_mismatch"
    assert result["observation_id"] is None
    async with engine.begin() as connection:
        exception_count = (
            await connection.exec_driver_sql(
                """
                SELECT count(*) FROM seo_agent.tasks
                 WHERE payload->>'kind'='strategy_exception'
                   AND payload->>'action_id'=$1
                   AND payload->>'error_code'='ACTION_READBACK_MISMATCH'
                """,
                (action["action_id"],),
            )
        ).scalar_one()
        observation_count = (
            await connection.exec_driver_sql(
                """
                SELECT count(*) FROM seo_agent.tasks
                 WHERE payload->>'kind'='strategy_action_observation'
                   AND payload->>'action_id'=$1
                """,
                (action["action_id"],),
            )
        ).scalar_one()
    assert exception_count == 1
    assert observation_count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_on_page_missing_adapter_cannot_create_pg17_action():
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed_pg17_action_lineage(engine)
    capability, _adapter_identity = _pg17_on_page_capability(
        f"capability-{uuid4()}"
    )

    async with factory() as session:
        with pytest.raises(ValueError, match="requires an exact readable"):
            await create_action(
                SQLActionStore(session),
                run_id=str(ids["run_id"]),
                run_mode="approval_execution",
                plan_id=str(ids["plan_id"]),
                source_strategy_task_id=str(ids["strategy_id"]),
                business_id=ids["business_id"],
                site_id=str(ids["site_id"]),
                action_type="product_seo",
                page_type="product",
                target_asset_id="42",
                remote_object_id="product-42",
                target_url=f"https://{ids['site_id']}.test/products/example",
                connector_type="custom_openapi",
                expected_fields=["meta_title"],
                adapter_identity=None,
                idempotency_key=f"missing-adapter-{uuid4()}",
                capability_snapshot=capability,
            )
        await session.rollback()
    async with engine.begin() as connection:
        action_count = (
            await connection.exec_driver_sql(
                """
                SELECT count(*) FROM seo_agent.tasks
                 WHERE payload->>'kind'='strategy_action'
                   AND payload->>'run_id'=$1
                """,
                (str(ids["run_id"]),),
            )
        ).scalar_one()
    assert action_count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_shopify_unknown_remote_state_blocks_full_pg17_lineage(monkeypatch):
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    site_id, article_id = uuid4(), uuid4()
    run_id, action_id, strategy_id, execution_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )

    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE seo_agent.articles ADD COLUMN IF NOT EXISTS "
            "qa_summary jsonb NOT NULL DEFAULT '{}'::jsonb"
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.sites
              (id, business_id, site_key, name, site_type, domain, base_url,
               status, strategy_enabled, api_config)
            VALUES ($1, 'pg17-remote-outcome', $2, 'HealthyOxy PG17', 'shopify',
                    'healthyoxy.test', 'https://healthyoxy.test', 'active', true,
                    '{"connector_type":"shopify","blogHandle":"news"}'::jsonb)
            """,
            (site_id, f"unknown-{site_id}"),
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.tasks (id, task_type, status, priority, title, payload, decision)
            VALUES
              ($1::uuid, 'review', 'blocked', 'P1', 'run',
               jsonb_build_object('kind','strategy_run','business_id','pg17-remote-outcome',
                                  'root_run_id',$1::text),
               '{"status":"awaiting_approval","current_stage":"awaiting_approval"}'::jsonb),
              ($2::uuid, 'review', 'queued', 'P1', 'action',
               jsonb_build_object('kind','strategy_action','action_id',$2::text,
                                  'run_id',$1::text,'business_id','pg17-remote-outcome',
                                  'site_id',$3::text,'status','approved'),
               '{"status":"approved"}'::jsonb)
            """,
            (run_id, action_id, str(site_id)),
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.tasks
              (id, task_type, status, priority, site_id, title, payload, decision)
            VALUES
              ($1::uuid, 'review', 'done', 'P1', $2::uuid, 'approved strategy',
               jsonb_build_object('kind','seo_strategy','strategy_run_id',$3::text),
               jsonb_build_object('review_status','approved','execution_task_id',$4::text)),
              ($4::uuid, 'new_article', 'running', 'P1', $2::uuid, 'execution',
               jsonb_build_object('strategy_task_id',$1::text,
                                  'strategy_run_id',$3::text,
                                  'strategy_action_id',$5::text),
               jsonb_build_object('source_strategy_id',$1::text))
            """,
            (strategy_id, site_id, str(run_id), str(execution_id), str(action_id)),
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO seo_agent.articles
              (id, task_id, site_id, title, slug, status, content_md,
               meta_title, meta_description, language_code, market,
               qa_checklist, qa_summary)
            VALUES ($1::uuid, $2::uuid, $3::uuid, 'Guide', 'guide', 'generated', '# Guide',
                    'Guide', 'Guide description', 'en', 'US',
                    '[{"key":"content","ok":true}]'::jsonb, '{}'::jsonb)
            """,
            (article_id, execution_id, site_id),
        )

    class TimeoutPublisher:
        calls = 0

        async def find_article_by_slug(self, _slug):
            return None

        async def publish(self, _req):
            self.calls += 1
            return PublishResult(
                ok=False,
                dry_run=False,
                error="create timeout; readback timeout",
                remote_outcome="unknown_remote_state",
                raw={
                    "create_error": "timeout",
                    "readback_error": "timeout",
                    "retry_policy": "manual_readback_required",
                },
            )

    publisher = TimeoutPublisher()

    async def runtime_publisher(*_args, **_kwargs):
        return publisher

    monkeypatch.setattr(
        publish_service, "publisher_for_site_runtime", runtime_publisher
    )
    async with factory() as session:
        first = await publish_service.publish_article(
            session,
            article_id=str(article_id),
            site_id=str(site_id),
            dry_run=False,
            actor="pg17",
        )
    assert first["remote_outcome"] == "unknown_remote_state"
    assert publisher.calls == 1

    async with factory() as session:
        with pytest.raises(publish_service.PublishError, match="human-approved"):
            await publish_service.publish_article(
                session,
                article_id=str(article_id),
                site_id=str(site_id),
                dry_run=False,
                actor="pg17-retry",
            )
    assert publisher.calls == 1

    async with engine.begin() as connection:
        rows = (
            await connection.exec_driver_sql(
                """
                SELECT id::text, task_type, status, priority, payload, decision
                  FROM seo_agent.tasks
                 WHERE id = ANY($1::uuid[])
                    OR payload->>'kind'='strategy_exception'
                """,
                ([run_id, action_id, execution_id, first["task_id"]],),
            )
        ).mappings().all()
    by_id = {row["id"]: row for row in rows}
    assert by_id[str(first["task_id"])]["status"] == "blocked"
    assert by_id[str(execution_id)]["status"] == "blocked"
    assert by_id[str(action_id)]["status"] == "blocked"
    assert by_id[str(run_id)]["decision"]["status"] in {"partial", "blocked"}
    exceptions = [
        row for row in rows
        if row["payload"].get("kind") == "strategy_exception"
        and row["payload"].get("publish_task_id") == str(first["task_id"])
    ]
    assert len(exceptions) == 1
    exception = exceptions[0]
    assert exception["priority"] == "P1"
    assert exception["payload"]["run_id"] == str(run_id)
    assert exception["payload"]["action_id"] == str(action_id)
    assert exception["payload"]["execution_task_id"] == str(execution_id)
    assert exception["payload"]["publish_task_id"] == str(first["task_id"])
    assert exception["payload"]["retryable"] is False
    expected_exception_id = exception["payload"]["exception_id"]
    assert first["exception_id"] == expected_exception_id
    assert by_id[str(first["task_id"])]["payload"]["exception_id"] == expected_exception_id
    assert by_id[str(execution_id)]["payload"]["exception_id"] == expected_exception_id
    assert by_id[str(action_id)]["payload"]["exception_id"] == expected_exception_id
    assert by_id[str(run_id)]["decision"]["exception_id"] == expected_exception_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_content_audit_requests_reuse_one_pg17_batch(monkeypatch):
    sqlalchemy_dsn = _dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    scheduled: list[str] = []
    monkeypatch.setattr(
        content_audit_service,
        "_schedule_content_audit",
        lambda batch_id, _options: scheduled.append(batch_id),
    )
    business_id = f"pg17-audit-{uuid4()}"

    async def start_once():
        async with factory() as session:
            return await start_content_audit(session, business_id=business_id)

    first, second = await asyncio.gather(start_once(), start_once())

    assert first["batch_id"] == second["batch_id"]
    assert {first["reused"], second["reused"]} == {False, True}
    assert scheduled == [first["batch_id"]]
    async with engine.begin() as connection:
        count = (
            await connection.exec_driver_sql(
                """
                SELECT count(*)
                  FROM seo_agent.tasks
                 WHERE payload->>'kind'='content_audit_run'
                   AND payload->>'business_id'=$1
                   AND status IN ('queued', 'running')
                """,
                (business_id,),
            )
        ).scalar_one()
    assert count == 1
    await engine.dispose()
