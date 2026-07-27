from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.api.v1 import endpoints
from app.services import article_generation_service, automation_service, keyword_ai_service, strategy_execution, strategy_service
from app.services.article_generation_service import generate_article_pipeline


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def one(self):
        return self.rows[0]

    def scalar_one(self):
        return self.rows[0]


class _Session:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return _Rows(self.rows)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_formal_article_pipeline_requires_approved_strategy() -> None:
    result = await generate_article_pipeline(None, "keyword-id")  # type: ignore[arg-type]

    assert result["status"] == "failed"
    assert result["steps"][0]["key"] == "strategy"
    assert "已审核" in result["steps"][0]["message"]


@pytest.mark.asyncio
async def test_keywordless_update_uses_ephemeral_context_without_keyword_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict = {}

    async def resolve_site_id(*args, **kwargs):
        return "site-id"

    async def serp(*args, **kwargs):
        return {"id": None, "source": "test", "organic_results": []}

    async def brief(*args, **kwargs):
        return {"brief": "brief"}

    async def outline(*args, **kwargs):
        return {"content": "# Outline"}

    async def generate(*args, **kwargs):
        return {"content": "# Existing topic\n\n## Details\n\nExisting topic body\n\n## FAQ\n\nAnswer"}

    async def save(_session, payload):
        saved.update(payload)
        return {"id": "article-id", "title": payload["title"]}

    monkeypatch.setattr(article_generation_service, "is_stage_configured", lambda _stage: True)
    monkeypatch.setattr(article_generation_service, "resolve_site_id", resolve_site_id)
    monkeypatch.setattr(article_generation_service, "_latest_or_fetch_serp", serp)
    monkeypatch.setattr(article_generation_service, "build_brief_with_optional_ai", brief)
    monkeypatch.setattr(article_generation_service, "_generate_outline", outline)
    monkeypatch.setattr(article_generation_service, "generate_ai_content", generate)
    monkeypatch.setattr(article_generation_service, "save_article", save)
    monkeypatch.setattr(article_generation_service, "_qa", lambda *args, **kwargs: [{"key": "ok", "ok": True}])
    session = _Session()

    result = await generate_article_pipeline(
        session,  # type: ignore[arg-type]
        None,
        forced_site_id="site-id",
        approved_strategy={"strategy_type": "update_article", "query": "existing topic"},
        keyword_context={
            "id": None,
            "keyword": "existing topic",
            "assigned_site_id": "site-id",
            "assigned_site_label": "Site",
            "market": "US",
            "language_code": "en",
        },
    )

    assert result["status"] == "done"
    assert saved["keyword_id"] is None
    assert saved["primary_keyword"] == "existing topic"
    assert all("UPDATE seo_agent.keywords" not in sql for sql, _ in session.calls)


@pytest.mark.asyncio
async def test_keywordless_new_article_is_rejected() -> None:
    result = await generate_article_pipeline(
        None,  # type: ignore[arg-type]
        None,
        approved_strategy={"strategy_type": "new_article", "query": "new topic"},
        keyword_context={"keyword": "new topic"},
    )

    assert result["status"] == "failed"
    assert "证据快照" in result["steps"][0]["message"]


@pytest.mark.asyncio
async def test_keywordless_new_article_reaches_execution_with_verified_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = {
        "business_id": "business-a",
        "strategy_type": "new_article",
        "query": "how to choose a titanium tea set",
        "risk_level": "standard",
        "risk_gate_passed": True,
        "scope_key": "scope-id",
        "strategy_fingerprint": "strategy-id",
        "evidence_fingerprint": "evidence-id",
        "execution_evidence": {
            "search_intent": {"status": "confirmed", "source": "live_serp", "snapshot_id": "serp-1"},
            "topic_basis": {"product_or_category_match": True, "content_gap": True},
            "cannibalization": {"status": "clear"},
            "product_facts": [{"name": "material", "value": "titanium"}],
            "authority_sources": [],
            "evidence_sources": ["products", "live_serp", "content_audit"],
            "snapshots": {"serp": "serp-1", "content_audit": "audit-1"},
        },
    }

    class Session(_Session):
        async def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params or {}))
            if "SELECT t.decision, t.site_id" in sql:
                return _Rows([{
                    "decision": {**decision, "execution_task_id": "execution-id"},
                    "site_id": "site-id", "keyword_id": None, "post_id": None,
                    "site_status": "active", "business_id": "business-a", "strategy_enabled": True,
                    "task_business_id": "business-a", "candidate_id": "candidate-id",
                    "plan_id": "plan-id", "analysis_batch_id": "analysis-id",
                }])
            if "SELECT id, task_type, status, site_id, keyword_id" in sql:
                return _Rows([{
                    "id": "execution-id", "task_type": "new_article", "status": "running",
                    "site_id": "site-id", "keyword_id": None, "post_id": None,
                    "payload": {"strategy": decision, "auto_publish": False},
                }])
            if "AS candidate_ok" in sql:
                return _Rows([{
                    "candidate_ok": True, "plan_ok": True, "analysis_ok": True,
                    "keyword_ok": True, "target_ok": True, "conflict_ok": True, "effect_ok": True,
                }])
            if "SELECT name, status, business_id" in sql:
                return _Rows([{
                    "id": "site-id", "name": "Tea", "status": "active",
                    "business_id": "business-a", "strategy_enabled": True,
                    "market": "US", "language_code": "en", "site_type": "main",
                    "domain": "example.com", "base_url": "https://example.com",
                    "api_base_url": None, "api_config": {},
                }])
            return _Rows([])

    captured: dict = {}

    async def pipeline(_session, keyword_id, **kwargs):
        captured.update(keyword_id=keyword_id, **kwargs)
        return {"status": "done", "article": {"id": "article-id"}}

    async def baseline(*args, **kwargs):
        return {"id": "effect-id"}

    monkeypatch.setattr(article_generation_service, "generate_article_pipeline", pipeline)
    monkeypatch.setattr(strategy_execution, "ensure_effect", baseline)
    session = Session()

    result = await strategy_execution.execute_strategy(
        session,  # type: ignore[arg-type]
        task_id="strategy-id",
        execution_task_id="execution-id",
        allow_running=True,
    )

    assert result["ok"] is True
    assert captured["keyword_id"] is None
    assert captured["keyword_context"]["keyword"] == decision["query"]
    assert captured["keyword_context"]["evidence_sources"] == ["products", "live_serp", "content_audit"]
    assert captured["keyword_context"]["evidence_snapshot"] == {
        "serp": "serp-1",
        "content_audit": "audit-1",
    }
    assert not any("UPDATE seo_agent.keywords" in sql for sql, _ in session.calls)


@pytest.mark.asyncio
async def test_keywordless_update_can_be_reviewed_and_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    decision = {
        "business_id": "business-a",
        "strategy_type": "update_article",
        "query": "existing topic",
        "target_url": "https://example.com/blogs/detail/42",
        "priority": "P1",
        "scope_key": "scope-id",
        "strategy_fingerprint": "strategy-fingerprint",
        "evidence_fingerprint": "evidence-fingerprint",
        "policy_version": "strategy_policy_v1",
        "evidence": {"content_audit": {"post_analysis": {"id": "analysis-id"}}},
    }

    class ReviewSession(_Session):
        async def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params or {}))
            if "FOR UPDATE OF t" in sql:
                return _Rows([{
                    "id": "review-id", "site_id": "site-id", "keyword_id": None, "post_id": "post-id",
                    "article_id": None, "title": "Update", "decision": decision, "site_type": "blog",
                    "site_status": "active", "business_id": "business-a", "strategy_enabled": True,
                    "task_business_id": "business-a", "candidate_id": "candidate-id", "plan_id": "plan-id",
                    "analysis_batch_id": "batch-id",
                }])
            if "AS candidate_ok" in sql:
                return _Rows([{"candidate_ok": True, "plan_ok": True, "analysis_ok": True, "keyword_ok": True, "target_ok": True, "conflict_ok": True, "effect_ok": True}])
            if "INSERT INTO seo_agent.tasks" in sql:
                return _Rows(["execution-id"])
            return _Rows([])

    review_session = ReviewSession()
    reviewed = await strategy_service.review_strategy(review_session, task_id="review-id", approved=True)  # type: ignore[arg-type]
    assert reviewed["status"] == "approved"
    execution_insert = next(params for sql, params in review_session.calls if "INSERT INTO seo_agent.tasks" in sql)
    assert execution_insert["keyword_id"] is None
    assert not any("UPDATE seo_agent.keywords" in sql for sql, _ in review_session.calls)

    class ExecuteSession(_Session):
        async def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params or {}))
            if (params or {}).get("stage") == "publishing":
                events.append("publishing")
            if "SELECT t.decision, t.site_id" in sql:
                return _Rows([{
                    "decision": {**decision, "execution_task_id": "execution-id"},
                    "site_id": "site-id", "keyword_id": None, "post_id": "post-id",
                    "site_status": "active", "business_id": "business-a", "strategy_enabled": True,
                    "task_business_id": "business-a", "candidate_id": "candidate-id", "plan_id": "plan-id",
                    "analysis_batch_id": "batch-id",
                }])
            if "SELECT id, task_type, status, site_id, keyword_id" in sql:
                return _Rows([{
                    "id": "execution-id", "task_type": "update_article", "status": "running",
                    "site_id": "site-id", "keyword_id": None, "post_id": "post-id",
                    "payload": {"strategy": decision, "auto_publish": False},
                }])
            if "SELECT name, status, business_id" in sql:
                return _Rows([{
                    "name": "Site", "status": "active", "business_id": "business-a",
                    "strategy_enabled": True, "market": "US", "language_code": "en",
                }])
            if "SELECT external_id, url, slug FROM seo_agent.posts" in sql:
                return _Rows([{
                    "external_id": "remote-id",
                    "url": "https://example.com/blogs/guide",
                    "slug": "guide",
                }])
            if "AS candidate_ok" in sql:
                return _Rows([{"candidate_ok": True, "plan_ok": True, "analysis_ok": True, "keyword_ok": True, "target_ok": True, "conflict_ok": True, "effect_ok": True}])
            return _Rows([])

    captured: dict = {}

    async def pipeline(_session, keyword_id, **kwargs):
        captured.update(keyword_id=keyword_id, **kwargs)
        return {"status": "done", "article": {"id": "article-id"}}

    async def publish(*args, **kwargs):
        events.append("publish")
        return {"ok": True}

    effect_call: dict = {}

    async def effect(*args, **kwargs):
        events.append("baseline")
        effect_call.update(kwargs)
        return {"id": "effect-id"}

    async def published_effect(*args, **kwargs):
        return None

    monkeypatch.setattr(article_generation_service, "generate_article_pipeline", pipeline)
    monkeypatch.setattr("app.services.publish_service.publish_article", publish)
    monkeypatch.setattr(strategy_execution, "ensure_effect", effect)
    monkeypatch.setattr(strategy_execution, "mark_effect_published", published_effect)
    execute_session = ExecuteSession()
    executed = await strategy_service.execute_strategy(
        execute_session,  # type: ignore[arg-type]
        task_id="review-id",
        execution_task_id="execution-id",
        allow_running=True,
    )

    assert executed["ok"] is True
    assert events == ["baseline", "publishing", "publish"]
    assert captured["keyword_id"] is None
    assert captured["keyword_context"] == {
        "id": None,
        "keyword": "existing topic",
        "business_id": "business-a",
        "assigned_site_id": "site-id",
        "assigned_site_label": "Site",
        "market": "US",
        "language_code": "en",
    }
    assert effect_call["strategy"]["target_url"] == "https://example.com/blogs/guide"


@pytest.mark.asyncio
async def test_get_strategy_effects_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    class ReadOnlySession:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("GET endpoint must not run maintenance writes")

    async def list_items(session, *, business_id, limit):
        assert isinstance(session, ReadOnlySession)
        assert business_id == "business"
        assert limit == 5
        return [{"id": "effect"}]

    monkeypatch.setattr(endpoints, "list_effects", list_items)
    result = await endpoints.get_strategy_effects(
        business_id=" business ",
        limit=5,
        session=ReadOnlySession(),  # type: ignore[arg-type]
    )

    assert result == {"items": [{"id": "effect"}]}


@pytest.mark.asyncio
async def test_keywordless_update_missing_query_persists_terminal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Session(_Session):
        async def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params or {}))
            if "SELECT t.decision, t.site_id" in sql:
                return _Rows([{
                    "decision": {"execution_task_id": "execution-id", "business_id": "business-a"},
                    "site_id": "site-id", "keyword_id": None, "post_id": "post-id",
                    "site_status": "active", "business_id": "business-a", "strategy_enabled": True,
                    "task_business_id": "business-a", "candidate_id": "candidate-id", "plan_id": "plan-id",
                    "analysis_batch_id": "batch-id",
                }])
            if "SELECT id, task_type, status, site_id, keyword_id" in sql:
                return _Rows([{
                    "id": "execution-id", "task_type": "update_article", "status": "running",
                    "site_id": "site-id", "keyword_id": None, "post_id": "post-id",
                    "payload": {"strategy": {"business_id": "business-a"}},
                }])
            if "SELECT name, status, business_id" in sql:
                return _Rows([{
                    "name": "Site", "status": "active", "business_id": "business-a",
                    "strategy_enabled": True, "market": "US", "language_code": "en",
                }])
            if "SELECT external_id, url, slug FROM seo_agent.posts" in sql:
                return _Rows([{
                    "external_id": "remote-id",
                    "url": "https://example.com/blogs/guide",
                    "slug": "guide",
                }])
            return _Rows([])

    async def valid(*_args, **_kwargs):
        return None

    monkeypatch.setattr(strategy_execution, "_validate_current_strategy", valid)
    session = Session()

    result = await strategy_execution.execute_strategy(
        session,  # type: ignore[arg-type]
        task_id="review-id",
        execution_task_id="execution-id",
        allow_running=True,
    )

    assert result["status"] == "failed"
    assert result["error"] == "更新策略缺少查询词"
    assert session.commits == 1
    terminal_updates = [
        params for sql, params in session.calls
        if "SET status = CAST(:status AS text)" in sql
    ]
    assert terminal_updates == [{
        "id": "execution-id",
        "status": "failed",
        "error_message": "更新策略缺少查询词",
    }]


@pytest.mark.asyncio
async def test_execution_is_blocked_when_current_evidence_validation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    class Session(_Session):
        async def execute(self, statement, params=None):
            sql = str(statement)
            calls.append((sql, params or {}))
            if "SELECT t.decision, t.site_id" in sql:
                return _Rows([{
                    "decision": {"execution_task_id": "execution", "business_id": "business"},
                    "site_id": "site", "keyword_id": "keyword", "post_id": None,
                    "site_status": "active", "business_id": "business", "strategy_enabled": True,
                    "task_business_id": "business", "candidate_id": "candidate", "plan_id": "plan",
                    "analysis_batch_id": "analysis",
                }])
            if "SELECT id, task_type, status, site_id, keyword_id" in sql:
                return _Rows([{
                    "id": "execution", "task_type": "new_article", "status": "running",
                    "site_id": "site", "keyword_id": "keyword", "post_id": None,
                    "payload": {"strategy": {"business_id": "business"}},
                }])
            return _Rows([])

    async def invalid(*_args, **_kwargs):
        raise ValueError("冷却期新增")

    monkeypatch.setattr(strategy_execution, "_validate_current_strategy", invalid)
    session = Session()
    result = await strategy_service.execute_strategy(
        session,  # type: ignore[arg-type]
        task_id="strategy",
        allow_running=True,
        execution_task_id="execution",
    )

    assert result["status"] == "blocked"
    assert session.commits == 1
    assert not any("SELECT name, status, business_id" in sql for sql, _ in calls)
    assert any(params.get("status") == "blocked" for _, params in calls)


@pytest.mark.asyncio
async def test_legacy_article_routes_are_closed() -> None:
    body = endpoints.ArticleGenerateBody(keywordId="keyword-id")

    for route in (endpoints.generate_article, endpoints.article_pipeline):
        with pytest.raises(HTTPException) as error:
            await route(body, None)  # type: ignore[arg-type]
        assert error.value.status_code == 409
        assert "今日计划" in error.value.detail


@pytest.mark.asyncio
async def test_keyword_analysis_rejects_mixed_business_ids() -> None:
    session = _Session([{"business_id": "business-a", "count": 1}, {"business_id": "business-b", "count": 1}])

    with pytest.raises(ValueError, match="同一业务"):
        await keyword_ai_service._resolve_analysis_business(session, ["keyword-a", "keyword-b"], None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_clear_unreviewed_assignments_is_business_scoped() -> None:
    unscoped = _Session()
    await keyword_ai_service.clear_unreviewed_assignments(unscoped)  # type: ignore[arg-type]
    assert not unscoped.calls

    scoped = _Session()
    await keyword_ai_service.clear_unreviewed_assignments(scoped, business_id="business-a", keyword_ids=["keyword-a"])  # type: ignore[arg-type]
    assert len(scoped.calls) == 2
    assert all("business_id = :business_id" in sql for sql, _ in scoped.calls)
    assert all(params["business_id"] == "business-a" for _, params in scoped.calls)
    task_update = scoped.calls[1][0]
    assert "task_type IN ('new_article', 'update_article')" in task_update
    assert "'review'" not in task_update


def test_keyword_analysis_task_and_queries_carry_business_scope() -> None:
    start_source = inspect.getsource(keyword_ai_service.start_keyword_analysis)
    load_source = inspect.getsource(keyword_ai_service._load_keywords)
    count_source = inspect.getsource(keyword_ai_service._count_unanalyzed)

    assert "payload->>'business_id' = :business_id" in start_source
    assert '"business_id": business_id' in start_source
    assert "business_id = :business_id" in load_source
    assert "business_id = :business_id" in count_source


def test_keyword_analysis_runs_once_per_validated_page_cluster() -> None:
    load_source = inspect.getsource(keyword_ai_service._load_keywords)
    count_source = inspect.getsource(keyword_ai_service._count_unanalyzed)
    save_source = inspect.getsource(keyword_ai_service._save_strategy)

    for source in (load_source, count_source):
        assert "cluster_role IN ('pillar', 'standalone')" in source
        assert "('validated', 'provisional')" in source
        assert "source <> 'semrush_strategy_builder'" in source
    assert "imported_page_url" in load_source
    assert "imported_page_title" in load_source
    assert "imported_page_description" in load_source
    assert "WHERE business_id = :business_id AND topic_cluster_id = :topic_cluster_id" in save_source
    assert "classify_keyword" not in save_source
    assert "topic_cluster = COALESCE" not in save_source
    assert "page_type = COALESCE" not in save_source


def test_latest_keyword_analysis_route_precedes_dynamic_run_route() -> None:
    paths = [route.path for route in endpoints.router.routes]
    fixed = paths.index("/workflow/semrush-strategy/ai-analyze/latest")
    dynamic = paths.index("/workflow/semrush-strategy/ai-analyze/{run_id}")
    assert fixed < dynamic


def test_execution_status_can_be_filtered_by_business_without_breaking_global_calls() -> None:
    source = inspect.getsource(automation_service.get_execution_status)
    assert "business_id: str | None = None" in source
    assert "LEFT JOIN seo_agent.sites s ON s.id = t.site_id" in source
    assert "s.business_id = :business_id" in source

    endpoint_source = inspect.getsource(endpoints.automation_status)
    assert "business_id: str | None = None" in endpoint_source
    assert "get_execution_status(session, business_id=" in endpoint_source
