import { useState, type ReactNode } from 'react'
import { BarDecor } from '@/components/Charts'
import { DataGuard } from '@/components/StateBlock'
import { publishArticle, runArticlePipeline, useKeywords, usePosts, useSites, type PipelineStep } from '@/hooks/useData'
import { ArticleResultDialog, type ArticleResultData } from '@/components/ArticleResultDialog'
import type { Keyword } from '@/types/domain'

function asNumber(value: number | string | null | undefined) {
  const n = Number(value ?? 0)
  return Number.isFinite(n) ? n : 0
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function priorityWeight(priority: string) {
  return { P0: 4, P1: 3, P2: 2, P3: 1, high: 3, 'medium-high': 2 }[priority] ?? 0
}

function isContentCandidate(k: Keyword) {
  return (
    ['analyzed', 'planned', 'queued'].includes(k.status) &&
    !['Hold', 'hold'].includes(k.priority) &&
    !['skip', 'manual_parent_review'].includes(k.content_action)
  )
}

function strategyText(k: Keyword) {
  return k.ai_review?.strategy?.briefDirection || k.ai_review?.strategy?.strategyReason || k.reason || k.content_action
}

function statusMeta(k: Keyword) {
  if (k.status === 'queued') return { label: '已入队', cls: 'brief-row__status--gold', progress: 65, color: '#c9a03c' }
  if (k.ai_review?.strategy) return { label: '待生成', cls: 'brief-row__status--pink', progress: 40, color: '#d77e6c' }
  return { label: '规则分析', cls: 'brief-row__status--green', progress: 25, color: '#93a96c' }
}

export function ContentPage({ onNotify }: { onNotify?: (title: string, detail?: string) => void }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [generating, setGenerating] = useState(false)
  const [message, setMessage] = useState<string>()
  const [steps, setSteps] = useState<PipelineStep[]>([])
  const [lastResult, setLastResult] = useState<ArticleResultData | null>(null)
  const [showResult, setShowResult] = useState(false)
  const [selectedSiteId, setSelectedSiteId] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [publishMessage, setPublishMessage] = useState<string>()
  const keywords = useKeywords(refreshKey)
  const posts = usePosts()
  const sites = useSites()
  const loading = keywords.loading || posts.loading
  const error = keywords.error || posts.error || sites.error
  const kw = keywords.data ?? []
  const candidates = kw
    .filter(isContentCandidate)
    .sort((a, b) => priorityWeight(b.priority) - priorityWeight(a.priority) || asNumber(b.score) - asNumber(a.score))
  const queue = candidates.slice(0, 6)
  const topicCount = new Set(candidates.map((k) => k.topic_cluster || k.keyword)).size
  const commercialCount = candidates.filter((k) =>
    `${k.page_role} ${k.page_type} ${k.content_action} ${k.assigned_site_label}`.toLowerCase().includes('commercial') ||
    k.content_action === 'create_commercial_page_first' ||
    k.priority === 'P0',
  ).length
  const reviewCount = kw.filter((k) => k.priority === 'Hold' || k.status === 'hold' || k.content_action === 'manual_parent_review').length
  const directions = candidates.slice(0, 3)
  const publishSites = (sites.data ?? []).filter((site) => site.status === 'active')
  const targetSiteId = selectedSiteId || publishSites.find((site) => site.publish_ready)?.id || publishSites[0]?.id || ''

  async function handleGenerateOne() {
    const keyword = queue[0]
    if (!keyword || generating) return
    setGenerating(true)
    setLastResult(null)
    setShowResult(false)
    setMessage(`正在执行完整生文链路：${keyword.keyword}`)
    setSteps([
      { key: 'keyword', label: '读取关键词', status: 'running' },
      { key: 'strategy', label: '读取 AI 策略', status: 'pending' },
      { key: 'serp', label: '获取 SERP', status: 'pending' },
      { key: 'brief', label: '生成 Brief', status: 'pending' },
      { key: 'outline', label: '生成文章大纲', status: 'pending' },
      { key: 'article', label: '生成文章', status: 'pending' },
      { key: 'save', label: '保存结果', status: 'pending' },
    ])
    try {
      const saved = await runArticlePipeline(keyword)
      setLastResult(saved)
      setSteps(saved.steps)
      if (saved.status === 'failed') {
        const failed = saved.steps.find((item) => item.status === 'failed')
        setMessage(`执行失败：${failed?.label || '未知步骤'}｜${failed?.message || '无错误信息'}`)
        return
      }
      const passed = (saved.qa || []).filter((item) => item.ok).length
      setMessage(`已生成：${saved.article?.title || keyword.keyword}｜SERP：${saved.serp?.source || saved.serp?.status || '未使用'}｜QA：${passed}/${saved.qa?.length || 0}`)
      onNotify?.('文章生成完成', `${saved.article?.title || keyword.keyword}｜QA ${passed}/${saved.qa?.length || 0}`)
      setShowResult(true)
      setRefreshKey((key) => key + 1)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '生成失败')
    } finally {
      setGenerating(false)
    }
  }

  async function handlePublish(dryRun: boolean) {
    const articleId = lastResult?.savedTo?.articleId || lastResult?.article?.id
    if (!articleId || !targetSiteId || publishing) return
    setPublishing(true)
    setPublishMessage(dryRun ? '正在发布预检…' : '正在确认发布…')
    try {
      const result = await publishArticle(articleId, targetSiteId, dryRun)
      setPublishMessage(
        result.ok
          ? dryRun
            ? `预检通过：${result.url || '可发布'}`
            : `发布成功：${result.url || result.post_id || '已发布'}`
          : `发布失败：${result.error || 'unknown'}`,
      )
      onNotify?.(
        dryRun ? '发布预检完成' : '文章发布完成',
        result.ok ? (result.url || result.post_id || '成功') : (result.error || '失败'),
      )
    } catch (error) {
      setPublishMessage(error instanceof Error ? error.message : '发布失败')
    } finally {
      setPublishing(false)
    }
  }

  return (
    <>
      <section className="page" data-screen-label="内容策略">
        <div>
          <h1>内容策略</h1>
          <p>基于真实关键词库、AI 策略结果和已同步站点文章，整理内容生产方向。</p>
        </div>

        <DataGuard loading={loading} error={error} empty={!kw.length} emptyTitle="暂无内容策略数据" emptyHint="请先导入关键词并运行 AI 分析策略">
          <div className="kpi-row">
            <Kpi color="gold" icon="description" label="待生成 Brief" value={formatNumber(candidates.length)} sub="来自 analyzed / planned / queued 关键词">
              <BarDecor width={80} height={32} gradient="gold" />
            </Kpi>
            <Kpi color="pink" icon="edit" label="内容主题" value={formatNumber(topicCount)} sub="按 topic_cluster 去重" />
            <Kpi color="blue" icon="workspace_premium" label="商业优先" value={formatNumber(commercialCount)} sub={`占比 ${candidates.length ? Math.round((commercialCount / candidates.length) * 100) : 0}%`} />
            <Kpi color="orange" icon="shield" label="待人工复核" value={formatNumber(reviewCount)} sub="Hold / manual review" />
          </div>

          <div className="content-grid-2">
            <div className="card">
              <div className="card__title">本周内容方向</div>
              {directions.length ? directions.map((k, index) => (
                <DirectionRow key={k.id} keyword={k} index={index} />
              )) : (
                <div className="timeline__desc">暂无可进入内容生产的关键词。</div>
              )}
            </div>

            <div className="card">
              <div className="card__title">
                <span>Brief 生成队列</span>
                <span className="tag tag--gray">真实数据</span>
              </div>

              {queue.length ? queue.map((k) => {
                const meta = statusMeta(k)
                return (
                  <div className="brief-row" key={k.id}>
                    <div className="brief-row__icon" style={{ background: '#f7edd3' }}>
                      <span className="msr" style={{ color: '#b98f2e' }}>description</span>
                    </div>
                    <div className="brief-row__main">
                      <div className="brief-row__top">
                        <span className="brief-row__title">{k.keyword}</span>
                        <span className="brief-row__cat">{k.page_role || k.page_type || k.assigned_site_label || '未分类'}</span>
                      </div>
                      <div className="brief-row__progress">
                        <div className="brief-row__bar">
                          <div className="brief-row__bar-fill" style={{ width: `${meta.progress}%`, background: meta.color }} />
                        </div>
                        <span className="brief-row__pct">{k.priority}</span>
                      </div>
                    </div>
                    <span className={`brief-row__status ${meta.cls}`}>{meta.label}</span>
                  </div>
                )
              }) : (
                <div className="timeline__desc">暂无 Brief 队列。</div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card__title">AI 推荐结构</div>
            <div className="content-grid-5">
              <StructCard iconClass="struct-card__icon--gold" iconText="H1" title="H1 标题" desc="围绕主关键词和搜索意图写首屏结论。" cta={`${formatNumber(candidates.length)} 个候选`} />
              <StructCard iconClass="struct-card__icon--pink" iconText="H2" title="H2 小标题" desc="按决策路径组织：结论、差异、适合谁、下一步、FAQ。" cta={`${formatNumber(topicCount)} 个主题`} />
              <StructCard iconClass="" iconText="quiz" iconColor="#5f84a8" title="FAQ 模块" desc="优先覆盖真实搜索问题，避免泛泛而谈。" cta="待 Brief 生成" />
              <StructCard iconClass="" iconText="link" iconColor="#7e9150" title="内链策略" desc="只链接已确认存在的 posts / 商业页。" cta={`${formatNumber(posts.data?.length ?? 0)} 篇站点文章`} />
              <StructCard iconClass="" iconText="ads_click" iconColor="#c97b3f" title="CTA 引导" desc="商业页强 CTA，知识文轻 CTA。" cta={`${formatNumber(commercialCount)} 个商业候选`} />
            </div>
          </div>
        </DataGuard>
      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="ai-card__head">
            <span className="msr msr-fill">auto_awesome</span>
            AI 策略摘要
          </div>
          <button className="btn--block" type="button" onClick={handleGenerateOne} disabled={!queue.length || generating}>
            {generating ? '正在生成文章…' : '生成 1 篇文章'}
            <span className="msr">{generating ? 'progress_activity' : 'edit_document'}</span>
          </button>
          {message && <div className="btn-caption">{message}</div>}
          {steps.length > 0 && (
            <div className="ai-list">
              {steps.map((step) => (
                <div className="ai-list__item" key={step.key}>
                  <div className={`ai-list__dot ${step.status === 'failed' ? 'ai-list__dot--pink' : 'ai-list__dot--gold'}`}>
                    <span className="msr">{step.status === 'failed' ? 'error' : step.status === 'done' ? 'check' : 'hourglass_top'}</span>
                  </div>
                  <div>
                    <div className="ai-list__label">{step.status}</div>
                    <div className="ai-list__title">{step.label}</div>
                    {step.message && <div className="ai-list__desc">{step.message}</div>}
                  </div>
                </div>
              ))}
            </div>
          )}
          {lastResult?.status === 'done' && (
            <div className="ai-list">
              <button className="btn--block" type="button" onClick={() => setShowResult(true)}>
                查看完整生成结果
                <span className="msr">open_in_full</span>
              </button>
              <select className="chip" value={targetSiteId} onChange={(event) => setSelectedSiteId(event.target.value)}>
                {publishSites.map((site) => (
                  <option key={site.id} value={site.id}>
                    {site.name} · {site.publish_adapter || site.site_type}{site.publish_ready ? '' : ' · 未就绪'}
                  </option>
                ))}
              </select>
              <button className="btn--block" type="button" disabled={!targetSiteId || publishing} onClick={() => void handlePublish(true)}>
                发布预检
                <span className="msr">{publishing ? 'progress_activity' : 'rule'}</span>
              </button>
              <button className="btn--block" type="button" disabled={!targetSiteId || publishing} onClick={() => void handlePublish(false)}>
                确认发布
                <span className="msr">{publishing ? 'progress_activity' : 'publish'}</span>
              </button>
              {publishMessage && <div className="btn-caption">{publishMessage}</div>}
            </div>
          )}
          <div className="ai-list">
            {queue.slice(0, 4).map((k) => (
              <div className="ai-list__item" key={k.id}>
                <div className="ai-list__dot ai-list__dot--gold">
                  <span className="msr">star</span>
                </div>
                <div>
                  <div className="ai-list__label">{k.priority} · {k.status}</div>
                  <div className="ai-list__title">{k.keyword}</div>
                  <div className="ai-list__desc">{strategyText(k)}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="btn--block btn--block-static" role="note">
            已同步站点文章：{formatNumber(posts.data?.length ?? 0)}
            <span className="msr">database</span>
          </div>
          <div className="btn-caption">当前页只读展示真实数据；任务创建和 worker 下一步接入。</div>
        </div>
      </aside>
      {showResult && lastResult?.status === 'done' && (
        <ArticleResultDialog
          result={lastResult}
          onClose={() => setShowResult(false)}
          publishSites={publishSites}
          targetSiteId={targetSiteId}
          onSiteChange={setSelectedSiteId}
          publishing={publishing}
          publishMessage={publishMessage}
          onPublish={handlePublish}
        />
      )}
    </>
  )
}

function Kpi({
  color,
  icon,
  label,
  value,
  sub,
  children,
}: {
  color: 'gold' | 'pink' | 'blue' | 'orange'
  icon: string
  label: string
  value: string
  sub: string
  children?: ReactNode
}) {
  return (
    <div className={`kpi kpi--${color}`}>
      <div className="kpi__head">
        <span className="kpi__label">{label}</span>
        <span className={`msr kpi__icon kpi__icon--${color}`}>{icon}</span>
      </div>
      <div className="kpi__value">{value}</div>
      <div className="kpi__delta">{sub}</div>
      {children && <div className="kpi__spark">{children}</div>}
    </div>
  )
}

function DirectionRow({ keyword, index }: { keyword: Keyword; index: number }) {
  const colors = [
    { bg: '#f7edd3', num: '#8a6d1f', cls: 'dir-row__priority--gold' },
    { bg: '#f9e4de', num: '#a14a3c', cls: 'dir-row__priority--pink' },
    { bg: '#e4ebf3', num: '#3f6182', cls: 'dir-row__priority--blue' },
  ][index]
  return (
    <div className="dir-row" style={{ background: colors.bg }}>
      <div className="dir-row__num" style={{ color: colors.num }}>{String(index + 1).padStart(2, '0')}</div>
      <div className="dir-row__body">
        <div className="dir-row__head">
          <span className="dir-row__title">{keyword.topic_cluster || keyword.keyword}</span>
          <span className={`dir-row__priority ${colors.cls}`}>{keyword.priority}</span>
        </div>
        <div className="dir-row__desc">{strategyText(keyword)}</div>
        <div className="dir-row__impact">
          搜索量 <b>{formatNumber(asNumber(keyword.volume))}</b> · Score <b>{asNumber(keyword.score).toFixed(1)}</b>
        </div>
      </div>
    </div>
  )
}

function StructCard({
  iconClass,
  iconText,
  iconColor,
  title,
  desc,
  cta,
}: {
  iconClass: string
  iconText: string
  iconColor?: string
  title: string
  desc: string
  cta: string
}) {
  return (
    <div className="struct-card">
      <div className={`struct-card__icon ${iconClass}`} style={iconColor ? { color: iconColor } : undefined}>
        {iconText.length <= 3 ? iconText : <span className="msr">{iconText}</span>}
      </div>
      <div className="struct-card__title">{title}</div>
      <div className="struct-card__desc">{desc}</div>
      <div className="struct-card__more" role="note">
        {cta}
        <span className="msr">chevron_right</span>
      </div>
    </div>
  )
}
