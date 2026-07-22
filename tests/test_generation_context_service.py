from __future__ import annotations

import pytest

from app.api.v1 import endpoints
from app.services import article_generation_service, generation_context_service


@pytest.mark.asyncio
async def test_content_site_context_is_frozen_from_confirmed_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_site(*_args, **_kwargs):
        return {
            "id": "content-site",
            "site_key": "trail-notes",
            "name": "Trail Notes",
            "site_type": "wp",
            "is_main": False,
            "content_role": "outdoor guides",
            "content_scope": "hiking, reusable bottles",
            "allow_external_links": False,
            "knowledge_profile": {
                "status": "confirmed",
                "positioning": "Practical outdoor gear guides",
                "audience": "weekend hikers",
                "tone": "plain and specific",
                "in_scope_topics": ["hiking", "hydration"],
                "out_of_scope_topics": ["medical treatment"],
            },
        }

    monkeypatch.setattr(generation_context_service, "get_site", get_site)
    context = await generation_context_service.build_generation_context(
        None,  # type: ignore[arg-type]
        site_id="content-site",
        keyword={"keyword": "how to carry water while hiking", "market": "US", "language_code": "en"},
        approved_strategy={"strategy_type": "new_article", "user_question": "How much water should a day hiker carry?"},
        serp={"id": "serp-1", "source": "cache", "related_questions": [{"question": "What bottle works for day hikes?"}]},
    )

    assert context["site_role"] == "content"
    assert context["hold_reasons"] == []
    assert context["site_profile"]["audience"] == "weekend hikers"
    assert context["context_hash"] == generation_context_service.context_hash(context)


@pytest.mark.asyncio
async def test_main_site_without_conversion_target_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_site(*_args, **_kwargs):
        return {"id": "main-site", "site_type": "main", "is_main": True, "knowledge_profile": {"status": "confirmed"}}

    monkeypatch.setattr(generation_context_service, "get_site", get_site)
    context = await generation_context_service.build_generation_context(
        None,  # type: ignore[arg-type]
        site_id="main-site",
        keyword={"keyword": "best hiking bottle"},
        approved_strategy={"strategy_type": "new_article"},
        serp={},
    )

    assert "main_site_target_asset_missing" in context["hold_reasons"]


@pytest.mark.asyncio
async def test_main_site_target_without_verified_facts_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_site(*_args, **_kwargs):
        return {"id": "main-site", "site_type": "main", "is_main": True, "knowledge_profile": {"status": "confirmed"}}

    monkeypatch.setattr(generation_context_service, "get_site", get_site)
    context = await generation_context_service.build_generation_context(
        None,  # type: ignore[arg-type]
        site_id="main-site",
        keyword={"keyword": "best hiking bottle", "target_asset_url": "https://example.com/products/bottle"},
        approved_strategy={"strategy_type": "new_article"},
        serp={},
    )

    assert "main_site_verified_facts_missing" in context["hold_reasons"]


@pytest.mark.asyncio
async def test_service_business_uses_explicit_commercial_role_and_verified_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_site(*_args, **_kwargs):
        return {
            "id": "service-site",
            "site_type": "other",
            "is_main": False,
            "knowledge_profile": {
                "status": "confirmed",
                "positioning": "Local plumbing services",
                "audience": "homeowners",
                "in_scope_topics": ["emergency plumbing"],
                "generation_policy": {"site_role": "local_service", "business_type": "local services"},
                "verified_assets": [{"url": "https://example.com/emergency-plumber", "type": "service", "facts": ["24-hour emergency callout"]}],
            },
        }

    monkeypatch.setattr(generation_context_service, "get_site", get_site)
    context = await generation_context_service.build_generation_context(
        None,  # type: ignore[arg-type]
        site_id="service-site",
        keyword={"keyword": "emergency plumber", "target_asset_url": "https://example.com/emergency-plumber"},
        approved_strategy={"strategy_type": "new_article"},
        serp={},
    )

    assert context["site_role"] == "main"
    assert context["hold_reasons"] == []
    assert context["target_asset"]["verified_facts"] == ["24-hour emergency callout"]


@pytest.mark.asyncio
async def test_content_business_without_user_question_or_evidence_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_site(*_args, **_kwargs):
        return {
            "id": "content-site",
            "site_type": "blog",
            "knowledge_profile": {"status": "confirmed", "positioning": "Home improvement guides", "audience": "homeowners", "in_scope_topics": ["repairs"]},
        }

    monkeypatch.setattr(generation_context_service, "get_site", get_site)
    context = await generation_context_service.build_generation_context(
        None,  # type: ignore[arg-type]
        site_id="content-site",
        keyword={"keyword": "fix a leaky tap"},
        approved_strategy={"strategy_type": "new_article"},
        serp={},
    )

    assert "content_user_question_missing" in context["hold_reasons"]
    assert "content_serp_or_sources_missing" in context["hold_reasons"]


@pytest.mark.asyncio
async def test_financial_policy_without_allowed_source_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_site(*_args, **_kwargs):
        return {
            "id": "finance-site",
            "site_type": "blog",
            "knowledge_profile": {
                "status": "confirmed",
                "positioning": "Personal finance education",
                "audience": "new investors",
                "in_scope_topics": ["mortgages"],
                "generation_policy": {"keyword_triggers": {"finance": ["mortgage"]}, "allowed_sources": []},
            },
        }

    monkeypatch.setattr(generation_context_service, "get_site", get_site)
    context = await generation_context_service.build_generation_context(
        None,  # type: ignore[arg-type]
        site_id="finance-site",
        keyword={"keyword": "mortgage payment guide"},
        approved_strategy={"strategy_type": "new_article", "user_question": "How do mortgage payments work?"},
        serp={"id": "serp-1"},
    )

    assert "high_risk_sources_missing" in context["hold_reasons"]


def test_multilingual_question_qa_and_slug_are_not_silently_downgraded() -> None:
    assert article_generation_service._slug("安全用药指南") != "article"
    assert article_generation_service._slug("安全用药指南") != article_generation_service._slug("儿童用药指南")

    missing = article_generation_service._answers_context_questions("# 标题\n\n## 说明\n\n这里没有用药答案。", ["儿童安全用药需要注意什么？"])
    covered = article_generation_service._answers_context_questions("# 标题\n\n## 儿童安全用药需要注意什么\n\n儿童用药前请阅读医生说明。", ["儿童安全用药需要注意什么？"])

    assert missing is False
    assert covered is True


def test_unrelated_serp_questions_are_not_made_mandatory_for_article_qa() -> None:
    questions = generation_context_service._user_questions(
        {"keyword": "Liquid Flavoring: How To Choose Without Overthinking"},
        {},
        {"related_questions": [
            {"question": "What is the healthiest brand of water flavoring?"},
            {"question": "How unhealthy is water flavoring?"},
        ]},
    )

    assert questions == []


def test_context_changes_article_prompt_and_quality_gates_high_risk_claims() -> None:
    first = {"context_version": "v1", "context_hash": "a", "site_profile": {"positioning": "Hiking guides"}}
    second = {"context_version": "v1", "context_hash": "b", "site_profile": {"positioning": "Urban commuting guides"}}
    assert article_generation_service._compose_article_prompt({}, {}, "brief", {}, "outline", generation_context=first) != article_generation_service._compose_article_prompt({}, {}, "brief", {}, "outline", generation_context=second)

    content = "# Bottle Safety\n\n## Details\n\nBattery safety matters.\n\n## FAQ\n\nAnswer.\n" + ("Useful detail. " * 100)
    context = {"required_modules": [], "target_asset": {}, "site_profile": {}, "keyword_and_intent": {}, "article_goal": {}, "facts_and_sources": [], "requires_claim_sources": True}
    checks = {item["key"]: item["ok"] for item in article_generation_service._qa(content, "bottle safety", "A valid meta description that is long enough to pass the article quality gate and describe the page clearly for search users.", generation_context=context)}

    assert checks["high_risk_claims_have_sources"] is False


@pytest.mark.asyncio
async def test_dry_run_returns_article_without_save_or_keyword_write(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve_site_id(*_args, **_kwargs):
        return "site-id"

    async def context(*_args, **_kwargs):
        return {
            "context_version": "v1",
            "context_hash": "hash",
            "site_role": "content",
            "site_profile": {},
            "keyword_and_intent": {},
            "article_goal": {},
            "target_asset": {},
            "link_policy": {"internal_links": []},
            "facts_and_sources": [],
            "required_modules": [],
            "hold_reasons": [],
        }

    async def brief(*_args, **_kwargs):
        return {"brief": "brief"}

    async def outline(*_args, **_kwargs):
        return {"content": "# Outline"}

    async def generate(*_args, **_kwargs):
        return {"content": "# Test topic\n\n## Details\n\n" + ("Useful detail. " * 120)}

    async def save(*_args, **_kwargs):
        raise AssertionError("dry run must not save an article")

    monkeypatch.setattr(article_generation_service, "is_stage_configured", lambda _stage: True)
    monkeypatch.setattr(article_generation_service, "resolve_site_id", resolve_site_id)
    monkeypatch.setattr(article_generation_service, "build_generation_context", context)
    monkeypatch.setattr(article_generation_service, "build_brief_with_optional_ai", brief)
    monkeypatch.setattr(article_generation_service, "_generate_outline", outline)
    monkeypatch.setattr(article_generation_service, "generate_ai_content", generate)
    monkeypatch.setattr(article_generation_service, "save_article", save)
    monkeypatch.setattr(article_generation_service, "_qa", lambda *args, **kwargs: [{"key": "ok", "ok": True}])

    class Session:
        calls: list = []

        async def execute(self, statement, params=None):
            self.calls.append((str(statement), params))

        async def commit(self):
            raise AssertionError("dry run must not commit")

    result = await article_generation_service.generate_article_pipeline(
        Session(),  # type: ignore[arg-type]
        None,
        forced_site_id="site-id",
        approved_strategy={"strategy_type": "update_article"},
        keyword_context={"id": None, "keyword": "test topic", "market": "US", "language_code": "en"},
        dry_run=True,
        serp_override={"id": None, "source": "test", "organic_results": []},
    )

    assert result["status"] == "done"
    assert result["dryRun"] is True
    assert result["article"] is None


@pytest.mark.asyncio
async def test_article_test_endpoint_always_requests_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def pipeline(*_args, **kwargs):
        captured.update(kwargs)
        return {"status": "done", "dryRun": True, "content": "# Test"}

    monkeypatch.setattr(endpoints, "generate_article_pipeline", pipeline)
    result = await endpoints.article_test(
        endpoints.ArticleTestBody(siteId="site-id", keyword="test topic"),
        None,  # type: ignore[arg-type]
    )

    assert captured["dry_run"] is True
    assert "task_id" not in captured
    assert result["testMode"] is True
    assert result["persistence"] == "none"
