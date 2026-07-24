import type { Site, SiteKnowledgeProfile } from '@/types/domain'
import type { testSiteConnector } from '@/data/connectors'
import type { discoverSiteBusiness } from '@/data/sites'

export const TYPE_LABEL: Record<string, string> = {
  main: '主站',
  blog: '博客站',
  wp: 'WordPress',
}

export const TYPE_TONE: Record<string, 'gold' | 'blue' | 'violet'> = {
  main: 'gold',
  blog: 'blue',
  wp: 'violet',
}

export const SITE_TYPE_OPTIONS = [
  { value: 'main', label: '商业主站（OEMApps / 自建站）' },
  { value: 'shopify', label: 'Shopify 商业主站' },
  { value: 'wp', label: 'WordPress 内容站' },
  { value: 'blog', label: '博客站' },
  { value: 'other', label: '其他' },
] as const

export const PRIORITY_META: Record<string, { label: string; tone: 'gold' | 'pink' | 'blue' | 'green'; width: number; fill: string }> = {
  commercial: { label: '高', tone: 'gold', width: 85, fill: '#c9a03c' },
  educational: { label: '中高', tone: 'pink', width: 70, fill: '#d77e6c' },
  scenario: { label: '中', tone: 'pink', width: 45, fill: '#d77e6c' },
  comparison: { label: '中高', tone: 'green', width: 68, fill: '#93a96c' },
}

export function roleKey(role: Site['content_role']) {
  if (role?.includes('主站') || role?.includes('commercial')) return 'commercial'
  if (role?.includes('对比') || role?.includes('评测') || role?.includes('comparison')) return 'comparison'
  if (role?.includes('场景') || role?.includes('人群') || role?.includes('scenario')) return 'scenario'
  return 'educational'
}

export function iconFor(role: string) {
  switch (role) {
    case 'commercial':
      return 'workspace_premium'
    case 'educational':
      return 'menu_book'
    case 'comparison':
      return 'balance'
    case 'scenario':
      return 'folder'
    default:
      return 'apartment'
  }
}

export function iconBg(role: string) {
  switch (role) {
    case 'commercial':
      return '#f7edd3'
    case 'educational':
      return '#e4ebf3'
    case 'scenario':
      return '#f9e4de'
    case 'comparison':
      return '#eaefdc'
    default:
      return '#f4eee3'
  }
}

export function iconColor(role: string) {
  switch (role) {
    case 'commercial':
      return '#b98f2e'
    case 'educational':
      return '#5f84a8'
    case 'scenario':
      return '#c4685a'
    case 'comparison':
      return '#7e9150'
    default:
      return '#17161a'
  }
}

export type SiteForm = {
  site_key: string
  name: string
  site_type: string
  connector_type: string
  domain: string
  base_url: string
  api_base_url: string
  articlesPath: string
  publishPath: string
  market: string
  language_code: string
  google_gl: string
  google_hl: string
  semrush_database: string
  content_role: string
  content_scope: string
  business_id: string
  is_main: boolean
  allow_external_links: boolean
  strategy_enabled: boolean
  notes: string
  openApiKey: string
  tokenA: string
  tokenB: string
  username: string
  applicationPassword: string
}

export type ConnectorResult = Awaited<ReturnType<typeof testSiteConnector>>
export type BusinessDiscoveryResult = Awaited<ReturnType<typeof discoverSiteBusiness>>

export type BusinessConfirmation = {
  business_id: string
  market: string
  language_code: string
  target_url: string
  strategy_enabled: boolean
}

export type BusinessOnboardingForm = {
  business_name: string
  site_type: string
  base_url: string
}

export function blankBusinessOnboarding(): BusinessOnboardingForm {
  return {
    business_name: '', site_type: 'main', base_url: '',
  }
}

export function splitLines(value: string) {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)
}

export function siteKeyFromBusinessId(value: string) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

export function businessIdFromSite(name: string, baseUrl: string) {
  try {
    const url = new URL(baseUrl.includes('://') ? baseUrl : `https://${baseUrl}`)
    const hostname = url.hostname.replace(/^www\./i, '')
    const fromHost = siteKeyFromBusinessId(hostname.split('.')[0] || hostname)
    if (fromHost.length >= 2) return fromHost.slice(0, 64)
  } catch {
    // The caller validates the URL and shows the actionable error.
  }
  return siteKeyFromBusinessId(name).slice(0, 64)
}

export function isOemAppsUrl(value: string) {
  try {
    const url = new URL(value.includes('://') ? value : `https://${value}`)
    return url.hostname.toLowerCase() === 'openapi.oemapps.com'
  } catch {
    return false
  }
}

export function articlePathsForForm(form: Pick<SiteForm, 'connector_type' | 'api_base_url' | 'articlesPath' | 'publishPath'>) {
  if (form.connector_type === 'custom_openapi' && isOemAppsUrl(form.api_base_url)) {
    return { articlesPath: '/posts', publishPath: '/posts', isPreset: true }
  }
  return { articlesPath: form.articlesPath, publishPath: form.publishPath, isPreset: false }
}

export function isBusinessMain(site: Pick<Site, 'is_main' | 'site_type'>) {
  return site.is_main || ['main', 'shopify'].includes(site.site_type)
}

export function isBusinessMainForm(form: Pick<SiteForm, 'is_main' | 'site_type'>) {
  return form.is_main || ['main', 'shopify'].includes(form.site_type)
}

export function formFromSite(site: Site): SiteForm {
  return {
    site_key: site.site_key,
    name: site.name,
    site_type: site.site_type,
    connector_type: site.connector_type || (site.site_type === 'wp' ? 'wordpress' : 'custom_openapi'),
    domain: site.domain || '',
    base_url: site.base_url || '',
    api_base_url: site.api_base_url || '',
    articlesPath: site.api_config_summary?.articles_path || (site.connector_type === 'wordpress' ? '/wp-json/wp/v2/posts' : '/posts'),
    publishPath: site.api_config_summary?.publish_path || (site.connector_type === 'wordpress' ? '/wp-json/wp/v2/posts' : '/posts/batch'),
    market: site.market || '',
    language_code: site.language_code || '',
    google_gl: site.google_gl || '',
    google_hl: site.google_hl || '',
    semrush_database: site.semrush_database || '',
    content_role: site.content_role || '',
    content_scope: site.content_scope || '',
    business_id: site.business_id || '',
    is_main: site.is_main,
    allow_external_links: site.allow_external_links,
    strategy_enabled: site.strategy_enabled,
    notes: site.notes || '',
    openApiKey: '',
    tokenA: '',
    tokenB: '',
    username: '',
    applicationPassword: '',
  }
}

export function blankSite(): Site {
  return {
    id: '', site_key: '', name: '', site_type: 'blog', domain: '', base_url: '', api_base_url: '',
    market: '', language_code: '', google_gl: '', google_hl: '', semrush_database: '', content_role: '',
    content_scope: '', business_id: null, strategy_enabled: false, is_main: false, allow_external_links: false, publish_config: {}, api_config: {},
    status: 'active', notes: '',
  }
}

export function emptyKnowledgeProfile(): SiteKnowledgeProfile {
  return {
    status: 'draft', positioning: '', audience: '', products: [], in_scope_topics: [],
    out_of_scope_topics: [], content_types: [], tone: '', conversion_goals: [],
    editorial_rules: [], evidence: [],
  }
}

export function hasKnowledgeData(profile?: SiteKnowledgeProfile | null) {
  return Boolean(profile && (
    profile.positioning || profile.audience || profile.tone || profile.products?.length ||
    profile.in_scope_topics?.length || profile.content_types?.length || profile.evidence?.length ||
    profile.core_pages?.length || profile.index_scan?.indexed_urls
  ))
}
