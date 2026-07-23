import { useEffect, useMemo, useState } from 'react'
import { ArticleResultDialog, type ArticleResultData } from '@/components/ArticleResultDialog'
import { DataGuard } from '@/components/StateBlock'
import {
  getArticleDetail,
  publishArticle,
  syncArticleSeoMetadata,
  syncSitePosts,
  useArticleKpi,
  useArticleMonthlyStats,
  useArticlePage,
  useArticleTimeseries,
  usePosts,
  type DateField,
  type TimeseriesBucket,
  type TimeseriesGranularity,
} from '@/data/articles'
import { useSites } from '@/data/sites'
import { useBusinessScope } from '@/businessScope'

function formatDate(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

function monthLabel(key: string) {
  const parts = key.split('-')
  return parts.length === 2 ? `${Number(parts[1])}月` : key
}

function shiftMonth(yyyymm: string, delta: number): string {
  const [yStr, mStr] = yyyymm.split('-')
  let y = Number(yStr)
  let m = Number(mStr) + delta
  while (m < 1) { m += 12; y -= 1 }
  while (m > 12) { m -= 12; y += 1 }
  return `${y}-${String(m).padStart(2, '0')}`
}

function listRecentMonths(count: number): string[] {
  const now = new Date()
  const cur = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const out: string[] = []
  for (let i = 0; i < count; i += 1) {
    out.push(shiftMonth(cur, -i))
  }
  return out.reverse()
}

function monthToFirstDay(yyyymm: string): string {
  return `${yyyymm}-01`
}

function monthToLastDay(yyyymm: string): string {
  const [yStr, mStr] = yyyymm.split('-')
  const y = Number(yStr)
  const m = Number(mStr)
  const last = new Date(y, m, 0).getDate()
  return `${y}-${String(m).padStart(2, '0')}-${String(last).padStart(2, '0')}`
}

function formatBucketLabel(label: string, granularity: TimeseriesGranularity | string): string {
  if (granularity === 'day') return label.slice(5) // MM-DD
  if (granularity === 'week') return label // "W 2026-07-20"
  return label // YYYY-MM
}

const PALETTE = ['#e8b94f', '#7c9ec1', '#93a96c', '#c97b3f', '#9c8fbf', '#c4685a', '#6b5e8e', '#5f84a8']

function ArticleLineChart({
  buckets,
  bySite,
  granularity,
  loading,
  error,
  total,
}: {
  buckets: TimeseriesBucket[]
  bySite: { site_id: string | null; site_name: string; series: TimeseriesBucket[]; total: number }[]
  granularity: TimeseriesGranularity | string
  loading: boolean
  error: string | null
  total: number
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const [width, setWidth] = useState(720)
  const height = 220
  const padding = { top: 12, right: 16, bottom: 28, left: 40 }
  const innerW = width - padding.left - padding.right
  const innerH = height - padding.top - padding.bottom

  // 自适应宽度
  useEffect(() => {
    const onResize = () => {
      const el = document.querySelector('.article-chart')
      if (el) setWidth(el.clientWidth || 720)
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const maxCount = useMemo(() => {
    const all = [...buckets.map((b) => b.count), ...bySite.flatMap((s) => s.series.map((b) => b.count))]
    return Math.max(1, ...all)
  }, [buckets, bySite])

  if (loading) {
    return <div className="article-chart__loading">曲线加载中…</div>
  }
  if (error) {
    return <div className="article-chart__error">曲线加载失败：{error}</div>
  }
  if (buckets.length === 0) {
    return <div className="article-chart__empty">所选范围内没有数据</div>
  }

  const n = buckets.length
  const stepX = n > 1 ? innerW / (n - 1) : innerW
  const xFor = (i: number) => padding.left + i * stepX
  const yFor = (v: number) => padding.top + innerH - (v / maxCount) * innerH

  // 折线 + 面积
  const linePath = buckets
    .map((b, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i)} ${yFor(b.count)}`)
    .join(' ')
  const areaPath = linePath
    + ` L ${xFor(n - 1)} ${padding.top + innerH}`
    + ` L ${xFor(0)} ${padding.top + innerH} Z`

  // Y 轴 4 个刻度
  const yTicks = [0, maxCount / 2, maxCount].map((v) => Math.round(v))

  // X 轴标签采样：最多 8 个
  const xLabelEvery = Math.max(1, Math.ceil(n / 8))

  return (
    <div className="article-chart">
      <svg
        className="article-chart__svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        onMouseLeave={() => setHoverIdx(null)}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect()
          const scaleX = width / rect.width
          const localX = (event.clientX - rect.left) * scaleX - padding.left
          if (localX < 0 || localX > innerW) { setHoverIdx(null); return }
          const idx = Math.round(localX / stepX)
          setHoverIdx(Math.max(0, Math.min(n - 1, idx)))
        }}
      >
        {/* 网格线 + Y 轴标签 */}
        {yTicks.map((v) => (
          <g key={v}>
            <line
              x1={padding.left} x2={width - padding.right}
              y1={yFor(v)} y2={yFor(v)}
              className="article-chart__grid"
            />
            <text x={padding.left - 6} y={yFor(v) + 4} className="article-chart__axis-label" textAnchor="end">
              {v}
            </text>
          </g>
        ))}
        {/* X 轴标签 */}
        {buckets.map((b, i) => {
          if (i % xLabelEvery !== 0 && i !== n - 1) return null
          return (
            <text key={b.key} x={xFor(i)} y={height - 8} className="article-chart__axis-label" textAnchor="middle">
              {formatBucketLabel(b.label, granularity)}
            </text>
          )
        })}
        {/* 面积 */}
        <path d={areaPath} className="article-chart__area" />
        {/* 主线 */}
        <path d={linePath} className="article-chart__line" />
        {/* 数据点 + hover 高亮 */}
        {buckets.map((b, i) => {
          const isHover = hoverIdx === i
          return (
            <g key={b.key}>
              <circle
                cx={xFor(i)} cy={yFor(b.count)}
                r={isHover ? 5 : 2.5}
                className={`article-chart__dot ${isHover ? 'article-chart__dot--active' : ''}`}
              />
              {isHover && (
                <line
                  x1={xFor(i)} x2={xFor(i)}
                  y1={padding.top} y2={padding.top + innerH}
                  className="article-chart__crosshair"
                />
              )}
            </g>
          )
        })}
      </svg>
      {hoverIdx != null && buckets[hoverIdx] && (
        <div className="article-chart__tooltip" role="tooltip">
          <div className="article-chart__tooltip-title">{buckets[hoverIdx].label}</div>
          <div className="article-chart__tooltip-row">
            <span className="article-chart__tooltip-label">总计</span>
            <span className="article-chart__tooltip-value">{buckets[hoverIdx].count} 篇</span>
          </div>
          {bySite.length > 0 && (
            <div className="article-chart__tooltip-divider" />
          )}
          {bySite.map((b, i) => {
            const v = b.series[hoverIdx]?.count ?? 0
            if (v === 0) return null
            return (
              <div className="article-chart__tooltip-row" key={b.site_id ?? i}>
                <span
                  className="article-chart__tooltip-dot"
                  style={{ background: PALETTE[i % PALETTE.length] }}
                />
                <span className="article-chart__tooltip-label">{b.site_name}</span>
                <span className="article-chart__tooltip-value">{v} 篇</span>
              </div>
            )
          })}
        </div>
      )}
      {bySite.length > 0 && (
        <div className="article-chart__legend">
          {bySite.map((b, i) => (
            <span className="article-chart__legend-item" key={b.site_id ?? i}>
              <span className="article-chart__legend-dot" style={{ background: PALETTE[i % PALETTE.length] }} />
              {b.site_name}
              <span className="article-chart__legend-count">{b.total}</span>
            </span>
          ))}
        </div>
      )}
      <div className="article-chart__footer">
        <span>粒度：{granularity === 'day' ? '日' : granularity === 'week' ? '周' : granularity === 'month' ? '月' : granularity}</span>
        <span>共 {total} 篇 / {n} 个时间桶</span>
      </div>
    </div>
  )
}

export function ArticlesPage() {
  const { businessId } = useBusinessScope()
  const [refreshKey, setRefreshKey] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [selectedSiteId, setSelectedSiteId] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [message, setMessage] = useState<string>()
  const [openingArticleId, setOpeningArticleId] = useState<string>()
  const [selectedArticle, setSelectedArticle] = useState<ArticleResultData | null>(null)
  const [articleTargetSiteId, setArticleTargetSiteId] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [publishMessage, setPublishMessage] = useState<string>()

  // 图表日期范围状态
  const monthOptions = useMemo(() => listRecentMonths(18), [])
  const [rangeStart, setRangeStart] = useState(() => monthOptions[Math.max(0, monthOptions.length - 12)] ?? monthOptions[0])
  const [rangeEnd, setRangeEnd] = useState(() => monthOptions[monthOptions.length - 1] ?? monthOptions[monthOptions.length - 1])
  const [granularity, setGranularity] = useState<TimeseriesGranularity>('auto')
  const [dateField, setDateField] = useState<DateField>('published_at')

  const sites = useSites()
  const posts = usePosts(refreshKey, selectedSiteId || undefined)
  const publishSites = (sites.data ?? []).filter((site) => site.status === 'active' && site.business_id === businessId)
  const articlePage = useArticlePage(refreshKey, page, pageSize, { siteId: selectedSiteId || undefined })
  const monthlyStats = useArticleMonthlyStats(refreshKey, 12, selectedSiteId || undefined)
  const kpi = useArticleKpi(refreshKey, selectedSiteId || undefined, dateField)
  const startDate = useMemo(() => monthToFirstDay(rangeStart), [rangeStart])
  const endDate = useMemo(() => monthToLastDay(rangeEnd), [rangeEnd])
  const timeseries = useArticleTimeseries(refreshKey, startDate, endDate, granularity, selectedSiteId || undefined, dateField)

  // 切换筛选或刷新时回到第一页
  useEffect(() => {
    setPage(1)
  }, [selectedSiteId, pageSize])

  useEffect(() => {
    if (!publishSites.some((site) => site.id === selectedSiteId)) {
      setSelectedSiteId(publishSites[0]?.id || '')
    }
  }, [businessId, publishSites, selectedSiteId])

  // 范围不合法时纠正
  useEffect(() => {
    if (rangeStart > rangeEnd) {
      setRangeEnd(rangeStart)
    }
  }, [rangeStart, rangeEnd])

  const articles = articlePage.data?.items ?? []
  const total = articlePage.data?.total ?? 0
  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [total, pageSize])
  const pageStart = total === 0 ? 0 : (page - 1) * pageSize + 1
  const pageEnd = Math.min(page * pageSize, total)
  const monthsReversed = useMemo(
    () => (monthlyStats.data?.months ? [...monthlyStats.data.months].reverse() : []),
    [monthlyStats.data],
  )
  const monthlyTotals = monthlyStats.data?.totals ?? {}

  // 月度分布图（保留旧版用，叠加在曲线图下方当月度概览）
  const monthlyMax = Math.max(1, ...monthsReversed.map((m) => monthlyTotals[m] ?? 0))

  async function handleSyncSelected() {
    if (!selectedSiteId || syncing) return
    const site = publishSites.find((item) => item.id === selectedSiteId)
    setSyncing(true)
    setMessage(`正在读取 ${site?.name || '目标站点'} 的已有文章…`)
    try {
      const result = await syncSitePosts(selectedSiteId, 100)
      setMessage(result.ok ? `${site?.name || '目标站点'} 读取完成：获取 ${result.fetched} 篇，写入 ${result.saved} 篇。` : `${site?.name || '目标站点'} 读取失败：${result.error || '未知错误'}`)
      if (result.ok) setRefreshKey((key) => key + 1)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '站点文章读取失败')
    } finally {
      setSyncing(false)
    }
  }

  async function handleSync() {
    if (syncing) return
    setSyncing(true)
    if (!publishSites.length) return
    setMessage('正在同步当前业务的所有站点文章…')
    try {
      const results = await Promise.all(publishSites.map((site) => syncSitePosts(site.id, 100)))
      const saved = results.reduce((sum, item) => sum + item.saved, 0)
      const failed = results.filter((item) => !item.ok).length
      setMessage(`同步完成：写入 ${saved} 篇，失败站点 ${failed} 个。`)
      setRefreshKey((key) => key + 1)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '同步失败')
    } finally {
      setSyncing(false)
    }
  }

  async function handleOpenArticle(articleId: string) {
    if (openingArticleId) return
    setOpeningArticleId(articleId)
    try {
      const detail = await getArticleDetail(articleId)
      const parts = detail.article_parts || {}
      const defaultSiteId = detail.site_id && publishSites.some((site) => site.id === detail.site_id)
        ? detail.site_id
        : publishSites.find((site) => site.publish_ready)?.id || publishSites[0]?.id || ''
      setArticleTargetSiteId(defaultSiteId)
      setPublishMessage(undefined)
      setSelectedArticle({
        status: detail.status,
        steps: [],
        article: {
          id: detail.id,
          site_id: detail.site_id,
          title: detail.title,
          status: detail.status,
          meta_title: detail.meta_title,
          meta_description: detail.meta_description,
          primary_keyword: detail.primary_keyword,
        },
        brief: { source: 'saved', text: detail.brief_md || '' },
        outline: typeof parts.outline === 'string' ? parts.outline : '',
        content: detail.content_md || detail.content_html || '',
        savedTo: { table: 'seo_agent.articles', articleId: detail.id },
        serp: { id: detail.serp_snapshot_id, source: detail.serp_snapshot_id ? 'saved' : undefined, status: detail.serp_snapshot_id ? 'available' : 'not-loaded' },
        qa: detail.qa_checklist || [],
        provider: detail.generation_provider || undefined,
        model: detail.generation_model || undefined,
        contentLength: (detail.content_md || detail.content_html || '').length,
      })
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '文章详情读取失败')
    } finally {
      setOpeningArticleId(undefined)
    }
  }

  async function handlePublish(dryRun: boolean) {
    const articleId = selectedArticle?.article?.id
    if (!articleId || publishing) return
    if (!dryRun) {
      setPublishMessage('正式发布已关闭。请回到“今日策略”完成审核并由策略执行链路发布。')
      return
    }
    setPublishing(true)
    setPublishMessage('正在执行只读发布预检…')
    try {
      const result = await publishArticle(articleId, articleTargetSiteId, dryRun)
      setPublishMessage(result.ok ? `预检通过：${result.url || '可以回今日策略审核执行'}` : `预检失败：${result.error || '未知错误'}`)
    } catch (error) {
      setPublishMessage(error instanceof Error ? error.message : '发布预检失败')
    } finally {
      setPublishing(false)
    }
  }

  async function handleSyncSeoMetadata() {
    const articleId = selectedArticle?.article?.id
    if (!articleId || publishing) return
    if (!window.confirm('仅同步 Shopify 的页面标题和元描述，不会修改正文或调用 AI。确认同步吗？')) return
    setPublishing(true)
    setPublishMessage('正在同步 Shopify SEO 元数据…')
    try {
      const result = await syncArticleSeoMetadata(articleId)
      setPublishMessage(result.ok
        ? `SEO 元数据已同步${result.url ? `：${result.url}` : ''}`
        : `同步失败：${result.error || '未知错误'}`)
      if (result.ok) setRefreshKey((key) => key + 1)
    } catch (error) {
      setPublishMessage(error instanceof Error ? error.message : 'SEO 元数据同步失败')
    } finally {
      setPublishing(false)
    }
  }

  function applyQuickRange(monthsBack: number) {
    const end = monthOptions[monthOptions.length - 1]
    const startIdx = Math.max(0, monthOptions.length - 1 - monthsBack + 1)
    setRangeStart(monthOptions[startIdx])
    setRangeEnd(end)
  }

  return (
    <section className="page articles-page--preview-only" data-screen-label="文章管理">
      <div>
        <h1>文章管理</h1>
        <p>这里展示 AI 生成稿及其目标发布站点；文章页仅提供只读发布预检，正式发布必须回到“今日策略”审核执行。</p>
        {message && <p>{message}</p>}
      </div>

      {/* KPI 卡片 */}
      <div className="article-kpi-grid">
        <div className="article-kpi">
          <div className="article-kpi__label">累计{dateField === 'published_at' ? '已发布' : '生成'}</div>
          <div className="article-kpi__value">{kpi.data?.total ?? '—'}</div>
          <div className="article-kpi__sub">所有时间</div>
        </div>
        <div className="article-kpi">
          <div className="article-kpi__label">今日{dateField === 'published_at' ? '发布' : '生成'}</div>
          <div className="article-kpi__value article-kpi__value--accent">{kpi.data?.today ?? '—'}</div>
          <div className="article-kpi__sub">当日 00:00 起</div>
        </div>
        <div className="article-kpi">
          <div className="article-kpi__label">本周{dateField === 'published_at' ? '发布' : '生成'}</div>
          <div className="article-kpi__value">{kpi.data?.this_week ?? '—'}</div>
          <div className="article-kpi__sub">本周一 00:00 起</div>
        </div>
        <div className="article-kpi">
          <div className="article-kpi__label">本月{dateField === 'published_at' ? '发布' : '生成'}</div>
          <div className="article-kpi__value">{kpi.data?.this_month ?? '—'}</div>
          <div className="article-kpi__sub">本月 1 号起 · 上月 {kpi.data?.last_month ?? 0} 篇</div>
        </div>
        <div className="article-kpi">
          <div className="article-kpi__label">近 7 天{dateField === 'published_at' ? '发布' : '生成'}</div>
          <div className="article-kpi__value">{kpi.data?.last_7_days ?? '—'}</div>
          <div className="article-kpi__sub">滚动 7 天</div>
        </div>
        <div className="article-kpi">
          <div className="article-kpi__label">近 30 天{dateField === 'published_at' ? '发布' : '生成'}</div>
          <div className="article-kpi__value">{kpi.data?.last_30_days ?? '—'}</div>
          <div className="article-kpi__sub">滚动 30 天</div>
        </div>
      </div>

      {/* 曲线图卡片 */}
      <div className="article-chart-card">
        <div className="article-chart-card__head">
          <div>
            <h2 className="article-chart-card__title">文章{dateField === 'published_at' ? '发布' : '生成'}趋势</h2>
            <p className="article-chart-card__sub">按时间桶聚合；鼠标悬停查看具体数值与各站点贡献。</p>
          </div>
          <div className="article-chart-card__range">
            <div className="article-chart-card__quick">
              <button className="chip" type="button" onClick={() => applyQuickRange(1)}>本月</button>
              <button className="chip" type="button" onClick={() => applyQuickRange(3)}>近 3 月</button>
              <button className="chip" type="button" onClick={() => applyQuickRange(6)}>近 6 月</button>
              <button className="chip" type="button" onClick={() => applyQuickRange(12)}>近 12 月</button>
            </div>
            <div className="article-chart-card__picks">
              <label className="article-chart-card__pick">
                统计维度
                <select value={dateField} onChange={(e) => setDateField(e.target.value as DateField)}>
                  <option value="published_at">发布时间</option>
                  <option value="created_at">生成时间</option>
                </select>
              </label>
              <label className="article-chart-card__pick">
                起始
                <select value={rangeStart} onChange={(e) => setRangeStart(e.target.value)}>
                  {monthOptions.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <span className="article-chart-card__arrow">→</span>
              <label className="article-chart-card__pick">
                结束
                <select value={rangeEnd} onChange={(e) => setRangeEnd(e.target.value)}>
                  {monthOptions.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label className="article-chart-card__pick">
                粒度
                <select value={granularity} onChange={(e) => setGranularity(e.target.value as TimeseriesGranularity)}>
                  <option value="auto">自动</option>
                  <option value="day">日</option>
                  <option value="week">周</option>
                  <option value="month">月</option>
                </select>
              </label>
            </div>
          </div>
        </div>
        <ArticleLineChart
          buckets={timeseries.data?.buckets ?? []}
          bySite={timeseries.data?.by_site ?? []}
          granularity={timeseries.data?.granularity ?? granularity}
          loading={timeseries.loading}
          error={timeseries.error}
          total={timeseries.data?.total ?? 0}
        />
        <div className="article-chart-card__meta">
          {timeseries.data && (
            <>
              <span>范围：{timeseries.data.range.start} 至 {timeseries.data.range.end}（{timeseries.data.range.days} 天）</span>
              <span>· 维度：{dateField === 'published_at' ? '发布时间' : '生成时间'}</span>
              <span>· 共 {timeseries.data.total} 篇文章</span>
            </>
          )}
        </div>
      </div>

      {/* 月度分布小图（保留为概览） */}
      <div className="article-monthly-card">
        <div className="article-monthly-card__head">
          <div>
            <h2 className="article-monthly-card__title">月度分布概览</h2>
            <p className="article-monthly-card__sub">最近 12 个月；用于跨期对比。</p>
          </div>
        </div>
        <div className="article-monthly__chart" style={{ height: 80 }}>
          {monthsReversed.map((m) => {
            const total = monthlyTotals[m] ?? 0
            const heightPct = total === 0 ? 3 : Math.max(8, Math.round((total / monthlyMax) * 100))
            return (
              <div className="article-monthly__col" key={m} title={`${m}：${total} 篇`}>
                <div className="article-monthly__bar" style={{ height: `${heightPct}%` }}>
                  {total > 0 && <span className="article-monthly__bar-value">{total}</span>}
                </div>
                <span className="article-monthly__label">{monthLabel(m)}</span>
              </div>
            )
          })}
        </div>
      </div>

      <div className="filter-row">
        <select className="chip" value={selectedSiteId} onChange={(event) => setSelectedSiteId(event.target.value)} disabled={sites.loading}>
          {!publishSites.length && <option value="">当前业务暂无可用站点</option>}
          {publishSites.map((site) => <option value={site.id} key={site.id}>{site.name}</option>)}
        </select>
        <button className="btn btn--primary" type="button" onClick={() => void handleSyncSelected()} disabled={syncing || !selectedSiteId}>
          <span className="msr">{syncing ? 'progress_activity' : 'download'}</span>
          {syncing ? '读取中…' : '读取当前站点文章'}
        </button>
        <button className="btn btn--primary" type="button" onClick={handleSync} disabled={syncing}>
          <span className="msr">{syncing ? 'progress_activity' : 'sync'}</span>
          {syncing ? '同步中' : '同步当前业务文章'}
        </button>
        <span className="chip chip--active">
          <span className="msr">article</span>
          已同步 {posts.data?.length ?? '—'}
        </span>
        <span className="chip">
          <span className="msr">filter_list</span>
          共 {total} 篇
        </span>
      </div>

      <div className="tbl-wrap" style={{ marginBottom: 18 }}>
        <DataGuard
          loading={articlePage.loading}
          error={articlePage.error}
          empty={!articlePage.data || articles.length === 0}
          emptyTitle={total === 0 ? '暂无生成稿件' : '当前页没有稿件'}
          emptyHint={total === 0 ? '通过今日策略审核执行生成文章后，稿件会展示在这里' : '尝试调整筛选或翻到其他页'}
        >
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 36 }}>#</th>
                <th>生成稿标题</th>
                <th>目标站点</th>
                <th>状态</th>
                <th>关键词</th>
                <th>模型厂商 / 模型</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {articles.map((article, i) => (
                <tr key={article.id}>
                  <td style={{ color: 'var(--ink-400)' }}>{pageStart + i}</td>
                  <td>
                    <div className="tbl-strong">{article.title}</div>
                    <div style={{ fontSize: 11, color: 'var(--ink-400)', marginTop: 2 }}>{article.id}</div>
                  </td>
                  <td>
                    <span className="tag tag--blue">{article.site_label || sites.data?.find((site) => site.id === article.site_id)?.name || '未分配站点'}</span>
                    <div style={{ marginTop: 4, color: 'var(--ink-400)', fontSize: 11 }}>{article.site_id || '需要人工选择'}</div>
                  </td>
                  <td><span className="tag tag--green">{article.status}</span></td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{article.primary_keyword || article.keyword_id || '—'}</td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>
                    {article.generation_provider || 'unknown'} / {article.generation_model || 'unknown'}
                  </td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{formatDate(article.created_at)}</td>
                  <td>
                    <button className="btn btn--ghost btn--xs" type="button" onClick={() => void handleOpenArticle(article.id)} disabled={openingArticleId === article.id}>
                      {openingArticleId === article.id ? '读取中…' : '查看与预检'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataGuard>

        <div className="pager">
          <div className="pager__info">
            {total === 0 ? '共 0 条' : `第 ${pageStart}–${pageEnd} 条 / 共 ${total} 条`}
          </div>
          <div className="pager__controls">
            <label className="pager__size">
              每页
              <select
                value={pageSize}
                onChange={(event) => setPageSize(Number(event.target.value))}
                disabled={articlePage.loading}
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>{size}</option>
                ))}
              </select>
              条
            </label>
            <button
              className="btn btn--ghost btn--xs"
              type="button"
              onClick={() => setPage(1)}
              disabled={page <= 1 || articlePage.loading}
              aria-label="首页"
            >
              «
            </button>
            <button
              className="btn btn--ghost btn--xs"
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || articlePage.loading}
              aria-label="上一页"
            >
              上一页
            </button>
            <span className="pager__page">
              第 {page} / {totalPages} 页
            </span>
            <button
              className="btn btn--ghost btn--xs"
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || articlePage.loading}
              aria-label="下一页"
            >
              下一页
            </button>
            <button
              className="btn btn--ghost btn--xs"
              type="button"
              onClick={() => setPage(totalPages)}
              disabled={page >= totalPages || articlePage.loading}
              aria-label="末页"
            >
              »
            </button>
          </div>
        </div>
      </div>

      <div className="tbl-wrap">
        <DataGuard
          loading={posts.loading}
          error={posts.error}
          empty={!posts.data || posts.data.length === 0}
          emptyTitle="暂无站点文章"
          emptyHint="点击“同步所有站点文章”后会写入并展示"
        >
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 36 }}>#</th>
                <th>标题</th>
                <th>所属站点</th>
                <th>状态</th>
                <th>文章链接</th>
                <th>发布时间</th>
                <th>同步时间</th>
              </tr>
            </thead>
            <tbody>
              {(posts.data ?? []).map((post, i) => {
                const site = sites.data?.find((item) => item.id === post.site_id)
                const articleUrl = post.url && /^https?:\/\//i.test(post.url) ? post.url : ''
                return <tr key={post.id}>
                  <td style={{ color: 'var(--ink-400)' }}>{i + 1}</td>
                  <td>
                    <div className="tbl-strong">{post.title}</div>
                    <div style={{ fontSize: 11, color: 'var(--ink-400)', marginTop: 2 }}>
                      远端 ID：{post.external_id || post.id}
                    </div>
                  </td>
                  <td>
                    <span className="tag tag--blue">{site?.name || '未知站点'}</span>
                    <div style={{ marginTop: 4, color: 'var(--ink-400)', fontSize: 11 }}>同步方式：{post.source}</div>
                  </td>
                  <td><span className="tag tag--gray">{post.status || 'unknown'}</span></td>
                  <td style={{ maxWidth: 360, fontSize: 12, overflowWrap: 'anywhere' }}>
                    {articleUrl ? <a href={articleUrl} target="_blank" rel="noreferrer">{articleUrl}</a> : '未返回文章地址'}
                  </td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{formatDate(post.published_at)}</td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{formatDate(post.fetched_at)}</td>
                </tr>
              })}
            </tbody>
          </table>
        </DataGuard>
      </div>

      {selectedArticle && (
        <ArticleResultDialog
          result={selectedArticle}
          onClose={() => setSelectedArticle(null)}
          publishSites={publishSites}
          targetSiteId={articleTargetSiteId}
          onSiteChange={setArticleTargetSiteId}
          publishing={publishing}
          publishMessage={publishMessage}
          onPublish={handlePublish}
          onSyncSeoMetadata={handleSyncSeoMetadata}
        />
      )}
    </section>
  )
}
