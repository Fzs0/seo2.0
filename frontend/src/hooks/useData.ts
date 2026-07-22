/** Centralized typed access to the FastAPI backend. */
import { useEffect, useState } from 'react'
import type {
  Site,
  SiteIndexScanResult,
  SiteKnowledgeProfile,
  Keyword,
  Article,
  Post,
  GscQuery,
  Ga4Channel,
  Ga4LandingPage,
  GscBreakdown,
  DashboardSummary,
  SyncLogEntry,
  StrategyEffect,
  MainSiteContentPlan,
} from '@/types/domain'

export interface SourceSummary {
  id: string
  name?: string
  domain?: string
  gscSiteUrl?: string
  ga4PropertyId?: string
}

export interface GscPage {
  page: string
  clicks: number
  impressions: number
  ctr: number
  avg_position: number
  last_seen: string | null
}

export interface AnalyticsOverviewData {
  sites: Site[]
  selectedSiteId: string | null
  dashboard: DashboardSummary | null
  opportunities: GscQuery[]
  pages: GscPage[]
  channels: Ga4Channel[]
  gscCountries: GscBreakdown[]
  gscDevices: GscBreakdown[]
  landingPages: Ga4LandingPage[]
  syncLog: SyncLogEntry[]
}

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

export interface StandardPayload {
  name?: string
  version?: string
  scoring?: {
    maxScore?: number
    components?: string[]
    thresholds?: Record<string, number>
  }
  signals?: Record<string, string[]>
  articleRules?: string[]
  articleBriefTemplate?: {
    modules?: Array<{ key?: string; label?: string; field?: string; required?: boolean }>
    qualityGate?: string[]
  }
  globalPlanning?: {
    hardGates?: Array<{ key: string; label: string; blocker?: string; priority?: string }>
  }
}

export interface StandardResponse {
  standard: StandardPayload
  version: string
  source: string
  healthy: boolean
}

export function useSites(refreshKey = 0): AsyncState<Site[]> {
  const [state, setState] = useState<AsyncState<Site[]>>({ data: null, loading: true, error: null })

  useEffect(() => {
    const controller = new AbortController()
    void getJson<{ items: Site[] }>('/api/v1/sites?limit=500', controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载站点' })
        }
      })
    return () => controller.abort()
  }, [refreshKey])

  return state
}

export function useMainSiteContent(siteId: string, refreshKey = 0): AsyncState<MainSiteContentPlan> {
  const [state, setState] = useState<AsyncState<MainSiteContentPlan>>({ data: null, loading: false, error: null })

  useEffect(() => {
    if (!siteId) {
      setState({ data: null, loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    void getJson<MainSiteContentPlan>(`/api/v1/sites/${encodeURIComponent(siteId)}/main-content`, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setState({ data, loading: false, error: null }) })
      .catch((error) => { if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载主站内容规划' }) })
    return () => controller.abort()
  }, [siteId, refreshKey])

  return state
}

export function useKeywords(refreshKey = 0, limit = 50): AsyncState<Keyword[]> {
  const [state, setState] = useState<AsyncState<Keyword[]>>({ data: null, loading: true, error: null })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    void getJson<{ items: Keyword[] }>(`/api/v1/keywords?limit=${encodeURIComponent(limit)}`, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载关键词' })
        }
      })
    return () => controller.abort()
  }, [refreshKey, limit])

  return state
}

export interface KeywordPage {
  items: Keyword[]
  total: number
  limit: number
  offset: number
}

export interface KeywordFilters {
  businessId?: string
  aiAnalyzed?: string
  intent?: string
  serpFeature?: string
}

export function useKeywordPage(refreshKey = 0, page = 1, pageSize = 100, filters: KeywordFilters = {}): AsyncState<KeywordPage> {
  const [state, setState] = useState<AsyncState<KeywordPage>>({ data: null, loading: true, error: null })
  useEffect(() => {
    const controller = new AbortController()
    const offset = Math.max(0, page - 1) * pageSize
    setState({ data: null, loading: true, error: null })
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) })
    if (filters.businessId) params.set('business_id', filters.businessId)
    if (filters.aiAnalyzed) params.set('ai_analyzed', filters.aiAnalyzed)
    if (filters.intent) params.set('intent', filters.intent)
    if (filters.serpFeature) params.set('serp_feature', filters.serpFeature)
    void getJson<KeywordPage>(`/api/v1/keywords?${params.toString()}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result, loading: false, error: null }) })
      .catch((error) => { if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载关键词' }) })
    return () => controller.abort()
  }, [refreshKey, page, pageSize, filters.businessId, filters.aiAnalyzed, filters.intent, filters.serpFeature])
  return state
}

export function useArticles(refreshKey = 0, siteId?: string): AsyncState<Article[]> {
  const [state, setState] = useState<AsyncState<Article[]>>({ data: null, loading: true, error: null })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    const query = siteId ? `&site_id=${encodeURIComponent(siteId)}` : ''
    void getJson<{ items: Article[] }>(`/api/v1/articles?limit=100${query}`, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载生成文章' })
        }
      })
    return () => controller.abort()
  }, [refreshKey, siteId])

  return state
}

export interface ArticlePage {
  items: Article[]
  total: number
  limit: number
  offset: number
}

export interface ArticleFilters {
  siteId?: string
  status?: string
}

export function useArticlePage(
  refreshKey = 0,
  page = 1,
  pageSize = 20,
  filters: ArticleFilters = {},
): AsyncState<ArticlePage> {
  const [state, setState] = useState<AsyncState<ArticlePage>>({ data: null, loading: true, error: null })
  useEffect(() => {
    const controller = new AbortController()
    const offset = Math.max(0, page - 1) * pageSize
    setState({ data: null, loading: true, error: null })
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) })
    if (filters.siteId) params.set('site_id', filters.siteId)
    if (filters.status) params.set('status', filters.status)
    void getJson<ArticlePage>(`/api/v1/articles?${params.toString()}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result, loading: false, error: null }) })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载文章' })
        }
      })
    return () => controller.abort()
  }, [refreshKey, page, pageSize, filters.siteId, filters.status])
  return state
}

export interface ArticleMonthlyBucket {
  site_id: string | null
  site_name: string
  by_month: Record<string, number>
}

export interface ArticleMonthlyStats {
  months: string[]
  totals: Record<string, number>
  by_site: ArticleMonthlyBucket[]
  total_articles: number
  generated_at: string
  months_window: number
}

export function useArticleMonthlyStats(
  refreshKey = 0,
  months = 12,
  siteId?: string,
): AsyncState<ArticleMonthlyStats> {
  const [state, setState] = useState<AsyncState<ArticleMonthlyStats>>({ data: null, loading: true, error: null })
  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    const params = new URLSearchParams({ months: String(months) })
    if (siteId) params.set('site_id', siteId)
    void getJson<ArticleMonthlyStats>(`/api/v1/articles/stats/monthly?${params.toString()}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result, loading: false, error: null }) })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载月度统计' })
        }
      })
    return () => controller.abort()
  }, [refreshKey, months, siteId])
  return state
}

export type DateField = 'created_at' | 'published_at'

export interface ArticleKpi {
  total: number
  today: number
  this_week: number
  this_month: number
  last_7_days: number
  last_30_days: number
  last_month: number
  date_field: DateField
  generated_at: string
}

export function useArticleKpi(refreshKey = 0, siteId?: string, dateField: DateField = 'created_at'): AsyncState<ArticleKpi> {
  const [state, setState] = useState<AsyncState<ArticleKpi>>({ data: null, loading: true, error: null })
  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    const params = new URLSearchParams({ date_field: dateField })
    if (siteId) params.set('site_id', siteId)
    void getJson<ArticleKpi>(`/api/v1/articles/stats/kpi?${params.toString()}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result, loading: false, error: null }) })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载 KPI' })
        }
      })
    return () => controller.abort()
  }, [refreshKey, siteId, dateField])
  return state
}

export type TimeseriesGranularity = 'day' | 'week' | 'month' | 'auto'

export interface TimeseriesBucket {
  key: string
  label: string
  count: number
}

export interface TimeseriesSite {
  site_id: string | null
  site_name: string
  series: TimeseriesBucket[]
  total: number
}

export interface ArticleTimeseries {
  granularity: TimeseriesGranularity
  range: { start: string; end: string; days: number }
  buckets: TimeseriesBucket[]
  by_site: TimeseriesSite[]
  total: number
  date_field: DateField
  generated_at: string
}

export function useArticleTimeseries(
  refreshKey: number,
  start: string,
  end: string,
  granularity: TimeseriesGranularity,
  siteId?: string,
  dateField: DateField = 'created_at',
): AsyncState<ArticleTimeseries> {
  const [state, setState] = useState<AsyncState<ArticleTimeseries>>({ data: null, loading: true, error: null })
  useEffect(() => {
    if (!start || !end) {
      setState({ data: null, loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    const params = new URLSearchParams({ start, end, granularity, date_field: dateField })
    if (siteId) params.set('site_id', siteId)
    void getJson<ArticleTimeseries>(`/api/v1/articles/stats/timeseries?${params.toString()}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result, loading: false, error: null }) })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载时间序列' })
        }
      })
    return () => controller.abort()
  }, [refreshKey, start, end, granularity, siteId, dateField])
  return state
}

export function getArticleDetail(articleId: string) {
  return getJson<{
    id: string
    site_id?: string | null
    title: string
    status: string
    serp_snapshot_id?: string | null
    brief_md?: string | null
    content_md?: string | null
    content_html?: string | null
    article_parts?: Record<string, unknown> | null
    qa_checklist?: Array<{ key: string; ok: boolean }> | null
    generation_provider?: string | null
    generation_model?: string | null
    primary_keyword?: string | null
    meta_title?: string | null
    meta_description?: string | null
  }>(`/api/v1/articles/${encodeURIComponent(articleId)}`, new AbortController().signal)
}

export function syncArticleSeoMetadata(articleId: string) {
  return postJson<{ ok: boolean; error?: string | null; url?: string | null; task_id?: string | null; action: string }>(
    `/api/v1/articles/${encodeURIComponent(articleId)}/sync-seo-metadata`,
    {},
  )
}

export function usePosts(refreshKey = 0, siteId?: string): AsyncState<Post[]> {
  const [state, setState] = useState<AsyncState<Post[]>>({ data: null, loading: true, error: null })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    const query = siteId ? `&site_id=${encodeURIComponent(siteId)}` : ''
    void getJson<{ items: Post[] }>(`/api/v1/posts?limit=200${query}`, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载站点文章' })
        }
      })
    return () => controller.abort()
  }, [refreshKey, siteId])

  return state
}

export function useDashboard(siteId?: string, refreshKey = 0): AsyncState<DashboardSummary> {
  const [state, setState] = useState<AsyncState<DashboardSummary>>({ data: null, loading: Boolean(siteId), error: null })

  useEffect(() => {
    if (!siteId) {
      setState({ data: null, loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    void getJson<DashboardSummary>(`/api/v1/analytics/dashboard/${encodeURIComponent(siteId)}`, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载同步概览' })
        }
      })
    return () => controller.abort()
  }, [siteId, refreshKey])

  return state
}

export function useAnalyticsSources(refreshKey = 0): AsyncState<SourceSummary[]> {
  const [state, setState] = useState<AsyncState<SourceSummary[]>>({ data: null, loading: true, error: null })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    void getJson<{ items: SourceSummary[] }>('/api/v1/analytics/sources', controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载数据源' })
        }
      })
    return () => controller.abort()
  }, [refreshKey])

  return state
}

export function useSyncLog(siteId?: string, refreshKey = 0): AsyncState<SyncLogEntry[]> {
  const [state, setState] = useState<AsyncState<SyncLogEntry[]>>({ data: null, loading: true, error: null })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    const query = siteId ? `&site_id=${encodeURIComponent(siteId)}` : ''
    void getJson<{ items: SyncLogEntry[] }>(`/api/v1/analytics/sync-log?limit=50${query}`, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载同步日志' })
        }
      })
    return () => controller.abort()
  }, [siteId, refreshKey])

  return state
}

export function useStandardRules(): AsyncState<StandardResponse> {
  const [state, setState] = useState<AsyncState<StandardResponse>>({ data: null, loading: true, error: null })
  useEffect(() => {
    const controller = new AbortController()
    void getJson<StandardResponse>('/api/v1/workflow/standard', controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载规则配置' })
      })
    return () => controller.abort()
  }, [])
  return state
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

async function postJson<T>(path: string, body: unknown, method: 'POST' | 'PUT' = 'POST'): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!response.ok) {
        const detail = await response.text()
        throw new Error(detail || `${response.status} ${response.statusText}`)
      }
      return response.json() as Promise<T>
    } catch (error) {
      if (attempt === 0 && error instanceof TypeError) {
        await new Promise((resolve) => window.setTimeout(resolve, 300))
        continue
      }
      if (error instanceof TypeError) {
        throw new Error(`后端连接中断：${API_BASE}${path}。请确认后端服务正在运行。`)
      }
      throw error
    }
  }
  throw new Error('请求后端失败')
}

export function triggerAnalyticsSync(siteId: string, daysBack = 7) {
  return postJson<{ ok: boolean; results?: Array<{ ok: boolean; type: string; rows_written?: number; error?: string }> }>(
    '/api/v1/analytics/sync',
    { siteId, daysBack },
  )
}

export interface StrategyTask {
  id: string
  status: 'pending' | 'approved' | 'rejected' | string
  priority: string
  score: number
  site_id?: string | null
  site_name?: string | null
  keyword_id?: string | null
  article_id?: string | null
  title: string
  strategy_type: 'update_article' | 'new_article' | string
  query: string
  confidence: number
  evidence_level: 'confirmed' | 'directional' | 'insufficient' | string
  reason: string
  recommended_action: string
  internal_link_plan?: Array<{ target_url?: string; title?: string; anchor?: string; reason?: string }>
  evidence: { gsc?: Record<string, number>; ga4?: Record<string, number> }
  execution_task_id?: string
  execution_status?: 'queued' | 'running' | 'done' | 'failed' | 'blocked' | 'canceled' | string | null
  execution_error?: string | null
  execution_logs?: Array<{ at?: string; stage?: string; message?: string }>
  execution_started_at?: string | null
  execution_finished_at?: string | null
  execution_run_after?: string | null
  auto_publish?: boolean | null
  created_at?: string
}

export interface StrategyCandidate extends StrategyTask {
  candidate_status?: 'available' | 'selected' | 'excluded' | 'hold' | 'superseded' | 'executed' | string
  selected?: boolean
}

export interface StrategyCandidatePage {
  items: StrategyCandidate[]
  total: number
  page: number
  limit: number
}

export interface StrategyPlan {
  id?: string
  business_id: string
  analysis_batch_id?: string | null
  action_budget: number
  site_quotas: Record<string, number>
  selected_candidate_ids: string[]
  status?: string
  created_at?: string
}

export interface StrategyFilters {
  search?: string
  siteId?: string
  strategyType?: string
  priority?: string
  evidenceLevel?: string
}

export function generateSeoStrategies(businessId: string, siteId?: string, actionBudget = 4, siteQuotas: Record<string, number> = {}, minImpressions = 20) {
  return postJson<{ business_id: string; site_scope: Array<{ id: string; name: string; site_type: string }>; items: StrategyTask[]; created: number; candidates: number; total_candidates?: number; planned_actions?: number; analysis_batch_id?: string; plan?: StrategyPlan; unassigned?: number; quarantined?: number; error?: string }>('/api/v1/workflow/strategies/generate', {
    businessId,
    siteId,
    limit: Math.max(1, Math.min(actionBudget || 4, 10)),
    actionBudget,
    siteQuotas,
    minImpressions,
  })
}

export function useStrategyCandidates(refreshKey = 0, businessId?: string): AsyncState<StrategyCandidatePage> {
  const [state, setState] = useState<AsyncState<StrategyCandidatePage>>({ data: null, loading: true, error: null })
  useEffect(() => {
    if (!businessId) {
      setState({ data: { items: [], total: 0, page: 1, limit: 100 }, loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState((current) => ({ data: current.data, loading: current.data == null, error: null }))
    const load = async () => {
      const items: StrategyCandidate[] = []
      let page = 1
      let total = 0
      do {
        const result = await getJson<StrategyCandidatePage>(`/api/v1/workflow/strategies/candidates?business_id=${encodeURIComponent(businessId)}&page=${page}&limit=100`, controller.signal)
        items.push(...result.items)
        total = result.total
        page += 1
      } while (!controller.signal.aborted && items.length < total)
      return { items, total, page: 1, limit: 100 }
    }
    void load()
      .then((result) => { if (!controller.signal.aborted) setState({ data: result, loading: false, error: null }) })
      .catch((error) => { if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载策略候选池' }) })
    return () => controller.abort()
  }, [refreshKey, businessId])
  return state
}

export function useStrategyPlan(refreshKey = 0, businessId?: string): AsyncState<StrategyPlan> {
  const [state, setState] = useState<AsyncState<StrategyPlan>>({ data: null, loading: true, error: null })
  useEffect(() => {
    if (!businessId) {
      setState({ data: null, loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState((current) => ({ data: current.data, loading: current.data == null, error: null }))
    void getJson<StrategyPlan>(`/api/v1/workflow/strategies/plan?business_id=${encodeURIComponent(businessId)}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result, loading: false, error: null }) })
      .catch((error) => { if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载今日计划' }) })
    return () => controller.abort()
  }, [refreshKey, businessId])
  return state
}

export function saveStrategyPlan(businessId: string, actionBudget: number, siteQuotas: Record<string, number>, selectedCandidateIds: string[]) {
  return postJson<StrategyPlan>('/api/v1/workflow/strategies/plan', { businessId, actionBudget, siteQuotas, selectedCandidateIds }, 'PUT')
}

export function useStrategies(refreshKey = 0, status = 'pending', filters: StrategyFilters = {}, businessId?: string): AsyncState<StrategyTask[]> {
  const [state, setState] = useState<AsyncState<StrategyTask[]>>({ data: null, loading: true, error: null })
  useEffect(() => {
    if (!businessId) {
      setState({ data: [], loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState((current) => ({
      data: current.data,
      loading: current.data == null,
      error: null,
    }))
    const params = new URLSearchParams({ status, limit: '200' })
    params.set('business_id', businessId)
    if (filters.search?.trim()) params.set('search', filters.search.trim())
    if (filters.siteId) params.set('site_id', filters.siteId)
    if (filters.strategyType) params.set('strategy_type', filters.strategyType)
    if (filters.priority) params.set('priority', filters.priority)
    if (filters.evidenceLevel) params.set('evidence_level', filters.evidenceLevel)
    void getJson<{ items: StrategyTask[] }>(`/api/v1/workflow/strategies?${params.toString()}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null }) })
      .catch((error) => { if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载策略审核队列' }) })
    return () => controller.abort()
  }, [refreshKey, status, filters.search, filters.siteId, filters.strategyType, filters.priority, filters.evidenceLevel, businessId])
  return state
}

export function useStrategyEffects(refreshKey = 0, businessId?: string): AsyncState<StrategyEffect[]> {
  const [state, setState] = useState<AsyncState<StrategyEffect[]>>({ data: null, loading: true, error: null })
  useEffect(() => {
    if (!businessId) {
      setState({ data: [], loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    void getJson<{ items: StrategyEffect[] }>(`/api/v1/workflow/strategies/effects?business_id=${encodeURIComponent(businessId)}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null }) })
      .catch((error) => { if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载策略效果' }) })
    return () => controller.abort()
  }, [refreshKey, businessId])
  return state
}

export function reviewSeoStrategy(taskId: string, approved: boolean, executeNow = false) {
  return postJson<{ ok: boolean; status: string; execution_task_id?: string }>(`/api/v1/workflow/strategies/${encodeURIComponent(taskId)}/review`, { approved, executeNow })
}

export function executeSeoStrategy(taskId: string) {
  return postJson<{ ok: boolean; status: string; execution_task_id?: string; error?: string; result?: unknown }>(`/api/v1/workflow/strategies/${encodeURIComponent(taskId)}/execute`, {})
}

export function cancelSeoStrategy(taskId: string) {
  return postJson<{ ok: boolean; status: string; execution_task_id?: string; error?: string }>(`/api/v1/workflow/strategies/${encodeURIComponent(taskId)}/cancel`, {})
}

export function stopSeoStrategy(taskId: string) {
  return postJson<{ ok: boolean; status: string; execution_task_id?: string; error?: string }>(`/api/v1/workflow/strategies/${encodeURIComponent(taskId)}/stop`, {})
}

export interface AutomationStatus {
  business_id?: string
  parallel_limit: number
  queued: number
  running: number
  done: number
  failed: number
  recent: Array<{ id: string; task_type: string; title: string; status: string; error_message?: string | null; finished_at?: string | null }>
}

export function useAutomationStatus(refreshKey = 0, businessId?: string): AsyncState<AutomationStatus> {
  const [state, setState] = useState<AsyncState<AutomationStatus>>({ data: null, loading: true, error: null })
  useEffect(() => {
    const controller = new AbortController()
    setState((current) => ({
      data: current.data,
      loading: current.data == null,
      error: null,
    }))
    const query = businessId ? `?business_id=${encodeURIComponent(businessId)}` : ''
    void getJson<AutomationStatus>(`/api/v1/workflow/automation/status${query}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: { ...result, business_id: businessId }, loading: false, error: null }) })
      .catch((error) => { if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载自动化状态' }) })
    return () => controller.abort()
  }, [refreshKey, businessId])
  return state
}

export function runAutomationOnce() {
  return postJson<{ status: string; processed: number; result?: unknown; generated?: unknown }>('/api/v1/workflow/automation/run-once', {})
}

export function clearStrategyQueue(businessId: string) {
  return postJson<{ executions_canceled: number; strategies_canceled: number; plans_cleared: number; candidates_cleared: number; analysis_batches_cleared: number }>(`/api/v1/workflow/automation/clear-queue?business_id=${encodeURIComponent(businessId)}`, {})
}

export function saveAutomationSettings(settings: { enabled: boolean; intervalSeconds: number; batchSize: number; minImpressions: number }) {
  return postJson<{ saved: boolean; restart_required: boolean }>('/api/v1/workflow/automation/settings', settings)
}

export interface SerpSearchResult {
  configured: boolean
  keyword: string
  status?: string
  organic_results: Array<{ position?: number; title?: string; link?: string; snippet?: string; displayed_link?: string }>
  related_questions: Array<{ question?: string; title?: string; snippet?: string; link?: string }>
  related_searches: Array<{ query?: string; title?: string }>
}

export function searchSerp(keyword: string, gl = 'us', hl = 'en') {
  return postJson<SerpSearchResult>('/api/v1/serpapi', { keyword, gl, hl })
}

export function syncAllPosts(limit = 100) {
  return postJson<{ ok: boolean; saved: number; results: Array<{ ok: boolean; site_id: string; saved: number; error?: string }> }>(
    '/api/v1/posts/sync-all',
    { limit },
  )
}

export function syncSitePosts(siteId: string, limit = 100) {
  return postJson<{ ok: boolean; site_id: string; fetched: number; saved: number; error?: string }>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/posts/sync`,
    { limit },
  )
}

export interface ContentAuditItem {
  id?: string
  action: 'update_article' | 'new_article' | 'hold' | string
  priority: string
  confidence: number
  evidence_level: string
  site_id: string
  site_name?: string | null
  post_id?: string | null
  keyword_id?: string | null
  title: string
  query: string
  url?: string | null
  reason: string
  recommended_action: string
  issues: Array<{ code: string; label: string; detail: string }>
  evidence: Array<{ source: string; fact: string }>
  confidence_factors: Array<{ name: string; value: number; detail: string }>
  data_evidence?: {
    gsc?: Record<string, unknown> | null
    ga4?: Record<string, unknown> | null
    keyword?: Record<string, unknown> | null
    serp?: Record<string, unknown> | null
  }
  ai?: {
    summary?: string
    reason?: string
    recommended_action?: string
    confidence?: number | null
    evidence_used?: string[]
    provider?: string
    model?: string
  }
  review_status?: string
  created_at?: string
  updated_at?: string
}

export interface ContentAuditReport {
  business_id: string
  site_scope: Array<{ id: string; name: string; site_type: string }>
  scanned_at: string
  sync?: { ok: boolean; saved: number; results: Array<{ ok: boolean; site_id: string; saved: number; error?: string }> } | null
  summary: { sites: number; articles: number; page_clusters?: number; coverage_review_clusters?: number; update_candidates: number; new_candidates: number; hold_candidates: number }
  data_sources?: {
    gsc?: { available: boolean; page_rows: number }
    ga4?: { available: boolean; landing_page_rows: number }
    keyword_data?: { available: boolean; rows: number }
    serp?: { configured: boolean; available?: boolean; cached: number; fetched: number; attempted?: number }
    ai?: { configured: boolean; status: string; reviewed: number; persisted?: number; model?: string }
  }
  items: ContentAuditItem[]
}

export function scanContentAudit(businessId: string, limitPerSite = 100, limit = 200) {
  return postJson<ContentAuditReport>('/api/v1/workflow/content-audit/scan', {
    businessId,
    refresh: true,
    limitPerSite,
    limit,
  })
}

export function useContentAuditReviews(refreshKey = 0, businessId?: string): AsyncState<ContentAuditItem[]> {
  const [state, setState] = useState<AsyncState<ContentAuditItem[]>>({ data: null, loading: true, error: null })

  useEffect(() => {
    if (!businessId) {
      setState({ data: [], loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    const businessQuery = `&business_id=${encodeURIComponent(businessId)}`
    void getJson<{ items: ContentAuditItem[] }>(`/api/v1/workflow/content-audit/reviews?status=pending&limit=50${businessQuery}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null }) })
      .catch((error) => { if (!controller.signal.aborted) setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载已保存的 AI 复核结果' }) })
    return () => controller.abort()
  }, [refreshKey, businessId])

  return state
}

export function testSiteConnector(siteId: string) {
  return getJson<{
    site_id: string
    site_name: string
    ok: boolean
    connector_type: string
    capabilities: string[]
    request?: { method: string; url: string; auth: string }
    config?: { base_url?: string; article_endpoint?: string; publish_endpoint?: string; articles_path?: string; publish_path?: string; configured_keys: string[]; missing_keys: string[] }
    checks?: Array<{ key: string; label: string; status: 'ok' | 'failed' | 'skipped'; detail: string }>
    sample_count?: number
    duration_ms?: number
    error?: string
  }>(`/api/v1/sites/${encodeURIComponent(siteId)}/connector`, new AbortController().signal)
}

export interface CustomConnector {
  id: string
  site_id: string
  name: string
  capability: string
  status: string
  current_version: number
  active_version?: number | null
  config?: { adapter?: string }
  configured_secret_names?: string[]
  verified_at?: string | null
  updated_at?: string
}

export function listCustomConnectors(siteId: string) {
  return getJson<{ items: CustomConnector[]; total: number }>(
    `/api/v1/connectors?site_id=${encodeURIComponent(siteId)}`,
    new AbortController().signal,
  )
}

export function configureOemAppsConnector(siteId: string, token: string) {
  return postJson<CustomConnector>('/api/v1/connectors/oemapps', { site_id: siteId, token })
}

export function testCustomConnector(connectorId: string) {
  return postJson<{ ok: boolean; total_items?: number; mapped_items?: number; errors?: string[] }>(
    `/api/v1/connectors/${encodeURIComponent(connectorId)}/test`,
    {},
  )
}

export function activateCustomConnector(connectorId: string) {
  return postJson<CustomConnector>(`/api/v1/connectors/${encodeURIComponent(connectorId)}/activate`, {})
}

export function syncCustomConnectorProducts(connectorId: string) {
  return postJson<{ ok: boolean; items_received: number; items_mapped: number; items_upserted: number; errors?: string[] }>(
    `/api/v1/connectors/${encodeURIComponent(connectorId)}/sync-products`,
    {},
  )
}

export function syncOemAppsCollections(connectorId: string) {
  return postJson<{ ok: boolean; collections_received: number; collections_upserted: number; products_scanned: number }>(
    `/api/v1/connectors/${encodeURIComponent(connectorId)}/sync-collections`,
    {},
  )
}

export function upsertSite(payload: Record<string, unknown>) {
  return postJson<Site>('/api/v1/sites', payload)
}

export function generateSiteKnowledge(siteId: string) {
  return postJson<{
    site_id: string
    knowledge_profile: SiteKnowledgeProfile
    ai: { status: string; configured: boolean; provider?: string; model?: string }
    sources: { posts: number; keywords: number }
  }>(`/api/v1/sites/${encodeURIComponent(siteId)}/knowledge/generate`, {})
}

export function discoverSiteBusiness(siteId: string) {
  return postJson<{
    site: { id: string; name: string; site_key: string; site_type: string; base_url: string }
    sitemap_url: string
    scan: { indexed_urls: number; scanned_urls: number; issues: number; product_hints: string[] }
    evidence: { existing_posts: number; product_records: number }
    candidate_targets: Array<{ url: string; type: string; title: string; facts: string[] }>
    knowledge_profile: SiteKnowledgeProfile
    recommended_next_step: string
  }>(`/api/v1/sites/${encodeURIComponent(siteId)}/business-discovery`, {})
}

export function saveSiteKnowledge(siteId: string, profile: SiteKnowledgeProfile) {
  return postJson<{ site_id: string; knowledge_profile: SiteKnowledgeProfile }>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/knowledge`,
    profile,
  )
}

export function clearSiteKnowledge(siteId: string, preserveIndexScan: boolean, profile?: SiteKnowledgeProfile | null) {
  return postJson<{ site_id: string; knowledge_profile: SiteKnowledgeProfile }>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/knowledge`,
    {
      status: 'draft',
      ...(preserveIndexScan ? { index_scan: profile?.index_scan || {} } : {}),
    },
  )
}

export function scanSiteIndex(siteId: string, file: File, replace = true) {
  const path = `/api/v1/sites/${encodeURIComponent(siteId)}/index-scan?replace=${replace ? 'true' : 'false'}`
  const lowerName = file.name.toLowerCase()
  const contentType = lowerName.endsWith('.gz') ? 'application/gzip' : lowerName.endsWith('.xml') ? 'application/xml' : 'text/plain'
  return fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': contentType, 'x-filename': file.name },
    body: file,
  }).then(async (response) => {
    if (!response.ok) {
      const detail = await response.text()
      throw new Error(detail || `${response.status} ${response.statusText}`)
    }
    return response.json() as Promise<SiteIndexScanResult>
  }).catch((error) => {
    if (error instanceof TypeError) {
      throw new Error(`后端连接中断：${API_BASE}${path}。请确认后端服务正在运行。`)
    }
    throw error
  })
}

export function deleteSite(siteId: string) {
  return fetch(`${API_BASE}/api/v1/sites/${encodeURIComponent(siteId)}`, { method: 'DELETE' }).then(async (response) => {
    if (!response.ok) throw new Error((await response.text()) || `${response.status} ${response.statusText}`)
    return response.json() as Promise<{ deleted: boolean }>
  })
}

export function publishArticle(articleId: string, siteId: string, dryRun: boolean) {
  return postJson<{
    ok: boolean
    dry_run: boolean
    post_id?: string | null
    url?: string | null
    error?: string | null
    task_id?: string
    article_id: string
    site_id: string
  }>('/api/v1/publish', {
    article_id: articleId,
    site_id: siteId,
    dry_run: dryRun,
  })
}

export interface SemrushStrategyPreview {
  filename: string
  previewOnly: boolean
  sheet: string
  rowCount: number
  databases: string[]
  topicCount: number
  pageClusterCount: number
  pageTypeCounts: Record<string, number>
  intentCounts: Record<string, number>
  metrics: {
    volumeSum: number
    kdAverage: number
    kdMin: number
    kdMax: number
    top10Coverage: number
  }
  clusterValidation: {
    ruleVersion: string
    counts: Record<string, number>
    aiReadyCount: number
    reviewCount: number
  }
  anomalies: Array<{ code: string; label: string; count: number; clusterIds: string[] }>
  clusters: Array<{
    id: string
    page: string
    topics: string[]
    pageTypes: string[]
    keywordCount: number
    intentCounts: Record<string, number>
    volumeSum: number
    kdAverage: number
    top10UrlCount: number
    contentReferenceCount: number
    validationStatus: string
    validationCenterKeyword: string
    validationReason: string
  }>
}

async function fileToBase64(file: File): Promise<string> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error || new Error('无法读取文件'))
    reader.readAsDataURL(file)
  })
  return dataUrl.split(',', 2)[1] || ''
}

export async function previewSemrushStrategyFile(file: File) {
  return postJson<SemrushStrategyPreview>('/api/v1/workflow/semrush-strategy/preview', {
    filename: file.name,
    contentBase64: await fileToBase64(file),
  })
}

export interface KeywordFileImportPreview {
  filename: string
  keywords: Array<{ topicClusterId?: string | null }>
  saved: number
  sourceBatchId: string
  preflightSummary: {
    total: number
    accepted: number
    rejected: number
    rejectionReasons: Record<string, number>
  }
  message: string
}

async function submitKeywordFile(file: File, businessId: string, market: string, save: boolean) {
  return postJson<KeywordFileImportPreview>('/api/v1/workflow/import-file', {
    filename: file.name,
    contentBase64: await fileToBase64(file),
    project: { businessId, market },
    save,
  })
}

export function previewKeywordFile(file: File, businessId: string, market: string) {
  return submitKeywordFile(file, businessId, market, false)
}

export function importKeywordFile(file: File, businessId: string, market: string) {
  return submitKeywordFile(file, businessId, market, true)
}

export async function importSemrushStrategyFile(file: File, businessId: string, market: string) {
  return postJson<{
    saved: number
    poolCount: number
    sourceBatchId: string
    businessId: string
    market: string
    preview: SemrushStrategyPreview
    message: string
  }>('/api/v1/workflow/semrush-strategy/import', {
    filename: file.name,
    contentBase64: await fileToBase64(file),
    businessId,
    market,
    confirmed: true,
  })
}

export function validateImportedSemrushStrategy(businessId: string, sourceBatchId?: string) {
  return postJson<{
    saved: number
    sourceBatchId: string
    businessId: string
    preview: SemrushStrategyPreview
    message: string
  }>('/api/v1/workflow/semrush-strategy/validate-imported', {
    businessId,
    ...(sourceBatchId ? { sourceBatchId } : {}),
  })
}

export interface SemrushStrategyAiAnalysisRun {
  id: string
  status: string
  decision?: { total?: number; updated?: number; remaining?: number; message?: string }
  error_message?: string
}

export function startSemrushStrategyAiAnalysis(businessId: string, keywordIds: string[] = [], limit = 10) {
  return postJson<{ run_id?: string | null; status: string; decision?: { total?: number; updated?: number; remaining?: number; message?: string } }>('/api/v1/workflow/semrush-strategy/ai-analyze', { businessId, keywordIds, limit })
}

export function getSemrushStrategyAiAnalysis(runId: string, signal = new AbortController().signal) {
  return getJson<SemrushStrategyAiAnalysisRun>(`/api/v1/workflow/semrush-strategy/ai-analyze/${encodeURIComponent(runId)}`, signal)
}

export function getLatestSemrushStrategyAiAnalysis(businessId: string, signal = new AbortController().signal) {
  return getJson<SemrushStrategyAiAnalysisRun | null>(`/api/v1/workflow/semrush-strategy/ai-analyze/latest?business_id=${encodeURIComponent(businessId)}`, signal)
}

export function cancelSemrushStrategyAiAnalysis(runId: string) {
  return postJson<{ run_id: string; status: string }>(`/api/v1/workflow/semrush-strategy/ai-analyze/${encodeURIComponent(runId)}/cancel`, {})
}

export async function generateArticleDraft(keyword: Keyword) {
  return postJson<{
    status?: string
    steps?: PipelineStep[]
    article: { id: string; title: string; status: string }
    serp: { id?: string | null; source?: string; status?: string }
    qa: Array<{ key: string; ok: boolean }>
    model?: string
    contentLength: number
  }>('/api/v1/workflow/article-generate', { keywordId: keyword.id })
}

export interface PipelineStep {
  key: string
  label: string
  status: 'pending' | 'running' | 'done' | 'failed'
  message?: string
  data?: Record<string, unknown>
}

export async function runArticlePipeline(keyword: Keyword) {
  return postJson<{
    status: 'done' | 'failed'
    steps: PipelineStep[]
    article?: {
      id: string
      site_id?: string | null
      title: string
      status: string
      slug?: string | null
      language_code?: string | null
      market?: string | null
      primary_keyword?: string | null
      meta_title?: string | null
      meta_description?: string | null
    }
    brief?: { source?: string; aiEnhanced?: boolean; aiMeta?: Record<string, unknown>; text?: string }
    outline?: string
    content?: string
    contentPreview?: string
    savedTo?: { table?: string; articleId?: string }
    serp?: { id?: string | null; source?: string; status?: string }
    qa?: Array<{ key: string; ok: boolean }>
    model?: string
    contentLength?: number
  }>('/api/v1/workflow/article-pipeline', { keywordId: keyword.id })
}

export function useAnalyticsOverview(siteId?: string, refreshKey = 0): AsyncState<AnalyticsOverviewData> {
  const [state, setState] = useState<AsyncState<AnalyticsOverviewData>>({
    data: null,
    loading: true,
    error: null,
  })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })

    void (async () => {
      try {
        const sitesResult = await getJson<{ items: Site[] }>('/api/v1/sites?limit=500', controller.signal)
        const selectedSiteId = siteId || sitesResult.items[0]?.id || null

        if (!selectedSiteId) {
          setState({
            data: {
              sites: sitesResult.items,
              selectedSiteId,
              dashboard: null,
              opportunities: [],
              pages: [],
              channels: [],
              gscCountries: [],
              gscDevices: [],
              landingPages: [],
              syncLog: [],
            },
            loading: false,
            error: null,
          })
          return
        }

        const query = `site_id=${encodeURIComponent(selectedSiteId)}`
        const [dashboard, opportunities, pages, channels, gscCountries, gscDevices, landingPages, syncLog] = await Promise.all([
          getJson<DashboardSummary>(`/api/v1/analytics/dashboard/${encodeURIComponent(selectedSiteId)}`, controller.signal),
          getJson<{ items: GscQuery[] }>(`/api/v1/analytics/gsc/opportunities?${query}&limit=20`, controller.signal),
          getJson<{ items: GscPage[] }>(`/api/v1/analytics/gsc/pages?${query}&limit=20`, controller.signal),
          getJson<{ items: Ga4Channel[] }>(`/api/v1/analytics/ga4/channels?${query}&days=28`, controller.signal),
          getJson<{ items: GscBreakdown[] }>(`/api/v1/analytics/gsc/breakdown?${query}&dimension=country&days=28&limit=10`, controller.signal),
          getJson<{ items: GscBreakdown[] }>(`/api/v1/analytics/gsc/breakdown?${query}&dimension=device&days=28&limit=10`, controller.signal),
          getJson<{ items: Ga4LandingPage[] }>(`/api/v1/analytics/ga4/landing-pages?${query}&days=28&limit=20`, controller.signal),
          getJson<{ items: SyncLogEntry[] }>(`/api/v1/analytics/sync-log?${query}&limit=8`, controller.signal),
        ])

        if (!controller.signal.aborted) {
          setState({
            data: {
              sites: sitesResult.items,
              selectedSiteId,
              dashboard,
              opportunities: opportunities.items,
              pages: pages.items,
              channels: channels.items,
              gscCountries: gscCountries.items,
              gscDevices: gscDevices.items,
              landingPages: landingPages.items,
              syncLog: syncLog.items,
            },
            loading: false,
            error: null,
          })
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setState({
            data: null,
            loading: false,
            error: error instanceof Error ? error.message : '无法加载数据分析数据',
          })
        }
      }
    })()

    return () => controller.abort()
  }, [siteId, refreshKey])

  return state
}
