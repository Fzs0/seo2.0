from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import get_settings
from .errors import ConflictError, NotFoundError
from .knowledge_service import CLAIM_ITEM_SQL, _claim_item_from_row
from .quality_gate import (
    OpenAICompatibleQualityAdapter,
    QualityCandidate,
    QualityDocumentContext,
    QualityReviewError,
    QualityReviewer,
    effective_decision,
)


MAX_ATTEMPTS = 3
_DEFAULT_REVIEWER = object()


def _reviewer_from_settings() -> QualityReviewer | None:
    settings = get_settings()
    if not (settings.ai_base_url and settings.ai_api_key and settings.ai_model):
        return None
    return QualityReviewer(
        model=settings.ai_model,
        adapter=OpenAICompatibleQualityAdapter(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_attempts=settings.ai_max_attempts,
        ),
    )


def _run_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "counts": {
            "queued": row["queued_live"],
            "processing": row["processing_live"],
            "completed": row["completed_live"],
            "failed": row["failed_live"],
            "cancelled": row["cancelled_live"],
            "auto_rejects": row["auto_reject_live"],
        },
        "limit_documents": row["limit_documents"],
        "include_reviewed": row["include_reviewed"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "applied_at": row["applied_at"],
    }


class QualityGateService:
    """Owns durable dry-run classification, explicit apply, and restore."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_backfill(
        self, limit_documents: int, include_reviewed: bool = False
    ) -> UUID:
        async with self._session.begin():
            run_id = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.quality_review_runs
                            (limit_documents, include_reviewed)
                        VALUES (:limit_documents, :include_reviewed)
                        RETURNING id
                        """
                    ),
                    {
                        "limit_documents": limit_documents,
                        "include_reviewed": include_reviewed,
                    },
                )
            ).scalar_one()
            await self._session.execute(
                text(
                    """
                    INSERT INTO knowledge.quality_review_items (run_id, document_id)
                    SELECT :run_id, candidate.document_id
                    FROM (
                        SELECT c.document_id, max(c.created_at) AS newest_claim
                        FROM knowledge.claims c
                        JOIN knowledge.documents d ON d.id=c.document_id
                        WHERE d.is_current
                          AND c.review_status='pending'
                          AND (
                              :include_reviewed
                              OR c.quality_status IN ('unreviewed', 'error')
                          )
                        GROUP BY c.document_id
                        ORDER BY newest_claim DESC, c.document_id
                        LIMIT :limit_documents
                    ) candidate
                    """
                ),
                {
                    "run_id": run_id,
                    "include_reviewed": include_reviewed,
                    "limit_documents": limit_documents,
                },
            )
            queued = (
                await self._session.execute(
                    text(
                        "SELECT count(*) FROM knowledge.quality_review_items "
                        "WHERE run_id=:run_id"
                    ),
                    {"run_id": run_id},
                )
            ).scalar_one()
            await self._session.execute(
                text(
                    """
                    UPDATE knowledge.quality_review_runs
                    SET queued_count=:queued,
                        status=CASE WHEN :queued=0 THEN 'completed' ELSE status END,
                        finished_at=CASE WHEN :queued=0 THEN now() ELSE NULL END
                    WHERE id=:run_id
                    """
                ),
                {"run_id": run_id, "queued": queued},
            )
        return run_id

    async def status(self, run_id: UUID) -> dict[str, Any]:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT r.*,
                      (SELECT count(*) FROM knowledge.quality_review_items i
                       WHERE i.run_id=r.id AND i.status IN ('queued','retry')) queued_live,
                      (SELECT count(*) FROM knowledge.quality_review_items i
                       WHERE i.run_id=r.id AND i.status='processing') processing_live,
                      (SELECT count(*) FROM knowledge.quality_review_items i
                       WHERE i.run_id=r.id AND i.status IN ('completed','applied')) completed_live,
                      (SELECT count(*) FROM knowledge.quality_review_items i
                       WHERE i.run_id=r.id AND i.status='failed') failed_live,
                      (SELECT count(*) FROM knowledge.quality_review_items i
                       WHERE i.run_id=r.id AND i.status='cancelled') cancelled_live,
                      (SELECT COALESCE(sum(i.auto_reject_count),0)
                       FROM knowledge.quality_review_items i WHERE i.run_id=r.id) auto_reject_live
                    FROM knowledge.quality_review_runs r WHERE r.id=:run_id
                    """
                ),
                {"run_id": run_id},
            )
        ).mappings().first()
        if row is None:
            raise NotFoundError("quality run not found")
        return _run_response(dict(row))

    async def latest(self) -> dict[str, Any]:
        run_id = (
            await self._session.execute(
                text(
                    "SELECT id FROM knowledge.quality_review_runs "
                    "ORDER BY created_at DESC, id DESC LIMIT 1"
                )
            )
        ).scalar_one_or_none()
        if run_id is None:
            raise NotFoundError("quality run not found")
        return await self.status(run_id)

    async def items(self, run_id: UUID) -> dict[str, Any]:
        exists = (
            await self._session.execute(
                text("SELECT 1 FROM knowledge.quality_review_runs WHERE id=:id"),
                {"id": run_id},
            )
        ).scalar_one_or_none()
        if exists is None:
            raise NotFoundError("quality run not found")
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT i.id, i.document_id, d.title AS document_title,
                           i.status, i.attempts, i.reviewed_claims,
                           i.auto_reject_count, i.document_decision,
                           i.document_reason_codes, i.document_rationale,
                           i.error, i.updated_at, i.results
                    FROM knowledge.quality_review_items i
                    JOIN knowledge.documents d ON d.id=i.document_id
                    WHERE i.run_id=:run_id ORDER BY i.created_at, i.id
                    """
                ),
                {"run_id": run_id},
            )
        ).mappings().all()
        return {"items": [dict(row) for row in rows]}

    async def apply(self, run_id: UUID) -> dict[str, Any]:
        async with self._session.begin():
            run = (
                await self._session.execute(
                    text(
                        "SELECT status FROM knowledge.quality_review_runs "
                        "WHERE id=:id FOR UPDATE"
                    ),
                    {"id": run_id},
                )
            ).mappings().first()
            if run is None:
                raise NotFoundError("quality run not found")
            if run["status"] == "applied":
                return await self.status(run_id)
            if run["status"] != "completed":
                raise ConflictError("quality run must be completed before apply")

            items = (
                await self._session.execute(
                    text(
                        "SELECT id, results FROM knowledge.quality_review_items "
                        "WHERE run_id=:run_id AND status='completed' FOR UPDATE"
                    ),
                    {"run_id": run_id},
                )
            ).mappings().all()
            for item in items:
                for result in item["results"] or []:
                    if result.get("effective_decision") != "reject":
                        continue
                    claim_id = UUID(str(result["claim_id"]))
                    claim = (
                        await self._session.execute(
                            text(
                                """
                                UPDATE knowledge.claims
                                SET review_status='rejected',
                                    review_note=:rationale,
                                    reviewed_by='ai-quality:' || COALESCE(quality_model,'unknown'),
                                    reviewed_at=now()
                                WHERE id=:claim_id AND review_status='pending'
                                RETURNING quality_model, quality_prompt_version
                                """
                            ),
                            {"claim_id": claim_id, "rationale": result["rationale"]},
                        )
                    ).mappings().first()
                    if claim is None:
                        continue
                    await self._session.execute(
                        text(
                            """
                            INSERT INTO knowledge.claim_quality_reviews
                                (claim_id, run_id, action, machine_decision,
                                 effective_decision, utility_score,
                                 reviewer_confidence, reason_codes, rationale,
                                 model, prompt_version, dry_run, applied)
                            VALUES (:claim_id, :run_id, 'apply', :machine_decision,
                                    'reject', :utility_score, :reviewer_confidence,
                                    CAST(:reason_codes AS jsonb), :rationale,
                                    :model, :prompt_version, false, true)
                            """
                        ),
                        {
                            "claim_id": claim_id,
                            "run_id": run_id,
                            "machine_decision": result["machine_decision"],
                            "utility_score": result["utility_score"],
                            "reviewer_confidence": result["reviewer_confidence"],
                            "reason_codes": json.dumps(result["reason_codes"]),
                            "rationale": result["rationale"],
                            "model": claim["quality_model"],
                            "prompt_version": claim["quality_prompt_version"],
                        },
                    )
                await self._session.execute(
                    text(
                        "UPDATE knowledge.quality_review_items SET status='applied' "
                        "WHERE id=:id"
                    ),
                    {"id": item["id"]},
                )
            await self._session.execute(
                text(
                    "UPDATE knowledge.quality_review_runs "
                    "SET status='applied', applied_at=now() WHERE id=:id"
                ),
                {"id": run_id},
            )
        return await self.status(run_id)

    async def cancel(self, run_id: UUID) -> dict[str, Any]:
        async with self._session.begin():
            changed = (
                await self._session.execute(
                    text(
                        """
                        UPDATE knowledge.quality_review_runs
                        SET cancel_requested=true,
                            status=CASE WHEN status IN ('queued','running')
                                        THEN 'cancelling' ELSE status END
                        WHERE id=:id RETURNING id
                        """
                    ),
                    {"id": run_id},
                )
            ).scalar_one_or_none()
            if changed is None:
                raise NotFoundError("quality run not found")
            await self._session.execute(
                text(
                    """
                    UPDATE knowledge.quality_review_items
                    SET status='cancelled', error='run cancelled'
                    WHERE run_id=:id AND status IN ('queued','retry')
                    """
                ),
                {"id": run_id},
            )
            await self._finish_run(self._session, run_id)
        return await self.status(run_id)

    async def restore(self, claim_id: UUID) -> dict[str, Any]:
        async with self._session.begin():
            prior = (
                await self._session.execute(
                    text(
                        """
                        SELECT quality_utility_score, quality_reviewer_confidence,
                               quality_reasons, quality_note, quality_model,
                               quality_prompt_version
                        FROM knowledge.claims
                        WHERE id=:id AND review_status='rejected'
                          AND reviewed_by LIKE 'ai-quality:%'
                        FOR UPDATE
                        """
                    ),
                    {"id": claim_id},
                )
            ).mappings().first()
            if prior is None:
                exists = (
                    await self._session.execute(
                        text("SELECT 1 FROM knowledge.claims WHERE id=:id"),
                        {"id": claim_id},
                    )
                ).scalar_one_or_none()
                if exists is None:
                    raise NotFoundError("claim not found")
                raise ConflictError("only AI quality rejections may be restored")
            await self._session.execute(
                text(
                    """
                    UPDATE knowledge.claims
                    SET review_status='pending', review_note=NULL,
                        reviewed_by=NULL, reviewed_at=NULL,
                        quality_status='uncertain',
                        quality_note='Restored from AI quality rejection',
                        quality_reviewed_at=now()
                    WHERE id=:id
                    """
                ),
                {"id": claim_id},
            )
            await self._session.execute(
                text(
                    """
                    INSERT INTO knowledge.claim_quality_reviews
                        (claim_id, action, machine_decision, effective_decision,
                         utility_score, reviewer_confidence, reason_codes,
                         rationale, model, prompt_version, dry_run, applied)
                    VALUES (:claim_id, 'restore', 'uncertain', 'uncertain',
                            :utility_score, :reviewer_confidence,
                            CAST(:reason_codes AS jsonb), :rationale, :model,
                            :prompt_version, false, false)
                    """
                ),
                {
                    "claim_id": claim_id,
                    "utility_score": prior["quality_utility_score"],
                    "reviewer_confidence": prior["quality_reviewer_confidence"],
                    "reason_codes": json.dumps(prior["quality_reasons"] or []),
                    "rationale": "Restored from AI quality rejection",
                    "model": prior["quality_model"],
                    "prompt_version": prior["quality_prompt_version"],
                },
            )
            row = (
                await self._session.execute(
                    text(CLAIM_ITEM_SQL + " WHERE c.id=:claim_id"),
                    {"claim_id": claim_id},
                )
            ).mappings().one()
        item = _claim_item_from_row(row)
        claim = item.pop("claim")
        return {**claim, **item}

    @staticmethod
    async def _finish_run(session: AsyncSession, run_id: UUID) -> None:
        await session.execute(
            text(
                """
                UPDATE knowledge.quality_review_runs r SET
                    status=CASE WHEN r.cancel_requested THEN 'cancelled'
                                ELSE 'completed' END,
                    finished_at=now()
                WHERE r.id=:run_id AND NOT EXISTS (
                    SELECT 1 FROM knowledge.quality_review_items i
                    WHERE i.run_id=r.id
                      AND i.status IN ('queued','retry','processing')
                )
                """
            ),
            {"run_id": run_id},
        )


class QualityReviewWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        reviewer: QualityReviewer | None | object = _DEFAULT_REVIEWER,
    ) -> None:
        self._factory = session_factory
        self._reviewer = (
            _reviewer_from_settings() if reviewer is _DEFAULT_REVIEWER else reviewer
        )

    async def run_once(self) -> bool:
        lease_token = uuid4()
        async with self._factory() as session:
            async with session.begin():
                recovered = (
                    await session.execute(
                        text(
                            """
                            UPDATE knowledge.quality_review_items
                            SET status=CASE WHEN attempts>=:max_attempts
                                            THEN 'failed' ELSE 'retry' END,
                                lease_token=NULL, lease_expires_at=NULL,
                                error='worker lease expired'
                            WHERE status='processing' AND lease_expires_at < now()
                            RETURNING run_id
                            """
                        ),
                        {"max_attempts": MAX_ATTEMPTS},
                    )
                ).mappings().all()
                for recovered_run_id in {
                    recovered_row["run_id"] for recovered_row in recovered
                }:
                    await QualityGateService._finish_run(session, recovered_run_id)
                row = (
                    await session.execute(
                        text(
                            """
                            SELECT i.id, i.run_id, i.document_id,
                                   r.include_reviewed
                            FROM knowledge.quality_review_items i
                            JOIN knowledge.quality_review_runs r ON r.id=i.run_id
                            WHERE i.status IN ('queued','retry')
                              AND i.next_attempt_at<=now()
                              AND i.attempts<:max_attempts
                              AND NOT r.cancel_requested
                            ORDER BY i.created_at, i.id
                            FOR UPDATE OF i SKIP LOCKED LIMIT 1
                            """
                        ),
                        {"max_attempts": MAX_ATTEMPTS},
                    )
                ).mappings().first()
                if row is None:
                    return False
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.quality_review_items
                        SET status='processing', attempts=attempts+1,
                            lease_token=:lease_token,
                            lease_expires_at=now()+interval '5 minutes', error=NULL
                        WHERE id=:id
                        """
                    ),
                    {"id": row["id"], "lease_token": lease_token},
                )
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.quality_review_runs
                        SET status='running', started_at=COALESCE(started_at,now())
                        WHERE id=:run_id AND status='queued'
                        """
                    ),
                    {"run_id": row["run_id"]},
                )

        try:
            if self._reviewer is None:
                raise QualityReviewError("AI quality review is not configured")
            async with self._factory() as read_session:
                document = (
                    await read_session.execute(
                        text(
                            """
                            SELECT d.title, d.canonical_url, d.raw_content,
                                   s.name AS source_name, s.channel
                            FROM knowledge.documents d
                            JOIN knowledge.sources s ON s.id=d.source_id
                            WHERE d.id=:document_id
                            """
                        ),
                        {"document_id": row["document_id"]},
                    )
                ).mappings().one()
                claims = (
                    await read_session.execute(
                        text(
                            """
                            SELECT c.id, c.statement, c.conditions, c.exceptions,
                                   c.recommended_action,
                                   e.excerpt AS evidence_excerpt,
                                   e.locator AS evidence_locator
                            FROM knowledge.claims c
                            LEFT JOIN LATERAL (
                                SELECT excerpt, locator FROM knowledge.evidence
                                WHERE claim_id=c.id ORDER BY created_at, id LIMIT 1
                            ) e ON true
                            WHERE c.document_id=:document_id
                              AND c.review_status='pending'
                              AND (:include_reviewed OR
                                   c.quality_status IN ('unreviewed','error'))
                            ORDER BY c.created_at, c.id
                            """
                        ),
                        {
                            "document_id": row["document_id"],
                            "include_reviewed": row["include_reviewed"],
                        },
                    )
                ).mappings().all()
            candidates = [
                QualityCandidate(
                    claim_id=claim["id"],
                    statement=claim["statement"],
                    conditions=list(claim["conditions"] or []),
                    exceptions=list(claim["exceptions"] or []),
                    recommended_action=claim["recommended_action"] or "",
                    evidence_excerpt=claim["evidence_excerpt"] or "",
                    evidence_locator=claim["evidence_locator"] or "",
                )
                for claim in claims
            ]
            result = await self._reviewer.review_candidates(
                QualityDocumentContext(
                    title=document["title"],
                    source_name=document["source_name"],
                    channel=document["channel"],
                    canonical_url=document["canonical_url"] or "",
                    raw_content=document["raw_content"],
                ),
                candidates,
            )
            await self._mark_success(row, claims, result)
        except Exception as exc:
            message = str(exc) if isinstance(exc, QualityReviewError) else "temporary processing failure"
            await self._mark_failure(row["id"], row["run_id"], message)
        return True

    async def _mark_success(self, row: Any, claims: Any, result: Any) -> None:
        saved_results: list[dict[str, Any]] = []
        async with self._factory() as session:
            async with session.begin():
                for claim, judgment in zip(claims, result.judgments, strict=True):
                    effective = effective_decision(judgment, result.document)
                    reasons = list(judgment.reason_codes)
                    rationale = judgment.rationale
                    confidence = judgment.reviewer_confidence
                    if result.document.decision == "reject":
                        reasons = list(dict.fromkeys(reasons + result.document.reason_codes))
                        rationale += f" Document: {result.document.rationale}"
                        confidence = max(confidence, result.document.reviewer_confidence)
                    await session.execute(
                        text(
                            """
                            UPDATE knowledge.claims
                            SET quality_status=:quality_status,
                                quality_utility_score=:utility_score,
                                quality_reviewer_confidence=:reviewer_confidence,
                                quality_reasons=CAST(:reason_codes AS jsonb),
                                quality_note=:rationale, quality_model=:model,
                                quality_prompt_version=:prompt_version,
                                quality_reviewed_at=now()
                            WHERE id=:claim_id AND review_status='pending'
                            """
                        ),
                        {
                            "claim_id": claim["id"],
                            "quality_status": effective,
                            "utility_score": judgment.utility_score,
                            "reviewer_confidence": confidence,
                            "reason_codes": json.dumps(reasons),
                            "rationale": rationale,
                            "model": result.model,
                            "prompt_version": result.prompt_version,
                        },
                    )
                    await session.execute(
                        text(
                            """
                            INSERT INTO knowledge.claim_quality_reviews
                                (claim_id, run_id, action, machine_decision,
                                 effective_decision, utility_score,
                                 reviewer_confidence, reason_codes, rationale,
                                 model, prompt_version, dry_run, applied)
                            VALUES (:claim_id, :run_id, 'classification',
                                    :machine_decision, :effective_decision,
                                    :utility_score, :reviewer_confidence,
                                    CAST(:reason_codes AS jsonb), :rationale,
                                    :model, :prompt_version, true, false)
                            """
                        ),
                        {
                            "claim_id": claim["id"],
                            "run_id": row["run_id"],
                            "machine_decision": judgment.decision,
                            "effective_decision": effective,
                            "utility_score": judgment.utility_score,
                            "reviewer_confidence": confidence,
                            "reason_codes": json.dumps(reasons),
                            "rationale": rationale,
                            "model": result.model,
                            "prompt_version": result.prompt_version,
                        },
                    )
                    saved_results.append(
                        {
                            "claim_id": str(claim["id"]),
                            "statement": claim["statement"],
                            "machine_decision": judgment.decision,
                            "effective_decision": effective,
                            "utility_score": judgment.utility_score,
                            "reviewer_confidence": confidence,
                            "reason_codes": reasons,
                            "rationale": rationale,
                        }
                    )
                auto_rejects = sum(
                    item["effective_decision"] == "reject" for item in saved_results
                )
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.quality_review_items
                        SET status='completed', reviewed_claims=:reviewed_claims,
                            auto_reject_count=:auto_rejects,
                            document_decision=:document_decision,
                            document_reviewer_confidence=:document_confidence,
                            document_reason_codes=CAST(:document_reasons AS jsonb),
                            document_rationale=:document_rationale,
                            results=CAST(:results AS jsonb), error=NULL,
                            lease_token=NULL, lease_expires_at=NULL
                        WHERE id=:id
                        """
                    ),
                    {
                        "id": row["id"],
                        "reviewed_claims": len(saved_results),
                        "auto_rejects": auto_rejects,
                        "document_decision": result.document.decision,
                        "document_confidence": result.document.reviewer_confidence,
                        "document_reasons": json.dumps(result.document.reason_codes),
                        "document_rationale": result.document.rationale,
                        "results": json.dumps(saved_results),
                    },
                )
                await QualityGateService._finish_run(session, row["run_id"])

    async def _mark_failure(self, item_id: UUID, run_id: UUID, message: str) -> None:
        async with self._factory() as session:
            async with session.begin():
                attempts = (
                    await session.execute(
                        text(
                            "SELECT attempts FROM knowledge.quality_review_items "
                            "WHERE id=:id"
                        ),
                        {"id": item_id},
                    )
                ).scalar_one()
                status = "failed" if attempts >= MAX_ATTEMPTS else "retry"
                delay = min(300, 30 * (2 ** max(attempts - 1, 0)))
                await session.execute(
                    text(
                        """
                        UPDATE knowledge.quality_review_items
                        SET status=:status, error=:error,
                            next_attempt_at=now()+make_interval(secs=>:delay),
                            lease_token=NULL, lease_expires_at=NULL
                        WHERE id=:id
                        """
                    ),
                    {
                        "id": item_id,
                        "status": status,
                        "error": message[:2000],
                        "delay": delay,
                    },
                )
                await QualityGateService._finish_run(session, run_id)

    async def run_forever(self) -> None:
        while True:
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                worked = False
            await asyncio.sleep(0.2 if worked else 2.0)
