from __future__ import annotations

from typing import Any
from datetime import date

import pytest

from app.clients import ai_provider
from app.clients.http_client import ExternalCallError
from app.api.v1 import endpoints
from app.services import article_generation_service, keyword_ai_service, publish_service, strategy_service


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
async def test_ai_provenance_uses_vendor_and_response_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "model": "deepseek-v4-pro-202607",
            "choices": [{"message": {"content": "ok"}}],
        }

    monkeypatch.setattr(ai_provider, "is_stage_configured", lambda stage: True)
    monkeypatch.setattr(ai_provider, "request_json", request_json)
    monkeypatch.setattr(ai_provider._settings, "ai_article_generation_base_url", "https://api.openai-proxy.org/v1")
    monkeypatch.setattr(ai_provider._settings, "ai_article_generation_provider", "deepseek")
    monkeypatch.setattr(ai_provider._settings, "ai_article_generation_model", "deepseek-v4-pro")

    result = await ai_provider.generate_ai_content(stage="article_generation", prompt="test")

    assert result["provider"] == "deepseek"
    assert result["providerSource"] == "configuration"
    assert result["requestedModel"] == "deepseek-v4-pro"
    assert result["model"] == "deepseek-v4-pro-202607"
    assert result["modelSource"] == "response"


@pytest.mark.asyncio
async def test_ai_provenance_infers_vendor_instead_of_protocol_name(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai_provider, "is_stage_configured", lambda stage: True)
    monkeypatch.setattr(ai_provider, "request_json", request_json)
    monkeypatch.setattr(ai_provider._settings, "ai_article_generation_base_url", "https://proxy.example/v1")
    monkeypatch.setattr(ai_provider._settings, "ai_article_generation_provider", "")
    monkeypatch.setattr(ai_provider._settings, "ai_article_generation_model", "gpt-5.1-2026-06-01")

    result = await ai_provider.generate_ai_content(stage="article_generation", prompt="test")

    assert result["provider"] == "openai"
    assert result["providerSource"] == "inference"
    assert result["model"] == "gpt-5.1-2026-06-01"
    assert result["modelSource"] == "configuration"


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


def test_article_qa_accepts_strategy_target_url_as_contextual_link() -> None:
    target_url = "https://avinoti.shop/blogs/detail/2624326"
    content = (
        "# Titanium Insulated Bottle Guide\n\n"
        "## What to compare\n\n"
        f"Read the [titanium cutting board guide]({target_url}) for another material comparison.\n\n"
        "## FAQ\n\nTitanium insulated bottle questions answered."
    )
    description = (
        "Compare titanium insulated bottles by capacity, lid, dimensions, care, "
        "weight and insulation details before choosing one for daily travel."
    )
    context = {
        "required_modules": ["contextual_internal_link"],
        "target_asset": {},
        "site_profile": {},
        "keyword_and_intent": {},
        "article_goal": {},
        "facts_and_sources": [],
    }

    checks = {
        item["key"]: item["ok"]
        for item in article_generation_service._qa(
            content,
            "titanium insulated bottle",
            description,
            internal_link_plan=[{"target_url": target_url}],
            generation_context=context,
        )
    }

    assert checks["has_planned_internal_link"] is True
    assert checks["has_required_contextual_link"] is True


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


def test_legacy_strategy_write_routes_are_retired() -> None:
    routes = {(route.path, tuple(sorted(route.methods or []))) for route in endpoints.router.routes}

    assert ("/workflow/strategies/candidates", ("GET",)) in routes
    retired = {
        "/workflow/strategies/generate",
        "/workflow/strategies/plan",
        "/workflow/strategies/{task_id}/review",
        "/workflow/strategies/{task_id}/execute",
        "/workflow/strategies/{task_id}/cancel",
        "/workflow/strategies/{strategy_task_id}/stop",
        "/workflow/automation/run-once",
        "/workflow/automation/clear-queue",
        "/workflow/automation/settings",
    }
    assert not retired.intersection(path for path, _methods in routes)


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
