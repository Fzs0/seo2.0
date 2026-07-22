import { useEffect, useState } from 'react'
import { DataGuard } from '@/components/StateBlock'
import { useBusinessScope } from '@/businessScope'
import { activateCustomConnector, clearSiteKnowledge, configureOemAppsConnector, deleteSite, discoverSiteBusiness, generateSiteKnowledge, listCustomConnectors, saveSiteKnowledge, scanSiteIndex, syncCustomConnectorProducts, syncOemAppsCollections, syncSitePosts, testCustomConnector, testSiteConnector, upsertSite, useSites } from '@/hooks/useData'
import type { CustomConnector } from '@/hooks/useData'
import type { Site, SiteIndexScanResult, SiteKnowledgeProfile } from '@/types/domain'

const TYPE_LABEL: Record<string, string> = {
  main: '主站',
  blog: '博客站',
  wp: 'WordPress',
}

const TYPE_TONE: Record<string, 'gold' | 'blue' | 'violet'> = {
  main: 'gold',
  blog: 'blue',
  wp: 'violet',
}

const PRIORITY_META: Record<string, { label: string; tone: 'gold' | 'pink' | 'blue' | 'green'; width: number; fill: string }> = {
  commercial: { label: '高', tone: 'gold', width: 85, fill: '#c9a03c' },
  educational: { label: '中高', tone: 'pink', width: 70, fill: '#d77e6c' },
  scenario: { label: '中', tone: 'pink', width: 45, fill: '#d77e6c' },
  comparison: { label: '中高', tone: 'green', width: 68, fill: '#93a96c' },
}

function roleKey(role: Site['content_role']) {
  if (role?.includes('主站') || role?.includes('commercial')) return 'commercial'
  if (role?.includes('对比') || role?.includes('评测') || role?.includes('comparison')) return 'comparison'
  if (role?.includes('场景') || role?.includes('人群') || role?.includes('scenario')) return 'scenario'
  return 'educational'
}

function iconFor(role: string) {
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

function iconBg(role: string) {
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

function iconColor(role: string) {
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

type SiteForm = {
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

type ConnectorResult = Awaited<ReturnType<typeof testSiteConnector>>
type BusinessDiscoveryResult = Awaited<ReturnType<typeof discoverSiteBusiness>>

type BusinessConfirmation = {
  business_id: string
  market: string
  language_code: string
  target_url: string
  strategy_enabled: boolean
}

type BusinessOnboardingForm = {
  business_name: string
  site_type: string
  base_url: string
}

function blankBusinessOnboarding(): BusinessOnboardingForm {
  return {
    business_name: '', site_type: 'shopify', base_url: '',
  }
}

function splitLines(value: string) {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)
}

function siteKeyFromBusinessId(value: string) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

function businessIdFromSite(name: string, baseUrl: string) {
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

function isOemAppsUrl(value: string) {
  try {
    const url = new URL(value.includes('://') ? value : `https://${value}`)
    return url.hostname.toLowerCase() === 'openapi.oemapps.com'
  } catch {
    return false
  }
}

function articlePathsForForm(form: Pick<SiteForm, 'connector_type' | 'api_base_url' | 'articlesPath' | 'publishPath'>) {
  if (form.connector_type === 'custom_openapi' && isOemAppsUrl(form.api_base_url)) {
    return { articlesPath: '/posts', publishPath: '/posts', isPreset: true }
  }
  return { articlesPath: form.articlesPath, publishPath: form.publishPath, isPreset: false }
}

function isBusinessMain(site: Pick<Site, 'is_main' | 'site_type'>) {
  return site.is_main || ['main', 'shopify'].includes(site.site_type)
}

function isBusinessMainForm(form: Pick<SiteForm, 'is_main' | 'site_type'>) {
  return form.is_main || ['main', 'shopify'].includes(form.site_type)
}

function formFromSite(site: Site): SiteForm {
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

function blankSite(): Site {
  return {
    id: '', site_key: '', name: '', site_type: 'blog', domain: '', base_url: '', api_base_url: '',
    market: '', language_code: '', google_gl: '', google_hl: '', semrush_database: '', content_role: '',
    content_scope: '', business_id: null, strategy_enabled: false, is_main: false, allow_external_links: false, publish_config: {}, api_config: {},
    status: 'active', notes: '',
  }
}

function emptyKnowledgeProfile(): SiteKnowledgeProfile {
  return {
    status: 'draft', positioning: '', audience: '', products: [], in_scope_topics: [],
    out_of_scope_topics: [], content_types: [], tone: '', conversion_goals: [],
    editorial_rules: [], evidence: [],
  }
}

function hasKnowledgeData(profile?: SiteKnowledgeProfile | null) {
  return Boolean(profile && (
    profile.positioning || profile.audience || profile.tone || profile.products?.length ||
    profile.in_scope_topics?.length || profile.content_types?.length || profile.evidence?.length ||
    profile.core_pages?.length || profile.index_scan?.indexed_urls
  ))
}

export function SitesPage() {
  const { businessId, refresh: refreshBusinessScope } = useBusinessScope()
  const [refreshKey, setRefreshKey] = useState(0)
  const [editing, setEditing] = useState<Site | null>(null)
  const [form, setForm] = useState<SiteForm | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [connectorTesting, setConnectorTesting] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; fetched: number; error?: string }>>({})
  const [connectorResults, setConnectorResults] = useState<Record<string, ConnectorResult>>({})
  const [knowledgeGenerating, setKnowledgeGenerating] = useState<string | null>(null)
  const [knowledgeSaving, setKnowledgeSaving] = useState<string | null>(null)
  const [knowledgeClearing, setKnowledgeClearing] = useState<string | null>(null)
  const [knowledgeEditing, setKnowledgeEditing] = useState<{ site: Site; profile: SiteKnowledgeProfile } | null>(null)
  const [businessOnboarding, setBusinessOnboarding] = useState<BusinessOnboardingForm | null>(null)
  const [businessOnboardingSaving, setBusinessOnboardingSaving] = useState(false)
  const [businessDiscoveryOpen, setBusinessDiscoveryOpen] = useState(false)
  const [businessDiscoverySiteId, setBusinessDiscoverySiteId] = useState('')
  const [businessDiscoveryResult, setBusinessDiscoveryResult] = useState<BusinessDiscoveryResult | null>(null)
  const [businessDiscoveryRunning, setBusinessDiscoveryRunning] = useState(false)
  const [businessConfirmation, setBusinessConfirmation] = useState<BusinessConfirmation>({ business_id: '', market: '', language_code: 'en', target_url: '', strategy_enabled: false })
  const [businessConfirming, setBusinessConfirming] = useState(false)
  const [indexScanning, setIndexScanning] = useState<string | null>(null)
  const [indexScanResults, setIndexScanResults] = useState<Record<string, SiteIndexScanResult>>({})
  const sites = useSites(refreshKey)
  const tested = Object.values(testResults)
  const healthy = tested.filter((item) => item.ok).length
  const displaySites = [...(sites.data ?? [])]
    .filter((site) => Boolean(businessId) && site.business_id === businessId)
    .sort((left, right) => Number(isBusinessMain(right)) - Number(isBusinessMain(left)) || left.name.localeCompare(right.name))
  const currentMainSite = displaySites.find(isBusinessMain)

  function openEditor(site: Site) {
    setEditing(site)
    setForm(formFromSite(site))
  }

  function openNewSite() {
    if (!businessId || !currentMainSite) {
      window.alert('请先在顶部选择一个已建主站的业务，再新增该业务的附属站点。')
      return
    }
    const site = {
      ...blankSite(),
      business_id: businessId,
      market: currentMainSite.market,
      language_code: currentMainSite.language_code,
      google_gl: currentMainSite.google_gl,
      google_hl: currentMainSite.google_hl,
      semrush_database: currentMainSite.semrush_database,
      is_main: false,
    }
    setEditing(site)
    setForm(formFromSite(site))
  }

  function openManualBusinessOnboarding() {
    setBusinessOnboarding(blankBusinessOnboarding())
  }

  function openBusinessDiscovery() {
    setBusinessDiscoveryOpen(true)
    setBusinessDiscoverySiteId('')
    setBusinessDiscoveryResult(null)
    setBusinessConfirmation({ business_id: '', market: '', language_code: 'en', target_url: '', strategy_enabled: false })
  }

  async function runBusinessDiscovery(siteId: string) {
    if (!siteId || businessDiscoveryRunning) return
    setBusinessDiscoverySiteId(siteId)
    setBusinessDiscoveryResult(null)
    setBusinessDiscoveryRunning(true)
    try {
      const result = await discoverSiteBusiness(siteId)
      setBusinessDiscoveryResult(result)
      const currentSite = (sites.data || []).find((site) => site.id === siteId)
      const suggestedId = siteKeyFromBusinessId(String(currentSite?.site_key || result.site.site_key).replace(/-shopify$/i, ''))
      setBusinessConfirmation({
        business_id: suggestedId,
        market: currentSite?.market || '',
        language_code: currentSite?.language_code || 'en',
        target_url: result.candidate_targets[0]?.url || '',
        strategy_enabled: false,
      })
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '站点扫描或 AI 分析失败')
    } finally {
      setBusinessDiscoveryRunning(false)
    }
  }

  async function confirmDiscoveredBusiness() {
    if (!businessDiscoveryResult || !businessDiscoverySiteId || businessConfirming) return
    const currentSite = (sites.data || []).find((site) => site.id === businessDiscoverySiteId)
    const businessId = businessConfirmation.business_id.trim()
    if (!currentSite || !/^[a-z0-9][a-z0-9_-]{1,63}$/i.test(businessId)) {
      window.alert('请确认业务 ID（2–64 位字母、数字、- 或 _）。')
      return
    }
    if (businessConfirmation.strategy_enabled && !businessConfirmation.market.trim()) {
      window.alert('启用策略前请填写目标市场，例如 US、DE。')
      return
    }
    const selectedTarget = businessDiscoveryResult.candidate_targets.find((item) => item.url === businessConfirmation.target_url)
    const draftPolicy = businessDiscoveryResult.knowledge_profile.generation_policy || {}
    if (businessConfirmation.strategy_enabled && !selectedTarget) {
      window.alert('商业站启用策略前，请先确认一个候选承接页。')
      return
    }
    if (businessConfirmation.strategy_enabled && ['regulated', 'ymyl'].includes(String(draftPolicy.risk_level)) && !(draftPolicy.allowed_sources || []).length) {
      window.alert('高风险业务需先补充已批准的可靠来源，当前不能启用策略。')
      return
    }
    const hasApprovedSources = (draftPolicy.allowed_sources || []).length > 0
    const canConfirmProfile = Boolean(selectedTarget) && (!['regulated', 'ymyl'].includes(String(draftPolicy.risk_level)) || hasApprovedSources)
    const profile = {
      ...businessDiscoveryResult.knowledge_profile,
      status: canConfirmProfile ? 'confirmed' as const : 'draft' as const,
      conversion_targets: selectedTarget ? [selectedTarget.url] : [],
      verified_assets: selectedTarget ? [{ ...selectedTarget, facts: selectedTarget.facts }] : [],
    }
    const policy = profile.generation_policy || {}
    const siteRole = policy.site_role || (currentSite.site_type === 'shopify' ? 'commercial' : 'editorial')
    setBusinessConfirming(true)
    try {
      await upsertSite({
        site_key: currentSite.site_key,
        name: currentSite.name,
        site_type: currentSite.site_type,
        domain: currentSite.domain,
        base_url: currentSite.base_url,
        api_base_url: currentSite.api_base_url,
        market: businessConfirmation.market.trim().toUpperCase(),
        language_code: businessConfirmation.language_code.trim().toLowerCase(),
        google_gl: businessConfirmation.market.trim().toLowerCase(),
        google_hl: businessConfirmation.language_code.trim().toLowerCase(),
        semrush_database: businessConfirmation.market.trim().toLowerCase(),
        content_role: siteRole,
        content_scope: profile.in_scope_topics?.join('、') || currentSite.content_scope || '',
        business_id: businessId,
        is_main: siteRole !== 'editorial',
        strategy_enabled: businessConfirmation.strategy_enabled,
        allow_external_links: currentSite.allow_external_links,
        status: currentSite.status,
        notes: `${currentSite.notes || ''}\n由已有站点扫描创建业务：${businessId}`.trim(),
      })
      await saveSiteKnowledge(currentSite.id, profile)
      setBusinessDiscoveryOpen(false)
      setRefreshKey((key) => key + 1)
      refreshBusinessScope()
      window.alert(businessConfirmation.strategy_enabled ? '业务已确认并启用策略。请先做一篇文章干跑。' : (canConfirmProfile ? 'AI 资料卡已确认，业务保持未启用状态。请先做一篇文章干跑。' : '业务已创建为草案。高风险业务需补充经过核验的官方来源后，才可确认资料卡并生成文章。'))
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '确认业务失败')
    } finally {
      setBusinessConfirming(false)
    }
  }

  async function saveBusinessOnboarding() {
    if (!businessOnboarding || businessOnboardingSaving) return
    const form = businessOnboarding
    const businessName = form.business_name.trim()
    const rawBaseUrl = form.base_url.trim()
    let baseUrl = ''
    try {
      baseUrl = new URL(rawBaseUrl.includes('://') ? rawBaseUrl : `https://${rawBaseUrl}`).origin
    } catch {
      window.alert('请填写可访问的主站网址，例如 https://example.com。')
      return
    }
    const businessId = businessIdFromSite(businessName, baseUrl)
    if (!businessName || !/^[a-z0-9][a-z0-9_-]{1,63}$/i.test(businessId)) {
      window.alert('请填写业务名称，并使用带英文域名的主站网址。')
      return
    }
    const siteRole = ['shopify', 'main'].includes(form.site_type) ? 'commercial' : 'editorial'
    const siteKey = `${businessId}-${form.site_type}`
    if ((sites.data || []).some((site) => site.site_key === siteKey)) {
      window.alert('该主站已存在。请在站点列表中编辑已有站点，不会覆盖它的配置。')
      return
    }
    setBusinessOnboardingSaving(true)
    try {
      const saved = await upsertSite({
        site_key: siteKey,
        name: businessName,
        site_type: form.site_type,
        domain: baseUrl,
        base_url: baseUrl,
        content_role: siteRole,
        content_scope: '',
        business_id: businessId,
        is_main: siteRole === 'commercial',
        strategy_enabled: false,
        notes: `由简化新增业务入口创建；业务名称：${businessName}；等待产品/分类接口同步与 AI 资料卡生成。`,
      })
      setBusinessOnboarding(null)
      setRefreshKey((key) => key + 1)
      refreshBusinessScope()
      window.alert(`业务草案已创建（${saved.business_id || businessId}）。下一步接入产品/分类接口；同步完成后再由 AI 生成资料卡并确认策略。`)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '新增业务失败')
    } finally {
      setBusinessOnboardingSaving(false)
    }
  }

  async function saveEditor() {
    if (!form || saving) return
    const scopedBusinessId = form.business_id || businessId
    const derivedSiteKey = form.site_key.trim() || businessIdFromSite(form.name, form.base_url)
    if (!scopedBusinessId || !currentMainSite && !isBusinessMainForm(form)) {
      window.alert('请先选择一个已有主站的业务；附属站点不能脱离业务主站保存。')
      return
    }
    if (!/^[a-z0-9][a-z0-9_-]{1,63}$/i.test(derivedSiteKey)) {
      window.alert('请填写有效的公开站点网址，系统会据此生成站点标识。')
      return
    }
    setSaving(true)
    try {
      const connectorType = form.site_type === 'shopify' ? 'shopify' : form.connector_type
      const normalizedForm = { ...form, site_key: derivedSiteKey, business_id: scopedBusinessId, is_main: isBusinessMainForm(form), connector_type: connectorType }
      const credentials = connectorType === 'wordpress'
        ? { connector_type: connectorType, username: form.username, applicationPassword: form.applicationPassword }
        : { connector_type: connectorType, openApiKey: form.openApiKey, tokenA: form.tokenA, tokenB: form.tokenB }
      const articlePaths = articlePathsForForm(normalizedForm)
      const api_config = Object.fromEntries(Object.entries({ ...credentials, articlesPath: articlePaths.articlesPath, publishPath: articlePaths.publishPath }).filter(([, value]) => value))
      await upsertSite({ ...normalizedForm, api_config })
      setEditing(null)
      setForm(null)
      setRefreshKey((key) => key + 1)
      refreshBusinessScope()
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '站点配置保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function testPosts(site: Site) {
    if (testing) return
    setTesting(site.id)
    try {
      const result = await syncSitePosts(site.id, 1)
      setTestResults((items) => ({ ...items, [site.id]: { ok: result.ok, fetched: result.fetched, error: result.error } }))
    } catch (error) {
      setTestResults((items) => ({ ...items, [site.id]: { ok: false, fetched: 0, error: error instanceof Error ? error.message : '文章接口测试失败' } }))
    } finally {
      setTesting(null)
    }
  }

  async function testConnector(site: Site) {
    if (connectorTesting) return
    setConnectorTesting(site.id)
    try {
      const result = await testSiteConnector(site.id)
      setConnectorResults((items) => ({ ...items, [site.id]: result }))
    } catch (error) {
      setConnectorResults((items) => ({
        ...items,
        [site.id]: {
          site_id: site.id,
          site_name: site.name,
          ok: false,
          connector_type: site.site_type,
          capabilities: [],
          error: error instanceof Error ? error.message : '连接器检测失败',
        },
      }))
    } finally {
      setConnectorTesting(null)
    }
  }

  async function removeSite(site: Site) {
    if (deleting || !window.confirm(`确定删除站点“${site.name}”？关键词、文章和任务会保留，但会解除站点关联。`)) return
    setDeleting(site.id)
    try {
      await deleteSite(site.id)
      setTestResults((items) => { const next = { ...items }; delete next[site.id]; return next })
      setConnectorResults((items) => { const next = { ...items }; delete next[site.id]; return next })
      setRefreshKey((key) => key + 1)
      refreshBusinessScope()
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '删除站点失败')
    } finally {
      setDeleting(null)
    }
  }

  async function generateKnowledge(site: Site) {
    if (knowledgeGenerating) return
    setKnowledgeGenerating(site.id)
    try {
      await generateSiteKnowledge(site.id)
      setRefreshKey((key) => key + 1)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '站点知识生成失败')
    } finally {
      setKnowledgeGenerating(null)
    }
  }

  async function scanIndex(site: Site, files: File[]) {
    if (!files.length) return
    const invalid = files.find((file) => {
      const name = file.name.toLowerCase()
      return (!name.endsWith('.xml') && !name.endsWith('.txt') && !name.endsWith('.gz')) || file.size > 5 * 1024 * 1024
    })
    if (invalid && (!invalid.name.toLowerCase().endsWith('.xml') && !invalid.name.toLowerCase().endsWith('.txt') && !invalid.name.toLowerCase().endsWith('.gz'))) {
      window.alert('请选择 sitemap.xml、sitemap_index.xml、.txt URL 列表或 .gz 压缩索引')
      return
    }
    if (invalid) {
      window.alert('索引文件不能超过 5 MB')
      return
    }
    if (indexScanning) return
    setIndexScanning(site.id)
    try {
      for (const file of files) {
        const result = await scanSiteIndex(site.id, file, file === files[0])
        setIndexScanResults((items) => ({ ...items, [site.id]: result }))
      }
      setRefreshKey((key) => key + 1)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '主站索引扫描失败')
    } finally {
      setIndexScanning(null)
    }
  }

  function openKnowledgeEditor(site: Site) {
    setKnowledgeEditing({ site, profile: site.knowledge_profile || emptyKnowledgeProfile() })
  }

  async function saveKnowledge(confirmed: boolean) {
    if (!knowledgeEditing || knowledgeSaving) return
    const policy = knowledgeEditing.profile.generation_policy || {}
    if (confirmed && ['regulated', 'ymyl'].includes(String(policy.risk_level)) && !(policy.allowed_sources || []).length) {
      window.alert('高风险业务请先在“生文安全规则”中填写已核验的可靠来源，再确认使用。')
      return
    }
    setKnowledgeSaving(knowledgeEditing.site.id)
    try {
      await saveSiteKnowledge(knowledgeEditing.site.id, {
        ...knowledgeEditing.profile,
        status: confirmed ? 'confirmed' : knowledgeEditing.profile.status,
        updated_at: new Date().toISOString(),
      })
      setKnowledgeEditing(null)
      setRefreshKey((key) => key + 1)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '站点知识保存失败')
    } finally {
      setKnowledgeSaving(null)
    }
  }

  async function clearKnowledge(site: Site, preserveIndexScan: boolean) {
    if (knowledgeClearing || !hasKnowledgeData(site.knowledge_profile)) return
    const message = preserveIndexScan
      ? `清空“${site.name}”的 AI 知识画像？索引扫描结果会保留。`
      : `清空“${site.name}”的全部站点知识？包括索引扫描结果。`
    if (!window.confirm(message)) return
    setKnowledgeClearing(site.id)
    try {
      await clearSiteKnowledge(site.id, preserveIndexScan, site.knowledge_profile)
      setKnowledgeEditing((current) => current?.site.id === site.id ? null : current)
      setRefreshKey((key) => key + 1)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '站点知识清空失败')
    } finally {
      setKnowledgeClearing(null)
    }
  }
  return (
    <>
      <section className="page" data-screen-label="站点管理">
        <div>
          <h1>站点管理</h1>
          <p>
            先明确每个站点的内容边界，再让 AI 自动分配关键词和制定文章策略。
          </p>
        </div>

        <div className="kpi-row">
          <div className="kpi kpi--gold">
            <div className="kpi__head">
              <span className="kpi__label">当前业务站点</span>
              <span className="msr kpi__icon kpi__icon--gold">apartment</span>
            </div>
            <div className="kpi__value">{businessId ? displaySites.length : '—'}</div>
            <div className="kpi__delta">{businessId || '请先选择业务'}</div>
          </div>
          <div className="kpi kpi--blue">
            <div className="kpi__head">
              <span className="kpi__label">路由正常</span>
              <span className="msr msr-fill kpi__icon kpi__icon--blue">verified_user</span>
            </div>
            <div className="kpi__value">{tested.length ? `${Math.round((healthy / tested.length) * 100)}%` : '—'}</div>
            <div className="kpi__delta">已测试站点</div>
          </div>
          <div className="kpi kpi--pink">
            <div className="kpi__head">
              <span className="kpi__label">待确认分配</span>
              <span className="msr kpi__icon kpi__icon--pink">help</span>
            </div>
            <div className="kpi__value">{tested.filter((item) => !item.ok).length}</div>
            <div className="kpi__delta">当前需处理</div>
          </div>
          <div className="kpi kpi--green">
            <div className="kpi__head">
              <span className="kpi__label">API 健康</span>
              <span className="msr msr-fill kpi__icon kpi__icon--green">cloud_done</span>
            </div>
            <div className="kpi__value">{tested.length ? `${Math.round((healthy / tested.length) * 100)}%` : '—'}</div>
            <div className="kpi__delta">已测试站点</div>
          </div>
        </div>

        <div className="card">
          <div className="card__title">
            <span>站点列表 {businessId ? `· ${businessId}` : ''}</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button className="btn btn--primary btn--xs" type="button" onClick={openManualBusinessOnboarding}>
                <span className="msr">add_business</span>
                新增业务
              </button>
              <button className="btn btn--primary btn--xs" type="button" onClick={openNewSite} disabled={!businessId || !currentMainSite}>
                <span className="msr">add</span>
                新增当前业务站点
              </button>
              <span className="chip">
                <span className="msr">expand_more</span>
                全部状态
              </span>
              <div
                style={{
                  display: 'flex',
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  overflow: 'hidden',
                }}
              >
                <span
                  style={{
                    padding: '6px 9px',
                    display: 'flex',
                    alignItems: 'center',
                    background: 'var(--ink-900)',
                  }}
                >
                  <span className="msr" style={{ fontSize: 15, color: '#ffffff' }}>
                    grid_view
                  </span>
                </span>
                <span
                  style={{
                    padding: '6px 9px',
                    display: 'flex',
                    alignItems: 'center',
                  }}
                >
                  <span className="msr" style={{ fontSize: 15, color: 'var(--ink-400)' }}>
                    list
                  </span>
                </span>
              </div>
            </div>
          </div>
          <p style={{ color: 'var(--ink-500)', fontSize: 12, margin: '0 0 14px' }}>{businessId ? `仅展示业务“${businessId}”的主站和附属站点。新增站点会自动归入该主站所属业务。` : '请先在顶部选择业务；未选择业务时不展示或新增站点。'}</p>

          <DataGuard
            loading={sites.loading}
            error={sites.error}
            empty={!businessId || displaySites.length === 0}
            emptyTitle={businessId ? '当前业务暂无站点' : '请先选择业务'}
            emptyHint={businessId ? '请先确认该业务的主站；附属站点只能归入已有业务主站。' : '从顶部“当前业务”选择业务后查看站点。'}
          >
            <div className="site-grid">
              {displaySites.map((s) => {
                const tone = TYPE_TONE[s.site_type] ?? 'blue'
                const role = roleKey(s.content_role)
                const prio = PRIORITY_META[role]
                return (
                  <div className="site-card" key={s.id}>
                    <div className="site-card__head">
                      <div
                        className="site-card__icon"
                        style={{ background: iconBg(role) }}
                      >
                        <span
                          className="msr"
                          style={{
                            color: iconColor(role),
                            fontSize: 18,
                          }}
                        >
                          {iconFor(role)}
                        </span>
                      </div>
                      <div>
                        <span className={`tag tag--${tone}`}>{TYPE_LABEL[s.site_type] ?? s.site_type}</span>
                        <span className={`tag tag--${s.strategy_enabled ? 'green' : 'gray'}`} style={{ marginLeft: 6 }}>{s.strategy_enabled ? '参与策略' : '不参与策略'}</span>
                        <div className="site-card__name">{s.name}</div>
                      </div>
                    </div>
                    <div className="site-card__actions" style={{ display: 'flex', gap: 8, margin: '10px 0' }}>
                      <button className="btn btn--ghost btn--xs" type="button" onClick={() => void testConnector(s)} disabled={connectorTesting === s.id}>
                        <span className="msr">{connectorTesting === s.id ? 'progress_activity' : 'lan'}</span>
                        {connectorTesting === s.id ? '检测中' : '检测连接器'}
                      </button>
                      <button className="btn btn--ghost btn--xs" type="button" onClick={() => void testPosts(s)} disabled={testing === s.id}>
                        <span className="msr">{testing === s.id ? 'progress_activity' : 'sync'}</span>
                        {testing === s.id ? '测试中' : '测试文章接口'}
                      </button>
                      <button className="btn btn--ghost btn--xs" type="button" onClick={() => openEditor(s)}>编辑配置</button>
                      <button className="btn btn--ghost btn--xs" type="button" onClick={() => void removeSite(s)} disabled={deleting === s.id} style={{ color: '#a14a3c' }}>
                        {deleting === s.id ? '删除中…' : '删除站点'}
                      </button>
                    </div>
                    {testResults[s.id] && (
                      <div style={{ color: testResults[s.id].ok ? '#5c6e33' : '#a14a3c', fontSize: 12, marginBottom: 8 }}>
                        {testResults[s.id].ok ? `文章接口正常：读取 ${testResults[s.id].fetched} 篇` : `文章接口失败：${testResults[s.id].error || '未知错误'}`}
                      </div>
                    )}
                    {connectorResults[s.id] && (
                      <div className={`site-diagnostics ${connectorResults[s.id].ok ? 'site-diagnostics--ok' : 'site-diagnostics--failed'}`}>
                        <div className="site-diagnostics__head">
                          <strong>连接器诊断</strong>
                          <span className={`tag tag--${connectorResults[s.id].ok ? 'green' : 'pink'}`}>{connectorResults[s.id].ok ? '通过' : '失败'}</span>
                        </div>
                        <div className="site-diagnostics__grid">
                          <DiagnosticItem label="连接器" value={connectorResults[s.id].connector_type} />
                          <DiagnosticItem label="请求" value={`${connectorResults[s.id].request?.method || 'GET'} ${connectorResults[s.id].request?.url || '未生成'}`} />
                          <DiagnosticItem label="发布接口" value={connectorResults[s.id].config?.publish_endpoint || '未生成'} />
                          <DiagnosticItem label="鉴权方式" value={connectorResults[s.id].request?.auth || '未识别'} />
                          <DiagnosticItem label="耗时 / 样本" value={`${connectorResults[s.id].duration_ms ?? '—'} ms · ${connectorResults[s.id].sample_count ?? 0} 篇`} />
                          <DiagnosticItem label="已配置参数" value={connectorResults[s.id].config?.configured_keys.join('、') || '无'} />
                          <DiagnosticItem label="缺失参数" value={connectorResults[s.id].config?.missing_keys.join('、') || '无'} />
                        </div>
                        <div className="site-diagnostics__checks">
                          {(connectorResults[s.id].checks || []).map((check) => (
                            <div className="site-diagnostics__check" key={check.key}>
                              <span className={`site-diagnostics__check-dot site-diagnostics__check-dot--${check.status}`} />
                              <strong>{check.label}</strong>
                              <span>{check.detail}</span>
                            </div>
                          ))}
                        </div>
                        {connectorResults[s.id].error && <div className="site-diagnostics__error">错误：{connectorResults[s.id].error}</div>}
                      </div>
                    )}
                    {isBusinessMain(s) && isOemAppsUrl(s.api_base_url || '') && <SiteOemAppsConnectorCard site={s} />}
                    {isBusinessMain(s) && (
                      <SiteIndexScanCard
                        profile={s.knowledge_profile}
                        result={indexScanResults[s.id]}
                        scanning={indexScanning === s.id}
                        onFiles={(files) => void scanIndex(s, files)}
                      />
                    )}
                    <SiteKnowledgeCard
                      profile={s.knowledge_profile}
                      generating={knowledgeGenerating === s.id}
                      onGenerate={() => void generateKnowledge(s)}
                      onEdit={() => openKnowledgeEditor(s)}
                      clearing={knowledgeClearing === s.id}
                      onClear={() => void clearKnowledge(s, true)}
                      onClearAll={() => void clearKnowledge(s, false)}
                      onConfirm={() => {
                        const profile = s.knowledge_profile
                        if (profile) setKnowledgeEditing({ site: s, profile: { ...profile, status: 'confirmed' } })
                      }}
                    />
                    <div className="site-card__meta">
                      <span className="site-card__meta-label">域名</span>
                      <span className="site-card__meta-value">{s.domain}</span>
                      <span className="site-card__meta-label">市场 / 语言</span>
                      <span className="site-card__meta-value">
                        {s.market || '未设置'} / {s.language_code?.toUpperCase() || '未设置'}
                      </span>
                      <span className="site-card__meta-label">所属业务</span>
                      <span className="site-card__meta-value">{s.business_id || '未设置'}</span>
                      <span className="site-card__meta-label">知识画像</span>
                      <span
                        className={
                          'site-card__meta-value ' +
                          (s.knowledge_profile?.status === 'confirmed'
                            ? 'site-card__meta-value--ok'
                            : 'site-card__meta-value--warn')
                        }
                      >
                        ● {s.knowledge_profile?.status === 'confirmed' ? '已确认，可用于策略' : s.knowledge_profile ? '待确认' : '未生成'}
                      </span>
                      <span className="site-card__meta-label">内容优先级</span>
                      <span className="site-card__priority">
                        <span
                          className="site-card__priority-label"
                          style={{
                            color:
                              prio.tone === 'gold'
                                ? '#8a6d1f'
                                : prio.tone === 'pink'
                                ? '#a14a3c'
                                : prio.tone === 'blue'
                                ? '#3f6182'
                                : '#5c6e33',
                          }}
                        >
                          {prio.label}
                        </span>
                        <span className="site-card__priority-bar">
                          <span
                            className="site-card__priority-fill"
                            style={{
                              width: `${prio.width}%`,
                              background: prio.fill,
                            }}
                          />
                        </span>
                      </span>
                    </div>
                  </div>
                )
              })}
              <div className="site-card site-card--placeholder">
                <button className="btn btn--ghost btn--xs" type="button" onClick={openNewSite} disabled={!businessId || !currentMainSite}><span className="msr">add_circle</span>新增当前业务站点</button>
                <span className="site-add__title">站点接入占位</span>
                <span className="site-add__hint">会自动归入当前业务；配置连接信息后即可检测</span>
              </div>
            </div>
          </DataGuard>
        </div>

      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="card__title">站点知识进度</div>
          <div className="ai-list">
            {displaySites.map((site) => {
              const profile = site.knowledge_profile
              return (
                <div className="ai-list__item" key={site.id}>
                  <div className={`ai-list__dot ai-list__dot--${profile?.status === 'confirmed' ? 'green' : 'gold'}`}><span className="msr">{profile?.status === 'confirmed' ? 'verified' : 'lightbulb'}</span></div>
                  <div><div className="ai-list__label">{profile?.status === 'confirmed' ? '已确认' : profile ? '待确认' : '未生成'}</div><div className="ai-list__title">{site.name}</div><div className="ai-list__desc">{profile?.positioning || '生成知识画像后，AI 才会使用该站点的内容边界。'}</div></div>
                </div>
              )
            })}
          </div>
          <div className="btn--block btn--block-static" role="note">
            已确认 {sites.data?.filter((site) => site.knowledge_profile?.status === 'confirmed').length ?? 0} / {sites.data?.length ?? 0} 个站点
            <span className="msr">hub</span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-400)', marginTop: 9 }}>确认后才会参与关键词分配和文章策略。</div>
        </div>
      </aside>
      {editing && form && (
        <SiteEditor
          form={form}
          configuredKeys={editing.api_config_summary?.configured_keys || []}
          saving={saving}
          onChange={(key, value) => setForm((current) => current ? { ...current, [key]: value } : current)}
          onSave={() => void saveEditor()}
          onClose={() => { setEditing(null); setForm(null) }}
        />
      )}
      {knowledgeEditing && (
        <KnowledgeEditor
          site={knowledgeEditing.site}
          profile={knowledgeEditing.profile}
          saving={knowledgeSaving === knowledgeEditing.site.id}
          onChange={(profile) => setKnowledgeEditing((current) => current ? { ...current, profile } : current)}
          onSave={() => void saveKnowledge(false)}
          onConfirm={() => void saveKnowledge(true)}
          onClose={() => setKnowledgeEditing(null)}
        />
      )}
      {businessOnboarding && (
        <BusinessOnboardingEditor
          form={businessOnboarding}
          saving={businessOnboardingSaving}
          onChange={(key, value) => setBusinessOnboarding((current) => current ? { ...current, [key]: value } : current)}
          onSave={() => void saveBusinessOnboarding()}
          onClose={() => setBusinessOnboarding(null)}
        />
      )}
      {businessDiscoveryOpen && (
        <BusinessDiscoveryEditor
          sites={(sites.data || []).filter((site) => !site.business_id)}
          selectedSiteId={businessDiscoverySiteId}
          result={businessDiscoveryResult}
          scanning={businessDiscoveryRunning}
          confirmation={businessConfirmation}
          confirming={businessConfirming}
          onDiscover={(siteId) => void runBusinessDiscovery(siteId)}
          onConfirmationChange={(key, value) => setBusinessConfirmation((current) => ({ ...current, [key]: value }))}
          onConfirm={() => void confirmDiscoveredBusiness()}
          onManual={() => { setBusinessDiscoveryOpen(false); openManualBusinessOnboarding() }}
          onClose={() => setBusinessDiscoveryOpen(false)}
        />
      )}
    </>
  )
}

function BusinessDiscoveryEditor({
  sites,
  selectedSiteId,
  result,
  scanning,
  confirmation,
  confirming,
  onDiscover,
  onConfirmationChange,
  onConfirm,
  onManual,
  onClose,
}: {
  sites: Site[]
  selectedSiteId: string
  result: BusinessDiscoveryResult | null
  scanning: boolean
  confirmation: BusinessConfirmation
  confirming: boolean
  onDiscover: (siteId: string) => void
  onConfirmationChange: (key: keyof BusinessConfirmation, value: string | boolean) => void
  onConfirm: () => void
  onManual: () => void
  onClose: () => void
}) {
  const profile = result?.knowledge_profile
  const policy = profile?.generation_policy
  const field = (key: keyof Pick<BusinessConfirmation, 'business_id' | 'market' | 'language_code'>, label: string, hint?: string) => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}>
      <span>{label}</span>
      <input className="input" value={String(confirmation[key] ?? '')} onChange={(event) => onConfirmationChange(key, event.target.value)} />
      {hint && <span style={{ color: 'var(--ink-400)', fontSize: 10.5 }}>{hint}</span>}
    </label>
  )
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 22, background: 'rgba(23,22,20,.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: 'min(760px, 100%)', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="card__title"><span>从已有站点新增业务</span><button className="icon-btn" type="button" onClick={onClose}>close</button></div>
        <p style={{ color: 'var(--ink-500)', fontSize: 12, lineHeight: 1.65, marginTop: 8 }}>系统会读取公开 sitemap、站内页面和已导入文章，再由 AI 生成一份待确认资料卡。预览不会写入业务资料、发布文章或自动启用策略。</p>
        <div style={{ display: 'grid', gap: 8, marginTop: 14 }}>
          {!sites.length && <div className="agent-empty">没有未归属业务的站点。已有站点可先解除原业务归属，或使用手动创建。</div>}
          {sites.map((site) => (
            <button key={site.id} type="button" className={`btn ${selectedSiteId === site.id ? 'btn--primary' : 'btn--ghost'}`} style={{ justifyContent: 'flex-start', textAlign: 'left' }} onClick={() => onDiscover(site.id)} disabled={scanning}>
              <span className="msr">{site.site_type === 'shopify' ? 'storefront' : 'language'}</span>
              {scanning && selectedSiteId === site.id ? '正在扫描并分析…' : `${site.name} · ${site.site_type} · ${site.base_url || site.domain}`}
            </button>
          ))}
        </div>
        {result && profile && (
          <>
            <div className="site-diagnostics site-diagnostics--ok" style={{ marginTop: 16 }}>
              <div className="site-diagnostics__head"><strong>扫描完成</strong><span className="tag tag--green">草案</span></div>
              <div className="site-diagnostics__grid">
                <DiagnosticItem label="发现 URL" value={`${result.scan.indexed_urls} 个；实际扫描 ${result.scan.scanned_urls} 个`} />
                <DiagnosticItem label="既有文章 / 产品记录" value={`${result.evidence.existing_posts} / ${result.evidence.product_records}`} />
                <DiagnosticItem label="页面问题" value={`${result.scan.issues} 个`} />
                <DiagnosticItem label="风险等级" value={policy?.risk_level || '待确认'} />
              </div>
            </div>
            <div style={{ display: 'grid', gap: 7, marginTop: 14, fontSize: 12, lineHeight: 1.6 }}>
              <div><strong>AI 定位：</strong>{profile.positioning || '未识别'}</div>
              <div><strong>受众：</strong>{profile.audience || '未识别'}</div>
              <div><strong>建议主题：</strong>{profile.in_scope_topics?.join('、') || '未识别'}</div>
              <div><strong>产品/服务线索：</strong>{profile.products?.join('、') || result.scan.product_hints.join('、') || '未识别'}</div>
              <div style={{ color: 'var(--ink-500)' }}>{result.recommended_next_step}</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 16 }}>
              {field('business_id', '业务 ID *', '已根据站点 Key 预填，可修改')}
              {field('market', '目标市场', '仅在启用关键词策略时必填，例如 US')}
              {field('language_code', '语言代码', '例如 en / de / zh')}
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12, paddingTop: 20 }}><input type="checkbox" checked={confirmation.strategy_enabled} onChange={(event) => onConfirmationChange('strategy_enabled', event.target.checked)} /><span>我已核对资料卡，立即启用策略。</span></label>
            </div>
            {result.candidate_targets.length > 0 && <label style={{ display: 'grid', gap: 5, fontSize: 12, marginTop: 12 }}><span>候选承接页</span><select className="input" value={confirmation.target_url} onChange={(event) => onConfirmationChange('target_url', event.target.value)}>{result.candidate_targets.map((target) => <option value={target.url} key={target.url}>{target.type} · {target.title || target.url}</option>)}</select><span style={{ color: 'var(--ink-400)', fontSize: 10.5 }}>来自公开页面的标题和 H1；选择即确认它可作为文章内链承接页。医疗效果、安全或治疗说法仍必须有批准来源。</span></label>}
          </>
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onManual}>手动创建</button><div style={{ display: 'flex', gap: 8 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button>{result && <button className="btn btn--primary" type="button" onClick={onConfirm} disabled={confirming}>{confirming ? '确认中…' : (['regulated', 'ymyl'].includes(String(policy?.risk_level)) && !(policy?.allowed_sources || []).length ? '保存业务草案' : '确认业务资料卡')}</button>}</div></div>
      </div>
    </div>
  )
}

function BusinessOnboardingEditor({
  form,
  saving,
  onChange,
  onSave,
  onClose,
}: {
  form: BusinessOnboardingForm
  saving: boolean
  onChange: (key: keyof BusinessOnboardingForm, value: string | boolean) => void
  onSave: () => void
  onClose: () => void
}) {
  const field = (key: keyof BusinessOnboardingForm, label: string, hint?: string, type = 'text') => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}>
      <span>{label}</span>
      <input className="input" type={type} value={String(form[key] ?? '')} onChange={(event) => onChange(key, event.target.value)} />
      {hint && <span style={{ color: 'var(--ink-400)', fontSize: 10.5 }}>{hint}</span>}
    </label>
  )
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 22, background: 'rgba(23,22,20,.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: 'min(760px, 100%)', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="card__title"><span>新增业务</span><button className="icon-btn" type="button" onClick={onClose}>close</button></div>
        <p style={{ color: 'var(--ink-500)', fontSize: 12, lineHeight: 1.65, marginTop: 8 }}>先创建业务草案。产品和分类接口同步完成后，AI 会自动生成定位、受众、主题与内容安全规则；此处不会扫描 sitemap，也不会启用策略。</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
          {field('business_name', '业务名称 *', '例如 HealthyOxy；系统会根据主站域名自动生成内部业务标识')}
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>主站类型</span><select className="input" value={form.site_type} onChange={(event) => onChange('site_type', event.target.value)}><option value="shopify">Shopify 商业主站</option><option value="main">其他商业主站</option><option value="wp">WordPress 内容站</option><option value="blog">博客站</option><option value="other">其他</option></select></label>
          <div style={{ gridColumn: '1 / -1' }}>{field('base_url', '主站网址 *', '例如 https://example.com；用于后续关联产品与分类接口')}</div>
        </div>
        <div style={{ marginTop: 14, padding: 12, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--paper-100)', fontSize: 12, lineHeight: 1.65, color: 'var(--ink-500)' }}>
          <strong style={{ color: 'var(--ink-700)' }}>创建后会做什么</strong>
          <div>1. 接入并同步产品、分类和产品页数据</div>
          <div>2. AI 生成业务资料卡与内容边界</div>
          <div>3. 你确认资料卡后，才可以启用关键词和文章策略</div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button><button className="btn btn--primary" type="button" onClick={onSave} disabled={saving}>{saving ? '创建中…' : '创建业务草案'}</button></div>
      </div>
    </div>
  )
}

function SiteEditor({
  form,
  configuredKeys,
  saving,
  onChange,
  onSave,
  onClose,
}: {
  form: SiteForm
  configuredKeys: string[]
  saving: boolean
  onChange: (key: keyof SiteForm, value: string | boolean) => void
  onSave: () => void
  onClose: () => void
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const isWordPress = form.connector_type === 'wordpress'
  const isShopify = form.connector_type === 'shopify' || form.site_type === 'shopify'
  const articlePaths = articlePathsForForm(form)
  const field = (key: keyof SiteForm, label: string, type = 'text') => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}>
      <span>{label}</span>
      <input className="input" type={type} value={String(form[key] ?? '')} onChange={(event) => onChange(key, event.target.value)} />
    </label>
  )
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 20, background: 'rgba(23,22,20,.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: 'min(620px, 100%)', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="card__title"><span>编辑站点配置：{form.name}</span><button className="icon-btn" type="button" onClick={onClose}>close</button></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
          {field('name', '站点名称')}
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>站点类型</span><select className="input" value={form.site_type} onChange={(event) => onChange('site_type', event.target.value)}>{isBusinessMainForm(form) && <><option value="main">商业主站</option><option value="shopify">Shopify 商业主站</option></>}<option value="blog">博客</option><option value="wp">WordPress</option><option value="other">其他</option></select></label>
          {field('base_url', '公开站点网址')}
          <div style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>所属业务</span><div className="input" style={{ display: 'flex', alignItems: 'center', color: 'var(--ink-500)' }}>{form.business_id || '未选择业务'}</div></div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <input type="checkbox" checked={form.strategy_enabled} onChange={(event) => onChange('strategy_enabled', event.target.checked)} />
            <span>参与所属业务的策略扫描与规划</span>
          </label>
        </div>
        <div style={{ marginTop: 18, fontWeight: 700, fontSize: 13 }}>文章连接</div>
        {isShopify ? (
          <div style={{ marginTop: 6, color: 'var(--ink-500)', fontSize: 12 }}>Shopify 由已安装的应用授权连接；无需在这里填写文章接口地址或 Token。</div>
        ) : (
          <>
            <div style={{ marginTop: 6, color: 'var(--ink-500)', fontSize: 12 }}>已保存的密钥不会回显；留空表示保留原配置，填写新值后替换。</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
              {isWordPress ? <>{field('username', 'WordPress 用户名')}{field('applicationPassword', 'Application Password', 'password')}</> : <>{field('api_base_url', '文章接口地址')}{field('tokenA', '站点 Token', 'password')}</>}
            </div>
            {articlePaths.isPreset && <div style={{ marginTop: 8, color: 'var(--ink-500)', fontSize: 12 }}>已识别为 OEMApps：文章读取和发布均使用 <code>/posts</code>，仅 Token 因站点而异，无需手填路径。</div>}
          </>
        )}
        <div style={{ marginTop: 16 }}>
          <button className="btn btn--ghost btn--xs" type="button" onClick={() => setAdvancedOpen((value) => !value)}>{advancedOpen ? '收起高级配置' : '高级配置'}</button>
        </div>
        {advancedOpen && <>
          <div style={{ marginTop: 8, color: 'var(--ink-500)', fontSize: 12 }}>通常不需要修改。站点 Key、市场/语种和内容边界会由业务归属、产品接口和 AI 资料卡补全。</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
            {field('site_key', '站点 Key')}
            <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>连接器类型</span><select className="input" value={form.connector_type} onChange={(event) => onChange('connector_type', event.target.value)}><option value="custom_openapi">Custom OpenAPI</option><option value="wordpress">WordPress REST</option><option value="shopify">Shopify Admin</option></select></label>
            {field('domain', '技术域名（通常无需修改）')}
            {!isShopify && !isWordPress && field('openApiKey', 'OpenAPI Key（旧协议更新用）', 'password')}
            {!isShopify && !isWordPress && field('tokenB', '备用站点 Token', 'password')}
            {!articlePaths.isPreset && !isShopify && <>{field('articlesPath', '读取文章路径')}{field('publishPath', '发布文章路径')}</>}
            {field('market', '市场，例如 US / DE')}
            {field('language_code', '语言，例如 en / de')}
            {field('google_gl', 'Google 国家参数，例如 us')}
            {field('google_hl', 'Google 语言参数，例如 en')}
            {field('semrush_database', 'Semrush 数据库，例如 us')}
            {field('content_role', '内容角色')}
            {field('content_scope', '内容范围')}
            {field('notes', '备注')}
          </div>
          <div style={{ marginTop: 10, color: 'var(--ink-500)', fontSize: 12 }}>当前已保存字段：{configuredKeys.length ? configuredKeys.join('、') : '未检测或未配置'}</div>
        </>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button><button className="btn btn--primary" type="button" onClick={onSave} disabled={saving}>{saving ? '保存中…' : '保存配置'}</button></div>
      </div>
    </div>
  )
}

function SiteOemAppsConnectorCard({ site }: { site: Site }) {
  const [connector, setConnector] = useState<CustomConnector | null>(null)
  const [token, setToken] = useState('')
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState<'verify' | 'sync' | null>(null)
  const [message, setMessage] = useState('')

  async function refreshConnector() {
    const result = await listCustomConnectors(site.id)
    setConnector(result.items.find((item) => item.config?.adapter === 'oemapps') || null)
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setConnector(null)
    setMessage('')
    void listCustomConnectors(site.id)
      .then((result) => {
        if (!cancelled) setConnector(result.items.find((item) => item.config?.adapter === 'oemapps') || null)
      })
      .catch((error) => {
        if (!cancelled) setMessage(error instanceof Error ? `无法读取商品连接器：${error.message}` : '无法读取商品连接器')
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [site.id])

  async function verifyAndActivate() {
    const trimmedToken = token.trim()
    if (!connector && !trimmedToken) {
      window.alert('请填写 OEMApps 站点 Token。它只会加密保存到专用商品连接器，不会写入文章接口配置。')
      return
    }
    setAction('verify')
    setMessage('')
    try {
      const saved = trimmedToken ? await configureOemAppsConnector(site.id, trimmedToken) : connector
      if (!saved) throw new Error('未找到商品连接器')
      const tested = await testCustomConnector(saved.id)
      if (!tested.ok) throw new Error(tested.errors?.join('；') || '商品映射验证未通过')
      const active = await activateCustomConnector(saved.id)
      setConnector(active)
      setToken('')
      setMessage(`已验证并启用：可读取 ${tested.mapped_items ?? tested.total_items ?? 0} 个商品样本。下一步可同步商品与分类。`)
    } catch (error) {
      setMessage(error instanceof Error ? `验证失败：${error.message}` : '验证失败')
      void refreshConnector().catch(() => undefined)
    } finally {
      setAction(null)
    }
  }

  async function syncCatalog() {
    if (!connector || connector.status !== 'active') return
    setAction('sync')
    setMessage('')
    try {
      const products = await syncCustomConnectorProducts(connector.id)
      const collections = await syncOemAppsCollections(connector.id)
      setMessage(`同步完成：商品 ${products.items_upserted} 个，分类 ${collections.collections_upserted} 个。`)
    } catch (error) {
      setMessage(error instanceof Error ? `同步失败：${error.message}` : '同步失败')
    } finally {
      setAction(null)
    }
  }

  const status = connector?.status || '未接入'
  const tone = status === 'active' ? 'green' : status === 'verified' ? 'gold' : status === '未接入' ? 'blue' : 'pink'
  const canSync = connector?.status === 'active'
  return (
    <div style={{ background: 'var(--paper-100)', border: '1px solid var(--border)', borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <strong style={{ fontSize: 12 }}>OEMApps 商品与分类连接器</strong>
        <span className={`tag tag--${tone}`}>{loading ? '读取中' : status === 'active' ? '已启用' : status}</span>
      </div>
      <div style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.55, marginTop: 6 }}>
        专供 ExDivo、Avinoti 这类 OEMApps 主站。商品 Token 与文章接口独立加密保存；验证通过后才允许同步，不会向站点写入任何内容。
      </div>
      {!loading && (
        <>
          {connector && <div style={{ color: 'var(--ink-400)', fontSize: 10.5, marginTop: 7 }}>连接器：{connector.name} · 当前版本 v{connector.current_version}{connector.verified_at ? ' · 已验证' : ''}</div>}
          <label style={{ display: 'grid', gap: 5, fontSize: 11, marginTop: 10 }}>
            <span>{connector ? '替换 OEMApps Token（留空则仅重新验证）' : 'OEMApps 站点 Token'}</span>
            <input className="input" type="password" autoComplete="new-password" value={token} onChange={(event) => setToken(event.target.value)} placeholder={connector ? '可选：填写后会替换旧 Token' : '粘贴站点 Token'} disabled={action !== null} />
          </label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 10 }}>
            <button className="btn btn--primary btn--xs" type="button" onClick={() => void verifyAndActivate()} disabled={action !== null}>
              <span className="msr">{action === 'verify' ? 'progress_activity' : 'verified_user'}</span>{action === 'verify' ? '验证中…' : connector ? '重新验证并启用' : '保存并验证'}
            </button>
            {canSync && <button className="btn btn--ghost btn--xs" type="button" onClick={() => void syncCatalog()} disabled={action !== null}>
              <span className="msr">{action === 'sync' ? 'progress_activity' : 'inventory_2'}</span>{action === 'sync' ? '同步中…' : '同步商品与分类'}
            </button>}
          </div>
        </>
      )}
      {message && <div style={{ color: message.includes('失败') || message.includes('无法') ? '#a14a3c' : '#5c6e33', fontSize: 11, lineHeight: 1.55, marginTop: 9 }}>{message}</div>}
    </div>
  )
}

function SiteIndexScanCard({
  profile,
  result,
  scanning,
  onFiles,
}: {
  profile?: SiteKnowledgeProfile | null
  result?: SiteIndexScanResult
  scanning: boolean
  onFiles: (files: File[]) => void
}) {
  const persisted = profile?.index_scan
  const index = result?.index || persisted
  const summary = result?.seo_audit.summary || persisted?.summary
  const issues = result?.seo_audit.issues || persisted?.issues || []
  const productHints = result?.seo_audit.product_hints || persisted?.product_hints || profile?.products || []
  return (
    <div style={{ background: 'var(--paper-100)', border: '1px solid var(--border)', borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <strong style={{ fontSize: 12 }}>主站索引与 SEO 审查</strong>
        <span className={`tag tag--${summary ? (summary.issues ? 'gold' : 'green') : 'blue'}`}>{summary ? '已扫描' : '未扫描'}</span>
      </div>
      <div style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.5, marginTop: 6 }}>
        上传新的索引时默认覆盖旧库存；一次选择多个文件时，首个文件覆盖、后续文件继续合并。支持 XML、TXT 和 GZip 压缩索引。
      </div>
      {index && summary && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6, marginTop: 10 }}>
            <ScanMetric label="索引 URL" value={index.indexed_urls} />
            <ScanMetric label="已扫描" value={index.scanned_urls} />
            <ScanMetric label="可读取" value={summary.ok} />
            <ScanMetric label="问题页面" value={summary.issues} />
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>
            <span className="chip">缺 title {summary.missing_title}</span>
            <span className="chip">缺 description {summary.missing_description}</span>
            <span className="chip">缺 H1 {summary.missing_h1}</span>
            <span className="chip">重复 title {summary.duplicate_title}</span>
          </div>
          {productHints.length > 0 && <div style={{ color: 'var(--ink-500)', fontSize: 11, marginTop: 8 }}>识别到的产品：{productHints.slice(0, 6).join('、')}</div>}
          {issues.length > 0 && <div style={{ marginTop: 8, display: 'grid', gap: 4 }}>
            {issues.slice(0, 3).map((issue) => (
              <div key={issue.url} style={{ fontSize: 10.5, color: 'var(--ink-500)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={issue.url}>
                <strong style={{ color: '#a14a3c' }}>{issue.issues.join('、')}</strong> · {issue.url}
              </div>
            ))}
            {issues.length > 3 && <div style={{ fontSize: 10.5, color: 'var(--ink-400)' }}>还有 {issues.length - 3} 个问题页面</div>}
          </div>}
          <div style={{ color: 'var(--ink-400)', fontSize: 10.5, marginTop: 8 }}>文件：{index.files?.map((item) => item.filename).join('、') || index.filename}{index.nested_sitemaps ? ` · 展开 ${index.nested_sitemaps} 个子索引` : ''}</div>
        </>
      )}
      <div style={{ marginTop: 10 }}>
        <label className="btn btn--primary btn--xs" style={{ display: 'inline-flex', cursor: scanning ? 'wait' : 'pointer' }}>
          <span className="msr">{scanning ? 'progress_activity' : 'upload_file'}</span>{scanning ? '扫描中…' : summary ? '重新上传并扫描' : '上传索引并扫描'}
          <input
            type="file"
            multiple
            accept=".xml,.txt,.gz,text/xml,text/plain,application/gzip"
            disabled={scanning}
            style={{ display: 'none' }}
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files || [])
              event.currentTarget.value = ''
              if (files.length) onFiles(files)
            }}
          />
        </label>
      </div>
    </div>
  )
}

function ScanMetric({ label, value }: { label: string; value: number }) {
  return <div style={{ background: '#fff', borderRadius: 8, padding: '6px 7px' }}><div style={{ color: 'var(--ink-400)', fontSize: 10 }}>{label}</div><strong style={{ fontSize: 15 }}>{value}</strong></div>
}

function SiteKnowledgeCard({
  profile,
  generating,
  onGenerate,
  onEdit,
  onConfirm,
  clearing,
  onClear,
  onClearAll,
}: {
  profile?: SiteKnowledgeProfile | null
  generating: boolean
  onGenerate: () => void
  onEdit: () => void
  onConfirm: () => void
  clearing: boolean
  onClear: () => void
  onClearAll: () => void
}) {
  const activeProfile = hasKnowledgeData(profile) ? profile : null
  const status = activeProfile?.status === 'confirmed' ? '已确认' : activeProfile ? '待确认' : '未生成'
  const topics = activeProfile?.in_scope_topics || []
  return (
    <div style={{ background: 'var(--paper-100)', border: '1px solid var(--border)', borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <strong style={{ fontSize: 12 }}>站点知识</strong>
        <span className={`tag tag--${activeProfile?.status === 'confirmed' ? 'green' : activeProfile ? 'gold' : 'blue'}`}>{status}</span>
      </div>
      {activeProfile ? <>
        <div style={{ fontSize: 12, fontWeight: 700, marginTop: 8 }}>{activeProfile.positioning || '定位待补充'}</div>
        <div style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.5, marginTop: 4 }}>{activeProfile.audience || '目标受众待确认'}</div>
        {topics.length > 0 && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>{topics.slice(0, 6).map((topic) => <span className="chip" key={topic}>{topic}</span>)}</div>}
        <div style={{ color: 'var(--ink-400)', fontSize: 10.5, marginTop: 8 }}>依据：{activeProfile.evidence?.length || 0} 条站点、文章和关键词证据</div>
      </> : <div style={{ color: 'var(--ink-500)', fontSize: 11, marginTop: 7 }}>读取现有文章和关键词后，自动整理这个站点应该写什么。</div>}
      <div style={{ display: 'flex', gap: 7, marginTop: 10 }}>
        <button className="btn btn--primary btn--xs" type="button" onClick={onGenerate} disabled={generating}><span className="msr">{generating ? 'progress_activity' : 'auto_awesome'}</span>{generating ? '生成中…' : activeProfile ? '重新生成' : '生成知识'}</button>
        {activeProfile && <button className="btn btn--ghost btn--xs" type="button" onClick={onEdit}>编辑</button>}
        {activeProfile && activeProfile.status !== 'confirmed' && <button className="btn btn--ghost btn--xs" type="button" onClick={onConfirm}>确认使用</button>}
      </div>
      {activeProfile && <div style={{ display: 'flex', gap: 7, marginTop: 7 }}>
        <button className="btn btn--ghost btn--xs" type="button" onClick={onClear} disabled={clearing}>{clearing ? '清空中…' : '清空画像（保留扫描）'}</button>
        <button className="btn btn--ghost btn--xs" type="button" onClick={onClearAll} disabled={clearing} style={{ color: '#a14a3c' }}>全部清空</button>
      </div>}
    </div>
  )
}

function KnowledgeEditor({
  site,
  profile,
  saving,
  onChange,
  onSave,
  onConfirm,
  onClose,
}: {
  site: Site
  profile: SiteKnowledgeProfile
  saving: boolean
  onChange: (profile: SiteKnowledgeProfile) => void
  onSave: () => void
  onConfirm: () => void
  onClose: () => void
}) {
  const policy = profile.generation_policy || {}
  const updatePolicy = (patch: Partial<NonNullable<SiteKnowledgeProfile['generation_policy']>>) => onChange({ ...profile, generation_policy: { ...policy, ...patch } })
  const textField = (key: 'positioning' | 'audience' | 'tone', label: string) => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>{label}</span><textarea className="input" rows={2} value={profile[key]} onChange={(event) => onChange({ ...profile, [key]: event.target.value })} /></label>
  )
  const listField = (key: keyof Pick<SiteKnowledgeProfile, 'products' | 'in_scope_topics' | 'out_of_scope_topics' | 'content_types' | 'conversion_goals' | 'editorial_rules'>, label: string) => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>{label}（一行一项）</span><textarea className="input" rows={3} value={profile[key].join('\n')} onChange={(event) => onChange({ ...profile, [key]: event.target.value.split(/\n|[,，]/).map((item) => item.trim()).filter(Boolean) })} /></label>
  )
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 21, background: 'rgba(23,22,20,.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: 'min(700px, 100%)', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="card__title"><span>确认站点知识：{site.name}</span><button className="icon-btn" type="button" onClick={onClose}>close</button></div>
        <p style={{ color: 'var(--ink-500)', fontSize: 12, lineHeight: 1.6, marginTop: 8 }}>这里决定 AI 后续为这个站点选择哪些主题、内容类型和转化方向。确认前可以直接修改。</p>
        <div style={{ display: 'grid', gap: 12, marginTop: 14 }}>
          {textField('positioning', '站点定位')}
          {textField('audience', '目标受众')}
          {textField('tone', '写作语气')}
          {listField('products', '产品或服务')}
          {listField('in_scope_topics', '应该持续写的主题')}
          {listField('out_of_scope_topics', '暂不应该写的主题')}
          {listField('content_types', '内容类型')}
          {listField('conversion_goals', '转化目标')}
          {listField('editorial_rules', '编辑规则')}
        </div>
        <div style={{ marginTop: 18, fontWeight: 700, fontSize: 13 }}>生文安全规则</div>
        <p style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.55, marginTop: 6 }}>健康、金融和法律等业务必须保留受监管/高风险等级，并添加与目标市场匹配的官方或专业来源；系统会在缺来源时阻止生成。</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>站点角色</span><select className="input" value={policy.site_role || ''} onChange={(event) => updatePolicy({ site_role: event.target.value })}><option value="">由站点类型判断</option><option value="commercial">商业主站</option><option value="local_service">本地服务站</option><option value="editorial">内容站</option></select></label>
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>风险等级</span><select className="input" value={policy.risk_level || 'standard'} onChange={(event) => updatePolicy({ risk_level: event.target.value })}><option value="low">低</option><option value="standard">标准</option><option value="regulated">受监管</option><option value="ymyl">高风险（医疗/金融/法律）</option></select></label>
        </div>
        <label style={{ display: 'grid', gap: 5, fontSize: 12, marginTop: 12 }}><span>已核验的允许来源（一行一个 URL）</span><textarea className="input" rows={3} value={(policy.allowed_sources || []).map((item) => item.url).filter(Boolean).join('\n')} onChange={(event) => updatePolicy({ allowed_sources: splitLines(event.target.value).filter((url) => /^https:\/\//i.test(url)).map((url) => ({ url, label: url, source_type: 'approved' })) })} /><span style={{ color: 'var(--ink-400)', fontSize: 10.5 }}>只填写适用于当前目标市场、且已由你核验的来源；不要把搜索结果或竞品页面当作来源。</span></label>
        {profile.evidence.length > 0 && <div style={{ marginTop: 14, color: 'var(--ink-500)', fontSize: 11 }}>数据依据：{profile.evidence.map((item) => item.fact).filter(Boolean).join('；')}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button><button className="btn btn--ghost" type="button" onClick={onSave} disabled={saving}>保存草稿</button><button className="btn btn--primary" type="button" onClick={onConfirm} disabled={saving}>{saving ? '保存中…' : '确认并用于策略'}</button></div>
      </div>
    </div>
  )
}

function DiagnosticItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="site-diagnostics__item">
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  )
}
