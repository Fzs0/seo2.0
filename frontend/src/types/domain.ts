/**
 * Domain types aligned with the backend field names.
 * Mirrors models from:
 *   - app/api/v1/business.py           (sites, keywords, articles, products)
 *   - app/api/v1/analytics.py          (gsc/ga4 source payloads)
 *   - app/api/v1/endpoints.py          (brief, prompt, mock-article, standard)
 *   - app/services/keyword_query_service.py
 *   - app/services/brief_service.py
 *
 * NOTE: This is a **mock** layer — real values are loaded by the API client
 * once endpoints are wired. Pages should never call fetch directly; instead
 * they consume the typed values from the data hooks below so the swap is
 * a one-file change.
 */

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

export interface Site {
  id: string
  site_key: string
  name: string
  site_type: 'main' | 'blog' | 'wp' | string
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
  is_main: boolean
  allow_external_links: boolean
  publish_config: Record<string, unknown>
  api_config: Record<string, unknown>
  status: 'active' | 'paused' | 'review'
  notes: string | null
  publish_ready?: boolean
  publish_adapter?: string
  publish_hint?: string
}

export interface Keyword {
  id: string
  keyword: string
  normalized_keyword: string
  source: 'semrush' | 'manual' | 'gsc' | 'import'
  semrush_database: string
  market: string
  language_code: string
  google_gl: string
  google_hl: string
  volume: number | string | null
  kd: number | string | null
  cpc: number | string | null
  intent: 'informational' | 'commercial' | 'transactional' | 'navigational' | 'unknown' | string
  topic_cluster: string
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

export interface Brief {
  brief: string
  locale: { googleGl: string; googleHl: string }
  articleBriefTemplate: Array<{ name: string; required: boolean }>
  reference: { triggered: boolean; sources: Array<{ name: string; label: string; url: string }> }
  parentPage: { url: string; title: string } | null
  targetAsset: { url: string | null; status: string; contentAction: string }
  imagePlan: Array<{ name: string; position: string }>
  recommendedUrl: string
  briefSource: 'local' | 'ai-enhanced'
  aiEnhanced: boolean
  aiMeta: {
    stage: string
    provider: string
    model: string
    status?: string
  }
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
  conversions: number
  revenue: number
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
