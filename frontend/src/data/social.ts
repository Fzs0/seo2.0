import { getJson, postJson } from '@/data/internal/http'

export interface SocialPublishJob {
  id: string
  status: string
  platform: string
  content_type: string
  display_name: string
  container_code: string
  content_title?: string
  created_at?: string
  error_message?: string | null
}

export interface SocialBatchResultItem {
  ok: boolean
  job_id: string
  status: string
  post_url?: string
  error?: string
}

export interface SocialBatchResult {
  ok: boolean
  status: string
  total: number
  succeeded: number
  failed: number
  items: SocialBatchResultItem[]
}

export interface SocialExtensionPairing {
  pairing_id: string
  pairing_code: string
  expires_at: string
}

export interface SocialExtensionDevice {
  id: string
  business_id: string
  container_code: string
  display_name: string
  environment_name?: string
  paired_instances?: number
  last_seen_at?: string | null
}

export function getSocialPublishJobs(businessId: string, signal: AbortSignal) {
  const query = new URLSearchParams({
    business_id: businessId,
    limit: '100',
  })
  return getJson<{ items: SocialPublishJob[]; total: number }>(
    `/api/v1/social/publish-jobs?${query.toString()}`,
    signal,
  )
}

export function confirmPreparedSocialJobs(businessId: string, jobIds: string[]) {
  return postJson<SocialBatchResult>('/api/v1/social/publish-jobs/confirm-batch', {
    business_id: businessId,
    job_ids: jobIds,
  })
}

export function createSocialExtensionPairing(
  businessId: string,
  containerCode: string,
) {
  return postJson<SocialExtensionPairing>('/api/v1/social/extension/pairing-codes', {
    business_id: businessId,
    container_code: containerCode,
    display_name: `Hubstudio ${containerCode}`,
  })
}

export function getSocialExtensionDevices(businessId: string, signal: AbortSignal) {
  const query = new URLSearchParams({ business_id: businessId })
  return getJson<{ items: SocialExtensionDevice[]; total: number }>(
    `/api/v1/social/extension/devices?${query.toString()}`,
    signal,
  )
}
