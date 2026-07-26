from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import automation_service, strategy_effect_service as service


class Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def one(self):
        return self.rows[0]

    def scalar_one(self):
        return self.scalar if self.scalar is not None else self.rows[0]


def test_strategy_identity_targets_page_for_update_and_intent_for_new() -> None:
    common = {
        "site_id": "site",
        "market": "US",
        "language_code": "en",
        "topic_cluster_id": "topic-a",
        "post_id": "post-a",
        "query": "best vape",
        "objective": "improve",
    }
    update = service.strategy_identity("business", **common, action="update_article")
    update_topic_changed = service.strategy_identity("business", **{**common, "topic_cluster_id": "topic-b"}, action="update_article")
    update_post_changed = service.strategy_identity("business", **{**common, "post_id": "post-b"}, action="update_article")
    new = service.strategy_identity("business", **common, action="new_article")
    new_post_changed = service.strategy_identity("business", **{**common, "post_id": "post-b"}, action="new_article")
    new_topic_changed = service.strategy_identity("business", **{**common, "topic_cluster_id": "topic-b"}, action="new_article")

    assert update["scope_key"] == update_topic_changed["scope_key"]
    assert update["scope_key"] != update_post_changed["scope_key"]
    assert new["scope_key"] == new_post_changed["scope_key"]
    assert new["scope_key"] != new_topic_changed["scope_key"]
    assert update == service.strategy_identity("business", **common, action="update_article")


def test_update_strategy_resolves_the_existing_article_url_from_site_evidence() -> None:
    assert service.resolve_strategy_target_url({
        "strategy_type": "update_article",
        "evidence": {"site_content": {"published_url": "https://example.com/original-article"}},
    }) == "https://example.com/original-article"


@pytest.mark.asyncio
async def test_backfill_missing_effect_url_uses_historical_execution_evidence() -> None:
    writes: list[dict] = []

    class Session:
        committed = False

        async def execute(self, statement, params=None):
            sql = str(statement)
            if "FROM seo_agent.tasks effect" in sql:
                return Result([{
                    "id": "effect-id",
                    "target_url": None,
                    "payload": {
                        "action": "update_article",
                        "baseline": {"metric_scope": {"gsc": "query", "ga4": "site"}},
                    },
                    "strategy": {"evidence": {"site_content": {"published_url": "https://example.com/original-article"}}},
                    "article_url": None,
                }])
            if sql.lstrip().startswith("UPDATE seo_agent.tasks"):
                writes.append(params or {})
            return Result()

        async def commit(self):
            self.committed = True

    repaired = await service.backfill_missing_effect_target_urls(Session(), business_id="business")  # type: ignore[arg-type]

    assert repaired == 1
    assert writes[0]["target_url"] == "https://example.com/original-article"
    patch = json.loads(writes[0]["patch"])
    assert "初始基线创建时未绑定目标 URL" in patch["baseline_note"]
    assert patch["baseline_valid"] is False


def test_outcome_waits_until_day_28_and_contamination_wins() -> None:
    snapshot = {"gsc": {"impressions": 300}}
    positive = {"clicks": 0.3, "avg_position": 0.2, "conversions": 0.4}
    negative = {"clicks": -0.3, "avg_position": -0.2, "conversions": -0.4}

    assert service.classify_outcome(snapshot, positive, day=7) == "inconclusive"
    assert service.classify_outcome(snapshot, positive, day=14, contaminated=True) == "contaminated"
    assert service.classify_outcome(snapshot, positive, day=28) == "winner"
    assert service.classify_outcome(snapshot, negative, day=28) == "loser"
    assert service.classify_outcome({"gsc": {"impressions": 199}}, positive, day=90) == "inconclusive"


def test_outcome_rejects_an_invalid_baseline() -> None:
    snapshot = {"gsc": {"impressions": 1000}}
    positive = {"clicks": 1.0, "avg_position": 0.5, "conversions": 1.0}

    assert service.classify_outcome(
        snapshot,
        positive,
        baseline_valid=False,
        day=90,
    ) == "inconclusive"


def test_update_effect_invalidates_baseline_when_public_url_changes() -> None:
    payload = {
        "action": "update_article",
        "target_url": "https://example.com/blogs/detail/42",
        "baseline": {"gsc": {"clicks": 10}},
    }

    reconciled = service.reconcile_effect_target_url(
        payload,
        current_target_url="https://example.com/blogs/detail/42",
        new_target_url="https://example.com/blogs/guide",
    )

    assert reconciled["target_url"] == "https://example.com/blogs/guide"
    assert reconciled["baseline_valid"] is False
    assert reconciled["previous_target_url"] == "https://example.com/blogs/detail/42"
    assert "URL" in reconciled["baseline_note"]
    assert reconciled["baseline"] == payload["baseline"]


def test_new_article_url_change_keeps_its_zero_baseline_valid() -> None:
    payload = {
        "action": "new_article",
        "target_url": "https://example.com/draft-location",
        "baseline": {"ga4": {"sessions": 0, "conversions": 0}},
    }

    reconciled = service.reconcile_effect_target_url(
        payload,
        current_target_url=payload["target_url"],
        new_target_url="https://example.com/blogs/new-guide",
    )

    assert reconciled["target_url"] == "https://example.com/blogs/new-guide"
    assert "baseline_valid" not in reconciled
    assert "previous_target_url" not in reconciled
    assert reconciled["baseline"] == payload["baseline"]


def test_effect_url_reconciliation_is_idempotent_and_preserves_first_evidence() -> None:
    payload = {
        "action": "update_article",
        "target_url": "https://example.com/blogs/guide",
        "baseline_valid": False,
        "previous_target_url": "https://example.com/blogs/detail/42",
        "baseline_note": "首次修复证据",
    }

    reconciled = service.reconcile_effect_target_url(
        payload,
        current_target_url="https://example.com/blogs/guide/",
        new_target_url="https://example.com/blogs/guide",
    )

    assert reconciled == payload


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "new_article",
            "target_url": "https://example.com/old",
            "baseline": {"ga4": {"sessions": 0, "conversions": 0}},
            "checkpoints": [{"day": 7}],
        },
        {
            "target_url": "https://example.com/old",
            "baseline": {"ga4": {"sessions": 0, "conversions": 0}},
            "checkpoints": [],
        },
    ],
)
def test_effect_url_change_invalidates_non_initial_or_unknown_baseline(
    payload: dict,
) -> None:
    reconciled = service.reconcile_effect_target_url(
        payload,
        current_target_url=payload["target_url"],
        new_target_url="https://example.com/new",
    )

    assert reconciled["baseline_valid"] is False
    assert reconciled["previous_target_url"] == "https://example.com/old"


@pytest.mark.asyncio
async def test_scope_locks_release_update_after_28_days_but_keep_new_article() -> None:
    rows = [
        {"status": "queued", "scope_key": "executing", "action": "update_article", "target_url": None, "cooling": False},
        {"status": "queued", "scope_key": "cooled", "action": "update_article", "target_url": "/old", "cooling": False},
        {"status": "done", "scope_key": "cooling", "action": "update_article", "target_url": "/old", "cooling": True},
        {"status": "done", "scope_key": "new", "action": "new_article", "target_url": "/new", "cooling": False},
    ]

    class Session:
        async def execute(self, *_args, **_kwargs):
            return Result(rows)

    locks = await service.load_scope_locks(Session(), business_id="business")  # type: ignore[arg-type]

    assert set(locks) == {"executing", "cooling", "new"}
    assert "cooled" not in locks


@pytest.mark.asyncio
async def test_ensure_effect_reuses_execution_and_accepts_direct_target_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    class Session:
        async def execute(self, statement, params=None):
            sql = str(statement)
            calls.append((sql, params or {}))
            if "payload->>'execution_task_id'" in sql and sql.lstrip().startswith("SELECT id"):
                return Result()
            if sql.lstrip().startswith("INSERT INTO seo_agent.tasks"):
                return Result(scalar="effect-id")
            return Result()

    async def metrics(*_args, **kwargs):
        assert kwargs["target_url"] == "https://example.com/old"
        return {"metric_scope": {"gsc": "page", "ga4": "landing_page"}, "gsc": {}, "ga4": {}}

    monkeypatch.setattr(service, "capture_metrics", metrics)
    identity = service.strategy_identity(
        "business", site_id="site", post_id="post", action="update_article", objective="improve"
    )
    created = await service.ensure_effect(
        Session(),  # type: ignore[arg-type]
        execution_task_id="execution",
        strategy_task_id="strategy",
        site_id="site",
        article_id=None,
        strategy={
            "business_id": "business",
            "query": "query",
            "strategy_type": "update_article",
            "recommended_action": "improve",
            "target_url": "https://example.com/old",
            **identity,
        },
    )

    assert created["id"] == "effect-id"
    insert = next(params for sql, params in calls if sql.lstrip().startswith("INSERT INTO seo_agent.tasks"))
    assert json.loads(insert["payload"])["target_url"] == "https://example.com/old"


@pytest.mark.asyncio
async def test_ensure_effect_reactivates_canceled_retry_without_inserting(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple[str, dict]] = []

    class Session:
        async def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            writes.append((sql, params))
            if sql.lstrip().startswith("SELECT id::text AS id"):
                return Result([{"id": "effect-id", "status": "canceled", "payload": {"kind": "strategy_effect"}}])
            return Result()

    identity = service.strategy_identity("business", site_id="site", post_id=None, action="new_article", objective="create")
    result = await service.ensure_effect(
        Session(),  # type: ignore[arg-type]
        execution_task_id="execution",
        strategy_task_id="strategy",
        site_id="site",
        article_id=None,
        strategy={"business_id": "business", "query": "query", "strategy_type": "new_article", **identity},
    )

    assert result["id"] == "effect-id"
    assert not any(sql.lstrip().startswith("INSERT INTO seo_agent.tasks") for sql, _ in writes)
    assert any("status = 'queued'" in sql for sql, _ in writes)
    assert json.loads(next(params for sql, params in writes if "status = 'queued'" in sql)["payload"]) == {"outcome": "observing"}


@pytest.mark.asyncio
async def test_cancel_unpublished_effect_releases_only_pre_publish_observation() -> None:
    writes: list[tuple[str, dict]] = []

    class Session:
        async def execute(self, statement, params=None):
            writes.append((str(statement), params or {}))
            return Result()

    await service.cancel_unpublished_effect(
        Session(),  # type: ignore[arg-type]
        execution_task_id="execution",
        reason="qa failed",
    )

    sql, params = writes[0]
    assert "NOT (payload ? 'published_at')" in sql
    assert "status = 'canceled'" in sql
    assert params["execution_task_id"] == "execution"
    assert json.loads(params["decision"])["canceled_reason"] == "qa failed"
    assert json.loads(params["payload"])["outcome"] == "inconclusive"


def test_effect_list_hides_canceled_and_deduplicates_execution_records() -> None:
    import inspect

    source = inspect.getsource(service.list_effects)
    assert "t.status <> 'canceled'" in source
    assert "DISTINCT ON (t.payload->>'execution_task_id')" in source


@pytest.mark.asyncio
async def test_published_update_sets_7_day_check_and_28_day_cooldown() -> None:
    published_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    writes: list[dict] = []

    class Session:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if sql.lstrip().startswith("SELECT published_url"):
                return Result([{"published_url": "/published", "published_at": published_at}])
            writes.append(params)
            return Result()

    await service.mark_effect_published(
        Session(),  # type: ignore[arg-type]
        execution_task_id="execution",
        article_id="article",
        target_url=None,
        action="update_article",
    )

    patch = json.loads(writes[0]["patch"])
    assert patch["target_url"] == "/published"
    assert patch["next_checkpoint"]["day"] == 7
    assert datetime.fromisoformat(patch["next_checkpoint"]["due_at"]) == published_at + timedelta(days=7)
    assert datetime.fromisoformat(patch["cooldown_until"]) == published_at + timedelta(days=28)


@pytest.mark.asyncio
async def test_published_update_keeps_existing_page_url_when_connector_returns_placeholder() -> None:
    writes: list[dict] = []

    class Session:
        async def execute(self, statement, params=None):
            if str(statement).lstrip().startswith("SELECT published_url"):
                return Result([{
                    "published_url": "",
                    "published_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
                    "effect_payload": {"target_url": "https://example.com/original-article"},
                }])
            writes.append(params or {})
            return Result()

    await service.mark_effect_published(
        Session(),  # type: ignore[arg-type]
        execution_task_id="execution",
        article_id="article",
        target_url="待确认",
        action="update_article",
    )

    patch = json.loads(writes[0]["patch"])
    assert patch["target_url"] == "https://example.com/original-article"


@pytest.mark.asyncio
async def test_published_new_article_switches_ga4_baseline_to_new_landing_page() -> None:
    published_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    writes: list[dict] = []

    class Session:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if sql.lstrip().startswith("SELECT published_url"):
                return Result([{
                    "published_url": "/new-page",
                    "published_at": published_at,
                    "effect_payload": {
                        "baseline": {
                            "metric_scope": {"gsc": "query", "ga4": "site"},
                            "gsc": {"impressions": 20},
                            "ga4": {"sessions": 100, "conversions": 3},
                        }
                    },
                }])
            writes.append(params)
            return Result()

    await service.mark_effect_published(
        Session(),  # type: ignore[arg-type]
        execution_task_id="execution",
        article_id="article",
        target_url=None,
        action="new_article",
    )

    baseline = json.loads(writes[0]["patch"])["baseline"]
    assert baseline["gsc"]["impressions"] == 20
    assert baseline["metric_scope"] == {"gsc": "query", "ga4": "landing_page"}
    assert baseline["ga4"] == {"sessions": 0, "conversions": 0}


@pytest.mark.asyncio
async def test_process_due_effect_finishes_day_90(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "published_at": (now - timedelta(days=90)).isoformat(),
        "query": "query",
        "baseline": {
            "metric_scope": {"gsc": "query", "ga4": "site"},
            "gsc": {"impressions": 200, "clicks": 10, "avg_position": 10},
            "ga4": {"sessions": 100, "conversions": 1},
        },
        "checkpoints": [],
        "next_checkpoint": {"day": 90, "due_at": now.isoformat()},
    }
    writes: list[dict] = []

    class Session:
        committed = False

        async def execute(self, statement, params=None):
            sql = str(statement)
            if "FOR UPDATE SKIP LOCKED" in sql:
                return Result([{"id": "effect", "site_id": "site", "article_id": "article", "target_url": "/page", "payload": payload}])
            if sql.lstrip().startswith("UPDATE seo_agent.tasks"):
                writes.append(params)
            return Result()

        async def commit(self):
            self.committed = True

    async def metrics(*_args, **_kwargs):
        return {
            "gsc": {"impressions": 300, "clicks": 15, "avg_position": 8},
            "ga4": {"sessions": 120, "conversions": 1.2},
        }

    async def clean(*_args, **_kwargs):
        return False

    monkeypatch.setattr(service, "capture_metrics", metrics)
    monkeypatch.setattr(service, "_is_contaminated", clean)
    session = Session()
    result = await service.process_due_effects(session, limit=3)  # type: ignore[arg-type]

    assert result["items"] == [{"id": "effect", "day": 90, "outcome": "winner", "finished": True}]
    assert writes[0]["status"] == "done"
    assert writes[0]["run_after"] is None
    assert session.committed is True


@pytest.mark.asyncio
async def test_list_effects_returns_frontend_contract() -> None:
    payload = {
        "outcome": "winner",
        "action": "update_article",
        "baseline": {"gsc": {}},
        "checkpoints": [{"day": 28}],
        "cooldown_until": "2026-08-01T00:00:00+00:00",
        "next_checkpoint": {"day": 56, "due_at": "2026-08-29T00:00:00+00:00"},
    }

    class Session:
        async def execute(self, *_args, **_kwargs):
            return Result([{
                "id": "effect", "lifecycle_status": "queued", "site_id": "site", "site_name": "Site",
                "article_id": "article", "target_url": "/page", "title": "Effect", "payload": payload,
                "run_after": None, "created_at": None, "updated_at": None, "finished_at": None,
            }])

    items = await service.list_effects(Session(), business_id="business")  # type: ignore[arg-type]

    assert items[0]["status"] == "winner"
    assert items[0]["lifecycle_status"] == "queued"
    assert items[0]["action_type"] == "update_article"
    assert items[0]["next_check_at"] == "2026-08-29T00:00:00+00:00"
    assert items[0]["site_name"] == "Site"


@pytest.mark.asyncio
async def test_public_effect_runner_only_delegates_to_effect_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict = {}

    async def process(session, *, limit):
        called.update(session=session, limit=limit)
        return {"processed": 2, "items": []}

    monkeypatch.setattr(automation_service, "process_due_effects", process)
    marker = object()
    result = await automation_service.run_effect_checks_once(marker, batch_size=20)  # type: ignore[arg-type]

    assert result["processed"] == 2
    assert called == {"session": marker, "limit": 20}
