import pytest

from app.services.strategy_service import _build_strategy, _candidate_keys, _finish_execution_task, _select_daily_candidates


def test_finish_execution_task_uses_explicit_text_casts():
    import inspect

    source = inspect.getsource(_finish_execution_task)
    assert "CAST(:status AS text)" in source
    assert "CAST(:error_message AS text)" in source


def test_strategy_scope_uses_business_and_explicit_site_switch():
    import inspect

    source = inspect.getsource(__import__("app.services.strategy_service", fromlist=["generate_strategies"]))
    assert "s.business_id = :business_id" in source
    assert "s.strategy_enabled = true" in source
    assert "FROM seo_agent.tasks audit" in source
    assert "candidate.business_id = :business_id" in source
    assert "candidate.assigned_site_id = audit.site_id" in source
    assert "s.site_type IN ('blog', 'wp')" not in source
    assert "COALESCE(s.knowledge_profile, '{}'::jsonb) AS knowledge_profile" in source
    assert "s.knowledge_profile->>'status' = 'confirmed'" not in source
    assert "audit.payload->>'kind' = 'content_audit'" in source
    assert "latest_audit_scan" in source
    assert "content_audit_batch" in source
    assert "candidate_limit" not in source
    assert "p.id = audit.post_id" in source
    assert "candidate.id = audit.keyword_id" in source
    assert "k.ai_review ? 'strategy'" not in source
    assert "published_post_id = p.external_id" in source
    assert "published_url = p.url" in source
    assert "seo_agent.post_analyses" in source


def test_content_audit_is_the_candidate_root_for_update_and_new_article():
    update = _build_strategy(
        {
            "business_id": "exdivo",
            "query": "existing topic",
            "keyword_id": None,
            "post_id": "post-1",
            "post_analysis_id": "analysis-1",
            "knowledge_profile": {"status": "confirmed"},
            "audit_decision": {"action": "update_article", "priority": "P1"},
        },
        None,
        [],
    )
    new = _build_strategy(
        {
            "business_id": "exdivo",
            "query": "new topic",
            "keyword_id": "keyword-1",
            "post_id": None,
            "knowledge_profile": {"status": "confirmed"},
            "audit_decision": {"action": "new_article", "priority": "P2"},
        },
        None,
        [],
    )

    assert update and update["strategy_type"] == "update_article"
    assert new and new["strategy_type"] == "new_article"


def test_unconfirmed_site_knowledge_becomes_visible_hold_with_evidence():
    strategy = _build_strategy(
        {
            "business_id": "exdivo",
            "query": "existing topic",
            "post_id": "post-1",
            "post_analysis_id": "analysis-1",
            "audit_task_id": "audit-1",
            "serp_snapshot_id": "serp-1",
            "serp_result_count": 8,
            "impressions": 120,
            "clicks": 7,
            "last_seen": "2026-07-18",
            "knowledge_profile": {"status": "pending"},
            "audit_decision": {"action": "update_article", "priority": "P1"},
        },
        {"sessions": 30, "conversions": 2},
        [],
    )

    assert strategy and strategy["strategy_type"] == "hold"
    assert strategy["priority"] == "Hold"
    assert "站点知识画像未确认" in strategy["reason"]
    assert strategy["site_context"]["knowledge_profile"]["status"] == "pending"
    assert strategy["evidence"]["gsc"]["impressions"] == 120
    assert strategy["evidence"]["serp"]["snapshot_id"] == "serp-1"
    assert strategy["evidence"]["content_audit"]["task_id"] == "audit-1"


@pytest.mark.asyncio
async def test_strategy_generation_without_business_audit_is_read_only():
    from app.services.strategy_service import generate_strategies

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

        def first(self):
            return self.rows[0] if self.rows else None

    class Session:
        def __init__(self):
            self.statements = []

        async def execute(self, statement, params):
            self.statements.append(str(statement))
            if len(self.statements) == 1:
                return Result([{"id": "site-1", "name": "Site", "site_type": "blog"}])
            return Result([])

    session = Session()
    with pytest.raises(ValueError, match="请先运行当前业务的全站内容扫描"):
        await generate_strategies(session, business_id="exdivo")

    assert len(session.statements) == 2
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in session.statements)


def test_strategy_scope_is_rechecked_before_external_execution():
    import app.services.strategy_service as service

    assert service.execute_strategy.__module__ == "app.services.strategy_execution"
    assert service._validate_current_strategy.__module__ == "app.services.strategy_execution"


def test_daily_candidates_cover_each_site_first():
    rows = [
        {"site_id": "site-a", "query": "a-1"},
        {"site_id": "site-a", "query": "a-2"},
        {"site_id": "site-b", "query": "b-1"},
        {"site_id": "site-c", "query": "c-1"},
    ]

    selected = _select_daily_candidates(rows, 3)

    assert [row["query"] for row in selected] == ["a-1", "b-1", "c-1"]


def test_daily_plan_budget_zero_and_site_quotas():
    rows = [
        {"site_id": "site-a", "query": "a-1", "strategy_type": "new_article"},
        {"site_id": "site-a", "query": "a-2", "strategy_type": "new_article"},
        {"site_id": "site-b", "query": "b-1", "strategy_type": "update_article"},
        {"site_id": "site-b", "query": "b-hold", "strategy_type": "hold", "priority": "Hold"},
    ]

    assert _select_daily_candidates(rows, 0) == []
    selected = _select_daily_candidates(rows, 4, {"site-a": 1, "site-b": 2})
    assert [row["query"] for row in selected] == ["a-1", "b-1"]

    twenty = [{"site_id": f"site-{index % 3}", "query": str(index), "strategy_type": "new_article"} for index in range(20)]
    assert len(_select_daily_candidates(twenty, 4)) == 4
    assert _select_daily_candidates([{**rows[0], "executed": True}, rows[2]], 2) == [rows[2]]


def test_strategy_generation_persists_full_pool_before_daily_selection():
    import inspect
    import app.services.strategy_service as service

    source = inspect.getsource(service.generate_strategies)
    assert '"kind": "strategy_analysis_batch"' in source
    assert '"source_audit_batch_id"' in source
    assert '"source_audit_scanned_at"' in source
    assert '"min_impressions": max(1, min_impressions)' in source
    assert '"kind": "strategy_candidate"' in source
    assert "('review', 'done', :priority, :score" in source
    assert source.index("for row in candidate_rows") < source.index("_select_daily_candidates(candidates")
    assert '"total_candidates": len(candidates)' in source
    assert '"planned_actions": len(plan["items"])' in source


def test_candidate_keys_are_stable_and_evidence_version_changes():
    row = {
        "site_id": "site-a",
        "keyword_id": "keyword-a",
        "topic_cluster_id": "cluster-a",
        "post_id": "post-a",
        "audit_task_id": "audit-1",
        "serp_snapshot_id": "serp-1",
        "post_analysis_id": "analysis-1",
    }
    strategy = {"strategy_type": "update_article"}

    first = _candidate_keys("business-a", row, strategy)
    assert first == _candidate_keys("business-a", dict(row), dict(strategy))
    changed = _candidate_keys("business-a", {**row, "serp_snapshot_id": "serp-2"}, strategy)
    assert first[0] == changed[0]
    assert first[1] != changed[1]
    assert first[0] == _candidate_keys("business-a", {**row, "keyword_id": "keyword-b"}, strategy)[0]
    assert first[0] == _candidate_keys("business-a", {**row, "topic_cluster_id": "cluster-b"}, strategy)[0]
    assert first[0] != _candidate_keys("business-a", {**row, "post_id": "post-b"}, strategy)[0]


def test_clear_queue_cancels_instead_of_deleting_history():
    import inspect
    import app.services.strategy_service as service

    source = inspect.getsource(service.clear_strategy_queue)
    assert "DELETE FROM seo_agent.tasks" not in source
    assert "status = 'canceled'" in source
    assert "strategy_plan" in source
    assert "strategy_candidate" in source
    assert "strategy_analysis_batch" in source
    assert "business_id: str" in source
    assert "s.business_id = :business_id" in source
    assert "payload->>'business_id' = :business_id" in source


def test_canceled_strategy_batches_are_hidden_from_candidate_pool():
    import inspect
    import app.services.strategy_service as service

    source = inspect.getsource(service.list_strategy_candidates)
    assert "AND status = 'done'" in source
    assert "AND status = 'done'" in inspect.getsource(service.save_strategy_plan)


@pytest.mark.asyncio
async def test_clear_strategy_queue_clears_workspace_counts_without_deleting_history():
    from app.services.strategy_service import clear_strategy_queue

    class Result:
        def __init__(self, rows=(), scalar=None):
            self.rows = list(rows)
            self.scalar = scalar

        def scalar_one(self):
            return self.scalar

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        committed = False

        async def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT count(*)" in sql:
                return Result(scalar=0)
            if sql.lstrip().startswith("SELECT t.id::text"):
                return Result()
            if "status = 'queued' AND t.payload->>'kind' = 'seo_strategy'" in sql:
                return Result()
            if "payload->>'kind' = 'strategy_plan'" in sql:
                return Result([("plan-1",)])
            if "strategy_candidate', 'strategy_analysis_batch" in sql:
                return Result([("strategy_candidate",), ("strategy_candidate",), ("strategy_analysis_batch",)])
            return Result()

        async def commit(self):
            self.committed = True

    session = Session()
    result = await clear_strategy_queue(session, business_id="exdivo")

    assert result["plans_cleared"] == 1
    assert result["candidates_cleared"] == 2
    assert result["analysis_batches_cleared"] == 1
    assert session.committed is True


def test_plan_and_candidate_lifecycle_guards_are_persisted():
    import inspect
    import app.services.strategy_service as service

    candidates = inspect.getsource(service.list_strategy_candidates)
    save = inspect.getsource(service.save_strategy_plan)
    replace = inspect.getsource(service._replace_strategy_plan)
    current = inspect.getsource(service.get_strategy_plan)
    assert "execution_task_id" in candidates
    assert 'THEN \'executed\'' in candidates
    assert "已批准或执行的策略候选不能再次加入计划" in save
    assert "pg_advisory_xact_lock(hashtext(:business_id))" in replace
    assert 'ZoneInfo("Asia/Shanghai")' in replace
    assert "AT TIME ZONE 'Asia/Shanghai'" in current


def test_review_revalidates_candidate_plan_analysis_keyword_and_post_snapshot():
    import inspect
    import app.services.strategy_service as service

    source = inspect.getsource(service._validate_current_strategy)
    for token in (
        "candidate_ok",
        "plan_ok",
        "analysis_ok",
        "keyword_ok",
        "target_ok",
        "strategy_candidate",
        "strategy_analysis_batch",
        "source_audit_batch_id",
        "source_audit_scanned_at",
        "content_audit_batch",
        "latest_audit",
        "selected_candidate_ids",
        "keyword.assigned_site_id",
        "seo_agent.post_analyses",
        "post_analysis_id",
        "payload->>'analysis_batch_id' = CAST(:analysis_batch_id AS text)",
        "payload->>'execution_task_id' <> CAST(:current_execution_id AS text)",
    ):
        assert token in source
    assert "策略证据或业务范围已变化，请重新运行全站内容扫描并生成策略" in source


def test_strategy_uses_content_audit_evidence():
    strategy = _build_strategy(
        {
            "query": "test keyword",
            "keyword_priority": "P2",
            "keyword_score": 42,
            "audit_score": 91,
            "knowledge_profile": {"status": "confirmed"},
            "audit_task_id": "audit-1",
            "audit_scanned_at": "2026-07-17T00:00:00+00:00",
            "post_analysis_id": "analysis-1",
            "post_analysis_analyzed_at": "2026-07-16T00:00:00+00:00",
            "audit_decision": {
                "action": "update_article",
                "priority": "P0",
                "confidence": 0.91,
                "evidence_level": "confirmed",
                "recommended_action": "补齐竞争对手缺失章节",
                "ai": {"competitor_gap": {"missing_sections": ["FAQ"]}},
            },
        },
        None,
        [],
    )

    assert strategy is not None
    assert strategy["strategy_type"] == "update_article"
    assert strategy["priority"] == "P0"
    assert strategy["confidence"] == 0.91
    assert strategy["score"] == 91
    assert strategy["evidence_level"] == "confirmed"
    assert strategy["recommended_action"] == "补齐竞争对手缺失章节"
    assert strategy["evidence"]["content_audit"] == {
        "task_id": "audit-1",
        "scanned_at": "2026-07-17T00:00:00+00:00",
        "post_analysis": {"id": "analysis-1", "analyzed_at": "2026-07-16T00:00:00+00:00"},
        "competitor_gap": {"missing_sections": ["FAQ"]},
    }


def test_strategy_preserves_content_audit_hold():
    strategy = _build_strategy(
        {"query": "test keyword", "audit_decision": {"action": "hold"}},
        None,
        [],
    )
    assert strategy is not None
    assert strategy["strategy_type"] == "hold"
    assert strategy["priority"] == "Hold"
    assert strategy["confidence"] == 0.35


def test_published_article_without_analysis_is_held():
    strategy = _build_strategy(
        {
            "query": "test keyword",
            "keyword_priority": "P1",
            "article_id": "article-1",
            "article_status": "published",
            "article_published_url": "https://example.com/test",
            "knowledge_profile": {"status": "confirmed"},
            "audit_decision": {"action": "new_article", "priority": "P0", "confidence": 0.9},
        },
        None,
        [],
    )

    assert strategy is not None
    assert strategy["strategy_type"] == "hold"
    assert strategy["confidence"] == 0.35
    assert strategy["evidence"]["site_content"]["article_id"] == "article-1"
    assert "未进入文章诊断快照" in strategy["reason"]


def test_post_without_analysis_is_held():
    strategy = _build_strategy(
        {"query": "test keyword", "post_id": "post-1", "audit_decision": {"action": "update_article"}},
        None,
        [],
    )

    assert strategy is not None
    assert strategy["strategy_type"] == "hold"


def test_exact_existing_article_drops_conflicting_new_article_advice():
    strategy = _build_strategy(
        {
            "query": "test keyword",
            "keyword_priority": "P1",
            "article_id": "article-1",
            "post_id": "post-1",
            "post_analysis_id": "analysis-1",
            "post_match_type": "exact",
            "knowledge_profile": {"status": "confirmed"},
            "audit_decision": {
                "action": "new_article",
                "recommended_action": "Create a new brief",
                "reason": "Site has no article",
                "ai": {"competitor_gap": {"summary": "Site has no coverage"}},
            },
        },
        None,
        [],
    )

    assert strategy is not None
    assert strategy["strategy_type"] == "update_article"
    assert strategy["recommended_action"] != "Create a new brief"
    assert strategy["evidence"]["content_audit"]["competitor_gap"] is None
    assert "库存事实" in strategy["reason"]


def test_hold_priority_becomes_hold_strategy():
    strategy = _build_strategy(
        {"query": "test keyword", "audit_decision": {"action": "new_article", "priority": "Hold"}},
        None,
        [],
    )

    assert strategy is not None
    assert strategy["strategy_type"] == "hold"
    assert strategy["confidence"] == 0.35


def test_ai_relevance_can_override_unconfigured_local_scope():
    import inspect

    source = inspect.getsource(__import__("app.services.strategy_service", fromlist=["_quarantine_invalid_assignments"])._quarantine_invalid_assignments)
    assert '((row["ai_review"] or {}).get("strategy") or {}).get("relevance") != "relevant"' in source




def test_strategy_approval_enables_auto_publish_for_new_articles():
    import inspect

    source = inspect.getsource(__import__("app.services.strategy_service", fromlist=["review_strategy"]).review_strategy)
    assert '"auto_publish": execution_type == "new_article"' in source
    assert "Hold 策略必须先补齐诊断证据" in source
    assert "FOR UPDATE OF t" in source


def test_blog_update_is_executable_when_evidence_exists():
    strategy = _build_strategy(
        {
            "query": "existing article",
            "site_type": "blog",
            "keyword_priority": "P1",
            "post_id": "post-1",
            "post_analysis_id": "analysis-1",
            "knowledge_profile": {"status": "confirmed"},
            "audit_decision": {"action": "update_article", "priority": "P0"},
        },
        None,
        [],
    )

    assert strategy is not None
    assert strategy["strategy_type"] == "update_article"
    assert strategy["priority"] == "P0"


def test_cancel_strategy_claim_is_atomic():
    import inspect

    source = inspect.getsource(__import__("app.services.strategy_service", fromlist=["cancel_strategy"]).cancel_strategy)
    assert "RETURNING id" in source
    assert "execution task is already running or done" in source
