from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

import json

from app.services.article_qa import ArticleQaContractError, ArticleQaState, assess_article_qa
from app.services.article_service import get_article, save_article
from app.services.publish_service import PublishError, publish_article


def test_assess_article_qa_accepts_canonical_checks_without_mutating_input() -> None:
    raw = [
        {"key": "title_length", "ok": True, "value": 58},
        {"key": "sources_present", "ok": False},
    ]
    original = deepcopy(raw)

    assessment = assess_article_qa(raw)

    assert assessment.state is ArticleQaState.VALID
    assert assessment.passed is False
    assert assessment.failed_keys == ("sources_present",)
    assert assessment.checks == tuple(raw)
    assert raw == original


def test_assess_article_qa_preserves_and_converts_legacy_envelope() -> None:
    raw = {
        "ok": True,
        "checks": {
            "word_count": True,
            "images_present": True,
        },
        "word_count": 1778,
        "image_count": 2,
        "content_sha256": "abc123",
    }

    assessment = assess_article_qa(raw)

    assert assessment.state is ArticleQaState.LEGACY
    assert assessment.passed is True
    assert assessment.checks == (
        {"key": "images_present", "ok": True},
        {"key": "word_count", "ok": True, "value": 1778},
    )
    assert assessment.summary == {
        "ok": True,
        "word_count": 1778,
        "image_count": 2,
        "content_sha256": "abc123",
        "source_shape": "legacy_envelope",
    }


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        (None, ArticleQaState.MISSING),
        ([], ArticleQaState.MISSING),
        ("not-json-contract", ArticleQaState.INVALID),
        ({"checks": {"word_count": 1}}, ArticleQaState.INVALID),
        ([{"key": "", "ok": True}], ArticleQaState.INVALID),
        ([{"key": "duplicate", "ok": True}, {"key": "duplicate", "ok": False}], ArticleQaState.INVALID),
    ],
)
def test_assess_article_qa_fails_closed_for_missing_or_invalid_shapes(
    raw: object,
    state: ArticleQaState,
) -> None:
    assessment = assess_article_qa(raw)

    assert assessment.state is state
    assert assessment.passed is False
    assert assessment.message


def test_assess_article_qa_rejects_conflicting_legacy_summary() -> None:
    assessment = assess_article_qa({
        "ok": True,
        "checks": {"title_length": True, "sources_present": False},
    })

    assert assessment.state is ArticleQaState.INVALID
    assert assessment.passed is False
    assert assessment.failed_keys == ("sources_present",)
    assert "冲突" in (assessment.message or "")


def test_assess_article_qa_rejects_non_boolean_canonical_summary_result() -> None:
    assessment = assess_article_qa(
        [{"key": "title_length", "ok": True}],
        {"ok": "yes"},
    )

    assert assessment.state is ArticleQaState.INVALID
    assert assessment.passed is False
    assert "布尔" in (assessment.message or "")


class _Rows:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> dict[str, Any] | None:
        return self.row


class _ArticleSession:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.calls = 0
        self.statements: list[str] = []

    async def execute(self, statement: Any, *_args: Any, **_kwargs: Any) -> _Rows:
        self.calls += 1
        self.statements.append(str(statement))
        return _Rows(self.row)


class _WriteSession:
    def __init__(self) -> None:
        self.params: dict[str, Any] | None = None
        self.commits = 0

    async def execute(self, _sql: Any, params: dict[str, Any]) -> _Rows:
        self.params = params
        return _Rows({"id": "article-id", "title": params["title"]})

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_get_article_returns_stable_qa_contract_for_legacy_row() -> None:
    session = _ArticleSession({
        "id": "article-id",
        "published_post_id": "remote-42",
        "qa_checklist": {
            "ok": True,
            "checks": {"word_count": True, "images_present": True},
            "word_count": 1778,
        },
        "qa_summary": {},
    })

    article = await get_article(session, "article-id")  # type: ignore[arg-type]

    assert article is not None
    assert article["qa_state"] == "legacy"
    assert article["qa_passed"] is True
    assert article["published_post_id"] == "remote-42"
    assert "published_post_id" in session.statements[0]
    assert article["qa_checklist"] == [
        {"key": "images_present", "ok": True},
        {"key": "word_count", "ok": True, "value": 1778},
    ]
    assert article["qa_summary"]["word_count"] == 1778


@pytest.mark.asyncio
async def test_save_article_canonicalizes_legacy_qa_before_persistence() -> None:
    session = _WriteSession()

    await save_article(
        session,  # type: ignore[arg-type]
        {
            "title": "Contract test",
            "qa_checklist": {
                "ok": True,
                "checks": {"word_count": True},
                "word_count": 1778,
                "content_sha256": "abc123",
            },
        },
    )

    assert session.commits == 1
    assert session.params is not None
    assert json.loads(session.params["qa_checklist"]) == [
        {"key": "word_count", "ok": True, "value": 1778},
    ]
    assert json.loads(session.params["qa_summary"]) == {
        "ok": True,
        "word_count": 1778,
        "content_sha256": "abc123",
        "source_shape": "legacy_envelope",
    }


@pytest.mark.asyncio
async def test_save_article_does_not_treat_invalid_empty_summary_as_missing() -> None:
    session = _WriteSession()

    with pytest.raises(ArticleQaContractError, match="summary"):
        await save_article(
            session,  # type: ignore[arg-type]
            {
                "title": "Contract test",
                "qa_checklist": [{"key": "word_count", "ok": True}],
                "qa_summary": [],
            },
        )

    assert session.params is None
    assert session.commits == 0


@pytest.mark.asyncio
async def test_publish_article_rejects_invalid_qa_as_domain_error() -> None:
    session = _ArticleSession({
        "id": "article-id",
        "site_id": "site-id",
        "content_md": "# Body",
        "qa_checklist": {"unexpected": "shape"},
        "qa_summary": {},
    })

    with pytest.raises(PublishError, match="QA 数据格式异常"):
        await publish_article(
            session,  # type: ignore[arg-type]
            article_id="article-id",
            site_id=None,
        )

    assert session.calls == 1
