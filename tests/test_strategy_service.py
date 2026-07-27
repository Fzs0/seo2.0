import pytest

from app.services.strategy_service import (
    _build_site_coverage,
    _build_strategy,
    _candidate_keys,
    _required_strategy_data,
    _finish_execution_task,
    _select_daily_candidates,
    review_strategy,
)


def test_required_data_matches_the_selected_action_type():
    assert "gsc_28d" not in _required_strategy_data("new_article")
    assert _required_strategy_data("on_page_fix") == ["site_asset", "seo_audit"]
    assert "gsc_28d" in _required_strategy_data("update_article")


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
    assert "source_audit_scanned_at" in source
    assert "CAST(:source_audit_scanned_at AS timestamptz)" in source
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


def test_daily_candidates_use_one_ranking_for_articles_and_on_page_actions():
    rows = [
        {
            "id": "article-low",
            "site_id": "site-a",
            "query": "low value article",
            "strategy_type": "new_article",
            "priority": "P2",
            "score": 25,
            "readiness_score": 0.8,
            "risk_score": 0.2,
            "days_since_last_action": 30,
        },
        {
            "id": "product-high",
            "site_id": "site-a",
            "query": "product metadata",
            "strategy_type": "on_page_fix",
            "priority": "P1",
            "score": 88,
            "readiness_score": 0.95,
            "risk_score": 0.05,
            "days_since_last_action": 20,
        },
    ]

    assert [row["id"] for row in _select_daily_candidates(rows, 1)] == ["product-high"]


def test_daily_candidate_ranking_respects_cooldown_risk_readiness_and_site_quota():
    rows = [
        {"id": "cooling", "site_id": "a", "strategy_type": "update_article", "priority": "P0", "score": 99, "in_cooldown": True},
        {"id": "unsafe", "site_id": "a", "strategy_type": "new_article", "priority": "P0", "score": 98, "risk_gate_passed": False},
        {"id": "ready-a", "site_id": "a", "strategy_type": "on_page_fix", "priority": "P1", "score": 80, "readiness_score": 0.9, "risk_score": 0.1},
        {"id": "ready-b", "site_id": "b", "strategy_type": "new_article", "priority": "P1", "score": 75, "readiness_score": 0.8, "risk_score": 0.2},
    ]

    assert [row["id"] for row in _select_daily_candidates(rows, 4, {"a": 1, "b": 1})] == ["ready-a", "ready-b"]


def test_candidate_with_incomplete_site_configuration_cannot_enter_plan():
    selected = _select_daily_candidates(
        [{
            "id": "bad-config",
            "site_id": "site-a",
            "strategy_type": "on_page_fix",
            "priority": "P0",
            "score": 100,
            "site_configuration_ready": False,
        }],
        1,
        {},
    )
    assert selected == []


def test_site_coverage_includes_disabled_unconfigured_and_candidate_free_sites():
    sites = [
        {
            "id": "site-disabled",
            "name": "Disabled",
            "strategy_enabled": False,
            "market": "US",
            "language_code": "en",
            "base_url": "https://disabled.example",
        },
        {
            "id": "site-unconfigured",
            "name": "Unconfigured",
            "strategy_enabled": True,
            "market": None,
            "language_code": "en",
            "base_url": None,
        },
        {
            "id": "site-empty",
            "name": "Empty",
            "strategy_enabled": True,
            "market": "US",
            "language_code": "en",
            "base_url": "https://empty.example",
        },
    ]

    coverage = _build_site_coverage(sites, candidates=[], selected=[])

    assert coverage["complete"] is True
    assert coverage["discovered_sites"] == 3
    assert coverage["decided_sites"] == 3
    assert {row["site_id"] for row in coverage["decisions"]} == {
        "site-disabled",
        "site-unconfigured",
        "site-empty",
    }
    by_site = {row["site_id"]: row for row in coverage["decisions"]}
    assert by_site["site-disabled"]["action"] == "configuration_repair"
    assert by_site["site-unconfigured"]["action"] == "configuration_repair"
    for site_id in ("site-disabled", "site-unconfigured"):
        repair = by_site[site_id]
        assert repair["block_reason"]
        assert repair["unlock_condition"]
        assert repair["responsibility_type"] == "site_configuration_owner"
        assert repair["review_by"]
        assert repair["alternative_evidence"]
        assert repair["consecutive_hold_count"] == 0
    assert set(by_site["site-unconfigured"]["missing_configuration"]) == {"base_url", "market"}
    for site_id in ("site-disabled", "site-unconfigured"):
        repair = by_site[site_id]
        assert repair["reevaluation_trigger"] == "next_strategy_run"
        assert repair["reevaluation_status"] == "configuration_incomplete"
        assert repair["last_checked_at"]
    assert by_site["site-empty"]["action"] == "hold"
    assert by_site["site-empty"]["unlock_condition"]


def test_configuration_repair_is_rechecked_and_reenters_candidates_when_fixed():
    broken_site = {
        "id": "site-a",
        "name": "A",
        "strategy_enabled": True,
        "market": None,
        "language_code": "en",
        "base_url": None,
    }
    first = _build_site_coverage([broken_site], candidates=[], selected=[])
    repair = first["decisions"][0]

    assert repair["action"] == "configuration_repair"
    assert set(repair["missing_configuration"]) == {"base_url", "market"}
    assert repair["reevaluation_status"] == "configuration_incomplete"

    fixed_site = {**broken_site, "market": "US", "base_url": "https://a.example"}
    candidate = {
        "id": "candidate-a",
        "site_id": "site-a",
        "strategy_type": "new_article",
        "reason": "Fresh evidence supports an independent topic.",
    }
    second = _build_site_coverage([fixed_site], candidates=[candidate], selected=[candidate])

    assert second["decisions"][0]["action"] == "new_article"
    assert "missing_configuration" not in second["decisions"][0]


@pytest.mark.asyncio
async def test_latest_audit_is_reloaded_after_evidence_refresh(monkeypatch):
    import app.services.strategy_service as service

    seen = []

    async def fake_load(session, *, business_id):
        seen.append(business_id)
        return (
            {"id": "audit-a", "scanned_at": "2026-07-26T00:00:00Z"}
            if len(seen) == 1
            else {"id": "audit-b", "scanned_at": "2026-07-27T00:00:00Z"}
        )

    monkeypatch.setattr(service, "_load_latest_source_audit", fake_load)

    audit_a = await service._load_latest_source_audit_after_refresh(
        object(),
        business_id="business-a",
        evidence_refreshes=[],
    )
    audit_b = await service._load_latest_source_audit_after_refresh(
        object(),
        business_id="business-a",
        evidence_refreshes=[{"status": "success", "snapshot_id": "snapshot-b"}],
    )

    assert audit_a["id"] == "audit-a"
    assert audit_b == {"id": "audit-b", "scanned_at": "2026-07-27T00:00:00Z"}
    assert seen == ["business-a", "business-a"]


def test_coverage_matrix_contains_main_site_and_every_blog_site():
    sites = [
        {"id": "main", "name": "Main", "site_type": "main", "strategy_enabled": True, "market": "US", "language_code": "en", "base_url": "https://main.example"},
        {"id": "blog-a", "name": "Blog A", "site_type": "blog", "strategy_enabled": True, "market": "US", "language_code": "en", "base_url": "https://a.example"},
        {"id": "blog-b", "name": "Blog B", "site_type": "blog", "strategy_enabled": True, "market": "US", "language_code": "en", "base_url": "https://b.example"},
    ]
    candidates = [
        {"id": "main-fix", "site_id": "main", "strategy_type": "on_page_fix", "reason": "Metadata gap"},
        {"id": "blog-a-new", "site_id": "blog-a", "strategy_type": "new_article", "reason": "Content gap"},
    ]

    coverage = _build_site_coverage(sites, candidates, candidates)

    assert coverage["complete"] is True
    assert {item["site_id"] for item in coverage["decisions"]} == {"main", "blog-a", "blog-b"}
    assert next(item for item in coverage["decisions"] if item["site_id"] == "blog-b")["action"] == "hold"


def test_site_coverage_keeps_decisions_when_daily_budget_is_zero():
    sites = [
        {
            "id": "site-a",
            "name": "A",
            "strategy_enabled": True,
            "market": "US",
            "language_code": "en",
            "base_url": "https://a.example",
        }
    ]
    candidates = [
        {
            "id": "candidate-a",
            "site_id": "site-a",
            "strategy_type": "new_article",
            "priority": "P1",
            "reason": "Independent demand",
        }
    ]

    coverage = _build_site_coverage(sites, candidates=candidates, selected=[])

    assert coverage["complete"] is True
    assert coverage["decisions"] == [
        {
            "site_id": "site-a",
            "site_name": "A",
            "action": "hold",
            "reason": "Candidate exists but is not in the current action budget.",
            "unlock_condition": "Increase or free the site action budget, then re-evaluate the candidate.",
            "candidate_count": 1,
            "selected_candidate_id": None,
        }
    ]


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
        "post_url": "https://www.example.com/blog/post/",
        "base_url": "https://example.com",
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
    assert first[0] == _candidate_keys("business-a", {**row, "post_id": "post-b"}, strategy)[0]
    assert first[0] != _candidate_keys(
        "business-a",
        {**row, "post_id": "post-b", "post_url": "https://example.com/blog/other"},
        strategy,
    )[0]


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


def test_new_article_without_keyword_or_page_analytics_can_pass_on_complete_evidence():
    strategy = _build_strategy(
        {
            "business_id": "exdivo",
            "site_id": "site-1",
            "market": "US",
            "language_code": "en",
            "query": "independent product use case",
            "keyword_id": None,
            "knowledge_profile": {
                "status": "confirmed",
                "generation_policy": {"risk_level": "standard", "allowed_sources": []},
            },
            "audit_score": 84,
            "audit_decision": {
                "action": "new_article",
                "priority": "P1",
                "product_or_category_match": True,
                "content_gap": True,
                "serp_intent_confirmed": True,
                "cannibalization_risk": "none",
                "product_facts": [{"name": "capacity", "value": "2 ml"}],
                "authority_sources": [],
            },
        },
        None,
        [],
    )

    assert strategy is not None
    assert strategy["strategy_type"] == "new_article"
    assert strategy["priority"] == "P1"
    assert strategy["risk_gate_passed"] is True


def test_high_risk_new_article_is_held_before_selection_when_sources_are_incomplete():
    strategy = _build_strategy(
        {
            "business_id": "exdivo",
            "site_id": "site-1",
            "market": "US",
            "language_code": "en",
            "query": "vaping safety and regulation",
            "knowledge_profile": {
                "status": "confirmed",
                "generation_policy": {"risk_level": "regulated", "allowed_sources": []},
            },
            "audit_score": 95,
            "audit_decision": {
                "action": "new_article",
                "priority": "P0",
                "product_or_category_match": True,
                "content_gap": True,
                "serp_intent_confirmed": True,
                "cannibalization_risk": "none",
                "product_facts": [{"name": "model", "value": "X"}],
                "authority_sources": [],
            },
        },
        None,
        [],
    )

    assert strategy is not None
    assert strategy["strategy_type"] == "hold"
    assert strategy["priority"] == "Hold"
    assert strategy["risk_gate_passed"] is False
    assert "authoritative_sources" in strategy["risk_gate_failures"]


def test_activity_pressure_cannot_bypass_high_risk_gate():
    strategy = _build_strategy(
        {
            "query": "medical vaping claim",
            "market": "US",
            "language_code": "en",
            "days_since_last_action": 90,
            "activity_reassessment": True,
            "knowledge_profile": {
                "status": "confirmed",
                "generation_policy": {"risk_level": "ymyl", "allowed_sources": []},
            },
            "audit_decision": {
                "action": "new_article",
                "priority": "P0",
                "product_or_category_match": True,
                "content_gap": True,
                "serp_intent_confirmed": True,
                "cannibalization_risk": "none",
                "product_facts": [],
                "authority_sources": [],
            },
        },
        None,
        [],
    )

    assert strategy and strategy["strategy_type"] == "hold"
    assert strategy["risk_gate_passed"] is False


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


@pytest.mark.asyncio
async def test_approving_on_page_decision_never_creates_article_execution() -> None:
    calls: list[tuple[str, dict]] = []

    class Result:
        def __init__(self, rows=None):
            self.rows = rows or []

        def mappings(self):
            return self

        def first(self):
            return self.rows[0] if self.rows else None

    class Session:
        committed = False

        async def execute(self, statement, params=None):
            sql = str(statement)
            calls.append((sql, params or {}))
            if "FOR UPDATE OF t" in sql:
                return Result(
                    [
                        {
                            "id": "strategy-id",
                            "site_id": "site-id",
                            "keyword_id": None,
                            "post_id": None,
                            "article_id": None,
                            "title": "修复产品页 SEO",
                            "decision": {
                                "strategy_type": "on_page_fix",
                                "business_id": "business",
                                "requires_publish": False,
                            },
                            "site_type": "main",
                            "site_status": "active",
                            "business_id": "business",
                            "strategy_enabled": True,
                            "task_business_id": "business",
                            "candidate_id": "candidate-id",
                            "plan_id": "plan-id",
                            "analysis_batch_id": "analysis-id",
                        }
                    ]
                )
            return Result()

        async def commit(self):
            self.committed = True

    session = Session()
    result = await review_strategy(
        session,  # type: ignore[arg-type]
        task_id="strategy-id",
        approved=True,
    )

    assert result["strategy_type"] == "on_page_fix"
    assert result["execution_task_id"] is None
    assert result["execution_status"] == "blocked_pending_preview"
    assert result["requires_publish"] is True
    assert f"/workflow/strategies/strategy-id/on-page/preview" in result["unlock_condition"]
    assert session.committed is True
    assert not any(
        "INSERT INTO seo_agent.tasks" in sql
        and params.get("task_type") in {"new_article", "update_article"}
        for sql, params in calls
    )


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
