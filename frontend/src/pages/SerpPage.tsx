import { useEffect, useState } from 'react'
import { DataGuard } from '@/components/StateBlock'
import { searchSerp, useKeywords, type SerpSearchResult } from '@/hooks/useData'

function domainOf(url?: string) {
  if (!url) return '未知域名'
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function ResultCard({ result }: { result: SerpSearchResult }) {
  const organic = result.organic_results || []
  const questions = result.related_questions || []
  const related = result.related_searches || []
  const domains = [...new Set(organic.map((item) => domainOf(item.link)))]
  return (
    <>
      <div className="kpi-row">
        <Metric tone="pink" label="Organic 结果" value={organic.length} sub="本次真实 SERP" icon="search" />
        <Metric tone="gold" label="相关问题" value={questions.length} sub="People Also Ask" icon="quiz" />
        <Metric tone="blue" label="相关搜索" value={related.length} sub="Google Related Searches" icon="hub" />
        <Metric tone="orange" label="竞争域名" value={domains.length} sub="Top 结果去重" icon="domain" />
      </div>

      <div className="serp-grid">
        <div className="card">
          <div className="card__title">
            <span>真实搜索结果</span>
            <span className="tag tag--green">SerpApi</span>
          </div>
          {organic.length ? organic.slice(0, 10).map((item, index) => (
            <div className="serp-row" key={`${item.link || item.title}-${index}`}>
              <div className="serp-row__icon serp-row__icon--blue"><span className="msr">{item.position || index + 1}</span></div>
              <div className="serp-row__main">
                <div className="serp-row__title">{item.title || '无标题结果'}</div>
                <div className="serp-row__sub">{domainOf(item.link)} · {item.snippet || '暂无摘要'}</div>
              </div>
              <a className="serp-row__chev" href={item.link} target="_blank" rel="noreferrer" aria-label="打开搜索结果">
                <span className="msr">open_in_new</span>
              </a>
            </div>
          )) : <Empty text="SerpApi 没有返回 organic 结果。" />}
        </div>

        <div className="card">
          <div className="card__title"><span>竞争结果分布</span><small>{domains.length} 个域名</small></div>
          {domains.map((domain, index) => (
            <div className="serp-row" key={domain}>
              <div className={`serp-row__icon serp-row__icon--${index % 2 ? 'gold' : 'pink'}`}><span className="msr">domain</span></div>
              <div className="serp-row__main">
                <div className="serp-row__title">{domain}</div>
                <div className="serp-row__sub">出现在 Top 10 的第 {organic.findIndex((item) => domainOf(item.link) === domain) + 1} 位附近</div>
              </div>
              <span className="serp-row__count">{organic.filter((item) => domainOf(item.link) === domain).length}</span>
            </div>
          ))}
          {!domains.length && <Empty text="暂无竞争域名数据。" />}
        </div>
      </div>

      <div className="serp-grid-3">
        <SignalCard title="SERP 覆盖" value={`${organic.length}/10`} desc="已返回的自然结果数量" color="pink" />
        <SignalCard title="问答机会" value={`${questions.length} 条`} desc={questions[0]?.question || '暂无 People Also Ask'} color="gold" />
        <SignalCard title="相关主题" value={`${related.length} 条`} desc={related[0]?.query || related[0]?.title || '暂无相关搜索'} color="blue" />
      </div>

      <div className="footer-pipeline">
        <div className="pipeline__item pipeline__item--green"><span className="msr msr-fill">check_circle</span><div><div className="pipeline__label">SERP 查询完成</div><div className="pipeline__sub">{result.keyword}</div></div></div>
        <div className="pipeline__divider" />
        <div className="pipeline__item pipeline__item--gold"><span className="msr">search</span><div><div className="pipeline__label">真实数据来源</div><div className="pipeline__sub">SerpApi Google Search</div></div></div>
        <div className="pipeline__divider" />
        <div className="pipeline__item pipeline__item--blue"><span className="msr">database</span><div><div className="pipeline__label">可用于生文</div><div className="pipeline__sub">SERP 结果将进入 Brief 和大纲</div></div></div>
      </div>

      <aside className="rail">
        <div className="ai-card">
          <div className="ai-card__head ai-card__head--blue"><span className="msr">calendar_today</span>本次 SERP 详情</div>
          <div className="timeline">
            {questions.slice(0, 4).map((item, index) => <TimelineItem key={`${item.question}-${index}`} index={index + 1} title={item.question || item.title || '相关问题'} desc={item.snippet || item.link || 'SerpApi 返回的问题结果'} />)}
            {related.slice(0, 4).map((item, index) => <TimelineItem key={`${item.query}-${index}`} index={questions.length + index + 1} title={item.query || item.title || '相关搜索'} desc="Google Related Search" />)}
            {!questions.length && !related.length && <Empty text="暂无 PAA 或相关搜索。" />}
          </div>
        </div>
        <div className="suggest"><span className="msr suggest__icon">lightbulb</span><div className="suggest__body"><div className="suggest__title">数据说明</div><div className="suggest__desc">当前页面只展示本次 SerpApi 返回的真实数据，不再使用预置关键词或虚构判断。</div></div></div>
      </aside>
    </>
  )
}

export function SerpPage() {
  const keywords = useKeywords(0, 50)
  const [keyword, setKeyword] = useState('')
  const [result, setResult] = useState<SerpSearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!keyword && keywords.data?.[0]?.keyword) setKeyword(keywords.data[0].keyword)
  }, [keyword, keywords.data])

  async function handleSearch() {
    if (!keyword.trim() || loading) return
    setLoading(true)
    setError(null)
    try {
      const data = await searchSerp(keyword.trim())
      if (data.status === 'fetch-failed' || !data.configured) {
        setResult(null)
        setError(data.status === 'fetch-failed' ? 'SerpApi 请求失败，请检查网络或 API 配置。' : 'SerpApi 尚未配置。')
      } else {
        setResult(data)
      }
    } catch (requestError) {
      setResult(null)
      setError(requestError instanceof Error ? requestError.message : 'SERP 请求失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="page" data-screen-label="SERP 洞察">
      <div className="page-heading-row">
        <div><h1>SERP 洞察</h1><p>查询指定关键词的真实 Google 搜索结果、问答和相关搜索，用于机会判断与文章 Brief。</p></div>
        <div className="agent-heading-actions">
          <select className="chip" value={keyword} onChange={(event) => setKeyword(event.target.value)} disabled={keywords.loading || !keywords.data?.length}>
            {!keywords.data?.length && <option value="">暂无关键词</option>}
            {keywords.data?.map((item) => <option key={item.id} value={item.keyword}>{item.keyword}</option>)}
          </select>
          <button className="btn btn--primary" type="button" onClick={() => void handleSearch()} disabled={loading || !keyword.trim()}>
            <span className="msr">{loading ? 'progress_activity' : 'search'}</span>{loading ? '查询中…' : '查询 SERP'}
          </button>
        </div>
      </div>

      <DataGuard loading={keywords.loading} error={keywords.error} empty={!keywords.data?.length} emptyTitle="暂无关键词" emptyHint="请先导入关键词，再从这里查询真实 SERP。">
        {error && <div className="state-block state-block--error" role="alert">{error}</div>}
        {!result && !error && <div className="state-block"><b>请选择关键词并点击“查询 SERP”</b><span>页面不会自动调用 SerpApi，避免刷新时产生不必要的 API 消耗。</span></div>}
        {result && <ResultCard result={result} />}
      </DataGuard>
    </section>
  )
}

function Metric({ tone, label, value, sub, icon }: { tone: 'pink' | 'gold' | 'blue' | 'orange'; label: string; value: number; sub: string; icon: string }) {
  return <div className={`kpi kpi--${tone}`}><div className="kpi__head"><span className="kpi__label">{label}</span><span className={`msr kpi__icon kpi__icon--${tone}`}>{icon}</span></div><div className="kpi__value">{value}</div><div className="kpi__delta">{sub}</div></div>
}

function SignalCard({ title, value, desc, color }: { title: string; value: string; desc: string; color: 'pink' | 'gold' | 'blue' }) {
  return <div className={`judge judge--${color}`}><div className="judge__eyebrow">真实 SERP 信号</div><div className="judge__title">{title}</div><div className="judge__stats"><div><div className="judge__stat-label">当前值</div><div className="judge__stat-value">{value}</div></div></div><div className="judge__action">{desc}</div><div className="judge__cta"><span className={`tag tag--${color}`}>来自 SerpApi</span></div></div>
}

function TimelineItem({ index, title, desc }: { index: number; title: string; desc: string }) {
  return <div className="timeline__item"><div className="timeline__time">{index}</div><div className="timeline__rail"><div className="timeline__dot ai-list__dot--blue"><span className="msr">search</span></div><div className="timeline__line" /></div><div className="timeline__body"><div className="timeline__title">{title}</div><div className="timeline__desc">{desc}</div></div></div>
}

function Empty({ text }: { text: string }) {
  return <div className="agent-empty">{text}</div>
}
