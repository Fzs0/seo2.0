/** Domain types aligned with the backend field names. */

export type Priority = 'high' | 'medium-high' | 'medium' | 'medium-low' | 'low' | 'P0' | 'P1' | 'P2' | 'P3' | 'Hold'
export type Status =
  | 'planned'
  | 'analyzing'
  | 'ready'
  | 'brief_ready'
  | 'article_drafted'
  | 'published'
  | 'blocked'
  | 'imported'
  | 'analyzed'
  | 'queued'
  | 'written'
  | 'reviewed'
  | 'hold'
  | 'dropped'

export interface SiteKnowledgeProfile {
  status: 'draft' | 'confirmed' | string
  positioning: string
  audience: string
  products: string[]
  in_scope_topics: string[]
  out_of_scope_topics: string[]
  content_types: string[]
  tone: string
  conversion_goals: string[]
  conversion_targets?: string[]
  restricted_topics?: string[]
  internal_link_rules?: string[]
  editorial_rules: string[]
  evidence: Array<{ source?: string; fact?: string }>
  generated_at?: string
  updated_at?: string
  site_mode?: string
  core_pages?: Array<{ url: string; page_type?: string; title?: string; h1?: string }>
  services?: string[]
  verified_assets?: Array<{ url: string; title?: string; type?: string; facts?: string[] }>
  generation_policy?: {
    business_type?: string
    site_role?: 'main' | 'commercial' | 'local_service' | 'service' | 'content' | 'editorial' | 'blog' | string
    risk_level?: 'low' | 'standard' | 'regulated' | 'ymyl' | string
    keyword_triggers?: Record<string, string[]>
    claim_terms?: string[]
    allowed_sources?: Array<{ url: string; label?: string; source_type?: string }>
    forbidden_claims?: string[]
    required_modules?: string[]
  }
  index_scan?: {
    filename: string
    files?: Array<{ filename: string; source?: string; compressed?: boolean; indexed_urls?: number; scanned_urls?: number }>
    source: 'sitemap' | 'url_list' | string
    indexed_urls: number
    scanned_urls: number
    nested_sitemaps: number
    summary?: SiteIndexScanSummary
    issues?: SiteIndexScanIssue[]
    product_hints?: string[]
    urls?: string[]
    pages?: Array<{ url: string; page_type?: string; title?: string; h1?: string[]; status?: string }>
  }
}

export interface SiteIndexScanSummary {
  pages: number
  ok: number
  issues: number
  missing_title: number
  missing_description: number
  missing_h1: number
  duplicate_title: number
}

export interface SiteIndexScanIssue {
  url: string
  page_type?: string
  issues: string[]
}

export interface SiteIndexScanResult {
  site_id: string
  site_name?: string
  index: {
    filename: string
    files?: Array<{ filename: string; source?: string; compressed?: boolean; indexed_urls?: number; scanned_urls?: number }>
    indexed_urls: number
    scanned_urls: number
    nested_sitemaps: number
  }
  seo_audit: {
    summary: SiteIndexScanSummary
    issues: SiteIndexScanIssue[]
    product_hints: string[]
  }
  knowledge_profile?: SiteKnowledgeProfile
}

export interface MainSiteContentItem {
  url: string
  page_type: string
  role: string
  recommendation: string
  title: string
  h1: string[]
  status: string
}

export interface MainSiteContentPlan {
  site_id: string
  site_name: string
  indexed_urls: number
  scanned_pages: number
  unscanned_urls: number
  counts: Record<string, number>
  commercial_targets: MainSiteContentItem[]
  supporting_articles: MainSiteContentItem[]
  inventory: MainSiteContentItem[]
  rules: string[]
  references: string[]
}

export interface Site {
  id: string
  site_key: string
  name: string
  site_type: 'main' | 'blog' | 'wp' | string
  connector_type?: string
  domain: string
  api_base_url?: string | null
  base_url: string
  market: string | null
  language_code: string | null
  google_gl: string | null
  google_hl: string | null
  semrush_database: string | null
  content_role: string | null
  content_scope: string | null
  business_id: string | null
  strategy_enabled: boolean
  is_main: boolean
  allow_external_links: boolean
  publish_config: Record<string, unknown>
  api_config: Record<string, unknown>
  api_config_summary?: { configured_keys: string[]; articles_path?: string; publish_path?: string }
  status: 'active' | 'paused' | 'review'
  notes: string | null
  knowledge_profile?: SiteKnowledgeProfile | null
  publish_ready?: boolean
  publish_adapter?: string
  publish_hint?: string
}

export interface Keyword {
  id: string
  keyword: string
  normalized_keyword: string
  source: 'semrush' | 'semrush_strategy_builder' | 'manual' | 'gsc' | 'import'
  semrush_database: string
  market: string
  language_code: string
  google_gl: string
  google_hl: string
  volume: number | string | null
  kd: number | string | null
  cpc: number | string | null
  intent: 'informational' | 'commercial' | 'transactional' | 'navigational' | 'unknown' | string
  serp_features?: string[] | Record<string, unknown> | null
  trend?: number | string | null
  trend_data?: number[] | null
  pkd?: number | string | null
  potential_traffic?: number | string | null
  competitive_density?: number | string | null
  serp_results?: number | null
  keyword_type?: string | null
  preflight_status?: 'ready' | 'needs_review' | 'invalid' | string
  preflight_reason?: string | null
  cluster_validation_status?: string | null
  business_id?: string | null
  source_batch_id?: string | null
  topic_cluster: string
  topic_cluster_id?: string | null
  cluster_role?: 'pillar' | 'supporting' | 'standalone' | string | null
  cluster_size?: number | null
  pillar_keyword?: string | null
  seed_keyword: string
  page_group: string
  assigned_site_id: string
  assigned_site_label: string
  page_type: string
  page_role: string
  target_asset_url: string | null
  asset_status: string
  content_action: string
  priority: Priority
  score: number | string | null
  status: Status
  reason: string
  ai_review?: {
    strategy?: {
      strategyReason?: string | null
      briefDirection?: string | null
      confidence?: number | string | null
      pageRole?: string | null
      pageType?: string | null
      contentAction?: string | null
      assignedSiteLabel?: string | null
      targetAssetUrl?: string | null
    }
    provider?: string
    model?: string
  }
  imported_at: string
  created_at: string
  updated_at: string
}

export interface Article {
  id: string
  task_id: string | null
  site_id: string
  site_label: string
  keyword_id: string
  keyword: string
  serp_snapshot_id: string | null
  title: string
  slug: string
  target_url: string | null
  status: 'draft' | 'generated' | 'approved' | 'review' | 'ready' | 'published' | 'failed' | 'archived'
  language_code: string
  market: string
  primary_keyword: string
  secondary_keywords: string[]
  meta_title: string
  meta_description: string
  generation_provider: string
  generation_model: string
  created_at: string
  updated_at: string
}

export type StrategyEffectStatus = 'observing' | 'winner' | 'neutral' | 'loser' | 'inconclusive' | 'contaminated'

export interface StrategyEffect {
  id: string
  status: StrategyEffectStatus
  site_id: string | null
  site_name: string | null
  query: string
  action_type: string
  target_url: string | null
  strategy_fingerprint: string
  baseline: Record<string, unknown> | null
  baseline_note?: string | null
  checkpoints: Array<Record<string, unknown>>
  outcome: Record<string, unknown> | StrategyEffectStatus | null
  cooldown_until: string | null
  published_at?: string | null
  next_check_at: string | null
  created_at: string
  updated_at: string
}

export interface Post {
  id: string
  site_id: string
  external_id: string | null
  title: string
  slug: string | null
  url: string | null
  status: string | null
  author: string | null
  category_id: string | null
  language_code: string | null
  market: string | null
  primary_keyword: string | null
  topic_cluster: string | null
  page_type: string | null
  excerpt: string | null
  meta_title: string | null
  meta_description: string | null
  cover_url: string | null
  published_at: string | null
  modified_at: string | null
  fetched_at: string
  source: string
  created_at: string
  updated_at: string
}

export interface GscQuery {
  query: string
  clicks: number
  impressions: number
  ctr: number
  avgPosition: number
  lastSeen: string
}

export interface Ga4Channel {
  channel: string
  sessions: number
  users: number
  pageviews: number
  engagement_rate: number
  avg_session_duration?: number
  bounce_rate?: number
  conversions: number
  revenue: number
}

export interface GscBreakdown {
  dimension: string
  clicks: number
  impressions: number
  ctr: number
  avg_position: number
  last_seen: string | null
}

export interface Ga4LandingPage {
  landing_page: string
  sessions: number
  users: number
  pageviews: number
  engagement_rate: number
  bounce_rate: number
  avg_session_duration: number
  conversions: number
  revenue: number
  last_seen: string | null
}

export interface DashboardSummary {
  site: Site
  configured: boolean
  gsc28d: {
    clicks: number
    impressions: number
    ctr: number
    avg_position: number
  } | null
  ga428d: {
    sessions: number
    users: number
    new_users: number
    pageviews: number
    engagement_rate: number
    bounce_rate: number
    avg_session_duration: number
    conversions: number
    revenue: number
  } | null
  gsc7dTrend: Array<{ date: string; clicks: number; impressions: number }>
  ga47dTrend: Array<{
    date: string
    sessions: number
    users: number
    pageviews: number
  }>
  lastSync: Array<{
    source_type: string
    status: string
    started_at: string
    finished_at: string
    rows_written: number
  }>
}

export interface SyncLogEntry {
  id: string
  source_id?: string | null
  site_id?: string | null
  source_type: 'gsc' | 'ga4'
  status: 'success' | 'failed' | 'running'
  trigger: 'manual' | 'schedule' | 'scheduled' | string
  range_start?: string | null
  range_end?: string | null
  rows_fetched: number
  rows_written: number
  duration_ms: number
  error_message: string | null
  started_at: string
  finished_at: string | null
}

export interface StandardDoc {
  version: string
  positioning: {
    defaultProject: {
      domain: string
      market: string
      coreProducts: string
      mainPages: string
    }
  }
  sites: string[]
  rules: Array<{ group: string; title: string; body: string }>
}
