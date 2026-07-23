import { useEffect, useState } from 'react'
import type { Keyword } from '@/types/domain'
import type { AsyncState } from '@/data/internal/asyncState'
import { getJson, postJson } from '@/data/internal/http'

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
