from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge.backend.app.ai_extractor import AIExtractionError
from knowledge.backend.app.batch_ingestion import (
    BatchIngestionService,
    BatchWorker,
    DiscoveredUrl,
    DiscoveryResult,
)
from knowledge.backend.app.schemas import BatchSourceSpec
from knowledge.backend.app.web_importer import FetchedArticle
from knowledge.backend.app.quality_gate import (
    DocumentQualityAssessment,
    QualityJudgment,
    QualityReviewResult,
    QualityReviewError,
)


class StubResult:
    def __init__(self, *, row=None, scalar=None, rows=None):
        self.row = row
        self.scalar = scalar
        self.rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self.row

    def one(self):
        assert self.row is not None
        return self.row

    def all(self):
        return self.rows

    def scalar_one(self):
        assert self.scalar is not None
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


class RecordingSession:
    def __init__(self, results: list[StubResult]):
        self.results = results
        self.executions: list[tuple[str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement, params=None):
        self.executions.append((str(statement), params))
        return self.results.pop(0)


class WorkerClaimSession(RecordingSession):
    """Returns the leased item by query meaning, independent of write count."""

    def __init__(self, row):
        super().__init__([])
        self.row = row

    async def execute(self, statement, params=None):
        rendered = str(statement)
        self.executions.append((rendered, params))
        if "SELECT i.id" in rendered:
            return StubResult(row=self.row)
        return StubResult()


class SessionFactory:
    def __init__(self, sessions: list[RecordingSession]):
        self.sessions = sessions
        self.created: list[RecordingSession] = []

    def __call__(self) -> RecordingSession:
        session = self.sessions.pop(0)
        self.created.append(session)
        return session


def _spec(**overrides) -> BatchSourceSpec:
    values = {
        "seed_url": "https://blog.example.test/blog/",
        "source_name": "Example blog",
        "rights_confirmed": True,
        "include_unknown_dates": True,
    }
    values.update(overrides)
    return BatchSourceSpec(**values)


class FakeDiscovery:
    def __init__(self, items: tuple[DiscoveredUrl, ...]):
        self.items = items

    async def discover(self, _spec: BatchSourceSpec) -> DiscoveryResult:
        return DiscoveryResult(("sitemap",), self.items, ("fake warning",))


def _run_row(run_id, *, status: str = "queued", queued: int = 2) -> dict:
    now = datetime.now(UTC)
    return {
        "id": run_id,
        "status": status,
        "discovered_count": 3,
        "queued_count": queued,
        "processing_count": 0,
        "completed_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "cancelled_count": 0,
        "queued_count_live": queued,
        "processing_count_live": 0,
        "completed_count_live": 0,
        "failed_count_live": 0,
        "skipped_count_live": 0,
        "cancelled_count_live": 0,
        "warnings": ["fake warning"],
        "error": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }


@pytest.mark.asyncio
async def test_start_persists_run_spec_snapshot_and_selected_items() -> None:
    source_id = uuid4()
    run_id = uuid4()
    items = (
        DiscoveredUrl(
            "https://blog.example.test/blog/first",
            published_at=datetime(2026, 7, 3, tzinfo=UTC),
        ),
        DiscoveredUrl(
            "https://blog.example.test/blog/second",
            modified_at=datetime(2026, 7, 2, tzinfo=UTC),
        ),
        DiscoveredUrl(
            "https://blog.example.test/blog/limited-out",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
        ),
    )
    session = RecordingSession(
        [
            StubResult(scalar=source_id),
            StubResult(scalar=run_id),
            StubResult(),
            StubResult(),
            StubResult(row=_run_row(run_id)),
        ]
    )
    spec = _spec(max_articles=2)

    result = await BatchIngestionService(  # type: ignore[arg-type]
        session, FakeDiscovery(items)
    ).start(spec)

    run_insert = next(
        params
        for statement, params in session.executions
        if "INSERT INTO knowledge.sync_runs" in statement
    )
    item_inserts = [
        params
        for statement, params in session.executions
        if "INSERT INTO knowledge.crawl_items" in statement
    ]
    assert json.loads(run_insert["spec"]) == spec.model_dump(mode="json")
    assert [params["url"] for params in item_inserts] == [
        "https://blog.example.test/blog/first",
        "https://blog.example.test/blog/second",
    ]
    assert result["id"] == run_id
    assert result["status"] == "queued"


def _claim_row(spec: BatchSourceSpec) -> dict:
    return {
        "id": uuid4(),
        "run_id": uuid4(),
        "url": "https://blog.example.test/blog/article",
        "published_at": datetime(2026, 7, 1, tzinfo=UTC),
        "spec": spec.model_dump(mode="json"),
    }


def _article() -> FetchedArticle:
    return FetchedArticle(
        requested_url="https://blog.example.test/blog/article",
        final_url="https://blog.example.test/blog/article",
        title="Batch article",
        raw_content="Evidence-backed batch article content that is long enough.",
        language_code="en",
    )


class FakeFetcher:
    async def fetch(self, _url: str) -> FetchedArticle:
        return _article()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attempts", "expected_status", "expected_delay"),
    [(1, "retry", 30), (3, "failed", 120)],
)
async def test_temporary_ai_failure_never_falls_back_and_respects_retry_limit(
    attempts: int, expected_status: str, expected_delay: int
) -> None:
    spec = _spec()
    claimed = _claim_row(spec)
    claim_session = WorkerClaimSession(claimed)
    import_session = RecordingSession([StubResult(row=None)])
    mark_session = RecordingSession(
        [StubResult(scalar=attempts), StubResult(), StubResult()]
    )

    class FailingExtractor:
        async def extract(self, _raw_content: str):
            raise AIExtractionError("temporary model failure")

    worker = BatchWorker(  # type: ignore[arg-type]
        SessionFactory([claim_session, import_session, mark_session]),
        article_fetcher=FakeFetcher(),
        extractor=FailingExtractor(),
    )

    assert await worker.run_once() is True

    assert not any(
        "INSERT INTO knowledge.claims" in statement
        for statement, _params in import_session.executions
    )
    failure_params = next(
        params
        for statement, params in mark_session.executions
        if "SET status=:status" in statement
    )
    assert failure_params["status"] == expected_status
    assert failure_params["delay"] == expected_delay


@pytest.mark.asyncio
async def test_required_quality_failure_retries_without_importing_candidates() -> None:
    claimed = _claim_row(_spec())
    claim_session = WorkerClaimSession(claimed)
    import_session = RecordingSession([StubResult(row=None)])
    mark_session = RecordingSession([StubResult(scalar=1), StubResult(), StubResult()])

    class Extractor:
        async def extract(self, _raw_content: str):
            return SimpleNamespace(
                candidates=(
                    SimpleNamespace(
                        statement="Potential knowledge.",
                        conditions=[],
                        exceptions=[],
                        recommended_action="Apply it carefully.",
                        confidence=0.8,
                        evidence_excerpt="Evidence-backed batch article content",
                        evidence_locator="paragraph:1",
                    ),
                ),
                metadata={
                    "extraction_method": "ai",
                    "provider": "openai_compatible",
                    "model": "extract-test",
                    "prompt_version": "knowledge-claims-v1",
                },
            )

    class FailingQualityReviewer:
        async def review_candidates(self, _context, _candidates):
            raise QualityReviewError("temporary quality failure")

    worker = BatchWorker(  # type: ignore[arg-type]
        SessionFactory([claim_session, import_session, mark_session]),
        article_fetcher=FakeFetcher(),
        extractor=Extractor(),
        quality_reviewer=FailingQualityReviewer(),
    )

    assert await worker.run_once() is True
    assert not any(
        "INSERT INTO" in statement for statement, _params in import_session.executions
    )
    failure_params = next(
        params
        for statement, params in mark_session.executions
        if "SET status=:status" in statement
    )
    assert failure_params["status"] == "retry"
    assert "quality failure" in failure_params["error"]


@pytest.mark.asyncio
async def test_worker_success_creates_pending_ai_claim_and_marks_item_complete() -> None:
    spec = _spec()
    claimed = _claim_row(spec)
    claim_session = WorkerClaimSession(claimed)
    now = datetime.now(UTC)
    source_id = uuid4()
    document_id = uuid4()
    source = {
        "id": source_id,
        "name": "Example blog",
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
        "canonical_url": claimed["url"],
        "title": "Batch article",
        "content_type": "article",
        "language_code": "en",
        "market": None,
        "author": None,
        "published_at": claimed["published_at"],
        "content_hash": "a" * 64,
        "status": "imported",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    import_session = RecordingSession(
        [
            StubResult(row=None),
            StubResult(row=source),
            StubResult(row=None),
            StubResult(row=document),
            StubResult(scalar=uuid4()),
            StubResult(),
            StubResult(),
            StubResult(row={**document, "status": "processed"}),
        ]
    )
    mark_session = RecordingSession([StubResult(), StubResult()])
    candidate = SimpleNamespace(
        statement="Use evidence-backed batch knowledge.",
        conditions=[],
        exceptions=[],
        recommended_action="Retain the source evidence.",
        confidence=0.9,
        evidence_excerpt="Evidence-backed batch article content",
        evidence_locator="paragraph:1",
    )

    class SuccessfulExtractor:
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

    class SuccessfulQualityReviewer:
        async def review_candidates(self, _context, _candidates):
            return QualityReviewResult(
                document=DocumentQualityAssessment(
                    "keep", 0.95, [], "Document is in scope."
                ),
                judgments=(
                    QualityJudgment(
                        0,
                        "keep",
                        0.9,
                        0.95,
                        ["actionable_rule"],
                        "Reusable decision rule.",
                    ),
                ),
                model="quality-test",
                prompt_version="claim-quality-v2",
            )

    worker = BatchWorker(  # type: ignore[arg-type]
        SessionFactory([claim_session, import_session, mark_session]),
        article_fetcher=FakeFetcher(),
        extractor=SuccessfulExtractor(),
        quality_reviewer=SuccessfulQualityReviewer(),
    )

    assert await worker.run_once() is True

    claim_sql = next(
        statement
        for statement, _params in import_session.executions
        if "INSERT INTO knowledge.claims" in statement
    )
    completed_params = next(
        params
        for statement, params in mark_session.executions
        if "status='completed'" in statement
    )
    assert "'pending'" in claim_sql
    assert completed_params["document_id"] == document_id


@pytest.mark.asyncio
async def test_worker_recovers_expired_leases_applies_cancellation_and_reads_run_snapshot() -> None:
    session = WorkerClaimSession(None)
    worker = BatchWorker(  # type: ignore[arg-type]
        SessionFactory([session]), article_fetcher=FakeFetcher(), extractor=None
    )

    assert await worker.run_once() is False

    recovery_sql = session.executions[0][0]
    maintenance_sql = "\n".join(
        statement
        for statement, _params in session.executions
        if "SELECT i.id" not in statement
    )
    claim_sql = next(
        statement
        for statement, _params in session.executions
        if "SELECT i.id" in statement
    )
    assert "lease_expires_at < now()" in recovery_sql
    assert "status='retry'" in recovery_sql
    assert "cancel_requested" in maintenance_sql
    assert "status='cancelled'" in maintenance_sql
    assert "r.status='cancelling'" in maintenance_sql
    assert "r.spec" in claim_sql
    assert "s.spec" not in claim_sql
