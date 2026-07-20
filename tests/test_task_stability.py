from __future__ import annotations

import asyncio
from typing import Any
from datetime import date

import pytest

from app.clients import ai_provider
from app.clients.http_client import ExternalCallError
from app.api.v1 import endpoints
from app.services import article_generation_service, automation_service, keyword_ai_service, publish_service, strategy_service


@pytest.mark.asyncio
async def test_ai_requests_use_ai_specific_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def request_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai_provider, "is_stage_configured", lambda stage: True)
    monkeypatch.setattr(ai_provider, "request_json", request_json)
    monkeypatch.setattr(ai_provider._settings, "ai_timeout_seconds", 90)
    monkeypatch.setattr(ai_provider._settings, "ai_retry_max", 2)

    result = await ai_provider.generate_ai_content(stage="article_generation", prompt="test")

    assert result["content"] == "ok"
    assert captured["timeout"] == 90
    assert captured["max_attempts"] == 2


@pytest.mark.asyncio
async def test_ai_failure_keeps_provider_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ExternalCallError("ai_keyword_analysis 状态码 429")

    monkeypatch.setattr(ai_provider, "is_stage_configured", lambda stage: True)
    monkeypatch.setattr(ai_provider, "request_json", request_json)

    result = await ai_provider.generate_ai_content(stage="keyword_analysis", prompt="test")

    assert result["status"] == "ai-request-failed"
    assert result["error"] == "ai_keyword_analysis 状态码 429"


@pytest.mark.asyncio
async def test_task_stage_is_persisted() -> None:
    class Session:
        params: dict[str, Any] | None = None
        committed = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> None:
            assert "current_stage" in str(statement)
            self.params = params

        async def commit(self) -> None:
            self.committed = True

    session = Session()
    await article_generation_service._set_task_stage(session, "task-id", "outline")  # type: ignore[arg-type]

    assert session.params == {"id": "task-id", "stage": "outline"}
    assert session.committed


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def execute(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("database unavailable")

    class Owner:
        message = ""

        def cancel(self, message: str) -> None:
            self.message = message

    owner = Owner()
    monkeypatch.setattr(strategy_service, "SessionLocal", Session)

    await strategy_service._execution_heartbeat("task-id", owner, interval_seconds=0)  # type: ignore[arg-type]

    assert owner.message == "心跳异常：database unavailable"


def test_strategy_without_gsc_reuses_keyword_priority() -> None:
    strategy = strategy_service._build_strategy(
        {
            "query": "how much does a vape cost",
            "keyword_priority": "P1",
            "keyword_score": 75,
            "volume": 2900,
        },
        None,
        [],
    )

    assert strategy["priority"] == "P1"
    assert strategy["score"] == 75


def test_article_prompt_includes_current_date() -> None:
    prompt = article_generation_service._compose_article_prompt({}, {}, "brief", {}, "outline")

    assert date.today().isoformat() in prompt


def test_article_prompt_includes_approved_strategy_context() -> None:
    strategy = {
        "recommended_action": "Add the comparison table",
        "site_context": {"knowledge_profile": {"tone": "neutral"}},
        "evidence": {"content_audit": {"competitor_gap": {"missing_sections": ["FAQ"]}}},
    }

    prompt = article_generation_service._compose_article_prompt({}, {}, "brief", {}, "outline", strategy)

    assert "Add the comparison table" in prompt
    assert '"tone": "neutral"' in prompt
    assert '"missing_sections"' in prompt


@pytest.mark.asyncio
async def test_outline_uses_approved_strategy_instead_of_stale_keyword_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = ""

    async def generate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal captured
        captured = kwargs["prompt"]
        return {"content": "# Outline"}

    monkeypatch.setattr(article_generation_service, "generate_ai_content", generate)
    await article_generation_service._generate_outline(
        {"ai_review": {"strategy": {"recommended_action": "stale"}}},
        {},
        "brief",
        {},
        {"recommended_action": "approved"},
    )

    assert '"recommended_action": "approved"' in captured
    assert '"recommended_action": "stale"' not in captured


def test_article_qa_blocks_stale_year_and_accepts_full_faq_heading() -> None:
    content = "# 2024 Best Vape for 2026\n\n## Frequently Asked Questions\n\nbest vape\n" + ("word " * 300)
    description = "Learn how to choose the best vape for your routine, compare the features that matter, and make a confident decision without wasting time or money."

    checks = {item["key"]: item["ok"] for item in article_generation_service._qa(content, "best vape", description)}
    short_checks = {item["key"]: item["ok"] for item in article_generation_service._qa(content, "best vape", "Too short")}

    assert checks["has_faq"] is True
    assert checks["has_meta_description"] is True
    assert short_checks["has_meta_description"] is False
    assert checks["title_year_is_current"] is False


def test_missing_meta_description_falls_back_to_reader_facing_intro() -> None:
    content = (
        "# Best Vape Guide\n\n"
        "**Choosing the best vape starts with matching battery life, nicotine strength, and device size to your daily routine. "
        "This guide compares the practical tradeoffs so adult shoppers can decide confidently without paying for features they do not need.**\n\n"
        "## What to compare\n\nMore detail."
    )

    description = article_generation_service._meta_description(content)

    assert 120 <= len(description) <= 160
    assert not any(mark in description for mark in ('#', '*', '`'))


def test_generated_meta_description_is_trimmed_to_qa_limit() -> None:
    description = "Overwhelmed by Skywalker Digiflavor options? Our 3-step scenario guide helps new vapers, commuters, and flavor explorers pick the right device without the jargon."

    normalized = article_generation_service._normalize_meta_description(description)

    assert 120 <= len(normalized) <= 160
    assert normalized.endswith(".")
    assert 120 <= len(article_generation_service._normalize_meta_description("Short " + "x" * 200)) <= 160


def test_keyword_qa_accepts_title_and_punctuation_variants() -> None:
    checks = {item["key"]: item["ok"] for item in article_generation_service._qa(
        "# G-Wiz Vape Review\n\n## FAQ\n\n" + ("Useful detail. " * 100),
        "g wiz vape review",
        "A valid meta description that is long enough to pass the article quality gate and describe the page clearly for search users.",
        "G Wiz Vape Review",
    )}

    assert checks["has_keyword"] is True


def test_article_slug_uses_primary_keyword_and_stays_short() -> None:
    slug = article_generation_service._slug("G Wiz Vape Review")

    assert slug == "g-wiz-vape-review"
    assert len(slug) <= 70


def test_article_retry_refreshes_qa_and_generation_context() -> None:
    import inspect

    source = inspect.getsource(__import__("app.services.article_service", fromlist=["save_article"]).save_article)
    assert "qa_checklist = EXCLUDED.qa_checklist" in source
    assert "prompt_text = EXCLUDED.prompt_text" in source
    assert "article_parts = EXCLUDED.article_parts" in source


@pytest.mark.asyncio
async def test_publish_rejects_failed_qa() -> None:
    class Result:
        def mappings(self) -> "Result":
            return self

        def first(self) -> dict[str, Any]:
            return {"id": "article-id", "site_id": "site-id", "content_md": "# Body", "qa_checklist": [{"key": "has_meta_description", "ok": False}]}

    class Session:
        async def execute(self, *args: Any, **kwargs: Any) -> Result:
            return Result()

    with pytest.raises(publish_service.PublishError, match="QA not passed"):
        await publish_service.publish_article(Session(), article_id="article-id", site_id=None)  # type: ignore[arg-type]


def test_execution_queue_has_three_global_slots() -> None:
    assert automation_service._execution_slots(0) == 3
    assert automation_service._execution_slots(1) == 2
    assert automation_service._execution_slots(3) == 0


@pytest.mark.asyncio
async def test_keyword_analysis_noop_does_not_create_empty_task(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        def mappings(self) -> "Result":
            return self

        def first(self) -> None:
            return None

    class Session:
        async def execute(self, *args: Any, **kwargs: Any) -> Result:
            return Result()

    async def resolve(*args: Any, **kwargs: Any) -> str:
        return "exdivo"

    async def count(*args: Any, **kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(keyword_ai_service, "_resolve_analysis_business", resolve)
    monkeypatch.setattr(keyword_ai_service, "_count_unanalyzed", count)

    result = await keyword_ai_service.start_keyword_analysis(Session(), business_id="exdivo")  # type: ignore[arg-type]

    assert result["run_id"] == ""
    assert result["status"] == "done"
    assert result["decision"]["total"] == 0


def test_failed_article_qa_does_not_mark_keyword_written() -> None:
    import inspect

    source = inspect.getsource(article_generation_service.generate_article_pipeline)
    assert "if keyword_id and not failed_qa:" in source


@pytest.mark.asyncio
async def test_specific_execution_keeps_running_after_request_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        def __init__(self, value: object) -> None:
            self.value = value

        def mappings(self) -> "Result":
            return self

        def first(self) -> object:
            return self.value

        def scalar_one(self) -> object:
            return self.value

    class Session:
        calls = 0

        async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
            self.calls += 1
            if self.calls == 1:
                assert params == {"id": "strategy-id"}
                return Result({"execution_id": "execution-id", "status": "queued"})
            if self.calls == 2:
                return Result(0)
            assert params == {"id": "execution-id"}
            return Result(object())

        async def commit(self) -> None:
            return None

    keep_running = asyncio.Event()

    async def run(*args: Any) -> None:
        await keep_running.wait()

    monkeypatch.setattr(automation_service, "_run_claimed_execution", run)
    result = await automation_service.start_execution(Session(), "strategy-id")  # type: ignore[arg-type]
    task = automation_service._AUTOMATION_RUNS["execution-id"]
    try:
        assert result == {"ok": True, "status": "running", "execution_task_id": "execution-id"}
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        automation_service._AUTOMATION_RUNS.pop("execution-id", None)


@pytest.mark.asyncio
async def test_user_stop_cancels_running_execution() -> None:
    class Result:
        def __init__(self, value: object) -> None:
            self.value = value

        def scalar_one_or_none(self) -> object:
            return self.value

        def mappings(self) -> "Result":
            return self

        def first(self) -> object:
            return self.value

    class Session:
        statement = ""
        params: dict[str, Any] | None = None
        committed = False
        calls = 0

        async def execute(self, statement: Any, params: dict[str, Any]) -> Result:
            self.calls += 1
            if self.calls == 1:
                assert "current_stage" in str(statement)
                assert params == {"id": "strategy-id"}
                return Result({"execution_id": "execution-id", "status": "running", "current_stage": "article"})
            self.statement = str(statement)
            self.params = params
            return Result(object())

        async def commit(self) -> None:
            self.committed = True

    async def running() -> None:
        await asyncio.Event().wait()

    execution_id = "execution-id"
    task = asyncio.create_task(running())
    automation_service._AUTOMATION_RUNS[execution_id] = task
    session = Session()

    result = await automation_service.stop_execution(session, "strategy-id")  # type: ignore[arg-type]

    assert task.cancelled()
    assert execution_id not in automation_service._AUTOMATION_RUNS
    assert "status = 'canceled'" in session.statement
    assert "status IN ('running', 'failed')" in session.statement
    assert session.params == {"id": execution_id}
    assert session.committed
    assert result == {"ok": True, "status": "canceled", "execution_task_id": execution_id}


def test_stop_execution_api_uses_strategy_task_id() -> None:
    route = next(route for route in endpoints.router.routes if route.path == "/workflow/strategies/{strategy_task_id}/stop")

    assert route.methods == {"POST"}


def test_review_api_can_start_only_the_reviewed_strategy() -> None:
    import inspect

    source = inspect.getsource(endpoints.review_strategy_task)
    assert "body.executeNow" in source
    assert "start_execution(session, task_id)" in source
    start_source = inspect.getsource(automation_service.start_execution)
    assert "WHERE r.id = CAST(:id AS uuid)" in start_source
    assert "_claim_execution(session, row[\"execution_id\"])" in start_source


def test_strategy_candidate_and_plan_routes_are_registered() -> None:
    routes = {(route.path, tuple(sorted(route.methods or []))) for route in endpoints.router.routes}

    assert ("/workflow/strategies/candidates", ("GET",)) in routes
    assert ("/workflow/strategies/plan", ("GET",)) in routes
    assert ("/workflow/strategies/plan", ("PUT",)) in routes
    legacy = endpoints.StrategyGenerateBody(businessId="business-a")
    assert legacy.limit == 4
    assert legacy.actionBudget is None
    assert endpoints.StrategyGenerateBody(businessId="business-a", actionBudget=0).actionBudget == 0


def test_clear_queue_api_requires_business_scope() -> None:
    import inspect

    signature = inspect.signature(endpoints.clear_automation_queue)
    assert signature.parameters["business_id"].default is inspect.Parameter.empty


def test_keyword_ai_only_loads_enabled_sites_for_the_requested_businesses():
    import inspect

    source = inspect.getsource(keyword_ai_service._load_sites)
    assert "strategy_enabled = true" in source
    assert "business_id = ANY(:business_ids)" in source
    assert "site_type IN ('blog', 'wp')" not in source


@pytest.mark.asyncio
async def test_orphaned_keyword_analysis_is_reported_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        def mappings(self) -> "Result":
            return self

        def first(self) -> dict[str, Any]:
            return {"id": "run-id", "status": "queued", "error_message": None}

    class Session:
        async def execute(self, *args: Any, **kwargs: Any) -> Result:
            return Result()

    captured: dict[str, Any] = {}

    async def set_status(session: Any, run_id: str, status: str, **kwargs: Any) -> None:
        captured.update({"run_id": run_id, "status": status, **kwargs})

    monkeypatch.setattr(keyword_ai_service, "_set_analysis_status", set_status)
    keyword_ai_service._ANALYSIS_RUNS.clear()

    result = await keyword_ai_service.get_keyword_analysis(Session(), "run-id")  # type: ignore[arg-type]

    assert result and result["status"] == "failed"
    assert captured["status"] == "failed"
