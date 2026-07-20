from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge.backend.app.ai_extractor import AIExtractionError
from knowledge.backend.app.config import get_settings
from knowledge.backend.app.knowledge_service import KnowledgeService
from knowledge.backend.app.schemas import ImportDocumentRequest


class StubResult:
    def __init__(self, *, row=None, scalar=None):
        self.row = row
        self.scalar = scalar

    def mappings(self):
        return self

    def one(self):
        assert self.row is not None
        return self.row

    def first(self):
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


def _request(raw_content: str) -> ImportDocumentRequest:
    return ImportDocumentRequest(
        source_name="AI import test",
        canonical_url="https://example.test/ai-import",
        title="Search intent guide",
        channel="seo",
        language_code="en",
        market="US",
        raw_content=raw_content,
        rights_confirmed=True,
    )


def _session_for_import(
    claim_count: int, *, with_ai_preflight: bool = False
) -> RecordingSession:
    now = datetime.now(UTC)
    source_id = uuid4()
    document_id = uuid4()
    source = {
        "id": source_id,
        "name": "AI import test",
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
        "canonical_url": "https://example.test/ai-import",
        "title": "Search intent guide",
        "content_type": "article",
        "language_code": "en",
        "market": "US",
        "author": None,
        "published_at": None,
        "content_hash": "a" * 64,
        "status": "imported",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    processed = {**document, "status": "processed"}
    results = []
    if with_ai_preflight:
        results.append(StubResult(row=None))
    results.extend([StubResult(row=source), StubResult(row=document)])
    for _ in range(claim_count):
        results.extend([StubResult(scalar=uuid4()), StubResult(), StubResult()])
    results.append(StubResult(row=processed))
    return RecordingSession(results)


def _claim_executions(session: RecordingSession) -> list[tuple[str, dict]]:
    return [
        (statement, params)
        for statement, params in session.executions
        if "INSERT INTO knowledge.claims" in statement and params is not None
    ]


@pytest.mark.asyncio
async def test_missing_ai_configuration_imports_deterministic_pending_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "KNOWLEDGE_AI_BASE_URL",
        "KNOWLEDGE_AI_API_KEY",
        "KNOWLEDGE_AI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    session = _session_for_import(claim_count=2)
    try:
        result = await KnowledgeService(session).import_document(  # type: ignore[arg-type]
            _request("First grounded paragraph.\n\nSecond grounded paragraph.")
        )
    finally:
        get_settings.cache_clear()

    assert result["claims_created"] == 2
    assert result["extraction_method"] == "deterministic_fallback"
    assert result["model"] is None
    assert result["warning"] == "AI is not configured; deterministic fallback was used"
    claims = _claim_executions(session)
    assert len(claims) == 2
    assert all("'pending'" in statement for statement, _params in claims)
    assert all(
        json.loads(params["metadata"])["extraction_method"]
        == "deterministic_fallback"
        for _statement, params in claims
    )


@pytest.mark.asyncio
async def test_ai_failure_does_not_abort_import_and_uses_deterministic_pending_claims() -> None:
    class FailingExtractor:
        async def extract(self, _raw_content: str):
            raise AIExtractionError("simulated provider failure")

    session = _session_for_import(claim_count=1, with_ai_preflight=True)
    result = await KnowledgeService(  # type: ignore[arg-type]
        session, extractor=FailingExtractor()
    ).import_document(_request("Keep imports durable when the model is unavailable."))

    assert result["created"] is True
    assert result["claims_created"] == 1
    assert result["extraction_method"] == "deterministic_fallback"
    assert "fallback was used" in result["warning"]
    [(statement, params)] = _claim_executions(session)
    assert "'pending'" in statement
    assert json.loads(params["metadata"]) == {
        "extraction_method": "deterministic_fallback",
        "provider": None,
        "model": None,
        "prompt_version": None,
    }


@pytest.mark.asyncio
async def test_ai_claims_are_always_pending_and_include_extraction_metadata() -> None:
    candidate = SimpleNamespace(
        statement="Align the page with the dominant search intent.",
        conditions=["Before drafting"],
        exceptions=["Navigational queries"],
        recommended_action="Inspect the search results first.",
        confidence=0.91,
        evidence_excerpt="Match the page to the dominant search intent before writing.",
        evidence_locator="paragraph:1",
        # Even a compromised collaborator cannot control the review gate.
        review_status="approved",
    )

    class FakeExtractor:
        async def extract(self, _raw_content: str):
            return SimpleNamespace(
                candidates=(candidate,),
                metadata={
                    "extraction_method": "ai",
                    "provider": "openai_compatible",
                    "model": "test-model",
                    "prompt_version": "knowledge-claims-v1",
                },
            )

    session = _session_for_import(claim_count=1, with_ai_preflight=True)
    result = await KnowledgeService(  # type: ignore[arg-type]
        session, extractor=FakeExtractor()
    ).import_document(
        _request("Match the page to the dominant search intent before writing.")
    )

    assert result["extraction_method"] == "ai"
    assert result["model"] == "test-model"
    assert result["warning"] is None
    [(statement, params)] = _claim_executions(session)
    assert "'pending'" in statement
    assert "approved" not in statement
    assert json.loads(params["conditions"]) == ["Before drafting"]
    assert json.loads(params["exceptions"]) == ["Navigational queries"]
    assert json.loads(params["metadata"]) == {
        "extraction_method": "ai",
        "provider": "openai_compatible",
        "model": "test-model",
        "prompt_version": "knowledge-claims-v1",
    }
