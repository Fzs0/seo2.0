from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge.backend.app.knowledge_service import (
    APPROVED_ONLY_PREDICATE,
    RETRIEVE_SQL,
    KnowledgeService,
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


@pytest.mark.asyncio
async def test_changed_canonical_url_content_creates_revision_and_preserves_old_row() -> None:
    now = datetime.now(UTC)
    source_id = uuid4()
    old_document_id = uuid4()
    new_document_id = uuid4()
    source = {
        "id": source_id,
        "name": "Revision source",
        "channel": "seo",
        "source_type": "manual",
        "base_url": "https://blog.example.test",
        "domain": "blog.example.test",
        "rights_confirmed": True,
        "status": "active",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    old_current = {
        "id": old_document_id,
        "source_id": source_id,
        "content_hash": "a" * 64,
        "revision": 1,
    }
    new_document = {
        "id": new_document_id,
        "source_id": source_id,
        "canonical_url": "https://blog.example.test/article",
        "title": "Updated article",
        "content_type": "article",
        "language_code": "en",
        "market": "US",
        "author": None,
        "published_at": None,
        "content_hash": "b" * 64,
        "status": "imported",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    session = RecordingSession(
        [
            StubResult(row=None),  # different content hash
            StubResult(row=source),
            StubResult(row=old_current),
            StubResult(),  # mark the old revision non-current
            StubResult(row=new_document),
            StubResult(scalar=uuid4()),
            StubResult(),
            StubResult(),
            StubResult(row={**new_document, "status": "processed"}),
        ]
    )
    candidate = SimpleNamespace(
        statement="Use the updated recommendation.",
        conditions=[],
        exceptions=[],
        recommended_action="Use the updated recommendation.",
        confidence=0.9,
        evidence_excerpt="Updated evidence paragraph.",
        evidence_locator="paragraph:1",
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

    request = ImportDocumentRequest(
        source_name="Revision source",
        canonical_url="https://blog.example.test/article",
        title="Updated article",
        channel="seo",
        language_code="en",
        market="US",
        raw_content="Updated evidence paragraph.",
        rights_confirmed=True,
    )
    result = await KnowledgeService(  # type: ignore[arg-type]
        session, extractor=FakeExtractor()
    ).import_document(request, allow_revision=True, require_ai=True)

    retire_statement, retire_params = next(
        (statement, params)
        for statement, params in session.executions
        if "SET is_current=false" in statement
    )
    insert_statement, insert_params = next(
        (statement, params)
        for statement, params in session.executions
        if "INSERT INTO knowledge.documents" in statement
    )
    assert "DELETE" not in retire_statement
    assert retire_params == {"id": old_document_id}
    assert "revision" in insert_statement
    assert "supersedes_id" in insert_statement
    assert insert_params["revision"] == 2
    assert insert_params["supersedes_id"] == old_document_id
    assert result["created"] is True
    assert result["document"]["id"] == new_document_id


def test_production_retrieval_requires_approved_current_revision() -> None:
    assert APPROVED_ONLY_PREDICATE in RETRIEVE_SQL
    assert "AND d.is_current" in RETRIEVE_SQL
    assert "d.market = CAST(:market AS text)" not in RETRIEVE_SQL
