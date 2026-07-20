import { useState } from 'react'
import { DataGuard } from '@/components/StateBlock'
import { clearSiteKnowledge, deleteSite, generateSiteKnowledge, saveSiteKnowledge, scanSiteIndex, syncSitePosts, testSiteConnector, upsertSite, useSites } from '@/hooks/useData'
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
  strategy_enabled: boolean
  notes: string
  openApiKey: string
  tokenA: string
  tokenB: string
  username: string
  applicationPassword: string
}

type ConnectorResult = Awaited<ReturnType<typeof testSiteConnector>>

function formFromSite(site: Site): SiteForm {
  return {
    site_key: site.site_key,
    name: site.name,
    site_type: site.site_type,
    connector_type: site.connector_type || (site.site_type === 'wp' ? 'wordpress' : 'custom_openapi'),
    domain: site.domain || '',
    base_url: site.base_url || '',
    api_base_url: site.api_base_url || '',
    articlesPath: site.api_config_summary?.articles_path || (site.connector_type === 'wordpress' ? '/wp-json/wp/v2/posts' : '/articles'),
    publishPath: site.api_config_summary?.publish_path || (site.connector_type === 'wordpress' ? '/wp-json/wp/v2/posts' : '/articles/save'),
    market: site.market || '',
    language_code: site.language_code || '',
    google_gl: site.google_gl || '',
    google_hl: site.google_hl || '',
    semrush_database: site.semrush_database || '',
    content_role: site.content_role || '',
    content_scope: site.content_scope || '',
    business_id: site.business_id || '',
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
  const [indexScanning, setIndexScanning] = useState<string | null>(null)
  const [indexScanResults, setIndexScanResults] = useState<Record<string, SiteIndexScanResult>>({})
  const sites = useSites(refreshKey)
  const tested = Object.values(testResults)
  const healthy = tested.filter((item) => item.ok).length

  function openEditor(site: Site) {
    setEditing(site)
    setForm(formFromSite(site))
  }

  function openNewSite() {
    const site = blankSite()
    setEditing(site)
    setForm(formFromSite(site))
  }

  async function saveEditor() {
    if (!form || saving) return
    setSaving(true)
    try {
      const credentials = form.connector_type === 'wordpress'
        ? { connector_type: form.connector_type, username: form.username, applicationPassword: form.applicationPassword }
        : { connector_type: form.connector_type, openApiKey: form.openApiKey, tokenA: form.tokenA, tokenB: form.tokenB }
      const api_config = Object.fromEntries(Object.entries({ ...credentials, articlesPath: form.articlesPath, publishPath: form.publishPath }).filter(([, value]) => value))
      await upsertSite({ ...form, api_config })
      setEditing(null)
      setForm(null)
      setRefreshKey((key) => key + 1)
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
              <span className="kpi__label">活跃站点</span>
              <span className="msr kpi__icon kpi__icon--gold">apartment</span>
            </div>
            <div className="kpi__value">{sites.data?.length ?? 0}</div>
            <div className="kpi__delta">当前配置</div>
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
            <span>站点列表</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button className="btn btn--primary btn--xs" type="button" onClick={openNewSite}>
                <span className="msr">add</span>
                新增站点
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

          <DataGuard
            loading={sites.loading}
            error={sites.error}
            empty={!sites.data || sites.data.length === 0}
            emptyTitle="暂无站点"
            emptyHint="通过 /api/v1/sites 添加站点后会展示在此"
          >
            <div className="site-grid">
              {(sites.data ?? []).map((s) => {
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
                    {(s.is_main || s.site_type === 'main') && (
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
                <button className="btn btn--ghost btn--xs" type="button" onClick={openNewSite}><span className="msr">add_circle</span>新增站点</button>
                <span className="site-add__title">站点接入占位</span>
                <span className="site-add__hint">配置 API、鉴权和文章路径后即可检测</span>
              </div>
            </div>
          </DataGuard>
        </div>

      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="card__title">站点知识进度</div>
          <div className="ai-list">
            {(sites.data || []).map((site) => {
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
    </>
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
          {field('site_key', '站点 Key')}
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>站点类型</span><select className="input" value={form.site_type} onChange={(event) => onChange('site_type', event.target.value)}><option value="main">主站</option><option value="blog">博客</option><option value="wp">WordPress</option><option value="other">其他</option></select></label>
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>连接器类型</span><select className="input" value={form.connector_type} onChange={(event) => onChange('connector_type', event.target.value)}><option value="custom_openapi">Custom OpenAPI</option><option value="wordpress">WordPress REST</option></select></label>
          {field('domain', '域名，例如 https://example.com')}
          {field('base_url', '站点基础 URL')}
          {field('api_base_url', '文章 API 基础地址')}
          {field('articlesPath', '读取文章路径，例如 /articles')}
          {field('publishPath', '发布文章路径，例如 /articles/save')}
          {field('market', '市场，例如 US / DE')}
          {field('language_code', '语言，例如 en / de')}
          {field('google_gl', 'Google 国家参数，例如 us')}
          {field('google_hl', 'Google 语言参数，例如 en')}
          {field('semrush_database', 'Semrush 数据库，例如 us')}
          {field('content_role', '内容角色')}
          {field('content_scope', '内容范围，例如产品、教程、评测')}
          {field('business_id', '所属业务 ID')}
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <input type="checkbox" checked={form.strategy_enabled} onChange={(event) => onChange('strategy_enabled', event.target.checked)} />
            <span>参与所属业务的策略扫描与规划</span>
          </label>
          {field('notes', '备注')}
        </div>
        <div style={{ marginTop: 18, fontWeight: 700, fontSize: 13 }}>鉴权配置</div>
        <div style={{ marginTop: 6, color: 'var(--ink-500)', fontSize: 12 }}>当前已保存字段：{configuredKeys.length ? configuredKeys.join('、') : '未检测或未配置'}</div>
        <div style={{ marginTop: 6, color: 'var(--ink-500)', fontSize: 12 }}>已保存的密钥不会回显；留空表示保留原配置，填写新值后替换。</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
          {form.connector_type === 'wordpress' ? <>{field('username', 'WordPress 用户名')}{field('applicationPassword', 'Application Password', 'password')}</> : <>{field('openApiKey', 'OpenAPI Key', 'password')}{field('tokenA', 'Token A', 'password')}{field('tokenB', 'Token B', 'password')}</>}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button><button className="btn btn--primary" type="button" onClick={onSave} disabled={saving}>{saving ? '保存中…' : '保存配置'}</button></div>
      </div>
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
