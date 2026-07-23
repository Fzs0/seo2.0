import { useEffect, useState } from 'react'
import type { Article, Post } from '@/types/domain'
import type { AsyncState } from '@/data/internal/asyncState'
import { getJson, postJson } from '@/data/internal/http'

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

export function syncSitePosts(siteId: string, limit = 100) {
  return postJson<{ ok: boolean; site_id: string; fetched: number; saved: number; error?: string }>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/posts/sync`,
    { limit },
  )
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

export interface PipelineStep {
  key: string
  label: string
  status: 'pending' | 'running' | 'done' | 'failed'
  message?: string
  data?: Record<string, unknown>
}
