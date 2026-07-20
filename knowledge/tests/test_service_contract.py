from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from knowledge.backend.app.knowledge_service import (
    APPROVED_ONLY_PREDICATE,
    RETRIEVE_SQL,
    KnowledgeService,
)
from knowledge.backend.app.errors import InvalidInputError
from knowledge.backend.app.schemas import ImportDocumentRequest


class StubResult:
    def __init__(self, *, row=None, rows=None, scalar=None):
        self.row = row
        self.rows = rows
        self.scalar = scalar

    def mappings(self):
        return self

    def one(self):
        assert self.row is not None
        return self.row

    def first(self):
        return self.row

    def all(self):
        return self.rows or []

    def scalar_one(self):
        assert self.scalar is not None
        return self.scalar


class StubSession:
    def __init__(self, results: list[StubResult]):
        self.results = results
        self.statements: list[str] = []
        self.parameters: list[dict[str, object] | None] = []

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement, _params=None):
        self.statements.append(str(statement))
        self.parameters.append(_params)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_duplicate_import_does_not_create_more_claims() -> None:
    now = datetime.now(UTC)
    source_id = uuid4()
    document_id = uuid4()
    source = {
        "id": source_id,
        "name": "Test source",
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
    imported_document = {
        "id": document_id,
        "source_id": source_id,
        "canonical_url": "https://example.test/article",
        "title": "Intent guide",
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
    processed_document = {**imported_document, "status": "processed"}
    session = StubSession(
        [
            StubResult(row=source),
            StubResult(row=imported_document),
            StubResult(scalar=uuid4()),
            StubResult(),
            StubResult(),
            StubResult(scalar=uuid4()),
            StubResult(),
            StubResult(),
            StubResult(row=processed_document),
            StubResult(row=source),
            StubResult(row=None),
            StubResult(row=processed_document),
        ]
    )
    service = KnowledgeService(session)  # type: ignore[arg-type]
    request = ImportDocumentRequest(
        source_name="Test source",
        canonical_url="https://example.test/article",
        title="Intent guide",
        channel="seo",
        market="US",
        raw_content="First recommendation.\n\nSecond recommendation.",
        rights_confirmed=True,
    )

    created = await service.import_document(request)
    duplicate = await service.import_document(request)

    assert created["created"] is True
    assert created["claims_created"] == 2
    assert duplicate == {
        "created": False,
        "duplicate": True,
        "source": source,
        "document": processed_document,
        "claims_created": 0,
    }
    claim_inserts = [
        statement
        for statement in session.statements
        if "INSERT INTO knowledge.claims" in statement
    ]
    assert len(claim_inserts) == 2


def test_retrieval_gate_is_enforced_inside_sql() -> None:
    assert APPROVED_ONLY_PREDICATE == "c.review_status = 'approved'"
    assert APPROVED_ONLY_PREDICATE in RETRIEVE_SQL
    assert "d.market = CAST(:market AS text)" not in RETRIEVE_SQL
    assert "pending" not in RETRIEVE_SQL
    assert "rejected" not in RETRIEVE_SQL


@pytest.mark.asyncio
async def test_claim_archive_filters_are_applied_in_database_query() -> None:
    session = StubSession([StubResult(scalar=0), StubResult(rows=[])])
    service = KnowledgeService(session)  # type: ignore[arg-type]

    result = await service.list_claims(
        review_status="approved",
        query="search intent",
        source_id=uuid4(),
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
        limit=12,
        offset=24,
    )

    assert result == {"items": [], "total": 0}
    assert len(session.statements) == 2
    assert "search_evidence" in session.statements[0]
    assert "s.id = :source_id" in session.statements[0]
    assert "c.created_at >= CAST(:date_from AS date)" in session.statements[0]
    assert "c.created_at < CAST(:date_to AS date)" in session.statements[0]
    assert session.parameters[0]["query"] == "search intent"  # type: ignore[index]


@pytest.mark.asyncio
async def test_claim_archive_rejects_reversed_date_range() -> None:
    service = KnowledgeService(StubSession([]))  # type: ignore[arg-type]

    with pytest.raises(InvalidInputError, match="date_from"):
        await service.list_claims(
            review_status="rejected",
            date_from=date(2026, 2, 1),
            date_to=date(2026, 1, 31),
            limit=12,
            offset=0,
        )
