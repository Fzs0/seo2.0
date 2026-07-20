from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from knowledge.backend.app.errors import ConflictError
from knowledge.backend.app.knowledge_service import (
    CLAIM_FIELDS,
    DOCUMENT_FIELDS,
    SOURCE_FIELDS,
)
from knowledge.backend.app.quality_backfill import (
    QualityGateService,
    QualityReviewWorker,
)
from knowledge.backend.app.quality_gate import (
    DocumentQualityAssessment,
    QualityJudgment,
    QualityReviewError,
    QualityReviewResult,
)


class StubResult:
    def __init__(self, *, row=None, rows=None, scalar=None):
        self.row = row
        self.rows = rows or []
        self.scalar = scalar

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
    def __init__(self, results=None):
        self.results = list(results or [])
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
        return self.results.pop(0) if self.results else StubResult()


class LeaseSession(RecordingSession):
    def __init__(self, leased_row):
        super().__init__()
        self.leased_row = leased_row

    async def execute(self, statement, params=None):
        rendered = str(statement)
        self.executions.append((rendered, params))
        if "SELECT i.id" in rendered:
            return StubResult(row=self.leased_row)
        return StubResult()


class ReadSession(RecordingSession):
    def __init__(self, document, claims):
        super().__init__()
        self.document = document
        self.claims = claims

    async def execute(self, statement, params=None):
        rendered = str(statement)
        self.executions.append((rendered, params))
        if "SELECT d.title" in rendered:
            return StubResult(row=self.document)
        if "SELECT c.id" in rendered:
            return StubResult(rows=self.claims)
        return StubResult()


class SessionFactory:
    def __init__(self, sessions):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


def _status_row(run_id, status="completed", *, failed=0):
    now = datetime.now(UTC)
    return {
        "id": run_id,
        "status": status,
        "queued_live": 0,
        "processing_live": 0,
        "completed_live": 1,
        "failed_live": failed,
        "cancelled_live": 0,
        "auto_reject_live": 1,
        "limit_documents": 100,
        "include_reviewed": False,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "finished_at": now,
        "applied_at": now if status == "applied" else None,
    }


@pytest.mark.asyncio
async def test_start_backfill_persists_run_and_one_item_per_current_document() -> None:
    run_id = uuid4()
    session = RecordingSession(
        [StubResult(scalar=run_id), StubResult(), StubResult(scalar=2), StubResult()]
    )

    result = await QualityGateService(session).start_backfill(25)

    statements = "\n".join(statement for statement, _ in session.executions)
    assert result == run_id
    assert "INSERT INTO knowledge.quality_review_runs" in statements
    assert "INSERT INTO knowledge.quality_review_items" in statements
    assert "GROUP BY c.document_id" in statements
    assert "d.is_current" in statements
    assert "c.review_status='pending'" in statements


@pytest.mark.asyncio
async def test_latest_run_is_discoverable_after_browser_storage_is_lost() -> None:
    run_id = uuid4()
    session = RecordingSession(
        [StubResult(scalar=run_id), StubResult(row=_status_row(run_id))]
    )

    result = await QualityGateService(session).latest()

    assert result["id"] == run_id
    assert "ORDER BY created_at DESC" in session.executions[0][0]


@pytest.mark.asyncio
async def test_worker_reviews_all_document_claims_once_and_dry_run_never_rejects() -> None:
    run_id, item_id, document_id = uuid4(), uuid4(), uuid4()
    claims = [
        {
            "id": uuid4(),
            "statement": f"Candidate {index}",
            "conditions": [],
            "exceptions": [],
            "recommended_action": "Apply the method.",
            "evidence_excerpt": "Supporting text.",
            "evidence_locator": "paragraph:1",
        }
        for index in range(2)
    ]
    lease = LeaseSession(
        {
            "id": item_id,
            "run_id": run_id,
            "document_id": document_id,
            "include_reviewed": False,
        }
    )
    read = ReadSession(
        {
            "title": "One document",
            "source_name": "Neutral source",
            "channel": "seo",
            "canonical_url": "https://example.test/one",
            "raw_content": "Full document content.",
        },
        claims,
    )
    saved = RecordingSession()

    class Reviewer:
        calls = 0

        async def review_candidates(self, context, candidates):
            self.calls += 1
            assert context.title == "One document"
            assert len(candidates) == 2
            return QualityReviewResult(
                DocumentQualityAssessment("keep", 0.95, [], "In scope."),
                (
                    QualityJudgment(
                        0, "reject", 0.1, 0.98, ["isolated_metric"], "Noise."
                    ),
                    QualityJudgment(
                        1, "keep", 0.9, 0.95, ["durable_method"], "Useful."
                    ),
                ),
                "quality-test",
                "claim-quality-v2",
            )

    reviewer = Reviewer()
    worked = await QualityReviewWorker(
        SessionFactory([lease, read, saved]), reviewer=reviewer
    ).run_once()

    assert worked is True
    assert reviewer.calls == 1
    quality_updates = [
        (statement, params)
        for statement, params in saved.executions
        if "SET quality_status" in statement
    ]
    audits = [
        (statement, params)
        for statement, params in saved.executions
        if "INSERT INTO knowledge.claim_quality_reviews" in statement
    ]
    assert len(quality_updates) == 2
    assert len(audits) == 2
    assert all("dry_run, applied" in statement for statement, _ in audits)
    assert not any("SET review_status" in statement for statement, _ in saved.executions)


@pytest.mark.asyncio
@pytest.mark.parametrize(("attempts", "status"), [(1, "retry"), (3, "failed")])
async def test_worker_recovers_expired_leases_and_retries_with_a_limit(
    attempts: int, status: str
) -> None:
    run_id, item_id, document_id = uuid4(), uuid4(), uuid4()
    lease = LeaseSession(
        {
            "id": item_id,
            "run_id": run_id,
            "document_id": document_id,
            "include_reviewed": False,
        }
    )
    read = ReadSession(
        {
            "title": "One document",
            "source_name": "Source",
            "channel": "seo",
            "canonical_url": "https://example.test/one",
            "raw_content": "Body",
        },
        [],
    )
    failed = RecordingSession([StubResult(scalar=attempts), StubResult(), StubResult()])

    class Reviewer:
        async def review_candidates(self, _context, _candidates):
            raise QualityReviewError("temporary review failure")

    await QualityReviewWorker(
        SessionFactory([lease, read, failed]), reviewer=Reviewer()
    ).run_once()

    assert "lease_expires_at < now()" in lease.executions[0][0]
    retry_params = next(
        params
        for statement, params in failed.executions
        if "next_attempt_at" in statement
    )
    assert retry_params["status"] == status
    assert retry_params["error"] == "temporary review failure"


def _result(claim_id, effective_decision):
    return {
        "claim_id": str(claim_id),
        "statement": "Candidate",
        "machine_decision": "reject",
        "effective_decision": effective_decision,
        "utility_score": 0.1,
        "reviewer_confidence": 0.98,
        "reason_codes": ["isolated_metric"],
        "rationale": "Not reusable.",
    }


@pytest.mark.asyncio
async def test_apply_rejects_only_saved_safe_rejects_and_is_idempotent() -> None:
    run_id, item_id, rejected_id, uncertain_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    session = RecordingSession(
        [
            StubResult(row={"status": "completed"}),
            StubResult(
                rows=[
                    {
                        "id": item_id,
                        "results": [
                            _result(rejected_id, "reject"),
                            _result(uncertain_id, "uncertain"),
                        ],
                    }
                ]
            ),
            StubResult(
                row={
                    "quality_model": "quality-test",
                    "quality_prompt_version": "claim-quality-v2",
                }
            ),
            StubResult(),
            StubResult(),
            StubResult(),
            StubResult(row=_status_row(run_id, "applied")),
        ]
    )

    result = await QualityGateService(session).apply(run_id)

    claim_updates = [
        params
        for statement, params in session.executions
        if "UPDATE knowledge.claims" in statement
    ]
    assert [params["claim_id"] for params in claim_updates] == [rejected_id]
    assert "review_status='pending'" in next(
        statement
        for statement, _ in session.executions
        if "UPDATE knowledge.claims" in statement
    )
    assert result["status"] == "applied"

    second = RecordingSession(
        [
            StubResult(row={"status": "applied"}),
            StubResult(row=_status_row(run_id, "applied")),
        ]
    )
    assert (await QualityGateService(second).apply(run_id))["status"] == "applied"
    assert not any(
        "UPDATE knowledge.claims" in statement for statement, _ in second.executions
    )


@pytest.mark.asyncio
async def test_mixed_terminal_run_stays_completed_and_successful_items_can_apply() -> None:
    run_id, item_id, claim_id = uuid4(), uuid4(), uuid4()
    finishing = RecordingSession()

    await QualityGateService._finish_run(finishing, run_id)

    finish_sql = finishing.executions[0][0]
    assert "status='failed'" not in finish_sql
    assert "ELSE 'completed'" in finish_sql

    session = RecordingSession(
        [
            StubResult(row={"status": "completed"}),
            StubResult(
                rows=[
                    {
                        "id": item_id,
                        "results": [_result(claim_id, "reject")],
                    }
                ]
            ),
            StubResult(
                row={
                    "quality_model": "quality-test",
                    "quality_prompt_version": "claim-quality-v2",
                }
            ),
            StubResult(),
            StubResult(),
            StubResult(),
            StubResult(row=_status_row(run_id, "applied", failed=1)),
        ]
    )

    result = await QualityGateService(session).apply(run_id)

    assert result["status"] == "applied"
    assert result["counts"]["failed"] == 1
    assert any(
        params and params.get("claim_id") == claim_id
        for statement, params in session.executions
        if "UPDATE knowledge.claims" in statement
    )


@pytest.mark.asyncio
async def test_cancel_marks_unstarted_items_and_requests_worker_stop() -> None:
    run_id = uuid4()
    session = RecordingSession(
        [
            StubResult(scalar=run_id),
            StubResult(),
            StubResult(),
            StubResult(row=_status_row(run_id, "cancelled")),
        ]
    )

    result = await QualityGateService(session).cancel(run_id)

    statements = "\n".join(statement for statement, _ in session.executions)
    assert "cancel_requested=true" in statements
    assert "status IN ('queued','retry')" in statements
    assert result["status"] == "cancelled"


def _claim_item_row():
    row = {f"claim_{field}": None for field in CLAIM_FIELDS}
    row.update({f"source_{field}": None for field in SOURCE_FIELDS})
    row.update({f"document_{field}": None for field in DOCUMENT_FIELDS})
    row["evidence"] = []
    return row


@pytest.mark.asyncio
async def test_restore_accepts_only_ai_quality_rejections() -> None:
    claim_id = uuid4()
    prior = {
        "quality_utility_score": 0.1,
        "quality_reviewer_confidence": 0.98,
        "quality_reasons": ["isolated_metric"],
        "quality_note": "Noise.",
        "quality_model": "quality-test",
        "quality_prompt_version": "claim-quality-v2",
    }
    ai_session = RecordingSession(
        [StubResult(row=prior), StubResult(), StubResult(), StubResult(row=_claim_item_row())]
    )

    await QualityGateService(ai_session).restore(claim_id)

    restore_sql = next(
        statement
        for statement, _ in ai_session.executions
        if "SET review_status='pending'" in statement
    )
    assert "quality_status='uncertain'" in restore_sql
    assert "reviewed_by LIKE 'ai-quality:%'" in ai_session.executions[0][0]

    human_session = RecordingSession([StubResult(row=None), StubResult(scalar=1)])
    with pytest.raises(ConflictError, match="only AI quality rejections"):
        await QualityGateService(human_session).restore(claim_id)
    assert not any(
        "SET review_status='pending'" in statement
        for statement, _ in human_session.executions
    )
