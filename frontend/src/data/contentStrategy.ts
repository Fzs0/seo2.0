import { useEffect, useState } from 'react'
import type { StrategyEffect } from '@/types/domain'
import type { AsyncState } from '@/data/internal/asyncState'
import { getJson, postJson } from '@/data/internal/http'

interface ApiEnvelope<T> {
  ok: boolean
  request_id: string
  data: T
  error: { code: string; message: string } | null
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

export function useStandardRules(): AsyncState<StandardResponse> {
  const [state, setState] = useState<AsyncState<StandardResponse>>({
    data: null,
    loading: true,
    error: null,
  })
  useEffect(() => {
    const controller = new AbortController()
    void getJson<StandardResponse>('/api/v1/workflow/standard', controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setState({ data: result, loading: false, error: null })
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({
            data: null,
            loading: false,
            error: error instanceof Error ? error.message : '无法加载规则配置',
          })
        }
      })
    return () => controller.abort()
  }, [])
  return state
}

export interface StrategyRun {
  run_id: string
  business_id: string
  scope: string
  mode: 'dry_run' | 'approval_execution'
  status: string
  plan_id?: string | null
  created_at?: string | null
  updated_at?: string | null
  counts?: {
    execute_now?: number
    deferred?: number
    hold?: number
    configuration_repair?: number
    failed?: number
  }
}

export function useStrategyRuns(
  refreshKey = 0,
  businessId?: string,
): AsyncState<StrategyRun[]> {
  const [state, setState] = useState<AsyncState<StrategyRun[]>>({
    data: null,
    loading: true,
    error: null,
  })
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
    void getJson<ApiEnvelope<{ items: StrategyRun[] }>>(
      `/api/v1/strategy-runs?business_id=${encodeURIComponent(businessId)}&limit=50`,
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) {
          setState({ data: result.data.items, loading: false, error: null })
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({
            data: null,
            loading: false,
            error: error instanceof Error ? error.message : '无法加载 Strategy Runs',
          })
        }
      })
    return () => controller.abort()
  }, [refreshKey, businessId])
  return state
}

export async function createAndStartStrategyRun(
  businessId: string,
): Promise<StrategyRun> {
  const token = `${Date.now()}-${crypto.randomUUID()}`
  const created = await postJson<ApiEnvelope<StrategyRun>>('/api/v1/strategy-runs', {
    business_id: businessId,
    scope: 'all_sites',
    mode: 'approval_execution',
    requested_by: 'operator_ui',
    idempotency_key: `ui-create-${token}`,
    action_budget: 200,
    site_quotas: {},
    approval_policy: 'use_site_capabilities',
  })
  return (
    await postJson<ApiEnvelope<StrategyRun>>(
      `/api/v1/strategy-runs/${encodeURIComponent(created.data.run_id)}/start`,
      {},
      'POST',
      { 'Idempotency-Key': `ui-start-${token}` },
    )
  ).data
}

export function useStrategyEffects(
  refreshKey = 0,
  businessId?: string,
): AsyncState<StrategyEffect[]> {
  const [state, setState] = useState<AsyncState<StrategyEffect[]>>({
    data: null,
    loading: true,
    error: null,
  })
  useEffect(() => {
    if (!businessId) {
      setState({ data: [], loading: false, error: null })
      return
    }
    const controller = new AbortController()
    setState({ data: null, loading: true, error: null })
    void getJson<{ items: StrategyEffect[] }>(
      `/api/v1/workflow/strategies/effects?business_id=${encodeURIComponent(businessId)}`,
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) {
          setState({ data: result.items, loading: false, error: null })
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({
            data: null,
            loading: false,
            error: error instanceof Error ? error.message : '无法加载策略效果',
          })
        }
      })
    return () => controller.abort()
  }, [refreshKey, businessId])
  return state
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
  sync?: {
    ok: boolean
    saved: number
    results: Array<{ ok: boolean; site_id: string; saved: number; error?: string }>
  } | null
  summary: {
    sites: number
    articles: number
    page_clusters?: number
    coverage_review_clusters?: number
    update_candidates: number
    new_candidates: number
    hold_candidates: number
  }
  data_sources?: {
    gsc?: { available: boolean; page_rows: number }
    ga4?: { available: boolean; landing_page_rows: number }
    keyword_data?: { available: boolean; rows: number }
    serp?: {
      configured: boolean
      available?: boolean
      cached: number
      fetched: number
      attempted?: number
    }
    ai?: {
      configured: boolean
      status: string
      reviewed: number
      persisted?: number
      model?: string
    }
  }
  items: ContentAuditItem[]
}

export function scanContentAudit(
  businessId: string,
  limitPerSite = 100,
  limit = 200,
) {
  return postJson<ContentAuditReport>('/api/v1/workflow/content-audit/scan', {
    businessId,
    refresh: true,
    limitPerSite,
    limit,
  })
}
