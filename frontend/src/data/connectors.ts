import { getJson, postJson } from '@/data/internal/http'

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
