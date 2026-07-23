import { useEffect, useState } from 'react'
import type { MainSiteContentPlan, Site, SiteIndexScanResult, SiteKnowledgeProfile } from '@/types/domain'
import type { AsyncState } from '@/data/internal/asyncState'
import { API_BASE, getJson, postJson } from '@/data/internal/http'

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
