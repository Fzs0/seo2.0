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

export interface ShopifyConnection {
  site_id: string
  shop_domain?: string
  blog_handle?: string
  api_version?: string
  status: 'not_configured' | 'draft' | 'verified' | 'active' | 'failed' | 'disabled'
  scopes?: string[]
  configured_secret_names: string[]
  last_tested_at?: string | null
  last_error?: string | null
}

export function getShopifyConnection(siteId: string, signal: AbortSignal) {
  return getJson<ShopifyConnection>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/shopify/connection`,
    signal,
  )
}

export function configureShopifyConnection(siteId: string, payload: {
  shop_domain: string
  blog_handle: string
  api_version: string
  client_id?: string
  client_secret?: string
}) {
  return postJson<ShopifyConnection>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/shopify/connection`,
    payload,
  )
}

export function testShopifyConnection(siteId: string) {
  return postJson<{ ok: boolean; status: string; scopes?: string[]; error?: string }>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/shopify/connection/test`,
    {},
  )
}

export function activateShopifyConnection(siteId: string) {
  return postJson<ShopifyConnection>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/shopify/connection/activate`,
    {},
  )
}

export interface ShopifyProductSeoItem {
  id: number
  external_id: string
  title: string
  handle: string
  url?: string
  status: string
  meta_title?: string
  meta_description?: string
  source_updated_at: string
  seo_audit?: {
    missing_meta_title?: boolean
    missing_meta_description?: boolean
    images_missing_alt?: number
  }
}

export function syncShopifyProducts(siteId: string, limit = 250) {
  return postJson<{ ok: boolean; received: number; upserted: number; missing_seo: number }>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/shopify/products/sync`,
    { limit },
  )
}

export function getShopifyProductSeo(siteId: string, missingOnly: boolean, signal: AbortSignal) {
  return getJson<{ items: ShopifyProductSeoItem[]; total: number }>(
    `/api/v1/sites/${encodeURIComponent(siteId)}/shopify/products/seo?missing_only=${missingOnly ? 'true' : 'false'}&limit=100`,
    signal,
  )
}

export function previewShopifyProductSeo(siteId: string, payload: {
  product_id: number
  expected_updated_at: string
  meta_title: string
  meta_description: string
}) {
  return postJson<{
    ok: boolean
    dry_run: true
    preview_token: string
    before: { meta_title: string; meta_description: string }
    after: { meta_title: string; meta_description: string }
  }>(`/api/v1/sites/${encodeURIComponent(siteId)}/shopify/products/seo`, {
    ...payload,
    dry_run: true,
    confirm: false,
    actor: 'local_ui',
  })
}

export async function executeShopifyProductSeo(siteId: string, payload: {
  product_id: number
  expected_updated_at: string
  meta_title: string
  meta_description: string
  preview_token: string
  request_id: string
}) {
  const response = await fetch(`${API_BASE}/api/v1/sites/${encodeURIComponent(siteId)}/shopify/products/seo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, dry_run: false, confirm: true, actor: 'local_ui' }),
  })
  if (!response.ok) throw new Error((await response.text()) || `${response.status} ${response.statusText}`)
  return response.json() as Promise<{ ok: boolean; dry_run: false; after: { meta_title: string; meta_description: string; source_updated_at: string } }>
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
