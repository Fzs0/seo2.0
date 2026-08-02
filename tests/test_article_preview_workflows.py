from __future__ import annotations

import pytest

from app.services import brief_service


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
