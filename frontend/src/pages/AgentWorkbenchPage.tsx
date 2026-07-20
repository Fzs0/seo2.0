import { useEffect, useMemo, useState } from 'react'
import { DataGuard } from '@/components/StateBlock'
import {
  triggerAnalyticsSync,
  useAnalyticsOverview,
  useArticles,
  useKeywords,
  useSites,
} from '@/hooks/useData'
import type { GscQuery, Keyword, Site } from '@/types/domain'

export type AgentView = 'command' | 'execution' | 'risk'

interface AgentWorkbenchPageProps {
  view: AgentView
  onNotify?: (title: string, detail?: string) => void
}

type Tone = 'gold' | 'green' | 'blue' | 'pink' | 'purple'

interface Opportunity {
  id: string
  type: string
  tone: Tone
  title: string
  asset: string
  reason: string
  impact: string
}

function number(value: number | string | null | undefined) {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function formatNumber(value: number | string | null | undefined) {
  return new Intl.NumberFormat('zh-CN').format(number(value))
}

function formatDate(value?: string | null) {
  if (!value) return '暂无'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function keywordOpportunity(keyword: Keyword): Opportunity {
  const strategy = keyword.ai_review?.strategy
  const type = strategy?.pageType || keyword.page_type || '关键词机会'
  const tone: Tone = keyword.status === 'blocked' ? 'pink' : strategy?.contentAction ? 'gold' : 'blue'
  return {
    id: keyword.id,
    type,
    tone,
    title: keyword.keyword,
    asset: keyword.assigned_site_label || '未分配站点',
    reason: strategy?.strategyReason || keyword.reason || keyword.intent || '后端尚未提供策略说明',
    impact: keyword.volume ? `${formatNumber(keyword.volume)} 月搜索量` : '等待数据积累',
  }
}

export function AgentWorkbenchPage({ view, onNotify }: AgentWorkbenchPageProps) {
  const [siteId, setSiteId] = useState<string>()
  const [refreshKey, setRefreshKey] = useState(0)
  const [selectedOpportunity, setSelectedOpportunity] = useState<Opportunity | null>(null)
  const [syncing, setSyncing] = useState(false)
  const sites = useSites()
  const keywords = useKeywords(refreshKey, 200)
  const articles = useArticles(refreshKey)
  const analytics = useAnalyticsOverview(siteId, refreshKey)

  useEffect(() => {
    if (!siteId && sites.data?.[0]?.id) setSiteId(sites.data[0].id)
  }, [siteId, sites.data])

  const selectedSite = sites.data?.find((site) => site.id === siteId) || analytics.data?.sites.find((site) => site.id === siteId)
  const keywordItems = keywords.data || []
  const articleItems = articles.data || []
  const gscItems = analytics.data?.opportunities || []
  const opportunities = useMemo(() => {
    const keywordRows = keywordItems.map(keywordOpportunity)
    if (keywordRows.length) return keywordRows.slice(0, 12)
    return gscItems.slice(0, 12).map(gscOpportunity)
  }, [gscItems, keywordItems])
  const loading = sites.loading || keywords.loading || articles.loading || analytics.loading
  const error = sites.error || keywords.error || articles.error || analytics.error

  async function syncSelectedSite() {
    if (!selectedSite) {
      onNotify?.('没有可同步的站点', '请先导入或配置站点。')
      return
    }
    setSyncing(true)
    try {
      const result = await triggerAnalyticsSync(selectedSite.id, 28)
      setRefreshKey((key) => key + 1)
      onNotify?.('数据同步已完成', `${selectedSite.name} · ${result.results?.map((item) => `${item.type}:${item.rows_written || 0}`).join('，') || '已提交'}`)
    } catch (syncError) {
      onNotify?.('数据同步失败', syncError instanceof Error ? syncError.message : '后端返回未知错误')
    } finally {
      setSyncing(false)
    }
  }

  const header = headerFor(view)
  return (
    <section className="page agent-page" data-screen-label="SEO Agent 工作台">
      <div className="page-heading-row agent-heading">
        <div>
          <div className="agent-eyebrow">{header.eyebrow}</div>
          <h1>{header.title}</h1>
          <p>{header.description}</p>
        </div>
        <div className="agent-heading-actions">
          <label className="select-control">
            <span>资产范围</span>
            <select value={siteId || ''} onChange={(event) => setSiteId(event.target.value || undefined)} disabled={!sites.data?.length}>
              {!sites.data?.length && <option value="">暂无站点</option>}
              <option value="">全部资产</option>
              {sites.data?.map((site) => <option value={site.id} key={site.id}>{site.name}</option>)}
            </select>
          </label>
          <button type="button" className="btn btn--primary" onClick={syncSelectedSite} disabled={syncing || !selectedSite}>
            <span className="msr">sync</span>{syncing ? '同步中…' : '同步数据'}
          </button>
        </div>
      </div>

      <DataGuard loading={loading} error={error} empty={!sites.data && !keywords.data}>
        {view === 'command' && <CommandView sites={sites.data || []} keywords={keywordItems} articles={articleItems} analytics={analytics.data} opportunities={opportunities} onOpen={setSelectedOpportunity} />}
        {view === 'execution' && <ExecutionView keywords={keywordItems} articles={articleItems} />}
        {view === 'risk' && <RiskView sites={sites.data || []} keywords={keywordItems} analytics={analytics.data} />}
      </DataGuard>

      {selectedOpportunity && <OpportunityDrawer opportunity={selectedOpportunity} onClose={() => setSelectedOpportunity(null)} />}
    </section>
  )
}

function headerFor(view: AgentView) {
  const headers: Record<AgentView, { eyebrow: string; title: string; description: string }> = {
    command: { eyebrow: 'SEO AGENT · COMMAND CENTER', title: '全局增长控制台', description: 'Agent 正在从真实资产、机会和历史数据中生成下一批可执行决策。' },
    execution: { eyebrow: 'EXECUTION PIPELINE · 执行管线', title: '执行队列', description: '用真实关键词、文章和同步状态查看当前执行进度。' },
    risk: { eyebrow: 'RISK GOVERNANCE · 风险治理', title: '风险与策略边界', description: '把真实后端状态转成需要确认、可继续执行和已阻塞的动作。' },
  }
  return headers[view]
}

function CommandView({ sites, keywords, articles, analytics, opportunities, onOpen }: { sites: Site[]; keywords: Keyword[]; articles: Array<{ status: string }>; analytics: ReturnType<typeof useAnalyticsOverview>['data']; opportunities: Opportunity[]; onOpen: (item: Opportunity) => void }) {
  const pending = keywords.filter((keyword) => !['published', 'blocked', 'dropped'].includes(keyword.status)).length
  const high = keywords.filter((keyword) => ['high', 'P0', 'P1'].includes(keyword.priority)).length
  const failedSync = analytics?.syncLog.filter((item) => item.status === 'failed').length || 0
  return <>
    <div className="agent-kpi-row">
      <MetricCard tone="gold" label="管理站点" value={formatNumber(sites.length)} sub="来自后端站点配置" icon="domain" />
      <MetricCard tone="pink" label="已加载关键词" value={formatNumber(keywords.length)} sub={`${formatNumber(high)} 个高优先级 · 当前批次`} icon="key" />
      <MetricCard tone="green" label="待处理关键词" value={formatNumber(pending)} sub={`${formatNumber(articles.length)} 篇文章已保存`} icon="edit_document" />
      <MetricCard tone="blue" label="同步异常" value={formatNumber(failedSync)} sub={analytics?.selectedSiteId ? '当前站点最近同步' : '请选择站点查看'} icon="sync_problem" />
    </div>
    <div className="agent-main-grid">
      <div className="card"><div className="card__title"><span>优先机会队列</span><small>{opportunities.length ? '真实数据' : '暂无后端机会数据'}</small></div><OpportunityTable opportunities={opportunities} onOpen={onOpen} /></div>
      <div className="agent-rail"><AgentLoop keywords={keywords} articles={articles} /><div className="card"><div className="card__title"><span>需要人工判断</span><span className="tag tag--pink">{failedSync} 项</span></div><div className="agent-risk-list">{failedSync ? <div className="agent-risk-item"><span className="agent-risk-dot" /><div><b>数据同步存在失败记录</b><p>请进入同步日志查看具体错误。</p></div></div> : <div className="agent-empty">当前没有后端报告的同步异常。</div>}</div></div></div>
    </div>
  </>
}

function ExecutionView({ keywords, articles }: { keywords: Keyword[]; articles: Array<{ title: string; status: string; site_label?: string; updated_at?: string }> }) {
  const analyzed = keywords.filter((keyword) => keyword.ai_review?.strategy).length
  return <div className="agent-section-grid"><div className="card"><div className="card__title"><span>文章执行状态</span><small>来自 seo_agent.articles</small></div><div className="agent-queue">{articles.map((article) => <div className="agent-queue-row" key={`${article.title}-${article.updated_at}`}><span className={`tag tag--${article.status === 'published' ? 'green' : article.status === 'failed' ? 'pink' : 'gold'}`}>{article.status}</span><div><b>{article.title}</b><small>{article.site_label || '未分配站点'} · {formatDate(article.updated_at)}</small></div></div>)}{!articles.length && <div className="agent-empty">暂无已保存文章。可以在“内容策略”中从关键词启动生文流程。</div>}</div></div><div className="card"><div className="card__title"><span>关键词执行进度</span><span className="tag tag--blue">AI 策略 {analyzed}</span></div><ProgressRow label="已分析策略" value={analyzed} total={keywords.length} /><ProgressRow label="已生成文章" value={keywords.filter((keyword) => ['written', 'article_drafted', 'published'].includes(keyword.status)).length} total={keywords.length} /><ProgressRow label="已发布" value={keywords.filter((keyword) => keyword.status === 'published').length} total={keywords.length} /></div></div>
}

function RiskView({ sites, keywords, analytics }: { sites: Site[]; keywords: Keyword[]; analytics: ReturnType<typeof useAnalyticsOverview>['data'] }) {
  const blocked = keywords.filter((keyword) => keyword.status === 'blocked')
  const notReady = sites.filter((site) => !site.publish_ready)
  const failed = analytics?.syncLog.filter((item) => item.status === 'failed') || []
  return <><div className="agent-kpi-row"><MetricCard tone="pink" label="阻塞关键词" value={formatNumber(blocked.length)} sub="后端状态为 blocked" icon="block" /><MetricCard tone="gold" label="发布未就绪站点" value={formatNumber(notReady.length)} sub="按站点配置判断" icon="publish" /><MetricCard tone="blue" label="同步失败" value={formatNumber(failed.length)} sub="当前站点最近日志" icon="error" /></div><div className="agent-section-grid"><div className="card"><div className="card__title"><span>真实风险记录</span><small>无数据不展示假风险</small></div><div className="agent-queue">{blocked.map((keyword) => <div className="agent-queue-row" key={keyword.id}><span className="tag tag--pink">阻塞</span><div><b>{keyword.keyword}</b><small>{keyword.reason || '后端未提供原因'}</small></div></div>)}{failed.map((entry) => <div className="agent-queue-row" key={entry.id}><span className="tag tag--pink">同步失败</span><div><b>{entry.source_type.toUpperCase()} · {formatDate(entry.started_at)}</b><small>{entry.error_message || '后端未提供错误信息'}</small></div></div>)}{!blocked.length && !failed.length && <div className="agent-empty">当前没有后端返回的阻塞关键词或同步失败记录。</div>}</div></div><div className="card"><div className="card__title"><span>自动化边界</span><span className="tag tag--green">当前后端能力</span></div><p className="agent-copy">数据同步、关键词分析、生文和发布都有后端接口；站群新增、外链采购、批量结构变更等动作目前不在现有接口中，因此本页不展示为可执行按钮。</p></div></div></>
}

function gscOpportunity(row: GscQuery): Opportunity {
  return { id: `gsc-${row.query}`, type: 'GSC 机会', tone: 'blue', title: row.query, asset: '当前站点', reason: `GSC 已有 ${formatNumber(row.impressions)} 次展示，CTR ${(row.ctr * 100).toFixed(2)}%。`, impact: `${formatNumber(row.clicks)} 点击` }
}

function OpportunityTable({ opportunities, onOpen }: { opportunities: Opportunity[]; onOpen: (item: Opportunity) => void }) {
  if (!opportunities.length) return <div className="agent-empty">暂无后端机会数据。先导入关键词或同步 GSC。</div>
  return <div className="agent-table-wrap"><table className="agent-table"><thead><tr><th>机会</th><th>资产范围</th><th>AI/数据依据</th><th>影响</th><th /></tr></thead><tbody>{opportunities.map((item) => <tr key={item.id} onClick={() => onOpen(item)}><td><span className={`tag tag--${item.tone}`}>{item.type}</span><b>{item.title}</b></td><td>{item.asset}</td><td>{item.reason}</td><td className="agent-strong">{item.impact}</td><td><button type="button" className="btn btn--ghost btn--xs" onClick={(event) => { event.stopPropagation(); onOpen(item) }}>查看</button></td></tr>)}</tbody></table></div>
}

function AgentLoop({ keywords, articles }: { keywords: Keyword[]; articles: Array<{ status: string }> }) {
  const analyzed = keywords.filter((keyword) => keyword.ai_review?.strategy).length
  return <div className="card"><div className="card__title"><span>Agent 当前循环</span><small>真实后端状态</small></div><div className="agent-loop">{[['读取资产与数据', `${keywords.length} 个关键词 · ${articles.length} 篇文章`, true], ['分析并排序机会', `${analyzed} 个关键词已有 AI 策略`, analyzed > 0], ['等待执行确认', '生文和发布仍通过现有页面操作', articles.length > 0], ['执行后复盘', '等待 GSC / GA4 数据积累', false]].map(([title, desc, done], index) => <div className="agent-loop-step" key={String(title)}><span className={`agent-loop-dot ${done ? 'done' : index === 2 ? 'current' : ''}`}>{done ? '✓' : index + 1}</span><div><b>{title}</b><p>{desc}</p></div></div>)}</div></div>
}

function ProgressRow({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total ? Math.min(100, Math.round((value / total) * 100)) : 0
  return <div className="agent-progress-row"><div><span>{label}</span><b>{value} / {total}</b></div><div className="agent-progress"><i style={{ width: `${pct}%` }} /></div></div>
}

function MetricCard({ tone, label, value, sub, icon }: { tone: Tone; label: string; value: string; sub: string; icon: string }) {
  return <div className={`agent-metric agent-metric--${tone}`}><div className="agent-metric-head"><span>{label}</span><span className="msr">{icon}</span></div><strong>{value}</strong><small>{sub}</small></div>
}

function OpportunityDrawer({ opportunity, onClose }: { opportunity: Opportunity; onClose: () => void }) {
  return <div className="agent-drawer" role="presentation" onClick={(event) => event.target === event.currentTarget && onClose()}><aside className="agent-drawer-panel"><div className="agent-drawer-head"><div><div className="agent-eyebrow">机会详情 · 后端数据</div><h2>{opportunity.title}</h2></div><button type="button" className="icon-btn" onClick={onClose}>close</button></div><div className="agent-drawer-section"><h3>作用资产</h3><p>{opportunity.asset}</p></div><div className="agent-drawer-section"><h3>数据依据</h3><p>{opportunity.reason}</p></div><div className="agent-drawer-section"><h3>当前影响</h3><p>{opportunity.impact}</p></div><div className="agent-drawer-actions"><button type="button" className="btn btn--ghost" onClick={onClose}>关闭</button></div></aside></div>
}
