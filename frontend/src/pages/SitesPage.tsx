import { useState } from 'react'
import { DataGuard } from '@/components/StateBlock'
import { useBusinessScope } from '@/businessScope'
import { testSiteConnector } from '@/data/connectors'
import {
  clearSiteKnowledge,
  deleteSite,
  discoverSiteBusiness,
  generateSiteKnowledge,
  saveSiteKnowledge,
  scanSiteIndex,
  upsertSite,
  useSites,
} from '@/data/sites'
import { syncSitePosts } from '@/data/articles'
import type { Site, SiteIndexScanResult, SiteKnowledgeProfile } from '@/types/domain'
import {
  BusinessDiscoveryEditor,
  BusinessOnboardingEditor,
  DiagnosticItem,
  KnowledgeEditor,
  SiteEditor,
} from './sites/SiteDialogs'
import { SiteIndexScanCard, SiteKnowledgeCard } from './sites/SiteKnowledgePanels'
import { SiteOemAppsConnectorCard } from './sites/SiteOemAppsConnectorCard'
import { SiteShopifyConnectorCard } from './sites/SiteShopifyConnectorCard'
import { SiteShopifyProductSeoCard } from './sites/SiteShopifyProductSeoCard'
import {
  PRIORITY_META,
  TYPE_LABEL,
  TYPE_TONE,
  articlePathsForForm,
  blankBusinessOnboarding,
  blankSite,
  businessIdFromSite,
  emptyKnowledgeProfile,
  formFromSite,
  hasKnowledgeData,
  iconBg,
  iconColor,
  iconFor,
  isBusinessMain,
  isBusinessMainForm,
  isOemAppsUrl,
  roleKey,
  siteKeyFromBusinessId,
  type BusinessConfirmation,
  type BusinessDiscoveryResult,
  type BusinessOnboardingForm,
  type ConnectorResult,
  type SiteForm,
} from './sites/sitePageModel'

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
                    {isBusinessMain(s) && ['shopify', 'shopify_admin'].includes(s.site_type) && <SiteShopifyConnectorCard site={s} />}
                    {isBusinessMain(s) && ['shopify', 'shopify_admin'].includes(s.site_type) && <SiteShopifyProductSeoCard site={s} />}
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
