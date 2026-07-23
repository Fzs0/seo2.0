import { useEffect, useState } from 'react'
import type { AsyncState } from '@/data/internal/asyncState'
import { getJson, postJson } from '@/data/internal/http'

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

export function clearStrategyQueue(businessId: string) {
  return postJson<{ executions_canceled: number; strategies_canceled: number; plans_cleared: number; candidates_cleared: number; analysis_batches_cleared: number }>(`/api/v1/workflow/automation/clear-queue?business_id=${encodeURIComponent(businessId)}`, {})
}
