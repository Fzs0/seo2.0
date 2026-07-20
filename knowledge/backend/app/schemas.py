from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Channel = Literal["seo", "social", "forum", "video", "visual"]
ReviewStatus = Literal["pending", "approved", "rejected"]
ExtractionMethod = Literal["ai", "deterministic_fallback"]
SignalContentKind = Literal[
    "post",
    "comment",
    "reply",
    "review",
    "video_comment",
    "forum_thread",
    "forum_reply",
    "message",
    "other",
]


def normalize_optional_http_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 2048 or any(character.isspace() for character in value):
        raise ValueError("canonical_url is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("canonical_url must be an HTTP(S) URL without credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("canonical_url has an invalid port") from exc
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "", parsed.query, ""))


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)


class SourceOut(ApiModel):
    id: UUID
    name: str
    channel: str
    source_type: str
    base_url: str | None = None
    domain: str | None = None
    rights_confirmed: bool
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    document_count: int | None = None
    claim_count: int | None = None


class MarketSignalIn(ApiModel):
    external_id: str | None = Field(default=None, max_length=500)
    canonical_url: str | None = None
    content_kind: SignalContentKind = "post"
    title: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1, max_length=20000)
    author_handle: str | None = Field(default=None, max_length=300)
    thread_key: str | None = Field(default=None, max_length=500)
    parent_external_id: str | None = Field(default=None, max_length=500)
    language_code: str = Field(default="en", min_length=1, max_length=20)
    market: str | None = Field(default=None, max_length=50)
    published_at: datetime | None = None
    engagement: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("external_id", "title", "author_handle", "thread_key", "parent_external_id", "market")
    @classmethod
    def strip_optional_signal_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator("content", "language_code")
    @classmethod
    def strip_signal_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("canonical_url")
    @classmethod
    def validate_signal_url(cls, value: str | None) -> str | None:
        return normalize_optional_http_url(value)


class ImportSignalsRequest(ApiModel):
    source_name: str = Field(min_length=1, max_length=300)
    platform: str = Field(min_length=1, max_length=80)
    channel: Channel = "social"
    rights_confirmed: bool = False
    signals: list[MarketSignalIn] = Field(min_length=1, max_length=1000)

    @field_validator("source_name", "platform")
    @classmethod
    def strip_signal_source_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class MarketSignalOut(ApiModel):
    id: UUID
    source_id: UUID
    source_name: str
    platform: str
    channel: str
    external_id: str | None = None
    canonical_url: str | None = None
    content_kind: SignalContentKind
    title: str | None = None
    content: str
    author_handle: str | None = None
    thread_key: str | None = None
    parent_external_id: str | None = None
    language_code: str
    market: str | None = None
    published_at: datetime | None = None
    engagement: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: Literal["new", "analyzed", "ignored"]
    created_at: datetime
    updated_at: datetime


class SignalListResponse(ApiModel):
    items: list[MarketSignalOut]
    total: int


class SignalImportResponse(ApiModel):
    source: SourceOut
    received: int
    created: int
    duplicates: int


class SignalOverviewResponse(ApiModel):
    total: int
    last_7_days: int
    last_30_days: int
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_platform: dict[str, int] = Field(default_factory=dict)


class SignalCrawlJobIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=300)
    platform: str = Field(min_length=1, max_length=80)
    channel: Channel = "social"
    rights_confirmed: bool = False
    search_url_template: str = Field(min_length=1, max_length=2048)
    keywords: list[str] = Field(min_length=1, max_length=20)
    detail_url_contains: list[str] = Field(min_length=1, max_length=10)
    max_pages: int = Field(default=3, ge=1, le=20)
    max_signals: int = Field(default=100, ge=1, le=500)
    delay_ms: int = Field(default=1000, ge=250, le=60000)
    interval_hours: int = Field(default=24, ge=1, le=168)
    enabled: bool = True

    @field_validator("name", "source_name", "platform", "search_url_template")
    @classmethod
    def strip_crawl_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("keywords", "detail_url_contains")
    @classmethod
    def strip_crawl_list(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("must contain at least one non-blank value")
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def validate_crawl_template(self) -> "SignalCrawlJobIn":
        if "{query}" not in self.search_url_template:
            raise ValueError("search_url_template must contain {query}")
        if self.max_pages > 1 and "{page}" not in self.search_url_template:
            raise ValueError("search_url_template must contain {page} when max_pages > 1")
        allowed = {"query", "page"}
        import string

        for _, field_name, _, _ in string.Formatter().parse(self.search_url_template):
            if field_name is not None and field_name not in allowed:
                raise ValueError("search_url_template only supports {query} and {page}")
        return self


class SignalCrawlJobOut(SignalCrawlJobIn):
    id: UUID
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class SignalCrawlJobListResponse(ApiModel):
    items: list[SignalCrawlJobOut]


class SignalCrawlPreviewResponse(ApiModel):
    searches: int
    urls: list[str]
    total: int
    warnings: list[str] = Field(default_factory=list)


class SignalCrawlRunOut(ApiModel):
    id: UUID
    job_id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DocumentOut(ApiModel):
    id: UUID
    source_id: UUID
    canonical_url: str | None = None
    title: str
    content_type: str
    language_code: str
    market: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    content_hash: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    claim_count: int | None = None
    source: SourceOut | None = None


class EvidenceOut(ApiModel):
    id: UUID
    claim_id: UUID
    document_id: UUID
    excerpt: str
    locator: str | None = None
    created_at: datetime


class ClaimOut(ApiModel):
    id: UUID
    document_id: UUID
    channel: str
    topic: str | None = None
    knowledge_type: str
    statement: str
    conditions: list[Any] = Field(default_factory=list)
    exceptions: list[Any] = Field(default_factory=list)
    recommended_action: str | None = None
    confidence: Decimal
    review_status: ReviewStatus
    review_note: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    quality_status: Literal["unreviewed", "keep", "reject", "uncertain", "error"] = "unreviewed"
    quality_utility_score: Decimal | None = None
    quality_reviewer_confidence: Decimal | None = None
    quality_reasons: list[str] = Field(default_factory=list)
    quality_note: str | None = None
    quality_model: str | None = None
    quality_prompt_version: str | None = None
    quality_reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ClaimListItem(ClaimOut):
    source: SourceOut
    document: DocumentOut
    evidence: list[EvidenceOut] = Field(default_factory=list)


class OverviewClaims(ApiModel):
    pending: int
    approved: int
    rejected: int


class OverviewResponse(ApiModel):
    sources: int
    documents: int
    claims: OverviewClaims
    usages: int


class SourceListResponse(ApiModel):
    items: list[SourceOut]


class DocumentListResponse(ApiModel):
    items: list[DocumentOut]


class ClaimListResponse(ApiModel):
    items: list[ClaimListItem]
    total: int


class ImportDocumentRequest(ApiModel):
    source_name: str = Field(min_length=1, max_length=300)
    canonical_url: str | None = None
    title: str = Field(min_length=1, max_length=1000)
    channel: Channel = "seo"
    content_type: str = Field(default="article", min_length=1, max_length=100)
    language_code: str = Field(default="en", min_length=1, max_length=20)
    market: str | None = Field(default=None, max_length=50)
    author: str | None = Field(default=None, max_length=300)
    published_at: datetime | None = None
    raw_content: str = Field(min_length=1)
    rights_confirmed: bool

    @field_validator("source_name", "title", "content_type", "language_code")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("market", "author")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("raw_content")
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("raw_content must not be blank")
        return value

    @field_validator("canonical_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return normalize_optional_http_url(value)


class ImportUrlRequest(ApiModel):
    url: str
    rights_confirmed: bool
    source_name: str | None = Field(default=None, max_length=300)
    channel: Channel = "seo"
    language_code: str | None = Field(default=None, min_length=1, max_length=20)
    market: str | None = Field(default=None, max_length=50)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized = normalize_optional_http_url(value)
        if normalized is None:
            raise ValueError("url must not be blank")
        return normalized

    @field_validator("source_name", "language_code", "market")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class BatchSourceSpec(ApiModel):
    seed_url: str
    source_name: str = Field(min_length=1, max_length=300)
    discovery_mode: Literal["auto", "sitemap", "feed"] = "auto"
    years: int | None = Field(default=None, ge=1, le=100)
    date_from: datetime | None = None
    include_unknown_dates: bool = False
    max_articles: int = Field(default=100, ge=1, le=5000)
    channel: Channel = "seo"
    language_code: str | None = Field(default=None, min_length=1, max_length=20)
    market: str | None = Field(default=None, max_length=50)
    rights_confirmed: bool

    @field_validator("seed_url")
    @classmethod
    def validate_seed_url(cls, value: str) -> str:
        normalized = normalize_optional_http_url(value)
        if normalized is None:
            raise ValueError("seed_url must not be blank")
        return normalized

    @field_validator("source_name")
    @classmethod
    def strip_source_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source_name must not be blank")
        return value

    @field_validator("language_code", "market")
    @classmethod
    def strip_batch_optional(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def one_cutoff(self) -> "BatchSourceSpec":
        if self.years is not None and self.date_from is not None:
            raise ValueError("years and date_from are mutually exclusive")
        return self


class BatchPreviewItem(ApiModel):
    url: str
    title: str | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None
    date_status: Literal["published", "modified", "unknown"]


class BatchPreviewResponse(ApiModel):
    discovered_total: int
    eligible_total: int
    unknown_date_total: int
    selected_total: int
    sample_items: list[BatchPreviewItem]
    warnings: list[str] = Field(default_factory=list)
    discovery_mode: str


class BatchRunCounts(ApiModel):
    discovered: int
    queued: int
    processing: int
    succeeded: int
    skipped: int
    failed: int
    cancelled: int


class BatchRunResponse(ApiModel):
    id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelling", "cancelled"]
    counts: BatchRunCounts
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BatchRunItem(ApiModel):
    id: UUID
    url: str
    published_at: datetime | None = None
    modified_at: datetime | None = None
    source_kind: str
    status: Literal["queued", "processing", "retry", "completed", "failed", "skipped", "cancelled"]
    attempts: int
    document_id: UUID | None = None
    error: str | None = None
    next_attempt_at: datetime
    updated_at: datetime


class BatchRunItemsResponse(ApiModel):
    items: list[BatchRunItem]


class QualityBackfillRequest(ApiModel):
    limit_documents: int = Field(default=100, ge=1, le=1000)
    include_reviewed: bool = False


class QualityRunCounts(ApiModel):
    queued: int
    processing: int
    completed: int
    failed: int
    cancelled: int
    auto_rejects: int


class QualityRunResponse(ApiModel):
    id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelling", "cancelled", "applied"]
    counts: QualityRunCounts
    limit_documents: int
    include_reviewed: bool
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    applied_at: datetime | None = None


class QualityItemClaimResult(ApiModel):
    claim_id: UUID
    statement: str
    machine_decision: Literal["keep", "reject", "uncertain"]
    effective_decision: Literal["keep", "reject", "uncertain"]
    utility_score: float
    reviewer_confidence: float
    reason_codes: list[str]
    rationale: str


class QualityRunItem(ApiModel):
    id: UUID
    document_id: UUID
    document_title: str
    status: Literal["queued", "processing", "retry", "completed", "failed", "cancelled", "applied"]
    attempts: int
    reviewed_claims: int
    auto_reject_count: int
    document_decision: Literal["keep", "reject", "uncertain"] | None = None
    document_reason_codes: list[str] = Field(default_factory=list)
    document_rationale: str | None = None
    error: str | None = None
    updated_at: datetime
    results: list[QualityItemClaimResult] = Field(default_factory=list)


class QualityRunItemsResponse(ApiModel):
    items: list[QualityRunItem]


class ImportDocumentResponse(ApiModel):
    created: bool
    duplicate: bool
    source: SourceOut
    document: DocumentOut
    claims_created: int
    extraction_method: ExtractionMethod | None = None
    model: str | None = None
    warning: str | None = None


class ReviewClaimRequest(ApiModel):
    decision: Literal["approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("reviewer")
    @classmethod
    def strip_reviewer(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reviewer must not be blank")
        return value

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class RetrieveRequest(ApiModel):
    query: str = Field(min_length=1, max_length=1000)
    channel: Channel | None = None
    market: str | None = Field(
        default=None,
        max_length=50,
        description="Deprecated compatibility field; retrieval does not filter by market.",
    )
    language_code: str | None = Field(default=None, max_length=20)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("market", "language_code")
    @classmethod
    def normalize_filter(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class RetrieveItem(ApiModel):
    claim: ClaimOut
    source: SourceOut
    document: DocumentOut
    evidence: list[EvidenceOut] = Field(default_factory=list)
    score: float


class KnowledgePack(ApiModel):
    query: str
    filters: dict[str, str | None]
    items: list[RetrieveItem]


class RetrieveResponse(ApiModel):
    items: list[RetrieveItem]
    knowledge_pack: KnowledgePack


class HealthResponse(ApiModel):
    status: Literal["ok"]
    database: Literal["reachable"]
    schema_state: Literal["initialized"] = Field(alias="schema", serialization_alias="schema")
