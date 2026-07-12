/**
 * Data hooks — pages should NEVER call fetch / axios directly.
 * They import the typed `useXxx` hooks here and the swap from
 * mock data to a real backend is a single-file change.
 *
 * To wire the real API:
 *   1. Add a fetch wrapper under /src/api/http.ts that points at the
 *      FastAPI backend (see app/api/v1/... endpoint paths).
 *   2. Replace the body of each hook with a call to that wrapper.
 *   3. Keep the same shape, return type, and field names so page
 *      components don't need to change.
 */
import { useEffect, useState } from 'react'
import {
  mockSites,
  mockKeywords,
  mockBrief,
  mockGscOpportunities,
  mockGa4Channels,
  mockStandard,
  mockPipelineStages,
} from '@/mock/data'
import type {
  Site,
  Keyword,
  Article,
  Post,
  Brief,
  GscQuery,
  Ga4Channel,
  Ga4LandingPage,
  GscBreakdown,
  DashboardSummary,
  SyncLogEntry,
} from '@/types/domain'

export interface SourceSummary {
  id: string
  name?: string
  domain?: string
  gscSiteUrl?: string
  ga4PropertyId?: string
}

export interface DashboardOverviewData {
  sites: Site[]
  keywords: Keyword[]
  sources: SourceSummary[]
  syncLog: SyncLogEntry[]
  dashboard: DashboardSummary | null
  selectedSiteId: string | null
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

/** Wrap a synchronous mock value in a single-tick async shape so
 *  pages render an explicit loading / error / empty state. */
function mockAsync<T>(value: T): AsyncState<T> {
  return { data: value, loading: false, error: null }
}

function emptyAsync<T>(errorMsg = 'No data available'): AsyncState<T> {
  return { data: null, loading: false, error: errorMsg }
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

export function useArticles(refreshKey = 0): AsyncState<Article[]> {
  const [state, setState] = useState<AsyncState<Article[]>>({ data: null, loading: true, error: null })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    void getJson<{ items: Article[] }>('/api/v1/articles?limit=100', controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setState({ data: result.items, loading: false, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : '无法加载生成文章' })
        }
      })
    return () => controller.abort()
  }, [refreshKey])

  return state
}

export function getArticleDetail(articleId: string) {
  return getJson<{
    id: string
    title: string
    status: string
    serp_snapshot_id?: string | null
    brief_md?: string | null
    content_md?: string | null
    content_html?: string | null
    article_parts?: Record<string, unknown> | null
    qa_checklist?: Array<{ key: string; ok: boolean }> | null
    generation_model?: string | null
    primary_keyword?: string | null
  }>(`/api/v1/articles/${encodeURIComponent(articleId)}`, new AbortController().signal)
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

export function useBrief(keywordId?: string): AsyncState<Brief> {
  if (!keywordId) return emptyAsync<Brief>('No keyword selected')
  return mockAsync(mockBrief)
}

export function useGscOpportunities(): AsyncState<GscQuery[]> {
  return mockAsync(mockGscOpportunities)
}

export function useGa4Channels(): AsyncState<Ga4Channel[]> {
  return mockAsync(mockGa4Channels)
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

export function useStandardRules() {
  return mockAsync(mockStandard)
}

export function usePipelineStages() {
  return mockAsync(mockPipelineStages)
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

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

export function triggerAnalyticsSync(siteId: string, daysBack = 7) {
  return postJson<{ ok: boolean; results?: Array<{ ok: boolean; type: string; rows_written?: number; error?: string }> }>(
    '/api/v1/analytics/sync',
    { siteId, daysBack },
  )
}

export function analyzeKeywordStrategy(keywordIds: string[], limit?: number, opportunityType?: string) {
  return postJson<{ updated: number; error?: string }>('/api/v1/workflow/keywords/ai-analyze', {
    keywordIds,
    limit: limit || keywordIds.length || 10,
    opportunityType,
  })
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

export function testSiteConnector(siteId: string) {
  return getJson<{
    site_id: string
    site_name: string
    ok: boolean
    connector_type: string
    capabilities: string[]
    sample_count?: number
    error?: string
  }>(`/api/v1/sites/${encodeURIComponent(siteId)}/connector`, new AbortController().signal)
}

export function upsertSite(payload: Record<string, unknown>) {
  return postJson<Site>('/api/v1/sites', payload)
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

export async function importSemrushFile(file: File) {
  return postJson<{ keywords: Keyword[]; saved: number; filename: string }>('/api/v1/workflow/import-file', {
    filename: file.name,
    contentText: await file.text(),
    save: true,
  })
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
    article?: { id: string; title: string; status: string }
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

export function useDashboardOverview(siteId?: string): AsyncState<DashboardOverviewData> {
  const [state, setState] = useState<AsyncState<DashboardOverviewData>>({
    data: null,
    loading: true,
    error: null,
  })

  useEffect(() => {
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })

    void (async () => {
      try {
        const [sitesResult, keywordsResult, sourcesResult, syncLogResult] = await Promise.all([
          getJson<{ items: Site[] }>('/api/v1/sites?limit=500', controller.signal),
          getJson<{ items: Keyword[] }>('/api/v1/keywords?limit=500', controller.signal),
          getJson<{ items: SourceSummary[] }>('/api/v1/analytics/sources', controller.signal),
          getJson<{ items: SyncLogEntry[] }>('/api/v1/analytics/sync-log?limit=10', controller.signal),
        ])
        const selectedSiteId = siteId || sitesResult.items[0]?.id || null
        const dashboard = selectedSiteId
          ? await getJson<DashboardSummary>(
              `/api/v1/analytics/dashboard/${encodeURIComponent(selectedSiteId)}`,
              controller.signal,
            )
          : null

        if (!controller.signal.aborted) {
          setState({
            data: {
              sites: sitesResult.items,
              keywords: keywordsResult.items,
              sources: sourcesResult.items,
              syncLog: syncLogResult.items,
              dashboard,
              selectedSiteId,
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
            error: error instanceof Error ? error.message : '无法加载决策总览数据',
          })
        }
      }
    })()

    return () => controller.abort()
  }, [siteId])

  return state
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
