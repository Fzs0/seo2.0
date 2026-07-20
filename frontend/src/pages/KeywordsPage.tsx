import { useEffect, useState } from 'react'
import { DataGuard } from '@/components/StateBlock'
import { cancelSemrushStrategyAiAnalysis, getLatestSemrushStrategyAiAnalysis, getSemrushStrategyAiAnalysis, importSemrushStrategyFile, previewSemrushStrategyFile, startSemrushStrategyAiAnalysis, useKeywordPage, useSites, validateImportedSemrushStrategy, type KeywordFilters, type SemrushStrategyAiAnalysisRun, type SemrushStrategyPreview } from '@/hooks/useData'
import type { Priority } from '@/types/domain'

const INTENT_TAG: Record<string, { label: string; tone: 'pink' | 'gold' | 'blue' | 'green' }> = {
  commercial: { label: '商业', tone: 'pink' },
  transactional: { label: '交易', tone: 'gold' },
  informational: { label: '信息', tone: 'blue' },
  navigational: { label: '导航', tone: 'green' },
  unknown: { label: '未知', tone: 'green' },
}

const PRIORITY_TAG: Record<Priority, { label: string; tone: 'pink' | 'gold' | 'blue' | 'green' }> = {
  high: { label: '高', tone: 'pink' },
  'medium-high': { label: '中高', tone: 'gold' },
  medium: { label: '中', tone: 'blue' },
  'medium-low': { label: '中低', tone: 'green' },
  low: { label: '低', tone: 'green' },
  P0: { label: 'P0', tone: 'pink' },
  P1: { label: 'P1', tone: 'gold' },
  P2: { label: 'P2', tone: 'blue' },
  P3: { label: 'P3', tone: 'green' },
  Hold: { label: 'Hold', tone: 'green' },
}

const STATUS_LABEL: Record<string, string> = {
  planned: '待启动',
  analyzing: '分析中',
  ready: '可生成 Brief',
  brief_ready: 'Brief 已就绪',
  article_drafted: '草稿已生成',
  published: '已发布',
  blocked: '阻塞',
  imported: '已导入',
  analyzed: '已分析',
  queued: '排队中',
  written: '已写稿',
  reviewed: '已复核',
  hold: '暂停',
  dropped: '放弃',
}

const STRATEGY_BLOCKING_ANOMALIES = new Set([
  'missing_page', 'missing_keyword', 'missing_topic', 'missing_page_type',
  'unsupported_page_type', 'mixed_topic', 'mixed_page_type', 'keyword_in_multiple_pages',
])

const CLUSTER_VALIDATION_LABEL: Record<string, string> = {
  validated: '已验证',
  provisional: '暂可用',
  bridge_review: '桥接复核',
  split_review: '拆簇复核',
}

const AI_RUN_TERMINAL = new Set(['done', 'failed', 'canceled'])
const AI_RUN_LABEL: Record<string, string> = { queued: '排队中', running: '分析中', done: '已完成', failed: '失败', canceled: '已停止' }

function formatNumber(n: number | string | null | undefined) {
  return new Intl.NumberFormat('en-US').format(Number(n ?? 0))
}

export function KeywordsPage({ onNotify, onOpenContent }: { onNotify?: (title: string, detail?: string) => void; onOpenContent?: () => void }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [importing, setImporting] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [validating, setValidating] = useState(false)
  const [aiRun, setAiRun] = useState<SemrushStrategyAiAnalysisRun | null>(null)
  const [cancelingAi, setCancelingAi] = useState(false)
  const [page, setPage] = useState(1)
  const [message, setMessage] = useState<string>()
  const [strategyPreview, setStrategyPreview] = useState<SemrushStrategyPreview | null>(null)
  const [strategyFile, setStrategyFile] = useState<File | null>(null)
  const [importedBatchId, setImportedBatchId] = useState('')
  const [businessId, setBusinessId] = useState('')
  const [targetMarket, setTargetMarket] = useState('US / English')
  const [keywordFilters, setKeywordFilters] = useState<KeywordFilters>({})
  const kw = useKeywordPage(refreshKey, page, 100, keywordFilters)
  const sites = useSites()
  const strategySites = (sites.data ?? []).filter((site) => site.status === 'active' && site.strategy_enabled && site.business_id)
  const businessIds = Array.from(new Set(strategySites.map((site) => site.business_id as string))).sort()
  const blockingAnomalies = strategyPreview?.anomalies.filter((item) => STRATEGY_BLOCKING_ANOMALIES.has(item.code)) ?? []
  const aiRunning = !!aiRun && !AI_RUN_TERMINAL.has(aiRun.status)

  useEffect(() => {
    if (!businessIds.length) {
      setBusinessId('')
      return
    }
    if (!businessIds.includes(businessId)) setBusinessId(businessIds[0])
  }, [businessId, businessIds.join('|')])

  useEffect(() => {
    setPage(1)
  }, [keywordFilters.aiAnalyzed, keywordFilters.intent, keywordFilters.serpFeature])

  useEffect(() => {
    setAiRun(null)
    if (!businessId) return
    const controller = new AbortController()
    void getLatestSemrushStrategyAiAnalysis(businessId, controller.signal)
      .then((run) => {
        if (!controller.signal.aborted && run && !AI_RUN_TERMINAL.has(run.status)) setAiRun(run)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : '无法恢复页面簇 AI 任务状态')
      })
    return () => controller.abort()
  }, [businessId])

  useEffect(() => {
    if (!aiRun || AI_RUN_TERMINAL.has(aiRun.status)) return
    const controller = new AbortController()
    const timer = window.setInterval(() => {
      void getSemrushStrategyAiAnalysis(aiRun.id, controller.signal)
        .then(setAiRun)
        .catch((error) => { if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : '无法读取 AI 任务状态') })
    }, 2000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [aiRun?.id, aiRun?.status])

  useEffect(() => {
    if (!aiRun || !AI_RUN_TERMINAL.has(aiRun.status)) return
    setPage(1)
    setRefreshKey((key) => key + 1)
    const detail = aiRun.error_message || aiRun.decision?.message || `已更新 ${formatNumber(aiRun.decision?.updated)} 个页面簇`
    setMessage(`页面簇 AI 任务${AI_RUN_LABEL[aiRun.status] || aiRun.status}：${detail}`)
    onNotify?.(`页面簇 AI 任务${AI_RUN_LABEL[aiRun.status] || aiRun.status}`, detail)
  }, [aiRun?.id, aiRun?.status])

  async function handleFile(file?: File) {
    if (!file || importing) return
    setImporting(true)
    setStrategyFile(null)
    setStrategyPreview(null)
    setImportedBatchId('')
    setMessage(`正在生成 Semrush Strategy Builder 只读预览：${file.name}…`)
    try {
      const result = await previewSemrushStrategyFile(file)
      setStrategyFile(file)
      setStrategyPreview(result)
      if (result.databases.length === 1) {
        const fileMarket = result.databases[0] === 'us' ? 'US / English' : result.databases[0] === 'de' ? 'DE / German' : ''
        if (fileMarket) setTargetMarket(fileMarket)
      }
      setMessage(`预览完成：${formatNumber(result.rowCount)} 条关键词、${formatNumber(result.pageClusterCount)} 个页面簇；未写入正式关键词池。`)
      onNotify?.('Semrush 页面簇预览完成', `${formatNumber(result.pageClusterCount)} 个页面簇，未写入数据库`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '导入失败')
    } finally {
      setImporting(false)
    }
  }

  async function handleConfirmImport() {
    if (!strategyFile || !strategyPreview || !businessId || !targetMarket || confirming || importedBatchId) return
    setConfirming(true)
    setMessage(`正在确认导入 ${formatNumber(strategyPreview.rowCount)} 条关键词…`)
    try {
      const result = await importSemrushStrategyFile(strategyFile, businessId, targetMarket)
      setImportedBatchId(result.sourceBatchId)
      setMessage(`${result.message} 当前业务关键词池共 ${formatNumber(result.poolCount)} 条。`)
      setPage(1)
      setRefreshKey((key) => key + 1)
      onNotify?.('Strategy Builder 导入完成', `${formatNumber(result.saved)} 条关键词已写入；未启动 AI、分站或策略`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '确认导入失败')
    } finally {
      setConfirming(false)
    }
  }

  async function handleValidateImported() {
    if (!businessId || validating) return
    setValidating(true)
    setMessage(`正在用本地规则整理 ${businessId} 的已导入页面簇…`)
    try {
      const result = await validateImportedSemrushStrategy(businessId)
      setStrategyFile(null)
      setStrategyPreview(result.preview)
      setImportedBatchId(result.sourceBatchId)
      setMessage(result.message)
      setPage(1)
      setRefreshKey((key) => key + 1)
      onNotify?.('页面簇整理完成', `${formatNumber(result.saved)} 条关键词已按本地规则校验；未启动 AI、分站或策略`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '整理已导入页面簇失败')
    } finally {
      setValidating(false)
    }
  }

  async function handleStartAiAnalysis() {
    if (!businessId || aiRunning) return
    setMessage('正在创建页面簇级 AI 分析任务…')
    try {
      const result = await startSemrushStrategyAiAnalysis(businessId)
      if (!result.run_id && result.status === 'done') {
        const detail = result.decision?.message || '当前业务的可用页面簇均已完成 AI 分析'
        setAiRun(null)
        setMessage(detail)
        setRefreshKey((key) => key + 1)
        onNotify?.('页面簇 AI 已完成', detail)
      } else {
        const runId = result.run_id
        if (!runId) throw new Error('AI 分析任务未返回任务编号')
        setAiRun({ id: runId, status: result.status, decision: result.decision })
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '启动页面簇 AI 分析失败')
    }
  }

  async function handleCancelAiAnalysis() {
    if (!aiRun || !aiRunning || cancelingAi) return
    setCancelingAi(true)
    try {
      const result = await cancelSemrushStrategyAiAnalysis(aiRun.id)
      setAiRun((current) => current ? { ...current, status: result.status } : current)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '停止页面簇 AI 分析失败')
    } finally {
      setCancelingAi(false)
    }
  }

  return (
    <section className="page" data-screen-label="关键词库">
      <div>
        <h1>关键词库</h1>
        <p>
          关键词来源：Semrush / 手工 / GSC。Strategy Builder 文件导入后先做页面簇校验，不启动 AI。
        </p>
        {message && <p>{message}</p>}
      </div>

      <div className="filter-row">
        <select className="input" value={businessId} onChange={(event) => setBusinessId(event.target.value)} disabled={importing || confirming || validating || aiRunning || !businessIds.length} aria-label="页面簇目标业务" style={{ maxWidth: 220 }}>
          {!businessIds.length && <option value="">没有启用策略的业务</option>}
          {businessIds.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <label className="btn btn--primary">
          <span className="msr">{importing ? 'progress_activity' : 'upload_file'}</span>
          {importing ? '预览中' : '预览 Strategy Builder'}
          <input
            type="file"
            accept=".xlsx"
            style={{ display: 'none' }}
            disabled={importing || confirming || validating || aiRunning}
            onChange={(event) => {
              void handleFile(event.target.files?.[0])
              event.currentTarget.value = ''
            }}
          />
        </label>
        <button className="btn" type="button" onClick={() => void handleValidateImported()} disabled={!businessId || importing || confirming || validating || aiRunning}>
          <span className="msr">{validating ? 'progress_activity' : 'rule'}</span>
          {validating ? '整理中' : '整理已导入页面簇'}
        </button>
        <button className="btn btn--primary" type="button" onClick={() => void handleStartAiAnalysis()} disabled={!businessId || importing || confirming || validating || aiRunning}>
          <span className="msr">{aiRunning ? 'progress_activity' : 'psychology'}</span>
          {aiRunning ? 'AI 分析中' : 'AI 分析可用页面簇'}
        </button>
        {aiRunning && <button className="btn" type="button" onClick={() => void handleCancelAiAnalysis()} disabled={cancelingAi}>{cancelingAi ? '停止中' : '停止 AI 分析'}</button>}
        {!aiRunning && !!kw.data?.total && onOpenContent && <button className="btn" type="button" onClick={onOpenContent}>下一步：前往今日策略</button>}
      </div>
      <p>“整理已导入页面簇”仅按本地规则校验并保存结果，不是 AI 分析，不会分配站点或执行策略。</p>
      <p>AI 仅分析“已验证”和“暂可用”页面簇；“桥接复核”和“拆簇复核”不会进入。</p>
      {aiRun && <p>AI 任务：{AI_RUN_LABEL[aiRun.status] || aiRun.status}{aiRun.decision && ` · 总计 ${formatNumber(aiRun.decision.total)} · 已更新 ${formatNumber(aiRun.decision.updated)} · 剩余 ${formatNumber(aiRun.decision.remaining)}`}{aiRun.decision?.message && ` · ${aiRun.decision.message}`}{aiRun.error_message && ` · ${aiRun.error_message}`}</p>}

      {strategyPreview && (
        <section className="card">
          <div className="card__title">
            <span>Semrush Strategy Builder · 只读预览</span>
            <span className={`tag tag--${importedBatchId ? 'green' : 'gray'}`}>{importedBatchId ? '已确认导入' : '未写入关键词池'}</span>
          </div>
          <p>{strategyPreview.filename} · 工作表：{strategyPreview.sheet} · Database：{strategyPreview.databases.join(', ') || '缺失'}</p>
          <div className="rail-stats">
            <div className="rail-stat"><span className="rail-stat__label">关键词</span><strong className="rail-stat__value">{formatNumber(strategyPreview.rowCount)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">Topic</span><strong className="rail-stat__value">{formatNumber(strategyPreview.topicCount)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">Page 页面簇</span><strong className="rail-stat__value">{formatNumber(strategyPreview.pageClusterCount)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">Pillar / Sub</span><strong className="rail-stat__value">{formatNumber(strategyPreview.pageTypeCounts['Pillar page'] || 0)} / {formatNumber(strategyPreview.pageTypeCounts['Sub page'] || 0)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">Volume 合计</span><strong className="rail-stat__value">{formatNumber(strategyPreview.metrics.volumeSum)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">KD 平均</span><strong className="rail-stat__value">{strategyPreview.metrics.kdAverage.toFixed(2)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">已验证</span><strong className="rail-stat__value">{formatNumber(strategyPreview.clusterValidation.counts.validated)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">暂可用</span><strong className="rail-stat__value">{formatNumber(strategyPreview.clusterValidation.counts.provisional)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">桥接复核</span><strong className="rail-stat__value">{formatNumber(strategyPreview.clusterValidation.counts.bridge_review)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">拆簇复核</span><strong className="rail-stat__value">{formatNumber(strategyPreview.clusterValidation.counts.split_review)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">可进入后续 AI</span><strong className="rail-stat__value">{formatNumber(strategyPreview.clusterValidation.aiReadyCount)}</strong></div>
            <div className="rail-stat"><span className="rail-stat__label">待复核</span><strong className="rail-stat__value">{formatNumber(strategyPreview.clusterValidation.reviewCount)}</strong></div>
          </div>
          <p>TOP 10 URL 覆盖率：{Math.round(strategyPreview.metrics.top10Coverage * 100)}%。意图分布按 Semrush 标签拆分统计。</p>
          {strategyPreview.anomalies.length > 0 ? (
            <details open>
              <summary>异常检查（{strategyPreview.anomalies.length} 类）</summary>
              {strategyPreview.anomalies.map((item) => <div key={item.code}>{item.label}：{formatNumber(item.count)}</div>)}
            </details>
          ) : <p>未发现缺失 Page、混合 Topic/Page type 或缺少 TOP 10 URL 的页面簇。</p>}
          {!!blockingAnomalies.length && <p>存在结构阻断异常，修正文件后重新预览才能确认导入。</p>}
          <div className="filter-row">
            <select className="input" value={targetMarket} onChange={(event) => setTargetMarket(event.target.value)} disabled={confirming || !!importedBatchId} aria-label="导入目标市场" style={{ maxWidth: 220 }}>
              <option value="US / English">US / English</option>
              <option value="DE / German">DE / German</option>
            </select>
            <button className="btn btn--primary" type="button" onClick={() => void handleConfirmImport()} disabled={confirming || !!importedBatchId || !strategyFile || !businessId || !targetMarket || !!blockingAnomalies.length}>
              <span className="msr">{confirming ? 'progress_activity' : importedBatchId ? 'check_circle' : 'database'}</span>
              {confirming ? '导入中' : importedBatchId ? '已导入' : `确认导入 ${formatNumber(strategyPreview.rowCount)} 条`}
            </button>
          </div>
          <p>确认仅写入关键词池并保留 Topic / Page / Page type / TOP 10；不会启动 AI、分配站点或执行策略。</p>
          <details>
            <summary>页面簇样例（前 12 个）</summary>
            <div className="tbl-wrap">
              <table className="tbl">
                <thead><tr><th>Page</th><th>校验状态</th><th>Topic</th><th>Page type</th><th className="tbl-num">关键词</th><th className="tbl-num">Volume</th><th className="tbl-num">KD</th><th className="tbl-num">TOP 10 URL</th></tr></thead>
                <tbody>{strategyPreview.clusters.slice(0, 12).map((cluster) => <tr key={cluster.id}><td className="tbl-strong">{cluster.page || '—'}</td><td title={cluster.validationReason}>{CLUSTER_VALIDATION_LABEL[cluster.validationStatus] || cluster.validationStatus}</td><td>{cluster.topics.join(' / ') || '—'}</td><td>{cluster.pageTypes.join(' / ') || '—'}</td><td className="tbl-num">{formatNumber(cluster.keywordCount)}</td><td className="tbl-num">{formatNumber(cluster.volumeSum)}</td><td className="tbl-num">{cluster.kdAverage.toFixed(2)}</td><td className="tbl-num">{cluster.top10UrlCount}</td></tr>)}</tbody>
              </table>
            </div>
          </details>
        </section>
      )}

      <div className="filter-row">
        <span className="chip chip--active">
          <span className="msr">all_inclusive</span>
          全部 {kw.data ? formatNumber(kw.data.total) : '—'}
        </span>
        <span className="chip">
          <span className="msr">database</span>
          第 {page} 页 · 每页 100 条
        </span>
        <select className="chip" value={keywordFilters.aiAnalyzed || ''} onChange={(event) => setKeywordFilters((current) => ({ ...current, aiAnalyzed: event.target.value }))} aria-label="按 AI 分析状态筛选">
          <option value="">AI 分析：全部</option>
          <option value="true">AI 分析：已分析</option>
          <option value="false">AI 分析：未分析</option>
        </select>
        <select className="chip" value={keywordFilters.intent || ''} onChange={(event) => setKeywordFilters((current) => ({ ...current, intent: event.target.value }))} aria-label="按意图筛选">
          <option value="">意图：全部</option>
          <option value="commercial">商业意图</option>
          <option value="transactional">交易意图</option>
          <option value="informational">信息意图</option>
          <option value="navigational">导航意图</option>
        </select>
        <select className="chip" value={keywordFilters.serpFeature || ''} onChange={(event) => setKeywordFilters((current) => ({ ...current, serpFeature: event.target.value }))} aria-label="按 SERP 特征筛选">
          <option value="">SERP 特征：全部</option>
          <option value="AI Overview">AI Overview</option>
          <option value="People also ask">People also ask</option>
          <option value="Video">Video</option>
          <option value="Image">Image</option>
          <option value="Local pack">Local pack</option>
          <option value="Featured snippet">Featured snippet</option>
        </select>
        {Object.values(keywordFilters).some(Boolean) && <button className="btn btn--ghost btn--xs" type="button" onClick={() => setKeywordFilters({})}>清除筛选</button>}
      </div>

      <div className="tbl-wrap">
        <DataGuard
          loading={kw.loading}
          error={kw.error}
          empty={!kw.data || kw.data.items.length === 0}
          emptyTitle="暂无关键词"
          emptyHint="请先通过 /api/v1/workflow/import-csv 或 Semrush 导入"
        >
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 36 }}>#</th>
                <th>关键词</th>
                <th>市场 / 语种</th>
                <th>Semrush DB</th>
                <th>意图</th>
                <th>主题</th>
                <th>Page 页面簇</th>
                <th>边界校验</th>
                <th>站点</th>
                <th className="tbl-num">月搜索量</th>
                <th className="tbl-num">KD</th>
                <th className="tbl-num">CPC</th>
                <th className="tbl-num">Trend</th>
                <th>SERP 特征</th>
                <th className="tbl-num">Score</th>
                <th>优先级</th>
                <th>状态</th>
                <th>AI 策略</th>
              </tr>
            </thead>
            <tbody>
              {(kw.data?.items ?? []).map((k, i) => {
                const intent = INTENT_TAG[k.intent] ?? INTENT_TAG.unknown
                const prio = PRIORITY_TAG[k.priority] ?? PRIORITY_TAG.P3
                const strategy = k.ai_review?.strategy
                return (
                  <tr key={k.id}>
                    <td style={{ color: 'var(--ink-400)' }}>{(page - 1) * 100 + i + 1}</td>
                    <td className="tbl-strong">{k.keyword}</td>
                    <td style={{ color: 'var(--ink-500)' }}>{k.market || '—'} / {k.language_code || '—'}</td>
                    <td style={{ color: 'var(--ink-500)' }}>{k.semrush_database || '—'}</td>
                    <td>
                      <span className={`tag tag--${intent.tone}`}>{intent.label}</span>
                    </td>
                    <td style={{ color: 'var(--ink-500)' }}>{k.topic_cluster}{k.cluster_role === 'pillar' ? ' · 中心词' : k.cluster_role === 'supporting' ? ' · 簇内词' : ''}</td>
                    <td style={{ color: 'var(--ink-500)' }}>{k.page_group || '—'}</td>
                    <td style={{ color: 'var(--ink-500)' }}>{CLUSTER_VALIDATION_LABEL[k.cluster_validation_status || ''] || k.cluster_validation_status || '—'}</td>
                    <td style={{ color: 'var(--ink-500)' }}>{k.assigned_site_label}</td>
                    <td className="tbl-num">{formatNumber(k.volume)}</td>
                    <td className="tbl-num">{k.kd}</td>
                    <td className="tbl-num">${Number(k.cpc ?? 0).toFixed(2)}</td>
                    <td className="tbl-num">{Number(k.trend ?? 0).toFixed(2)}</td>
                    <td style={{ color: 'var(--ink-500)', fontSize: 12, maxWidth: 220 }}>{Array.isArray(k.serp_features) ? k.serp_features.join(' · ') : '—'}</td>
                    <td className="tbl-num tbl-strong">{Number(k.score ?? 0).toFixed(1)}</td>
                    <td>
                      <span className={`tag tag--${prio.tone}`}>{prio.label}</span>
                    </td>
                    <td>
                      <span className="tag tag--gray">{STATUS_LABEL[k.status] ?? k.status}</span>
                    </td>
                    <td style={{ color: 'var(--ink-500)', fontSize: 12, maxWidth: 320 }}>
                      {strategy
                        ? strategy.briefDirection || strategy.strategyReason || strategy.contentAction || '已保存 AI 策略'
                        : '未分析'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </DataGuard>
      </div>
      {!!kw.data?.total && (
        <div className="filter-row">
          <button className="btn btn--ghost btn--xs" type="button" disabled={page <= 1 || kw.loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
          <span className="chip">第 {page} / {Math.ceil(kw.data.total / 100)} 页</span>
          <button className="btn btn--ghost btn--xs" type="button" disabled={page >= Math.ceil(kw.data.total / 100) || kw.loading} onClick={() => setPage((value) => value + 1)}>下一页</button>
        </div>
      )}
    </section>
  )
}
