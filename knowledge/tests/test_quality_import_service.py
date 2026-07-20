from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge.backend.app.knowledge_service import KnowledgeService
from knowledge.backend.app.quality_gate import (
    DocumentQualityAssessment,
    QualityJudgment,
    QualityReviewError,
    QualityReviewResult,
)
from knowledge.backend.app.schemas import ImportDocumentRequest


class StubResult:
    def __init__(self, *, row=None, scalar=None):
        self.row = row
        self.scalar = scalar

    def mappings(self):
        return self

    def first(self):
        return self.row

    def one(self):
        assert self.row is not None
        return self.row

    def scalar_one(self):
        assert self.scalar is not None
        return self.scalar


class RecordingSession:
    def __init__(self, results: list[StubResult]):
        self.results = results
        self.executions: list[tuple[str, dict | None]] = []

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement, params=None):
        self.executions.append((str(statement), params))
        return self.results.pop(0)


def _request() -> ImportDocumentRequest:
    return ImportDocumentRequest(
        source_name="Quality import source",
        canonical_url="https://example.test/quality",
        title="Quality import",
        channel="seo",
        language_code="en",
        raw_content="Reusable evidence for quality classification.",
        rights_confirmed=True,
    )


def _candidate(index: int):
    return SimpleNamespace(
        statement=f"Candidate statement {index}",
        conditions=[],
        exceptions=[],
        recommended_action="Apply the reusable decision rule.",
        confidence=0.85,
        evidence_excerpt="Reusable evidence for quality classification.",
        evidence_locator="paragraph:1",
    )


class FakeExtractor:
    def __init__(self, count: int = 1):
        self.count = count

    async def extract(self, _raw_content: str):
        return SimpleNamespace(
            candidates=tuple(_candidate(index) for index in range(self.count)),
            metadata={
                "extraction_method": "ai",
                "provider": "openai_compatible",
                "model": "extract-test",
                "prompt_version": "knowledge-claims-v1",
            },
        )


def _rows_for_import(claim_count: int) -> tuple[list[StubResult], object]:
    now = datetime.now(UTC)
    source_id = uuid4()
    document_id = uuid4()
    source = {
        "id": source_id,
        "name": "Quality import source",
        "channel": "seo",
        "source_type": "manual",
        "base_url": "https://example.test",
        "domain": "example.test",
        "rights_confirmed": True,
        "status": "active",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    document = {
        "id": document_id,
        "source_id": source_id,
        "canonical_url": "https://example.test/quality",
        "title": "Quality import",
        "content_type": "article",
        "language_code": "en",
        "market": None,
        "author": None,
        "published_at": None,
        "content_hash": "a" * 64,
        "status": "imported",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    results = [StubResult(row=None), StubResult(row=source), StubResult(row=document)]
    for _ in range(claim_count):
        results.extend([StubResult(scalar=uuid4()), StubResult(), StubResult()])
    results.append(StubResult(row={**document, "status": "processed"}))
    return results, document_id


@pytest.mark.asyncio
async def test_new_import_only_auto_rejects_high_confidence_safe_garbage() -> None:
    class Reviewer:
        calls = 0

        async def review_candidates(self, context, candidates):
            self.calls += 1
            assert context.title == "Quality import"
            assert context.source_name == "Quality import source"
            assert context.channel == "seo"
            assert context.canonical_url == "https://example.test/quality"
            assert context.raw_content == _request().raw_content
            assert len(candidates) == 3
            return QualityReviewResult(
                document=DocumentQualityAssessment(
                    "keep", 0.95, [], "Document is in scope."
                ),
                judgments=(
                    QualityJudgment(
                        0,
                        "reject",
                        0.1,
                        0.98,
                        ["isolated_metric"],
                        "Isolated metric with no durable use.",
                    ),
                    QualityJudgment(
                        1,
                        "keep",
                        0.9,
                        0.95,
                        ["actionable_rule"],
                        "Reusable rule.",
                    ),
                    QualityJudgment(
                        2,
                        "reject",
                        0.1,
                        0.99,
                        ["weak_evidence"],
                        "Evidence is too weak for automatic rejection.",
                    ),
                ),
                model="quality-test",
                prompt_version="claim-quality-v2",
            )

    rows, _document_id = _rows_for_import(3)
    session = RecordingSession(rows)
    reviewer = Reviewer()

    result = await KnowledgeService(  # type: ignore[arg-type]
        session, extractor=FakeExtractor(3), quality_reviewer=reviewer
    ).import_document(_request())

    claim_params = [
        params
        for statement, params in session.executions
        if "INSERT INTO knowledge.claims" in statement
    ]
    audit_params = [
        params
        for statement, params in session.executions
        if "INSERT INTO knowledge.claim_quality_reviews" in statement
    ]
    assert reviewer.calls == 1
    assert [params["review_status"] for params in claim_params] == [
        "rejected",
        "pending",
        "pending",
    ]
    assert [params["quality_status"] for params in claim_params] == [
        "reject",
        "keep",
        "uncertain",
    ]
    assert all(params["review_status"] != "approved" for params in claim_params)
    assert claim_params[0]["reviewed_by"] == "ai-quality:quality-test"
    assert [params["applied"] for params in audit_params] == [True, False, False]
    assert result["claims_created"] == 3


class FailingReviewer:
    model = "quality-test"

    async def review_candidates(self, _context, _candidates):
        raise QualityReviewError("temporary quality failure")


@pytest.mark.asyncio
async def test_single_import_quality_failure_stays_pending_with_error_audit() -> None:
    rows, _document_id = _rows_for_import(1)
    session = RecordingSession(rows)

    result = await KnowledgeService(  # type: ignore[arg-type]
        session, extractor=FakeExtractor(), quality_reviewer=FailingReviewer()
    ).import_document(_request())

    claim_params = next(
        params
        for statement, params in session.executions
        if "INSERT INTO knowledge.claims" in statement
    )
    audit_params = next(
        params
        for statement, params in session.executions
        if "INSERT INTO knowledge.claim_quality_reviews" in statement
    )
    assert claim_params["review_status"] == "pending"
    assert claim_params["quality_status"] == "error"
    assert claim_params["quality_note"] == "temporary quality failure"
    assert audit_params["machine_decision"] == "error"
    assert audit_params["applied"] is False
    assert "Quality review failed: temporary quality failure" in result["warning"]


@pytest.mark.asyncio
async def test_required_quality_failure_happens_before_any_import_write() -> None:
    session = RecordingSession([StubResult(row=None)])

    with pytest.raises(QualityReviewError):
        await KnowledgeService(  # type: ignore[arg-type]
            session, extractor=FakeExtractor(), quality_reviewer=FailingReviewer()
        ).import_document(_request(), require_ai=True, require_quality=True)

    assert not any(
        "INSERT INTO" in statement for statement, _params in session.executions
    )
