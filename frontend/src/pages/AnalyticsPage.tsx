import { useEffect, useState } from 'react'
import { DataGuard, StateBlock } from '@/components/StateBlock'
import { useBusinessScope } from '@/businessScope'
import { triggerAnalyticsSync, useAnalyticsOverview } from '@/data/analytics'
function asNumber(value: number | string | null | undefined) {
  const n = Number(value ?? 0)
  return Number.isFinite(n) ? n : 0
}

function formatNumber(value: number | string | null | undefined) {
  return new Intl.NumberFormat('zh-CN').format(asNumber(value))
}

function formatPct(value: number | string | null | undefined, digits = 1) {
  return `${(asNumber(value) * 100).toFixed(digits)}%`
}

function formatDate(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function formatDuration(value: number | string | null | undefined) {
  const seconds = Math.round(asNumber(value))
  if (!seconds) return '—'
  return `${Math.floor(seconds / 60)}分 ${seconds % 60}秒`
}

export function AnalyticsPage() {
  const { businessId, businessSites } = useBusinessScope()
  const [siteId, setSiteId] = useState<string>()
  const [refreshKey, setRefreshKey] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string>()
  const overview = useAnalyticsOverview(siteId, refreshKey)
  const data = overview.data

  useEffect(() => {
    if (!businessSites.some((site) => site.id === siteId)) setSiteId(businessSites[0]?.id)
  }, [businessId, businessSites, siteId])

  const sites = (data?.sites ?? []).filter((site) => businessSites.some((businessSite) => businessSite.id === site.id))
  const dashboard = data?.dashboard
  const gscTrend = dashboard?.gsc7dTrend ?? []
  const ga4Trend = dashboard?.ga47dTrend ?? []
  const channelTotal = (data?.channels ?? []).reduce((sum, item) => sum + asNumber(item.sessions), 0)
  const activeSiteId = siteId
  const isCurrentBusinessData = Boolean(siteId && data?.selectedSiteId === siteId)

  async function handleSync() {
    if (!activeSiteId || syncing) return
    setSyncing(true)
    setSyncMessage('正在同步当前站点最近 7 天数据…')
    try {
      const result = await triggerAnalyticsSync(activeSiteId, 7)
      const rows = result.results?.reduce((sum, item) => sum + asNumber(item.rows_written), 0) ?? 0
      setSyncMessage(result.ok ? `同步成功，写入 ${formatNumber(rows)} 行。` : '同步完成，但有部分数据源失败，请查看同步日志。')
      setRefreshKey((key) => key + 1)
    } catch (error) {
      setSyncMessage(error instanceof Error ? error.message : '同步失败')
    } finally {
      setSyncing(false)
    }
  }

  return (
    <section className="page" data-screen-label="数据分析">
      <div className="page-heading-row">
        <div>
          <h1>数据分析</h1>
          <p>查看当前站点的 GSC、GA4 历史表现、机会词、页面表现和同步日志。</p>
          {syncMessage && <p>{syncMessage}</p>}
        </div>
        <div style={{ display: 'flex', alignItems: 'end', gap: 10 }}>
          <label className="select-control">
            <span>当前站点</span>
            <select
              value={siteId || ''}
              onChange={(event) => {
                setSiteId(event.target.value || undefined)
                setSyncMessage(undefined)
              }}
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
          <button className="btn btn--primary" type="button" onClick={handleSync} disabled={!activeSiteId || syncing}>
            <span className="msr">{syncing ? 'progress_activity' : 'sync'}</span>
            {syncing ? '同步中' : '同步数据'}
          </button>
        </div>
      </div>

      <div className="card automation-panel">
        <div>
          <strong>策略效果闭环</strong>
          <div className="automation-panel__meta">这里保留站点级 GSC / GA4 分析；策略级基线、检查点、效果结论和下次检查请从左侧进入“今日策略 → 5. 效果观察”查看。</div>
        </div>
      </div>

      <DataGuard loading={overview.loading} error={overview.error} empty={!isCurrentBusinessData} emptyTitle="当前业务暂无数据分析数据">
        {data && isCurrentBusinessData && (
          <>
            <div className="hero-grid">
              <Kpi icon="ads_click" tone="gold" label="GSC 点击 28 天" value={formatNumber(dashboard?.gsc28d?.clicks)} sub={`CTR ${formatPct(dashboard?.gsc28d?.ctr)}`} />
              <Kpi icon="visibility" tone="pink" label="GSC 展示 28 天" value={formatNumber(dashboard?.gsc28d?.impressions)} sub={`平均排名 ${asNumber(dashboard?.gsc28d?.avg_position).toFixed(1)}`} />
              <Kpi icon="group" tone="blue" label="GA4 会话 28 天" value={formatNumber(dashboard?.ga428d?.sessions)} sub={`参与率 ${formatPct(dashboard?.ga428d?.engagement_rate)}`} />
              <Kpi icon="shopping_bag" tone="green" label="GA4 转化 28 天" value={formatNumber(dashboard?.ga428d?.conversions)} sub={`收益 ${formatNumber(Math.round(asNumber(dashboard?.ga428d?.revenue)))}`} />
            </div>

            <div className="content-grid-5">
              <ReviewStat label="GA4 用户" value={formatNumber(dashboard?.ga428d?.users)} sub="总用户" />
              <ReviewStat label="GA4 新用户" value={formatNumber(dashboard?.ga428d?.new_users)} sub="新用户" />
              <ReviewStat label="页面浏览" value={formatNumber(dashboard?.ga428d?.pageviews)} sub="screen page views" />
              <ReviewStat label="平均停留" value={formatDuration(dashboard?.ga428d?.avg_session_duration)} sub="平均会话时长" />
              <ReviewStat label="跳出率" value={formatPct(dashboard?.ga428d?.bounce_rate)} sub="低越好" />
            </div>

            <div className="card">
              <CardTitle icon="login" tone="green" title="GA4 落地页表现" tag="入口页 · 最近 28 天" />
              {data.landingPages.length ? (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>落地页</th>
                      <th className="tbl-num">会话</th>
                      <th className="tbl-num">用户</th>
                      <th className="tbl-num">浏览量</th>
                      <th className="tbl-num">停留</th>
                      <th className="tbl-num">参与率</th>
                      <th className="tbl-num">跳出率</th>
                      <th className="tbl-num">转化</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.landingPages.map((page) => (
                      <tr key={page.landing_page}>
                        <td className="tbl-strong" style={{ maxWidth: 280, wordBreak: 'break-all' }}>{page.landing_page}</td>
                        <td className="tbl-num">{formatNumber(page.sessions)}</td>
                        <td className="tbl-num">{formatNumber(page.users)}</td>
                        <td className="tbl-num">{formatNumber(page.pageviews)}</td>
                        <td className="tbl-num">{formatDuration(page.avg_session_duration)}</td>
                        <td className="tbl-num">{formatPct(page.engagement_rate)}</td>
                        <td className="tbl-num">{formatPct(page.bounce_rate)}</td>
                        <td className="tbl-num">{formatNumber(page.conversions)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <StateBlock icon="login" title="暂无落地页数据" hint="完成一次包含 GA4 落地页维度的同步后会展示" />
              )}
            </div>

            <div className="serp-grid">
              <BreakdownCard title="GSC 国家分布" icon="public" rows={data.gscCountries} emptyHint="GSC 同步后按国家聚合展示" />
              <BreakdownCard title="GSC 设备分布" icon="devices" rows={data.gscDevices} emptyHint="GSC 同步后按设备聚合展示" />
            </div>

            <div className="serp-grid">
              <div className="card">
                <CardTitle icon="query_stats" tone="gold" title="GSC 7 天趋势" tag="点击 / 展示" />
                <Trend
                  a={gscTrend.map((item) => asNumber(item.clicks))}
                  b={gscTrend.map((item) => asNumber(item.impressions))}
                  labels={gscTrend.map((item) => String(item.date))}
                />
              </div>
              <div className="card">
                <CardTitle icon="monitoring" tone="blue" title="GA4 7 天趋势" tag="会话 / 用户" />
                <Trend
                  a={ga4Trend.map((item) => asNumber(item.sessions))}
                  b={ga4Trend.map((item) => asNumber(item.users))}
                  labels={ga4Trend.map((item) => String(item.date))}
                  colorA="#5f84a8"
                  colorB="#9bb6cf"
                />
              </div>
            </div>

            <div className="card">
              <CardTitle icon="pie_chart" tone="blue" title="GA4 渠道分布" tag="最近 28 天" />
              {data.channels.length ? (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>渠道</th>
                      <th className="tbl-num">会话</th>
                      <th className="tbl-num">用户</th>
                      <th className="tbl-num">浏览量</th>
                      <th className="tbl-num">参与率</th>
                      <th className="tbl-num">停留</th>
                      <th className="tbl-num">跳出率</th>
                      <th className="tbl-num">转化</th>
                      <th className="tbl-num">收益</th>
                      <th>占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.channels.map((channel) => {
                      const pct = channelTotal ? (asNumber(channel.sessions) / channelTotal) * 100 : 0
                      return (
                        <tr key={channel.channel}>
                          <td className="tbl-strong">{channel.channel}</td>
                          <td className="tbl-num">{formatNumber(channel.sessions)}</td>
                          <td className="tbl-num">{formatNumber(channel.users)}</td>
                          <td className="tbl-num">{formatNumber(channel.pageviews)}</td>
                          <td className="tbl-num">{formatPct(channel.engagement_rate)}</td>
                          <td className="tbl-num">{formatDuration(channel.avg_session_duration)}</td>
                          <td className="tbl-num">{formatPct(channel.bounce_rate)}</td>
                          <td className="tbl-num">{formatNumber(channel.conversions)}</td>
                          <td className="tbl-num">{formatNumber(Math.round(asNumber(channel.revenue)))}</td>
                          <td style={{ minWidth: 150 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <div className="bar" style={{ flex: 1 }}>
                                <div className="bar__fill bar__fill--blue" style={{ width: `${pct}%` }} />
                              </div>
                              <span style={{ fontSize: 11, color: 'var(--ink-500)' }}>{pct.toFixed(1)}%</span>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <StateBlock icon="pie_chart" title="暂无渠道数据" hint="GA4 同步完成后会展示渠道分布" />
              )}
            </div>

            <div className="serp-grid">
              <div className="card">
                <CardTitle icon="search" tone="gold" title="GSC 机会关键词" tag="高展示 · 接近首页" />
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>查询</th>
                      <th className="tbl-num">展示</th>
                      <th className="tbl-num">点击</th>
                      <th className="tbl-num">CTR</th>
                      <th className="tbl-num">平均排名</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.opportunities.map((item) => (
                      <tr key={item.query}>
                        <td className="tbl-strong">{item.query}</td>
                        <td className="tbl-num">{formatNumber(item.impressions)}</td>
                        <td className="tbl-num">{formatNumber(item.clicks)}</td>
                        <td className="tbl-num">{formatPct(item.ctr, 2)}</td>
                        <td className="tbl-num">{asNumber(item.avgPosition).toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!data.opportunities.length && <StateBlock icon="search" title="暂无机会词" hint="提高同步覆盖后会自动出现" />}
              </div>

              <div className="card">
                <CardTitle icon="article" tone="green" title="GSC 页面表现" tag="按点击排序" />
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>页面</th>
                      <th className="tbl-num">点击</th>
                      <th className="tbl-num">展示</th>
                      <th className="tbl-num">排名</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.pages.map((page) => (
                      <tr key={page.page}>
                        <td className="tbl-strong" style={{ maxWidth: 260, wordBreak: 'break-all' }}>{page.page}</td>
                        <td className="tbl-num">{formatNumber(page.clicks)}</td>
                        <td className="tbl-num">{formatNumber(page.impressions)}</td>
                        <td className="tbl-num">{asNumber(page.avg_position).toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!data.pages.length && <StateBlock icon="article" title="暂无页面数据" hint="GSC 页面数据同步后会展示" />}
              </div>
            </div>

            <div className="card">
              <CardTitle icon="history" tone="green" title="同步日志" tag="最近 8 条" />
              <table className="tbl">
                <thead>
                  <tr>
                    <th>来源</th>
                    <th>状态</th>
                    <th className="tbl-num">写入</th>
                    <th className="tbl-num">耗时</th>
                    <th>开始时间</th>
                    <th>错误</th>
                  </tr>
                </thead>
                <tbody>
                  {data.syncLog.map((log) => (
                    <tr key={log.id}>
                      <td className="tbl-strong">{log.source_type.toUpperCase()}</td>
                      <td>{log.status}</td>
                      <td className="tbl-num">{formatNumber(log.rows_written)}</td>
                      <td className="tbl-num">{Math.round(asNumber(log.duration_ms) / 1000)}s</td>
                      <td>{formatDate(log.started_at)}</td>
                      <td style={{ color: 'var(--ink-500)' }}>{log.error_message || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data.syncLog.length && <StateBlock icon="history" title="暂无同步日志" hint="执行同步后会展示历史记录" />}
            </div>
          </>
        )}
      </DataGuard>
    </section>
  )
}

function ReviewStat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="eyebrow">{label}</div>
      <div style={{ fontSize: 21, fontWeight: 750, marginTop: 6 }}>{value}</div>
      <div className="muted" style={{ marginTop: 3 }}>{sub}</div>
    </div>
  )
}

function BreakdownCard({
  title,
  icon,
  rows,
  emptyHint,
}: {
  title: string
  icon: string
  rows: Array<{ dimension: string; clicks: number; impressions: number; ctr: number; avg_position: number }>
  emptyHint: string
}) {
  return (
    <div className="card">
      <CardTitle icon={icon} tone="blue" title={title} tag="最近 28 天" />
      {rows.length ? (
        <table className="tbl">
          <thead>
            <tr>
              <th>维度</th>
              <th className="tbl-num">点击</th>
              <th className="tbl-num">展示</th>
              <th className="tbl-num">CTR</th>
              <th className="tbl-num">平均排名</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.dimension}>
                <td className="tbl-strong">{row.dimension}</td>
                <td className="tbl-num">{formatNumber(row.clicks)}</td>
                <td className="tbl-num">{formatNumber(row.impressions)}</td>
                <td className="tbl-num">{formatPct(row.ctr, 2)}</td>
                <td className="tbl-num">{asNumber(row.avg_position).toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <StateBlock icon={icon} title="暂无分布数据" hint={emptyHint} />
      )}
    </div>
  )
}

function Kpi({ icon, tone, label, value, sub }: { icon: string; tone: string; label: string; value: string; sub: string }) {
  return (
    <div className="hero-grid__kpi">
      <div className="hero-grid__head">
        <span className="hero-grid__label">{label}</span>
        <span className={`msr kpi__icon kpi__icon--${tone}`}>{icon}</span>
      </div>
      <div className="hero-grid__value">{value}</div>
      <div className="hero-grid__delta">{sub}</div>
    </div>
  )
}

function CardTitle({ icon, tone, title, tag }: { icon: string; tone: string; title: string; tag: string }) {
  return (
    <div className="card__title">
      <div className="card__title-icon">
        <span className={`msr kpi__icon--${tone}`}>{icon}</span>
        {title}
      </div>
      <span className="card__title-tag">{tag}</span>
    </div>
  )
}

function Trend({
  a,
  b,
  labels,
  colorA = '#c9a03c',
  colorB = '#d9b25c',
}: {
  a: number[]
  b: number[]
  labels: string[]
  colorA?: string
  colorB?: string
}) {
  if (a.length < 2) return <StateBlock icon="show_chart" title="暂无趋势数据" hint="同步历史数据后会展示 7 天趋势" />

  const w = 540
  const h = 140
  const padding = { left: 34, right: 12, top: 16, bottom: 22 }
  const chartW = w - padding.left - padding.right
  const chartH = h - padding.top - padding.bottom
  const stepX = chartW / (a.length - 1)
  const max = Math.max(...a, ...b)
  const min = Math.min(...a, ...b)
  const range = max - min || 1
  const toPoints = (values: number[]) =>
    values.map((value, index) => [
      padding.left + index * stepX,
      padding.top + chartH - ((value - min) / range) * chartH,
    ] as const)
  const ptsA = toPoints(a)
  const ptsB = toPoints(b)

  return (
    <div className="chart-box">
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        {[max, (max + min) / 2, min].map((value, index) => {
          const y = padding.top + (chartH * index) / 2
          return (
            <g key={index}>
              <line x1={padding.left} x2={padding.left + chartW} y1={y} y2={y} stroke="rgba(23,22,20,0.06)" strokeDasharray="3 3" />
              <text x={padding.left - 6} y={y + 3} textAnchor="end" fontSize={9} fill="var(--ink-400)">
                {Math.round(value).toLocaleString()}
              </text>
            </g>
          )
        })}
        {labels.map((label, index) => (
          <text key={`${label}-${index}`} x={padding.left + index * stepX} y={h - 6} textAnchor="middle" fontSize={9} fill="var(--ink-400)">
            {label.slice(5, 10)}
          </text>
        ))}
        <polyline points={ptsB.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')} fill="none" stroke={colorB} strokeWidth={1.5} strokeDasharray="4 3" opacity={0.6} />
        <polyline points={ptsA.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')} fill="none" stroke={colorA} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
        {ptsA.map(([x, y], index) => (
          <circle key={index} cx={x} cy={y} r={3} fill={colorA} />
        ))}
      </svg>
    </div>
  )
}
