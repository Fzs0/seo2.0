import { useEffect, useState } from 'react'
import type {
  DashboardSummary,
  Ga4Channel,
  Ga4LandingPage,
  GscBreakdown,
  GscQuery,
  Site,
  SyncLogEntry,
} from '@/types/domain'
import type { AsyncState } from '@/data/internal/asyncState'
import { getJson, postJson } from '@/data/internal/http'

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

export function triggerAnalyticsSync(siteId: string, daysBack = 7) {
  return postJson<{ ok: boolean; results?: Array<{ ok: boolean; type: string; rows_written?: number; error?: string }> }>(
    '/api/v1/analytics/sync',
    { siteId, daysBack },
  )
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
