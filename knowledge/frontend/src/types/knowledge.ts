export type Channel = "seo" | "social" | "forum" | "video" | "visual";
export type ReviewDecision = "approved" | "rejected";
export type ReviewStatus = "pending" | ReviewDecision;
export type SignalContentKind = "post" | "comment" | "reply" | "review" | "video_comment" | "forum_thread" | "forum_reply" | "message" | "other";

export interface Overview {
  sources: number;
  documents: number;
  pending_claims: number;
  approved_claims: number;
  rejected_claims: number;
}

export interface Source {
  id: string;
  name: string;
  channel: Channel;
  source_type: string;
  base_url: string | null;
  domain: string | null;
  rights_confirmed: boolean;
  status: string;
  document_count?: number;
  claim_count?: number;
  created_at: string;
  updated_at: string;
}

export interface Document {
  id: string;
  source_id: string;
  canonical_url: string | null;
  title: string;
  content_type: string;
  language_code: string;
  market: string | null;
  author: string | null;
  published_at: string | null;
  status: string;
  claim_count?: number;
  pending_claim_count?: number;
  created_at: string;
}

export interface Evidence {
  id: string;
  excerpt: string;
  locator: string | null;
}

export interface KnowledgeClaim {
  id: string;
  document_id: string;
  channel: Channel;
  topic: string | null;
  knowledge_type: string;
  statement: string;
  conditions: unknown[];
  exceptions: unknown[];
  recommended_action: string | null;
  confidence: number;
  review_status: ReviewStatus;
  review_note: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  quality_status?: QualityDecision;
  quality_utility_score?: number | null;
  quality_reviewer_confidence?: number | null;
  quality_reasons?: string[];
  quality_note?: string | null;
  quality_model?: string | null;
  quality_prompt_version?: string | null;
  quality_reviewed_at?: string | null;
  created_at: string;
}

export interface Claim extends KnowledgeClaim {
  source: Source;
  document: Document;
  evidence: Evidence[];
}

export interface MarketSignal {
  id: string;
  source_id: string;
  source_name: string;
  platform: string;
  channel: Channel;
  external_id: string | null;
  canonical_url: string | null;
  content_kind: SignalContentKind;
  title: string | null;
  content: string;
  author_handle: string | null;
  thread_key: string | null;
  parent_external_id: string | null;
  language_code: string;
  market: string | null;
  published_at: string | null;
  engagement: Record<string, unknown>;
  metadata: Record<string, unknown>;
  status: "new" | "analyzed" | "ignored";
  created_at: string;
  updated_at: string;
}

export interface SignalOverview {
  total: number;
  last_7_days: number;
  last_30_days: number;
  by_kind: Record<string, number>;
  by_platform: Record<string, number>;
}

export interface SignalCrawlJob {
  id: string;
  name: string;
  source_name: string;
  platform: string;
  channel: Channel;
  rights_confirmed: boolean;
  search_url_template: string;
  keywords: string[];
  detail_url_contains: string[];
  max_pages: number;
  max_signals: number;
  delay_ms: number;
  interval_hours: number;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface SignalCrawlPreview {
  searches: number;
  urls: string[];
  total: number;
  warnings: string[];
}

export interface SignalCrawlRun {
  id: string;
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  counts: Record<string, number>;
  warnings: string[];
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface SignalCrawlJobRequest {
  name: string;
  source_name: string;
  platform: string;
  channel: Channel;
  rights_confirmed: boolean;
  search_url_template: string;
  keywords: string[];
  detail_url_contains: string[];
  max_pages: number;
  max_signals: number;
  delay_ms: number;
  interval_hours: number;
  enabled: boolean;
}

export interface SignalImportRequest {
  source_name: string;
  platform: string;
  channel: Channel;
  rights_confirmed: boolean;
  signals: Array<{
    external_id?: string;
    canonical_url?: string;
    content_kind: SignalContentKind;
    title?: string;
    content: string;
    author_handle?: string;
    thread_key?: string;
    parent_external_id?: string;
    language_code: string;
    market?: string;
    published_at?: string;
    engagement: Record<string, unknown>;
    metadata: Record<string, unknown>;
  }>;
}

export interface SignalImportResponse {
  received: number;
  created: number;
  duplicates: number;
}

export interface ImportDocumentRequest {
  source_name: string;
  canonical_url: string;
  title: string;
  channel: Channel;
  content_type: string;
  language_code: string;
  market: string;
  author: string | null;
  published_at: string | null;
  raw_content: string;
  rights_confirmed: boolean;
}

export interface ImportUrlRequest {
  url: string;
  rights_confirmed: boolean;
  source_name?: string;
  channel?: Channel;
  language_code?: string;
  market?: string;
}

export interface ImportDocumentResponse {
  created: boolean;
  duplicate: boolean;
  source: Source;
  document: Document;
  claims_created: number;
  extraction_method?: "ai" | "deterministic_fallback" | null;
  model?: string | null;
  warning?: string | null;
}

export type BatchDiscoveryMode = "auto" | "sitemap" | "feed";
export type BatchRunStatus = "queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled";

export interface BatchDiscoveryRequest {
  seed_url: string;
  source_name: string;
  discovery_mode: BatchDiscoveryMode;
  years?: 1 | 2 | 3 | 5;
  date_from?: string;
  include_unknown_dates: boolean;
  max_articles: number;
  channel?: Channel;
  language_code?: string;
  market?: string;
  rights_confirmed: boolean;
}

export interface BatchPreviewItem {
  url: string;
  title?: string | null;
  published_at?: string | null;
  modified_at?: string | null;
  date_status?: string | null;
}

export interface BatchPreviewResponse {
  discovered_total: number;
  eligible_total: number;
  unknown_date_total: number;
  selected_total: number;
  discovery_mode: BatchDiscoveryMode;
  sample_items: BatchPreviewItem[];
  warnings: string[];
}

export interface BatchRunCounts {
  discovered: number;
  queued: number;
  processing: number;
  succeeded: number;
  skipped: number;
  failed: number;
  cancelled: number;
}

export interface BatchRun {
  id: string;
  status: BatchRunStatus;
  counts: BatchRunCounts;
  warnings: string[];
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type BatchRunItemStatus = "queued" | "processing" | "retry" | "completed" | "failed" | "skipped" | "cancelled";

export interface BatchRunItem {
  id: string;
  url: string;
  published_at: string | null;
  modified_at: string | null;
  source_kind: string;
  status: BatchRunItemStatus;
  attempts: number;
  document_id: string | null;
  error: string | null;
  next_attempt_at: string | null;
  updated_at: string;
}

export type QualityDecision = "unreviewed" | "keep" | "reject" | "uncertain" | "error";
export type QualityMachineDecision = "keep" | "reject" | "uncertain";
export type QualityRunStatus = "queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled" | "applied";
export type QualityItemStatus = "queued" | "processing" | "retry" | "completed" | "failed" | "cancelled" | "applied";

export interface StartQualityRunRequest {
  limit_documents: number;
  include_reviewed: boolean;
}

export interface QualityRunCounts {
  queued: number;
  processing: number;
  completed: number;
  failed: number;
  cancelled: number;
  auto_rejects: number;
}

export interface QualityRun {
  id: string;
  status: QualityRunStatus;
  counts: QualityRunCounts;
  limit_documents: number;
  include_reviewed: boolean;
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  applied_at: string | null;
}

export interface QualityResult {
  claim_id: string;
  statement: string;
  machine_decision: QualityMachineDecision;
  effective_decision: QualityMachineDecision;
  utility_score: number;
  reviewer_confidence: number;
  reason_codes: string[];
  rationale: string;
}

export interface QualityRunItem {
  id: string;
  document_id: string;
  document_title: string;
  status: QualityItemStatus;
  attempts: number;
  reviewed_claims: number;
  auto_reject_count: number;
  document_decision: QualityMachineDecision | null;
  document_reason_codes: string[];
  document_rationale: string | null;
  error: string | null;
  updated_at: string;
  results: QualityResult[];
}

export interface ReviewClaimRequest {
  decision: ReviewDecision;
  reviewer: string;
  note: string | null;
}

export interface RetrieveRequest {
  query: string;
  channel: Channel;
  market?: string;
  language_code: string;
  limit: number;
}

export interface RetrieveItem {
  claim: KnowledgeClaim;
  source: Source;
  document: Document;
  evidence: Evidence[];
  score: number;
  match_reason?: string;
}

export interface KnowledgePack {
  query: string;
  channel: Channel;
  market: string | null;
  language_code: string;
  items: RetrieveItem[];
}
