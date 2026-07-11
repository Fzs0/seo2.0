import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { BarDecor, PriorityDecor, Sparkline } from '@/components/Charts'
import { DataGuard, StateBlock } from '@/components/StateBlock'
import { useDashboardOverview } from '@/hooks/useData'
import type { Keyword } from '@/types/domain'

const statusLabels: Record<string, string> = {
  planned: '待分析',
  analyzing: '分析中',
  ready: '待生成 Brief',
  brief_ready: 'Brief 已就绪',
  article_drafted: '草稿已生成',
  published: '已发布',
  blocked: '已阻塞',
  imported: '已导入',
  analyzed: '已分析',
  queued: '排队中',
  written: '已写作',
  reviewed: '已审核',
  hold: '暂缓',
  dropped: '已放弃',
}

const priorityLabels: Record<string, string> = {
  high: '高优先级',
  'medium-high': '中高优先级',
  medium: '中优先级',
  'medium-low': '中低优先级',
  low: '低优先级',
  P0: 'P0 最高优先级',
  P1: 'P1 高优先级',
  P2: 'P2 中优先级',
  P3: 'P3 低优先级',
  Hold: '暂缓',
}

function asNumber(value: number | string | null | undefined) {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function formatNumber(value: number | string | null | undefined) {
  if (value == null || value === '') return '—'
  return new Intl.NumberFormat('zh-CN').format(asNumber(value))
}

function formatDateTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function keywordColor(index: number) {
  return (['gold', 'green', 'blue'] as const)[index % 3]
}

export function DashboardPage({ project: _project }: { project: string }) {
  const [siteId, setSiteId] = useState<string>()
  const overview = useDashboardOverview(siteId)
  const data = overview.data

  useEffect(() => {
    if (!siteId && data?.selectedSiteId) setSiteId(data.selectedSiteId)
  }, [data?.selectedSiteId, siteId])

  const sites = data?.sites ?? []
  const keywords = data?.keywords ?? []
  const selectedSite = sites.find((site) => site.id === (data?.selectedSiteId || siteId))
  const highPriorityCount = keywords.filter((keyword) => ['high', 'P0', 'P1'].includes(keyword.priority)).length
  const pendingCount = keywords.filter((keyword) => !['published', 'blocked', 'dropped'].includes(keyword.status)).length
  const recentSync = data?.syncLog.slice(0, 4) ?? []
  const latestSync = recentSync[0]
  const decisionKeywords = [...keywords].sort((a, b) => asNumber(b.score) - asNumber(a.score)).slice(0, 3)

  return (
    <>
      <section className="page" data-screen-label="决策总览">
        <div className="page-heading-row">
          <div>
            <h1>SEO 决策总览</h1>
            <p>基于站点、关键词和 Google 数据查看当前工作状态。</p>
          </div>
          <label className="select-control">
            <span>当前站点</span>
            <select
              value={data?.selectedSiteId || ''}
              onChange={(event) => setSiteId(event.target.value || undefined)}
              disabled={!sites.length}
            >
              {!sites.length && <option value="">暂无站点</option>}
              {sites.map((site) => (
                <option value={site.id} key={site.id}>
                  {site.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <DataGuard loading={overview.loading} error={overview.error} empty={!data}>
          <div className="kpi-row">
            <Kpi color="gold" label="关键词总数" value={formatNumber(keywords.length)}>
              <BarDecor width={84} height={34} gradient="gold" />
            </Kpi>
            <Kpi color="pink" label="待处理关键词" value={formatNumber(pendingCount)}>
              <Sparkline values={[6, 9, 7, 13, 11, 19, 22, 26]} width={90} height={30} color="#d77e6c" />
            </Kpi>
            <Kpi color="green" label="高优先级" value={formatNumber(highPriorityCount)}>
              <PriorityDecor width={70} height={40} />
            </Kpi>
            <Kpi color="blue" label="数据状态" value={data?.dashboard?.configured ? '已配置' : '未配置'} small>
              <Sparkline values={[8, 10, 9, 13, 12, 15, 14]} width={90} height={24} color="#8faecb" />
            </Kpi>
          </div>

          {decisionKeywords.length ? (
            <div className="decision-list">
              {decisionKeywords.map((keyword, index) => (
                <DecisionRow keyword={keyword} index={index} key={keyword.id} />
              ))}
            </div>
          ) : (
            <StateBlock icon="key" title="暂无关键词" hint="请先导入或分析关键词。" />
          )}

          <div className="pipeline">
            <PipelineItem
              color="blue"
              icon="key"
              label="关键词库"
              sub={`${formatNumber(keywords.length)} 条记录，${formatNumber(pendingCount)} 条待处理`}
            />
            <div className="pipeline__divider" />
            <PipelineItem
              color="gold"
              icon="sync"
              label="Google 数据"
              sub={`${data?.sources.length ?? 0} 个数据源，最近同步 ${latestSync ? latestSync.status : '暂无'}`}
            />
            <div className="pipeline__divider" />
            <PipelineItem
              color="green"
              icon="apartment"
              label="当前站点"
              sub={selectedSite ? `${selectedSite.name} · ${selectedSite.domain}` : '尚未选择站点'}
            />
          </div>
        </DataGuard>
      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="ai-card__head">
            <span className="msr msr-fill">monitoring</span>
            当前站点数据
          </div>
          <div className="ai-card__sub">{selectedSite?.name || '未选择站点'}</div>
          <div className="rail-stats">
            <RailStat label="GSC 点击" value={formatNumber(data?.dashboard?.gsc28d?.clicks)} />
            <RailStat label="GSC 展示" value={formatNumber(data?.dashboard?.gsc28d?.impressions)} />
            <RailStat label="GA4 会话" value={formatNumber(data?.dashboard?.ga428d?.sessions)} />
            <RailStat label="GA4 用户" value={formatNumber(data?.dashboard?.ga428d?.users)} />
          </div>
          <div className="timeline">
            {recentSync.length ? (
              recentSync.map((entry, index) => (
                <TimelineItem
                  key={entry.id}
                  time={formatDateTime(entry.started_at)}
                  icon={entry.status === 'success' ? 'check_circle' : 'error'}
                  color={entry.status === 'success' ? 'green' : 'pink'}
                  title={`${entry.source_type.toUpperCase()} 同步 ${entry.status}`}
                  desc={`${formatNumber(entry.rows_written)} 条记录，触发方式：${entry.trigger}`}
                  last={index === recentSync.length - 1}
                />
              ))
            ) : (
              <div className="timeline__desc">暂无同步日志</div>
            )}
          </div>
          <div className="btn--block btn--block-static" role="note">
            最近同步：{latestSync ? formatDateTime(latestSync.started_at) : '暂无'}
          </div>
        </div>

        <div className="suggest">
          <span className="msr msr-fill suggest__icon">info</span>
          <div className="suggest__body">
            <div className="suggest__title">数据说明</div>
            <div className="suggest__desc">
              本页只展示后端已提供的站点、关键词、Google 数据和同步日志。
            </div>
          </div>
        </div>
      </aside>
    </>
  )
}

function Kpi({
  color,
  label,
  value,
  small,
  children,
}: {
  color: 'gold' | 'pink' | 'green' | 'blue'
  label: string
  value: string
  small?: boolean
  children: ReactNode
}) {
  return (
    <div className={`kpi kpi--${color}`}>
      <div className="kpi__head">
        <span className="kpi__label">{label}</span>
        <span className="msr kpi__icon">bar_chart</span>
      </div>
      <div className={`kpi__value${small ? ' kpi__value--sm' : ''}`}>{value}</div>
      <div className="kpi__delta">来自当前后端数据</div>
      <div className="kpi__spark">{children}</div>
    </div>
  )
}

function DecisionRow({ keyword, index }: { keyword: Keyword; index: number }) {
  const color = keywordColor(index)
  return (
    <div className="decision-row">
      <div className="decision-row__num">
        <div className={`decision-row__num-badge decision-row__num-badge--${color}`}>{index + 1}</div>
        <div className={`decision-row__num-icon decision-row__num-icon--${color}`}>
          <span className="msr">{keyword.status === 'published' ? 'check_circle' : 'key'}</span>
        </div>
      </div>
      <div className="decision-row__main">
        <div className="decision-row__eyebrow">关键词</div>
        <div className="decision-row__title">{keyword.keyword}</div>
        <div className="decision-row__desc">{keyword.reason || keyword.page_role || '暂无说明'}</div>
        <span className={`decision-row__tag decision-row__tag--${color}`}>
          {priorityLabels[keyword.priority] || keyword.priority}
        </span>
      </div>
      <Metric label="搜索量" value={formatNumber(keyword.volume)} sub="月搜索量" />
      <Metric label="SEO 分数" value={asNumber(keyword.score).toFixed(1)} sub={keyword.intent} />
      <Metric
        label="状态"
        value={statusLabels[keyword.status] || keyword.status}
        sub={keyword.content_action}
        danger={keyword.status === 'blocked'}
      />
      <div className="decision-row__actions">
        <div className="tag tag--gold">{keyword.assigned_site_label || '未分配站点'}</div>
      </div>
    </div>
  )
}

function Metric({ label, value, sub, danger }: { label: string; value: string; sub: string; danger?: boolean }) {
  return (
    <div className="decision-row__metric">
      <div className="decision-row__metric-label">{label}</div>
      <div className={`decision-row__metric-value${danger ? ' decision-row__metric-value--danger' : ''}`}>{value}</div>
      <div className="decision-row__metric-sub">{sub}</div>
    </div>
  )
}

function PipelineItem({ color, icon, label, sub }: { color: string; icon: string; label: string; sub: string }) {
  return (
    <div className={`pipeline__item pipeline__item--${color}`}>
      <span className="msr">{icon}</span>
      <div>
        <div className="pipeline__label">{label}</div>
        <div className="pipeline__sub">{sub}</div>
      </div>
    </div>
  )
}

function RailStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rail-stat">
      <div className="rail-stat__label">{label}</div>
      <div className="rail-stat__value">{value}</div>
    </div>
  )
}

function TimelineItem({
  time,
  icon,
  color,
  title,
  desc,
  last,
}: {
  time: string
  icon: string
  color: 'pink' | 'green'
  title: string
  desc: string
  last?: boolean
}) {
  return (
    <div className="timeline__item">
      <div className="timeline__time">{time}</div>
      <div className="timeline__rail">
        <div className={`timeline__dot ai-list__dot--${color}`}>
          <span className="msr">{icon}</span>
        </div>
        {!last && <div className="timeline__line" />}
      </div>
      <div className="timeline__body">
        <div className="timeline__title">{title}</div>
        <div className="timeline__desc">{desc}</div>
      </div>
    </div>
  )
}
