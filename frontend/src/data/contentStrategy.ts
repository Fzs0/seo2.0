import { useEffect, useState } from 'react'
import type { StrategyEffect } from '@/types/domain'
import type { AsyncState } from '@/data/internal/asyncState'
import { getJson, postJson } from '@/data/internal/http'

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
