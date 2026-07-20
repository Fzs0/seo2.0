from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge.backend.app.errors import UrlFetchError
from knowledge.backend.app.knowledge_service import (
    DOCUMENT_FIELDS,
    SOURCE_FIELDS,
    KnowledgeService,
)
from knowledge.backend.app.schemas import ImportUrlRequest
from knowledge.backend.app.web_importer import FetchedArticle


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


def _article() -> FetchedArticle:
    return FetchedArticle(
        requested_url="https://blog.example.test/post?source=feed",
        final_url="https://blog.example.test/post",
        title="A fetched article title",
        raw_content=(
            "Match the page to the dominant search intent before writing, then "
            "verify every recommendation against the current search results."
        ),
        author="Example Author",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        language_code="en",
    )


def _request() -> ImportUrlRequest:
    return ImportUrlRequest(
        url="https://blog.example.test/post?source=feed",
        rights_confirmed=True,
        source_name="Imported blog",
        channel="seo",
        market="US",
    )


def _rows_for_created_import() -> list[StubResult]:
    now = datetime.now(UTC)
    source_id = uuid4()
    document_id = uuid4()
    source = {
        "id": source_id,
        "name": "Imported blog",
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
    document = {
        "id": document_id,
        "source_id": source_id,
        "canonical_url": "https://blog.example.test/post",
        "title": "A fetched article title",
        "content_type": "article",
        "language_code": "en",
        "market": "US",
        "author": "Example Author",
        "published_at": datetime(2026, 7, 1, tzinfo=UTC),
        "content_hash": "a" * 64,
        "status": "imported",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    processed = {**document, "status": "processed"}
    return [
        StubResult(row=None),  # no document with the fetched final URL
        StubResult(row=None),  # no same-source content-hash duplicate
        StubResult(row=source),
        StubResult(row=document),
        StubResult(scalar=uuid4()),
        StubResult(),
        StubResult(),
        StubResult(row=processed),
    ]


@pytest.mark.asyncio
async def test_url_import_passes_fetched_title_and_content_into_pending_ai_import() -> None:
    article = _article()

    class FakeFetcher:
        async def fetch(self, url: str) -> FetchedArticle:
            assert url == "https://blog.example.test/post?source=feed"
            return article

    candidate = SimpleNamespace(
        statement="Match the page to the dominant search intent.",
        conditions=[],
        exceptions=[],
        recommended_action="Inspect current search results before writing.",
        confidence=0.9,
        evidence_excerpt=(
            "Match the page to the dominant search intent before writing"
        ),
        evidence_locator="paragraph:1",
    )

    class FakeExtractor:
        calls = 0

        async def extract(self, raw_content: str):
            self.calls += 1
            assert raw_content == article.raw_content
            return SimpleNamespace(
                candidates=(candidate,),
                metadata={
                    "extraction_method": "ai",
                    "provider": "openai_compatible",
                    "model": "test-model",
                    "prompt_version": "knowledge-claims-v1",
                },
            )

    session = RecordingSession(_rows_for_created_import())
    extractor = FakeExtractor()
    result = await KnowledgeService(  # type: ignore[arg-type]
        session, extractor=extractor
    ).import_url(_request(), fetcher=FakeFetcher())

    document_params = next(
        params
        for statement, params in session.executions
        if "INSERT INTO knowledge.documents" in statement
    )
    claim_statement, claim_params = next(
        (statement, params)
        for statement, params in session.executions
        if "INSERT INTO knowledge.claims" in statement
    )
    assert document_params["canonical_url"] == article.final_url
    assert document_params["title"] == article.title
    assert document_params["raw_content"] == article.raw_content
    assert document_params["author"] == article.author
    assert document_params["language_code"] == "en"
    assert extractor.calls == 1
    assert "'pending'" in claim_statement
    assert json.loads(claim_params["metadata"])["extraction_method"] == "ai"
    assert result["created"] is True
    assert result["claims_created"] == 1


@pytest.mark.asyncio
async def test_fetch_failure_performs_no_database_or_ai_work() -> None:
    class FailingFetcher:
        async def fetch(self, _url: str):
            raise UrlFetchError("simulated fetch failure")

    class CountingExtractor:
        calls = 0

        async def extract(self, _raw_content: str):
            self.calls += 1
            raise AssertionError("AI must not run after a fetch failure")

    session = RecordingSession([])
    extractor = CountingExtractor()
    with pytest.raises(UrlFetchError):
        await KnowledgeService(  # type: ignore[arg-type]
            session, extractor=extractor
        ).import_url(_request(), fetcher=FailingFetcher())

    assert session.executions == []
    assert extractor.calls == 0


@pytest.mark.asyncio
async def test_duplicate_final_url_returns_existing_document_without_ai_usage() -> None:
    article = _article()
    now = datetime.now(UTC)
    source_id = uuid4()
    document_id = uuid4()
    source = {
        "id": source_id,
        "name": "Imported blog",
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
    document = {
        "id": document_id,
        "source_id": source_id,
        "canonical_url": article.final_url,
        "title": article.title,
        "content_type": "article",
        "language_code": "en",
        "market": "US",
        "author": article.author,
        "published_at": article.published_at,
        "content_hash": "b" * 64,
        "status": "processed",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    existing_row = {
        **{f"existing_source_{key}": value for key, value in source.items()},
        **{f"existing_document_{key}": value for key, value in document.items()},
    }
    assert set(source) == set(SOURCE_FIELDS)
    assert set(document) == set(DOCUMENT_FIELDS)

    class FakeFetcher:
        async def fetch(self, _url: str) -> FetchedArticle:
            return article

    class CountingExtractor:
        calls = 0

        async def extract(self, _raw_content: str):
            self.calls += 1
            raise AssertionError("duplicate URL must not consume AI")

    session = RecordingSession([StubResult(row=existing_row)])
    extractor = CountingExtractor()
    result = await KnowledgeService(  # type: ignore[arg-type]
        session, extractor=extractor
    ).import_url(_request(), fetcher=FakeFetcher())

    assert result["created"] is False
    assert result["duplicate"] is True
    assert result["document"]["id"] == document_id
    assert extractor.calls == 0
    assert len(session.executions) == 1
    assert "WHERE d.canonical_url = :canonical_url" in session.executions[0][0]
