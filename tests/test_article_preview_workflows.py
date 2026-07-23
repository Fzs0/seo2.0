from __future__ import annotations

import pytest

from app.services import article_generation_service, brief_service


class _Store:
    def get(self, key: str, default=None):
        values = {
            "articleBriefTemplate.modules": [{"key": "intro"}],
            "anchorTextRules": {"max": 2},
            "articleOutputFormat": {"format": "markdown"},
        }
        return values.get(key, default)


def test_prompt_preview_prefers_override_and_preserves_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brief_service, "get_store", lambda: _Store())
    monkeypatch.setattr(brief_service, "locale_for_project", lambda _project: {"googleGl": "us", "googleHl": "en"})
    monkeypatch.setattr(brief_service, "article_brief_template_for", lambda _item, _project: {"modules": ["intro"]})
    monkeypatch.setattr(brief_service, "reference_plan", lambda _item: {"triggered": False, "sources": []})
    monkeypatch.setattr(brief_service, "build_brief", lambda *_args: pytest.fail("override must skip local brief"))

    result = brief_service.build_prompt_preview(
        keyword={"keyword": "heat pump"},
        project={"domain": "example.com", "market": "US", "coreProducts": "HVAC"},
        brief_override="  approved brief  ",
        brief="ignored",
    )

    assert set(result) == {"brief", "locale", "articleBriefTemplate", "prompt"}
    assert result["brief"] == "approved brief"
    assert result["locale"] == {"googleGl": "us", "googleHl": "en"}
    assert result["articleBriefTemplate"] == {"modules": ["intro"]}
    assert "主站：example.com" in result["prompt"]
    assert "approved brief" in result["prompt"]
    assert "gl=us / hl=en" in result["prompt"]


def test_prompt_preview_falls_back_to_local_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brief_service, "get_store", lambda: _Store())
    monkeypatch.setattr(brief_service, "locale_for_project", lambda _project: {})
    monkeypatch.setattr(brief_service, "article_brief_template_for", lambda _item, _project: {})
    monkeypatch.setattr(brief_service, "reference_plan", lambda _item: {})
    monkeypatch.setattr(brief_service, "build_brief", lambda _item, _project: {"brief": "local brief"})

    result = brief_service.build_prompt_preview(keyword={"keyword": "test"})

    assert result["brief"] == "local brief"
    assert "gl=not-set / hl=not-set" in result["prompt"]


@pytest.mark.asyncio
async def test_legacy_article_preview_maps_ai_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(article_generation_service, "is_stage_configured", lambda _stage: True)

    async def generate(**kwargs):
        assert kwargs["stage"] == "article_generation"
        assert "Primary keyword: heat pump" in kwargs["prompt"]
        return {"content": "# Draft", "provider": "proxy", "model": "model-x", "status": None}

    monkeypatch.setattr(article_generation_service, "generate_ai_content", generate)

    result = await article_generation_service.generate_legacy_article_preview(
        keyword={"keyword": "heat pump"},
        project={"domain": "example.com"},
        brief="brief",
        prompt="custom instruction",
    )

    assert result == {
        "content": "# Draft",
        "provider": "proxy",
        "model": "model-x",
        "status": None,
        "generated": True,
    }


@pytest.mark.asyncio
async def test_legacy_article_preview_preserves_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(article_generation_service, "is_stage_configured", lambda _stage: False)
    monkeypatch.setattr(
        article_generation_service,
        "reference_plan",
        lambda _item: {
            "triggered": True,
            "sources": [{"name": "Source", "label": "Evidence", "url": "https://example.com/evidence"}],
        },
    )
    monkeypatch.setattr(
        article_generation_service,
        "image_plan_for",
        lambda _item: [{"name": "hero", "position": "after intro"}],
    )

    result = await article_generation_service.generate_legacy_article_preview(
        keyword={"keyword": "heat pump"},
    )

    assert result["generated"] is False
    assert result["status"] == "ai-not-configured"
    assert "# Heat Pump" in result["content"]
    assert "- hero: after intro" in result["content"]
    assert "[Evidence](https://example.com/evidence)" in result["content"]
