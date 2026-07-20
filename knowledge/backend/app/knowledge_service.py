from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_extractor import AIExtractionError, AIExtractor
from .config import get_settings
from .errors import ConflictError, InvalidInputError, NotFoundError
from .quality_gate import (
    OpenAICompatibleQualityAdapter,
    QualityCandidate,
    QualityDocumentContext,
    QualityReviewError,
    QualityReviewer,
    effective_decision,
)
from .schemas import (
    ImportDocumentRequest,
    ImportUrlRequest,
    RetrieveRequest,
    ReviewClaimRequest,
)
from .web_importer import WebArticleFetcher


SOURCE_COLUMNS = """
    s.id, s.name, s.channel, s.source_type, s.base_url, s.domain,
    s.rights_confirmed, s.status, s.metadata, s.created_at, s.updated_at
"""

DOCUMENT_COLUMNS = """
    d.id, d.source_id, d.canonical_url, d.title, d.content_type,
    d.language_code, d.market, d.author, d.published_at, d.content_hash,
    d.status, d.metadata, d.created_at, d.updated_at
"""

CLAIM_COLUMNS = """
    c.id, c.document_id, c.channel, c.topic, c.knowledge_type, c.statement,
    c.conditions, c.exceptions, c.recommended_action, c.confidence,
    c.review_status, c.review_note, c.reviewed_by, c.reviewed_at,
    c.quality_status, c.quality_utility_score,
    c.quality_reviewer_confidence, c.quality_reasons, c.quality_note,
    c.quality_model, c.quality_prompt_version, c.quality_reviewed_at,
    c.metadata, c.created_at, c.updated_at
"""

CLAIM_ITEM_SQL = """
SELECT
    c.id AS claim_id, c.document_id AS claim_document_id,
    c.channel AS claim_channel, c.topic AS claim_topic,
    c.knowledge_type AS claim_knowledge_type,
    c.statement AS claim_statement, c.conditions AS claim_conditions,
    c.exceptions AS claim_exceptions,
    c.recommended_action AS claim_recommended_action,
    c.confidence AS claim_confidence,
    c.review_status AS claim_review_status,
    c.review_note AS claim_review_note,
    c.reviewed_by AS claim_reviewed_by,
    c.reviewed_at AS claim_reviewed_at,
    c.quality_status AS claim_quality_status,
    c.quality_utility_score AS claim_quality_utility_score,
    c.quality_reviewer_confidence AS claim_quality_reviewer_confidence,
    c.quality_reasons AS claim_quality_reasons,
    c.quality_note AS claim_quality_note,
    c.quality_model AS claim_quality_model,
    c.quality_prompt_version AS claim_quality_prompt_version,
    c.quality_reviewed_at AS claim_quality_reviewed_at,
    c.metadata AS claim_metadata, c.created_at AS claim_created_at,
    c.updated_at AS claim_updated_at,
    d.id AS document_id, d.source_id AS document_source_id,
    d.canonical_url AS document_canonical_url, d.title AS document_title,
    d.content_type AS document_content_type,
    d.language_code AS document_language_code, d.market AS document_market,
    d.author AS document_author, d.published_at AS document_published_at,
    d.content_hash AS document_content_hash, d.status AS document_status,
    d.metadata AS document_metadata, d.created_at AS document_created_at,
    d.updated_at AS document_updated_at,
    s.id AS source_id, s.name AS source_name, s.channel AS source_channel,
    s.source_type AS source_source_type, s.base_url AS source_base_url,
    s.domain AS source_domain,
    s.rights_confirmed AS source_rights_confirmed,
    s.status AS source_status, s.metadata AS source_metadata,
    s.created_at AS source_created_at, s.updated_at AS source_updated_at,
    COALESCE(ev.items, '[]'::jsonb) AS evidence
FROM knowledge.claims c
JOIN knowledge.documents d ON d.id = c.document_id
JOIN knowledge.sources s ON s.id = d.source_id
LEFT JOIN LATERAL (
    SELECT jsonb_agg(
        jsonb_build_object(
            'id', e.id,
            'claim_id', e.claim_id,
            'document_id', e.document_id,
            'excerpt', e.excerpt,
            'locator', e.locator,
            'created_at', e.created_at
        ) ORDER BY e.created_at, e.id
    ) AS items
    FROM knowledge.evidence e
    WHERE e.claim_id = c.id
) ev ON true
"""

# This predicate is deliberately a constant and is applied inside the database
# query, so callers cannot accidentally retrieve pending or rejected knowledge.
APPROVED_ONLY_PREDICATE = "c.review_status = 'approved'"

RETRIEVAL_SCORE_SQL = """
GREATEST(
    ts_rank_cd(c.search_vector,
        websearch_to_tsquery('simple', CAST(:query AS text))),
    CASE WHEN lower(c.statement)
              LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
         THEN 0.25 ELSE 0 END,
    CASE WHEN lower(COALESCE(c.topic, ''))
              LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
         THEN 0.15 ELSE 0 END,
    CASE WHEN lower(COALESCE(c.recommended_action, ''))
              LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
         THEN 0.10 ELSE 0 END
)
"""

RETRIEVE_SQL = CLAIM_ITEM_SQL.replace(
    "\nFROM knowledge.claims c",
    f",\n{RETRIEVAL_SCORE_SQL} AS score\nFROM knowledge.claims c",
) + f"""
WHERE {APPROVED_ONLY_PREDICATE}
  AND d.is_current
  AND (CAST(:channel AS text) IS NULL
       OR c.channel = CAST(:channel AS text))
  AND (CAST(:language_code AS text) IS NULL
       OR d.language_code = CAST(:language_code AS text))
  AND (
      c.search_vector @@ websearch_to_tsquery('simple', CAST(:query AS text))
      OR lower(c.statement)
          LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
      OR lower(COALESCE(c.topic, ''))
          LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
      OR lower(COALESCE(c.recommended_action, ''))
          LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
  )
ORDER BY score DESC,
    c.created_at DESC,
    c.id
LIMIT :limit
"""

_PARAGRAPH_BREAK = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)+")
_MARKDOWN_PREFIX = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE
)
_WHITESPACE = re.compile(r"\s+")
_AUTO_EXTRACTOR = object()
_AUTO_QUALITY_REVIEWER = object()


def content_fingerprint(raw_content: str) -> str:
    return hashlib.sha256(raw_content.encode("utf-8")).hexdigest()


def extract_claim_candidates(raw_content: str, limit: int = 8) -> list[dict[str, str]]:
    """Create stable, review-required candidates without calling a model or URL."""
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for paragraph_number, raw_paragraph in enumerate(
        _PARAGRAPH_BREAK.split(raw_content.replace("\r\n", "\n")), start=1
    ):
        excerpt = _WHITESPACE.sub(" ", raw_paragraph).strip()
        statement = _MARKDOWN_PREFIX.sub("", raw_paragraph)
        statement = _WHITESPACE.sub(" ", statement).strip()
        if not statement:
            continue
        key = statement.casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "statement": statement,
                "excerpt": excerpt,
                "locator": f"paragraph:{paragraph_number}",
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def _row_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def _prefixed(row: Mapping[str, Any], prefix: str, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row[f"{prefix}{field}"] for field in fields}


SOURCE_FIELDS = (
    "id",
    "name",
    "channel",
    "source_type",
    "base_url",
    "domain",
    "rights_confirmed",
    "status",
    "metadata",
    "created_at",
    "updated_at",
)
DOCUMENT_FIELDS = (
    "id",
    "source_id",
    "canonical_url",
    "title",
    "content_type",
    "language_code",
    "market",
    "author",
    "published_at",
    "content_hash",
    "status",
    "metadata",
    "created_at",
    "updated_at",
)
CLAIM_FIELDS = (
    "id",
    "document_id",
    "channel",
    "topic",
    "knowledge_type",
    "statement",
    "conditions",
    "exceptions",
    "recommended_action",
    "confidence",
    "review_status",
    "review_note",
    "reviewed_by",
    "reviewed_at",
    "quality_status",
    "quality_utility_score",
    "quality_reviewer_confidence",
    "quality_reasons",
    "quality_note",
    "quality_model",
    "quality_prompt_version",
    "quality_reviewed_at",
    "metadata",
    "created_at",
    "updated_at",
)


def _claim_item_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "claim": _prefixed(row, "claim_", CLAIM_FIELDS),
        "source": _prefixed(row, "source_", SOURCE_FIELDS),
        "document": _prefixed(row, "document_", DOCUMENT_FIELDS),
        "evidence": row["evidence"] or [],
    }


def _literal_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class KnowledgeService:
    """Deep module that owns knowledge import, review, retrieval and audit rules."""

    def __init__(
        self,
        session: AsyncSession,
        extractor: AIExtractor | None | object = _AUTO_EXTRACTOR,
        quality_reviewer: QualityReviewer | None | object = _AUTO_QUALITY_REVIEWER,
    ):
        self._session = session
        self._extractor = (
            self._extractor_from_settings() if extractor is _AUTO_EXTRACTOR else extractor
        )
        self._quality_reviewer = (
            self._quality_reviewer_from_settings()
            if quality_reviewer is _AUTO_QUALITY_REVIEWER
            else quality_reviewer
        )

    @staticmethod
    def _extractor_from_settings() -> AIExtractor | None:
        settings = get_settings()
        if not (
            settings.ai_base_url and settings.ai_api_key and settings.ai_model
        ):
            return None
        return AIExtractor(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_attempts=settings.ai_max_attempts,
        )

    @staticmethod
    def _quality_reviewer_from_settings() -> QualityReviewer | None:
        settings = get_settings()
        if not (settings.ai_base_url and settings.ai_api_key and settings.ai_model):
            return None
        adapter = OpenAICompatibleQualityAdapter(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_attempts=settings.ai_max_attempts,
        )
        return QualityReviewer(model=settings.ai_model, adapter=adapter)

    async def _extract_candidates(
        self, raw_content: str, *, require_ai: bool = False
    ) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
        if self._extractor is not None:
            try:
                result = await self._extractor.extract(raw_content)
                candidates = [
                    {
                        "statement": candidate.statement,
                        "conditions": candidate.conditions,
                        "exceptions": candidate.exceptions,
                        "recommended_action": candidate.recommended_action,
                        "confidence": candidate.confidence,
                        "excerpt": candidate.evidence_excerpt,
                        "locator": candidate.evidence_locator,
                    }
                    for candidate in result.candidates
                ]
                metadata = {
                    "extraction_method": "ai",
                    "provider": result.metadata["provider"],
                    "model": result.metadata["model"],
                    "prompt_version": result.metadata["prompt_version"],
                }
                return candidates, metadata, None
            except AIExtractionError:
                # Model failures are intentionally isolated from durable imports.
                if require_ai:
                    raise
            if require_ai:
                raise AIExtractionError("AI extraction returned no valid knowledge cards")
            warning = (
                "AI extraction was unavailable or invalid; "
                "deterministic fallback was used"
            )
        else:
            if require_ai:
                raise AIExtractionError("AI is not configured")
            warning = "AI is not configured; deterministic fallback was used"

        candidates = [
            {
                **candidate,
                "conditions": [],
                "exceptions": [],
                "recommended_action": candidate["statement"],
                "confidence": 0.55,
            }
            for candidate in extract_claim_candidates(raw_content)
        ]
        return (
            candidates,
            {
                "extraction_method": "deterministic_fallback",
                "provider": None,
                "model": None,
                "prompt_version": None,
            },
            warning,
        )

    async def _find_existing_import(
        self, *, source_name: str, channel: str, content_hash: str
    ) -> dict[str, Any] | None:
        source_columns = ", ".join(
            f"s.{field} AS existing_source_{field}" for field in SOURCE_FIELDS
        )
        document_columns = ", ".join(
            f"d.{field} AS existing_document_{field}" for field in DOCUMENT_FIELDS
        )
        async with self._session.begin():
            row = (
                await self._session.execute(
                    text(
                        f"""
                        SELECT {source_columns}, {document_columns}
                        FROM knowledge.sources s
                        JOIN knowledge.documents d ON d.source_id = s.id
                        WHERE lower(s.name) = lower(:source_name)
                          AND s.channel = :channel
                          AND d.content_hash = :content_hash
                          AND d.is_current
                        """
                    ),
                    {
                        "source_name": source_name,
                        "channel": channel,
                        "content_hash": content_hash,
                    },
                )
            ).mappings().first()
        if row is None:
            return None
        return {
            "created": False,
            "duplicate": True,
            "source": _prefixed(row, "existing_source_", SOURCE_FIELDS),
            "document": _prefixed(row, "existing_document_", DOCUMENT_FIELDS),
            "claims_created": 0,
        }

    async def _find_existing_url_import(
        self, *, canonical_url: str
    ) -> dict[str, Any] | None:
        source_columns = ", ".join(
            f"s.{field} AS existing_source_{field}" for field in SOURCE_FIELDS
        )
        document_columns = ", ".join(
            f"d.{field} AS existing_document_{field}" for field in DOCUMENT_FIELDS
        )
        async with self._session.begin():
            row = (
                await self._session.execute(
                    text(
                        f"""
                        SELECT {source_columns}, {document_columns}
                        FROM knowledge.sources s
                        JOIN knowledge.documents d ON d.source_id = s.id
                        WHERE d.canonical_url = :canonical_url
                          AND d.is_current
                        """
                    ),
                    {"canonical_url": canonical_url},
                )
            ).mappings().first()
        if row is None:
            return None
        return {
            "created": False,
            "duplicate": True,
            "source": _prefixed(row, "existing_source_", SOURCE_FIELDS),
            "document": _prefixed(row, "existing_document_", DOCUMENT_FIELDS),
            "claims_created": 0,
        }

    async def overview(self) -> dict[str, Any]:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM knowledge.sources) AS sources,
                        (SELECT count(*) FROM knowledge.documents
                            WHERE is_current) AS documents,
                        (SELECT count(*) FROM knowledge.claims c
                            JOIN knowledge.documents d ON d.id=c.document_id
                            WHERE c.review_status = 'pending' AND d.is_current) AS pending,
                        (SELECT count(*) FROM knowledge.claims c
                            JOIN knowledge.documents d ON d.id=c.document_id
                            WHERE c.review_status = 'approved' AND d.is_current) AS approved,
                        (SELECT count(*) FROM knowledge.claims c
                            JOIN knowledge.documents d ON d.id=c.document_id
                            WHERE c.review_status = 'rejected' AND d.is_current) AS rejected,
                        (SELECT count(*) FROM knowledge.usages) AS usages
                    """
                )
            )
        ).mappings().one()
        return {
            "sources": row["sources"],
            "documents": row["documents"],
            "claims": {
                "pending": row["pending"],
                "approved": row["approved"],
                "rejected": row["rejected"],
            },
            "usages": row["usages"],
        }

    async def list_sources(self, *, limit: int, offset: int) -> dict[str, Any]:
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT {SOURCE_COLUMNS},
                           count(DISTINCT d.id) AS document_count,
                           count(DISTINCT c.id) AS claim_count
                    FROM knowledge.sources s
                    LEFT JOIN knowledge.documents d ON d.source_id = s.id AND d.is_current
                    LEFT JOIN knowledge.claims c ON c.document_id = d.id
                    GROUP BY s.id
                    ORDER BY s.created_at DESC, s.id
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": limit, "offset": offset},
            )
        ).mappings().all()
        return {"items": [_row_dict(row) for row in rows]}

    async def list_documents(self, *, limit: int, offset: int) -> dict[str, Any]:
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT {DOCUMENT_COLUMNS}, count(c.id) AS claim_count,
                           s.id AS source_id_nested, s.name AS source_name,
                           s.channel AS source_channel,
                           s.source_type AS source_source_type,
                           s.base_url AS source_base_url,
                           s.domain AS source_domain,
                           s.rights_confirmed AS source_rights_confirmed,
                           s.status AS source_status,
                           s.metadata AS source_metadata,
                           s.created_at AS source_created_at,
                           s.updated_at AS source_updated_at
                    FROM knowledge.documents d
                    JOIN knowledge.sources s ON s.id = d.source_id
                    LEFT JOIN knowledge.claims c ON c.document_id = d.id
                    WHERE d.is_current
                    GROUP BY d.id, s.id
                    ORDER BY d.created_at DESC, d.id
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": limit, "offset": offset},
            )
        ).mappings().all()
        items: list[dict[str, Any]] = []
        for row in rows:
            document = {field: row[field] for field in DOCUMENT_FIELDS}
            document["claim_count"] = row["claim_count"]
            document["source"] = {
                "id": row["source_id_nested"],
                **{
                    field: row[f"source_{field}"]
                    for field in SOURCE_FIELDS
                    if field != "id"
                },
            }
            items.append(document)
        return {"items": items}

    async def list_claims(
        self,
        *,
        review_status: str | None,
        quality_status: str | None = None,
        query: str | None = None,
        source_id: UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        predicates = ["d.is_current"]
        params: dict[str, Any] = {
            "review_status": review_status,
            "quality_status": quality_status,
            "source_id": source_id,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "offset": offset,
        }
        if review_status:
            predicates.append("c.review_status = :review_status")
        if quality_status:
            predicates.append("c.quality_status = :quality_status")
        if query and query.strip():
            normalized_query = query.strip()
            predicates.append(
                """(
                    c.search_vector @@ websearch_to_tsquery('simple', CAST(:query AS text))
                    OR lower(c.statement) LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
                    OR lower(COALESCE(c.topic, '')) LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
                    OR lower(COALESCE(c.recommended_action, '')) LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
                    OR lower(d.title) LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
                    OR lower(s.name) LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1 FROM knowledge.evidence search_evidence
                        WHERE search_evidence.claim_id = c.id
                          AND lower(search_evidence.excerpt) LIKE lower(CAST(:literal_pattern AS text)) ESCAPE '\\'
                    )
                )"""
            )
            params["query"] = normalized_query
            params["literal_pattern"] = _literal_pattern(normalized_query)
        if source_id:
            predicates.append("s.id = :source_id")
        if date_from and date_to and date_from > date_to:
            raise InvalidInputError("date_from must be on or before date_to")
        if date_from:
            predicates.append("c.created_at >= CAST(:date_from AS date)")
        if date_to:
            predicates.append(
                "c.created_at < CAST(:date_to AS date) + INTERVAL '1 day'"
            )
        where = "WHERE " + " AND ".join(predicates)
        total = (
            await self._session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM knowledge.claims c
                    JOIN knowledge.documents d ON d.id = c.document_id
                    JOIN knowledge.sources s ON s.id = d.source_id
                    """
                    + where
                ),
                params,
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                text(
                    CLAIM_ITEM_SQL
                    + where
                    + " ORDER BY c.created_at DESC, c.id LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings().all()
        items = []
        for row in rows:
            item = _claim_item_from_row(row)
            claim = item.pop("claim")
            items.append({**claim, **item})
        return {"items": items, "total": total}

    async def import_document(
        self,
        request: ImportDocumentRequest,
        *,
        allow_revision: bool = False,
        require_ai: bool = False,
        require_quality: bool = False,
    ) -> dict[str, Any]:
        if request.rights_confirmed is not True:
            raise InvalidInputError("rights_confirmed must be true")

        content_hash = content_fingerprint(request.raw_content)
        parsed_url = urlsplit(request.canonical_url) if request.canonical_url else None
        base_url = (
            f"{parsed_url.scheme}://{parsed_url.netloc}" if parsed_url else None
        )
        domain = parsed_url.hostname.lower() if parsed_url and parsed_url.hostname else None

        # Avoid spending model tokens on an obvious duplicate. The write path
        # still enforces uniqueness to cover races after this read-only check.
        if self._extractor is not None:
            existing = await self._find_existing_import(
                source_name=request.source_name,
                channel=request.channel,
                content_hash=content_hash,
            )
            if existing is not None:
                return existing

        # Keep network latency outside the database write transaction.
        candidates, extraction_metadata, extraction_warning = (
            await self._extract_candidates(request.raw_content, require_ai=require_ai)
        )

        quality_result = None
        quality_error: str | None = None
        if candidates:
            if self._quality_reviewer is None:
                if require_quality:
                    quality_error = "AI quality review is not configured"
            else:
                try:
                    quality_result = await self._quality_reviewer.review_candidates(
                        QualityDocumentContext(
                            title=request.title,
                            source_name=request.source_name,
                            channel=request.channel,
                            canonical_url=request.canonical_url or "",
                            raw_content=request.raw_content,
                        ),
                        [
                            QualityCandidate(
                                claim_id=None,
                                statement=candidate["statement"],
                                conditions=candidate["conditions"],
                                exceptions=candidate["exceptions"],
                                recommended_action=candidate["recommended_action"],
                                evidence_excerpt=candidate["excerpt"],
                                evidence_locator=candidate["locator"],
                            )
                            for candidate in candidates
                        ],
                    )
                except QualityReviewError as exc:
                    quality_error = str(exc)
            if quality_error is not None:
                if require_quality:
                    raise QualityReviewError(quality_error)
                quality_warning = f"Quality review failed: {quality_error}"
                extraction_warning = (
                    f"{extraction_warning}; {quality_warning}"
                    if extraction_warning
                    else quality_warning
                )

        async with self._session.begin():
            source = (
                await self._session.execute(
                    text(
                        f"""
                        INSERT INTO knowledge.sources
                            (name, channel, source_type, base_url, domain,
                             rights_confirmed)
                        VALUES
                            (:name, :channel, 'manual', :base_url, :domain, true)
                        ON CONFLICT (lower(name), channel) DO UPDATE SET
                            base_url = COALESCE(knowledge.sources.base_url,
                                                EXCLUDED.base_url),
                            domain = COALESCE(knowledge.sources.domain,
                                              EXCLUDED.domain),
                            rights_confirmed = knowledge.sources.rights_confirmed
                                               OR EXCLUDED.rights_confirmed
                        RETURNING {SOURCE_COLUMNS.replace('s.', '')}
                        """
                    ),
                    {
                        "name": request.source_name,
                        "channel": request.channel,
                        "base_url": base_url,
                        "domain": domain,
                    },
                )
            ).mappings().one()

            revision = 1
            supersedes_id = None
            if allow_revision and request.canonical_url:
                current = (
                    await self._session.execute(
                        text(
                            """
                            SELECT id, source_id, content_hash, revision
                            FROM knowledge.documents
                            WHERE canonical_url=:canonical_url AND is_current
                            FOR UPDATE
                            """
                        ),
                        {"canonical_url": request.canonical_url},
                    )
                ).mappings().first()
                if current is not None:
                    if current["source_id"] != source["id"]:
                        raise ConflictError(
                            "canonical_url already belongs to a different source"
                        )
                    supersedes_id = current["id"]
                    revision = current["revision"] + 1
                    await self._session.execute(
                        text(
                            "UPDATE knowledge.documents SET is_current=false "
                            "WHERE id=:id"
                        ),
                        {"id": supersedes_id},
                    )

            inserted_document = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.documents
                            (source_id, canonical_url, title, content_type,
                             language_code, market, author, published_at,
                             raw_content, content_hash, status, revision,
                             is_current, supersedes_id)
                        VALUES
                            (:source_id, :canonical_url, :title, :content_type,
                             :language_code, :market, :author, :published_at,
                             :raw_content, :content_hash, 'imported', :revision,
                             true, :supersedes_id)
                        ON CONFLICT DO NOTHING
                        RETURNING id, source_id, canonical_url, title, content_type,
                                  language_code, market, author, published_at,
                                  content_hash, status, metadata, created_at, updated_at
                        """
                    ),
                    {
                        "source_id": source["id"],
                        "canonical_url": request.canonical_url,
                        "title": request.title,
                        "content_type": request.content_type,
                        "language_code": request.language_code,
                        "market": request.market,
                        "author": request.author,
                        "published_at": request.published_at,
                        "raw_content": request.raw_content,
                        "content_hash": content_hash,
                        "revision": revision,
                        "supersedes_id": supersedes_id,
                    },
                )
            ).mappings().first()

            if inserted_document is None:
                duplicate = (
                    await self._session.execute(
                        text(
                            """
                            SELECT id, source_id, canonical_url, title, content_type,
                                   language_code, market, author, published_at,
                                   content_hash, status, metadata, created_at, updated_at
                            FROM knowledge.documents
                            WHERE source_id = :source_id AND content_hash = :content_hash
                              AND is_current
                            """
                        ),
                        {"source_id": source["id"], "content_hash": content_hash},
                    )
                ).mappings().first()
                if duplicate is not None:
                    return {
                        "created": False,
                        "duplicate": True,
                        "source": _row_dict(source),
                        "document": _row_dict(duplicate),
                        "claims_created": 0,
                    }

                if request.canonical_url:
                    url_owner = (
                        await self._session.execute(
                            text(
                                "SELECT id FROM knowledge.documents "
                                "WHERE canonical_url = :canonical_url AND is_current"
                            ),
                            {"canonical_url": request.canonical_url},
                        )
                    ).scalar_one_or_none()
                    if url_owner is not None:
                        raise ConflictError(
                            "canonical_url already belongs to a different document"
                        )
                raise ConflictError("document conflicts with an existing record")

            document = _row_dict(inserted_document)
            for candidate_index, candidate in enumerate(candidates):
                judgment = (
                    quality_result.judgments[candidate_index]
                    if quality_result is not None
                    else None
                )
                if judgment is not None:
                    quality_status = effective_decision(
                        judgment, quality_result.document
                    )
                    quality_reasons = list(judgment.reason_codes)
                    quality_note = judgment.rationale
                    if quality_result.document.decision == "reject":
                        quality_reasons = list(
                            dict.fromkeys(
                                quality_reasons
                                + list(quality_result.document.reason_codes)
                            )
                        )
                        quality_note = (
                            f"{quality_note} Document: "
                            f"{quality_result.document.rationale}"
                        )
                    reviewer_confidence = max(
                        judgment.reviewer_confidence,
                        quality_result.document.reviewer_confidence
                        if quality_result.document.decision == "reject"
                        else 0.0,
                    )
                    quality_model = quality_result.model
                    quality_prompt_version = quality_result.prompt_version
                else:
                    quality_status = "error" if quality_error is not None else "unreviewed"
                    quality_reasons = []
                    quality_note = quality_error
                    reviewer_confidence = None
                    quality_model = (
                        self._quality_reviewer.model
                        if self._quality_reviewer is not None
                        else None
                    )
                    quality_prompt_version = None
                auto_reject = quality_status == "reject"
                claim_id = (
                    await self._session.execute(
                        text(
                            """
                            INSERT INTO knowledge.claims
                                (document_id, channel, topic, knowledge_type,
                                 statement, conditions, exceptions,
                                 recommended_action, confidence, review_status,
                                 review_note, reviewed_by, reviewed_at,
                                 quality_status, quality_utility_score,
                                 quality_reviewer_confidence, quality_reasons,
                                 quality_note, quality_model,
                                 quality_prompt_version, quality_reviewed_at,
                                 metadata)
                            VALUES
                                (:document_id, :channel, :topic, 'article_insight',
                                 :statement, CAST(:conditions AS jsonb),
                                 CAST(:exceptions AS jsonb), :recommended_action,
                                 :confidence,
                                 CASE WHEN :review_status='rejected'
                                      THEN 'rejected' ELSE 'pending' END,
                                 :review_note,
                                 :reviewed_by,
                                 CASE WHEN :auto_reject THEN now() ELSE NULL END,
                                 :quality_status, :quality_utility_score,
                                 :quality_reviewer_confidence,
                                 CAST(:quality_reasons AS jsonb), :quality_note,
                                 :quality_model, :quality_prompt_version,
                                 CASE WHEN :quality_attempted THEN now() ELSE NULL END,
                                 CAST(:metadata AS jsonb))
                            RETURNING id
                            """
                        ),
                        {
                            "document_id": document["id"],
                            "channel": request.channel,
                            "topic": request.title,
                            "statement": candidate["statement"],
                            "conditions": json.dumps(candidate["conditions"]),
                            "exceptions": json.dumps(candidate["exceptions"]),
                            "recommended_action": candidate["recommended_action"],
                            "confidence": candidate["confidence"],
                            "review_status": (
                                "rejected" if auto_reject else "pending"
                            ),
                            "review_note": quality_note if auto_reject else None,
                            "reviewed_by": (
                                f"ai-quality:{quality_model}" if auto_reject else None
                            ),
                            "auto_reject": auto_reject,
                            "quality_status": quality_status,
                            "quality_utility_score": (
                                judgment.utility_score if judgment is not None else None
                            ),
                            "quality_reviewer_confidence": reviewer_confidence,
                            "quality_reasons": json.dumps(quality_reasons),
                            "quality_note": quality_note,
                            "quality_model": quality_model,
                            "quality_prompt_version": quality_prompt_version,
                            "quality_attempted": (
                                judgment is not None or quality_error is not None
                            ),
                            "metadata": json.dumps(extraction_metadata),
                        },
                    )
                ).scalar_one()
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.evidence
                            (claim_id, document_id, excerpt, locator)
                        VALUES (:claim_id, :document_id, :excerpt, :locator)
                        """
                    ),
                    {
                        "claim_id": claim_id,
                        "document_id": document["id"],
                        "excerpt": candidate["excerpt"],
                        "locator": candidate["locator"],
                    },
                )
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.claim_quality_reviews
                            (claim_id, action, machine_decision,
                             effective_decision, utility_score,
                             reviewer_confidence, reason_codes, rationale,
                             model, prompt_version, dry_run, applied)
                        VALUES
                            (:claim_id, 'classification', :machine_decision,
                             :effective_decision, :utility_score,
                             :reviewer_confidence, CAST(:reason_codes AS jsonb),
                             :rationale, :model, :prompt_version, false, :applied)
                        """
                    ),
                    {
                        "claim_id": claim_id,
                        "machine_decision": (
                            judgment.decision if judgment is not None else "error"
                        ),
                        "effective_decision": (
                            quality_status if judgment is not None else "error"
                        ),
                        "utility_score": (
                            judgment.utility_score if judgment is not None else None
                        ),
                        "reviewer_confidence": reviewer_confidence,
                        "reason_codes": json.dumps(quality_reasons),
                        "rationale": quality_note,
                        "model": quality_model,
                        "prompt_version": quality_prompt_version,
                        "applied": auto_reject,
                    },
                )

            updated_document = (
                await self._session.execute(
                    text(
                        """
                        UPDATE knowledge.documents
                        SET status = 'processed',
                            metadata = metadata || CAST(:metadata AS jsonb)
                        WHERE id = :document_id
                        RETURNING id, source_id, canonical_url, title, content_type,
                                  language_code, market, author, published_at,
                                  content_hash, status, metadata, created_at, updated_at
                        """
                    ),
                    {
                        "document_id": document["id"],
                        "metadata": json.dumps(extraction_metadata),
                    },
                )
            ).mappings().one()

        return {
            "created": True,
            "duplicate": False,
            "source": _row_dict(source),
            "document": _row_dict(updated_document),
            "claims_created": len(candidates),
            "extraction_method": extraction_metadata["extraction_method"],
            "model": extraction_metadata["model"],
            "warning": extraction_warning,
        }

    async def import_url(
        self,
        request: ImportUrlRequest,
        fetcher: WebArticleFetcher | None = None,
    ) -> dict[str, Any]:
        if request.rights_confirmed is not True:
            raise InvalidInputError("rights_confirmed must be true")

        # Fetching and parsing happen before any write. A failed page never leaves
        # a source, document, claim or evidence record behind.
        article = await (fetcher or WebArticleFetcher()).fetch(request.url)

        # Canonical URLs are immutable in this first phase. Avoid model usage for
        # an already imported page; import_document separately checks content hash.
        existing = await self._find_existing_url_import(
            canonical_url=article.final_url
        )
        if existing is not None:
            return existing

        host = urlsplit(article.final_url).hostname or "web"
        source_name = request.source_name or host.removeprefix("www.")
        language_code = request.language_code or article.language_code or "und"
        return await self.import_document(
            ImportDocumentRequest(
                source_name=source_name,
                canonical_url=article.final_url,
                title=article.title,
                channel=request.channel,
                content_type="article",
                language_code=language_code,
                market=request.market,
                author=article.author,
                published_at=article.published_at,
                raw_content=article.raw_content,
                rights_confirmed=True,
            )
        )

    async def review_claim(
        self, claim_id: UUID, request: ReviewClaimRequest
    ) -> dict[str, Any]:
        async with self._session.begin():
            changed = (
                await self._session.execute(
                    text(
                        """
                        UPDATE knowledge.claims
                        SET review_status = :decision,
                            review_note = :note,
                            reviewed_by = :reviewer,
                            reviewed_at = now()
                        WHERE id = :claim_id
                          AND (
                              review_status = 'pending'
                              OR (review_status = 'approved' AND :decision = 'rejected')
                          )
                        RETURNING id
                        """
                    ),
                    {
                        "claim_id": claim_id,
                        "decision": request.decision,
                        "note": request.note,
                        "reviewer": request.reviewer,
                    },
                )
            ).scalar_one_or_none()
            if changed is None:
                current_status = (
                    await self._session.execute(
                        text("SELECT review_status FROM knowledge.claims WHERE id = :id"),
                        {"id": claim_id},
                    )
                ).scalar_one_or_none()
                if current_status is None:
                    raise NotFoundError("claim not found")
                raise ConflictError(f"claim is already {current_status}")

            row = (
                await self._session.execute(
                    text(CLAIM_ITEM_SQL + " WHERE c.id = :claim_id"),
                    {"claim_id": claim_id},
                )
            ).mappings().one()

        item = _claim_item_from_row(row)
        claim = item.pop("claim")
        return {**claim, **item}

    async def retrieve(self, request: RetrieveRequest) -> dict[str, Any]:
        params = {
            "query": request.query,
            "literal_pattern": _literal_pattern(request.query),
            "channel": request.channel,
            "language_code": request.language_code,
            "limit": request.limit,
        }
        async with self._session.begin():
            rows = (
                await self._session.execute(text(RETRIEVE_SQL), params)
            ).mappings().all()
            items: list[dict[str, Any]] = []
            for row in rows:
                item = _claim_item_from_row(row)
                item["score"] = max(float(row["score"]), 0.0)
                items.append(item)

            filters = {
                "channel": request.channel,
                # Kept in the response shape for API compatibility. Market is
                # document metadata, not a retrieval filter.
                "market": None,
                "language_code": request.language_code,
            }
            for item in items:
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.usages
                            (claim_id, stage, query, context, result)
                        VALUES
                            (:claim_id, 'retrieve', :query,
                             CAST(:context AS jsonb), CAST(:result AS jsonb))
                        """
                    ),
                    {
                        "claim_id": item["claim"]["id"],
                        "query": request.query,
                        "context": json.dumps(
                            {"filters": filters, "limit": request.limit}
                        ),
                        "result": json.dumps(item, default=str),
                    },
                )

        knowledge_pack = {
            "query": request.query,
            "filters": filters,
            "items": items,
        }
        return {"items": items, "knowledge_pack": knowledge_pack}
