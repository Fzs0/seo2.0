import { useEffect, useState } from 'react'
import { useBusinessScope } from '@/businessScope'
import { useMainSiteContent, useSites } from '@/hooks/useData'

const TYPE_LABELS: Record<string, string> = {
  product: '产品页',
  category: '分类页',
  article: '支持文章',
  home: '首页',
  other: '其他页面',
}

export function MainSiteContentPage() {
  const sites = useSites()
  const { businessId } = useBusinessScope()
  const mainSites = (sites.data ?? []).filter((site) => site.business_id === businessId && (site.is_main || site.site_type === 'main'))
  const [siteId, setSiteId] = useState('')
  const plan = useMainSiteContent(siteId)

  useEffect(() => {
    if (!mainSites.some((site) => site.id === siteId)) setSiteId(mainSites[0]?.id || '')
  }, [mainSites, siteId])

  return (
    <section className="page">
      <div>
        <h1>主站内容</h1>
        <p>产品页和分类页负责商业承接，博客文章负责解释需求并把用户引回商业页面。</p>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card__title"><span>主站 SEO 内容分层</span><span className="tag tag--gold">独立于今日策略</span></div>
        <div className="strategy-review-card__toolbar">
          <select className="input" value={siteId} onChange={(event) => setSiteId(event.target.value)} aria-label="选择主站">
            {!mainSites.length && <option value="">暂无主站</option>}
            {mainSites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
          </select>
          <span className="btn-caption">当前只读读取索引库存，不启动普通博客策略，也不自动发布。</span>
        </div>
      </div>

      {plan.loading && <div className="agent-empty">正在读取主站页面库存…</div>}
      {plan.error && <div className="strategy-review-card__error">{plan.error}</div>}
      {plan.data && <>
        <div className="agent-section-grid" style={{ marginBottom: 16 }}>
          <Metric label="索引 URL" value={plan.data.indexed_urls} />
          <Metric label="产品页" value={plan.data.counts.product || 0} />
          <Metric label="分类页" value={plan.data.counts.category || 0} />
          <Metric label="支持文章" value={plan.data.counts.article || 0} />
        </div>

        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card__title"><span>主站内容规则</span><span className="tag tag--blue">研究版 v1</span></div>
          <ol style={{ margin: '12px 0 0 18px', color: 'var(--ink-500)', lineHeight: 1.7, fontSize: 12 }}>
            {plan.data.rules.map((rule) => <li key={rule}>{rule}</li>)}
          </ol>
        </div>

        <div className="card">
          <div className="card__title"><span>页面库存与职责</span><span className="tag tag--blue">已扫描 {plan.data.scanned_pages} · 未扫描 {plan.data.unscanned_urls}</span></div>
          <div className="tbl-wrap" style={{ marginTop: 12 }}>
            <table className="tbl">
              <thead><tr><th>页面</th><th>类型</th><th>职责</th><th>当前状态</th><th>下一步</th></tr></thead>
              <tbody>{plan.data.inventory.slice(0, 200).map((item) => <tr key={item.url}>
                <td style={{ maxWidth: 330, overflowWrap: 'anywhere' }}><a href={item.url} target="_blank" rel="noreferrer">{item.url}</a>{item.title && <div style={{ color: 'var(--ink-400)', fontSize: 11, marginTop: 3 }}>{item.title}</div>}</td>
                <td><span className="tag tag--blue">{TYPE_LABELS[item.page_type] || item.page_type}</span></td>
                <td>{item.role}</td>
                <td>{item.status === 'ok' ? '已读取' : item.status === 'indexed_only' ? '仅有索引' : item.status}</td>
                <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{item.recommendation}</td>
              </tr>)}</tbody>
            </table>
          </div>
          {plan.data.inventory.length > 200 && <div className="btn-caption" style={{ marginTop: 8 }}>当前显示前 200 个页面，完整 URL 清单仍保存在索引记录中。</div>}
        </div>
      </>}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div className="card"><div style={{ color: 'var(--ink-400)', fontSize: 11 }}>{label}</div><strong style={{ display: 'block', marginTop: 6, fontSize: 22 }}>{value}</strong></div>
}
