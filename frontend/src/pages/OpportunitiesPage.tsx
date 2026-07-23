import { useEffect, useState } from 'react'
import { DataGuard, StateBlock } from '@/components/StateBlock'
import { useAnalyticsOverview } from '@/data/analytics'
function number(value: number | string | null | undefined) {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function formatNumber(value: number | string | null | undefined) {
  return new Intl.NumberFormat('zh-CN').format(number(value))
}

export function OpportunitiesPage() {
  const [siteId, setSiteId] = useState<string>()
  const overview = useAnalyticsOverview(siteId)
  const data = overview.data
  const opportunities = data?.opportunities ?? []
  const impressions = opportunities.reduce((sum, item) => sum + number(item.impressions), 0)
  const clicks = opportunities.reduce((sum, item) => sum + number(item.clicks), 0)
  const ctr = impressions ? (clicks / impressions) * 100 : 0

  useEffect(() => {
    if (!siteId && data?.selectedSiteId) setSiteId(data.selectedSiteId)
  }, [data?.selectedSiteId, siteId])

  return (
    <section className="page" data-screen-label="机会洞察">
      <div className="page-heading-row">
        <div>
          <h1>机会洞察</h1>
          <p>展示 GSC 中已有展现、排名接近首页的查询词；数据不足时保持空状态，不生成推测机会。</p>
        </div>
        <label className="select-control">
          <span>当前站点</span>
          <select value={data?.selectedSiteId || ''} onChange={(event) => setSiteId(event.target.value || undefined)} disabled={!data?.sites.length}>
            {!data?.sites.length && <option value="">暂无站点</option>}
            {data?.sites.map((site) => <option value={site.id} key={site.id}>{site.name}</option>)}
          </select>
        </label>
      </div>

      <DataGuard loading={overview.loading} error={overview.error} empty={!data} emptyTitle="暂无机会数据" emptyHint="请先配置站点并同步 GSC 数据" minHeight={180}>
        {data && (
          <>
            <div className="kpi-row">
              <Metric label="机会查询" value={formatNumber(opportunities.length)} sub="GSC 接近首页" tone="gold" />
              <Metric label="展示" value={formatNumber(impressions)} sub="当前机会集合" tone="blue" />
              <Metric label="点击" value={formatNumber(clicks)} sub="当前机会集合" tone="green" />
              <Metric label="加权 CTR" value={`${ctr.toFixed(2)}%`} sub="点击 / 展示" tone="pink" />
            </div>

            <div className="card">
              <div className="card__title">
                <div className="card__title-icon"><span className="msr">query_stats</span>GSC 机会查询</div>
                <span className="card__title-tag">展示 ≥ 100 · 排名 ≤ 20</span>
              </div>
              {opportunities.length ? (
                <div className="table-scroll">
                  <table className="tbl">
                    <thead><tr><th>查询词</th><th className="tbl-num">点击</th><th className="tbl-num">展示</th><th className="tbl-num">CTR</th><th className="tbl-num">平均排名</th><th>最后出现</th></tr></thead>
                    <tbody>{opportunities.map((item) => (
                      <tr key={item.query}>
                        <td className="tbl-strong">{item.query}</td>
                        <td className="tbl-num">{formatNumber(item.clicks)}</td>
                        <td className="tbl-num">{formatNumber(item.impressions)}</td>
                        <td className="tbl-num">{(number(item.ctr) * 100).toFixed(2)}%</td>
                        <td className="tbl-num">{number(item.avgPosition).toFixed(1)}</td>
                        <td>{item.lastSeen ? new Date(item.lastSeen).toLocaleDateString('zh-CN') : '—'}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              ) : (
                <StateBlock icon="query_stats" title="暂无接近首页的查询" hint="同步 GSC 后，符合条件的查询会出现在这里" />
              )}
            </div>
          </>
        )}
      </DataGuard>
    </section>
  )
}

function Metric({ label, value, sub, tone }: { label: string; value: string; sub: string; tone: 'gold' | 'blue' | 'green' | 'pink' }) {
  return (
    <div className={`kpi kpi--${tone}`}>
      <div className="kpi__head"><span className="kpi__label">{label}</span><span className="msr kpi__icon">insights</span></div>
      <div className="kpi__value">{value}</div>
      <div className="kpi__delta">{sub}</div>
    </div>
  )
}
