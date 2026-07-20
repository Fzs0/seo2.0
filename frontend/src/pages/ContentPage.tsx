import { useEffect, useState } from 'react'
import { cancelSeoStrategy, clearStrategyQueue, executeSeoStrategy, generateSeoStrategies, reviewSeoStrategy, saveStrategyPlan, scanContentAudit, stopSeoStrategy, useAutomationStatus, useSites, useStrategies, useStrategyCandidates, useStrategyEffects, useStrategyPlan, type ContentAuditReport, type StrategyCandidate, type StrategyFilters, type StrategyTask } from '@/hooks/useData'
import type { StrategyEffect, StrategyEffectStatus } from '@/types/domain'

const EFFECT_STATUSES: Array<{ status: StrategyEffectStatus; label: string; tone: string }> = [
  { status: 'observing', label: '观察中', tone: 'blue' },
  { status: 'winner', label: '有效', tone: 'green' },
  { status: 'neutral', label: '中性', tone: 'gray' },
  { status: 'loser', label: '未达预期', tone: 'pink' },
  { status: 'inconclusive', label: '证据不足', tone: 'gold' },
  { status: 'contaminated', label: '数据受干扰', tone: 'pink' },
]

const EFFECT_LABELS: Record<string, string> = {
  clicks: '点击', impressions: '展示', ctr: 'CTR', avg_position: '平均排名', position: '排名',
  sessions: '会话', users: '用户', conversions: '转化', revenue: '收益', checked_at: '检查时间', captured_at: '采集时间', window_days: '数据窗口', day: '观察天数',
}

function effectMetrics(value: unknown, prefix = ''): Array<[string, string]> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value as Record<string, unknown>).flatMap(([key, item]) => {
    const label = EFFECT_LABELS[key] || `${prefix}${key}`
    if (item && typeof item === 'object' && !Array.isArray(item)) return effectMetrics(item, `${label} · `)
    if (item == null || item === '') return []
    return [[label, typeof item === 'number' ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(item) : String(item)]]
  })
}

function formatEffectDate(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}

function latestCheckpoint(effect: StrategyEffect) {
  return effect.checkpoints?.[effect.checkpoints.length - 1]
}

function formatElapsed(startedAt?: string | null) {
  if (!startedAt) return ''
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.floor(seconds / 60)} 分钟`
}

function strategyGroupLabel(strategyType: string) {
  if (strategyType === 'new_article') return '生文策略'
  if (strategyType === 'update_article') return '更新旧文策略'
  return '其他 SEO 策略'
}

function isHoldStrategy(strategy?: StrategyTask) {
  return strategy?.strategy_type === 'hold' || strategy?.priority === 'Hold'
}

function isSelectableCandidate(candidate: StrategyCandidate) {
  return !isHoldStrategy(candidate) && !['excluded', 'superseded', 'executed'].includes(candidate.candidate_status || '')
}

export function ContentPage({ onNotify }: { onNotify?: (title: string, detail?: string) => void }) {
  const [strategyRefreshKey, setStrategyRefreshKey] = useState(0)
  const [strategyRunning, setStrategyRunning] = useState(false)
  const [strategyClearing, setStrategyClearing] = useState(false)
  const [strategyReviewing, setStrategyReviewing] = useState<string | null>(null)
  const [strategyCanceling, setStrategyCanceling] = useState<string | null>(null)
  const [strategyRetrying, setStrategyRetrying] = useState<string | null>(null)
  const [strategyStopping, setStrategyStopping] = useState<string | null>(null)
  const [selectedStrategyIds, setSelectedStrategyIds] = useState<Set<string>>(new Set())
  const [selectedExecuting, setSelectedExecuting] = useState(false)
  const [strategyMessage, setStrategyMessage] = useState<string>()
  const [actionBudget, setActionBudget] = useState(4)
  const [siteQuotas, setSiteQuotas] = useState<Record<string, number>>({})
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<Set<string>>(new Set())
  const [planSaving, setPlanSaving] = useState(false)
  const [auditRunning, setAuditRunning] = useState(false)
  const [auditMessage, setAuditMessage] = useState<string>()
  const [auditReport, setAuditReport] = useState<ContentAuditReport | null>(null)
  const [strategyFilters, setStrategyFilters] = useState<StrategyFilters>({})
  const [businessId, setBusinessId] = useState('')
  const sites = useSites()
  const publishSites = sites.data ?? []
  const strategySites = publishSites.filter((site) => site.status === 'active' && site.strategy_enabled && site.business_id)
  const businessIds = Array.from(new Set(strategySites.map((site) => site.business_id as string))).sort()
  const currentStrategySites = strategySites.filter((site) => site.business_id === businessId)
  const candidates = useStrategyCandidates(strategyRefreshKey, businessId || undefined)
  const todayPlan = useStrategyPlan(strategyRefreshKey, businessId || undefined)
  const strategies = useStrategies(strategyRefreshKey, 'pending', {}, businessId || undefined)
  const executionStrategies = useStrategies(strategyRefreshKey, 'approved', {}, businessId || undefined)
  const effects = useStrategyEffects(strategyRefreshKey, businessId || undefined)
  const automation = useAutomationStatus(strategyRefreshKey, businessId || undefined)
  useEffect(() => {
    if (!businessIds.length) {
      setBusinessId('')
      return
    }
    if (!businessIds.includes(businessId)) setBusinessId(businessIds[0])
  }, [businessId, businessIds.join('|')])
  const planFingerprint = JSON.stringify(todayPlan.data ?? null)
  const currentSiteIds = currentStrategySites.map((site) => site.id).join('|')
  useEffect(() => {
    if (!businessId) {
      setActionBudget(4)
      setSiteQuotas({})
      setSelectedCandidateIds(new Set())
      return
    }
    if (todayPlan.loading) return
    setActionBudget(todayPlan.data?.action_budget ?? 4)
    setSiteQuotas(Object.fromEntries(currentStrategySites.map((site) => [site.id, todayPlan.data?.site_quotas?.[site.id] ?? 2])))
    setSelectedCandidateIds(new Set(todayPlan.data?.selected_candidate_ids ?? []))
  }, [businessId, currentSiteIds, planFingerprint, todayPlan.loading])
  useEffect(() => {
    if (!executionStrategies.data?.some((item) => ['queued', 'running'].includes(item.execution_status || ''))) return
    const timer = window.setInterval(() => setStrategyRefreshKey((key) => key + 1), 5000)
    return () => window.clearInterval(timer)
  }, [executionStrategies.data])
  const groupedStrategies = (strategies.data ?? []).reduce<Record<string, StrategyTask[]>>((groups, strategy) => {
    const key = strategy.strategy_type || 'other'
    ;(groups[key] ||= []).push(strategy)
    return groups
  }, {})
  const filteredCandidates = (candidates.data?.items ?? []).filter((candidate) => {
    const search = strategyFilters.search?.trim().toLowerCase()
    return (!search || candidate.title.toLowerCase().includes(search) || candidate.query.toLowerCase().includes(search))
      && (!strategyFilters.siteId || candidate.site_id === strategyFilters.siteId)
      && (!strategyFilters.strategyType || candidate.strategy_type === strategyFilters.strategyType)
      && (!strategyFilters.priority || candidate.priority === strategyFilters.priority)
      && (!strategyFilters.evidenceLevel || candidate.evidence_level === strategyFilters.evidenceLevel)
  })
  const availableCandidates = filteredCandidates.filter(isSelectableCandidate)
  const holdCandidates = filteredCandidates.filter((candidate) => !isSelectableCandidate(candidate))
  const selectedCandidates = (candidates.data?.items ?? []).filter((candidate) => selectedCandidateIds.has(candidate.id) && isSelectableCandidate(candidate))
  const selectedBySite = selectedCandidates.reduce<Record<string, number>>((counts, candidate) => {
    if (candidate.site_id) counts[candidate.site_id] = (counts[candidate.site_id] || 0) + 1
    return counts
  }, {})
  const overQuotaSite = currentStrategySites.find((site) => (selectedBySite[site.id] || 0) > (siteQuotas[site.id] ?? 0))
  const planIssue = selectedCandidates.length > actionBudget
    ? `已选 ${selectedCandidates.length} 条，超过今日总预算 ${actionBudget} 条`
    : overQuotaSite
      ? `${overQuotaSite.name} 已选 ${selectedBySite[overQuotaSite.id]} 条，超过站点配额 ${siteQuotas[overQuotaSite.id] ?? 0} 条`
      : ''
  const activeExecutionStrategies = (executionStrategies.data ?? []).filter((strategy) => !['done', 'canceled'].includes(strategy.execution_status || ''))
  const selectableStrategies = (strategies.data ?? []).filter((strategy) => !isHoldStrategy(strategy))
  const selectedStrategies = selectableStrategies.filter((strategy) => selectedStrategyIds.has(strategy.id))
  const recentContentTasks = automation.data?.business_id === businessId
    ? automation.data.recent.filter((item) => ['new_article', 'update_article'].includes(item.task_type))
    : []
  const effectsWithoutBaseline = (effects.data ?? []).filter((effect) => !effect.baseline || !effectMetrics(effect.baseline).length).length
  async function handleGenerateStrategies() {
    if (strategyRunning || strategyClearing || !businessId) return
    setStrategyRunning(true)
    setStrategyMessage('正在读取全站证据并刷新候选池…')
    try {
      const result = await generateSeoStrategies(businessId, undefined, actionBudget, siteQuotas, 20)
      const totalCandidates = result.total_candidates ?? result.candidates
      const plannedActions = result.planned_actions ?? result.plan?.selected_candidate_ids?.length ?? result.items.length
      setStrategyMessage(result.error
        ? `${result.error}；当前保存 ${totalCandidates} 条候选。`
        : `候选池已刷新：共 ${totalCandidates} 条，今日计划 ${plannedActions} 条${result.unassigned ? `，已交给 AI 分配站点 ${result.unassigned} 个` : ''}${result.quarantined ? `，撤回不合规分配 ${result.quarantined} 个` : ''}。`)
      setSelectedStrategyIds(new Set())
      setStrategyRefreshKey((key) => key + 1)
      onNotify?.('策略候选池已刷新', `共 ${totalCandidates} 条候选，今日计划 ${plannedActions} 条`)
    } catch (error) {
      setStrategyMessage(error instanceof Error ? error.message : '策略生成失败')
    } finally {
      setStrategyRunning(false)
    }
  }

  async function handleClearStrategies() {
    if (!businessId || strategyClearing || planSaving || selectedExecuting || !!strategyReviewing) return
    if (!window.confirm(`确定清空业务 ${businessId} 的当前策略候选和今日计划吗？诊断数据、关键词、文章库存和执行历史会保留。`)) return
    setStrategyClearing(true)
    try {
      const result = await clearStrategyQueue(businessId)
      setSelectedCandidateIds(new Set())
      setSelectedStrategyIds(new Set())
      setStrategyMessage(`已清空 ${result.candidates_cleared} 条候选、${result.plans_cleared} 个计划和 ${result.strategies_canceled} 条待执行策略；诊断数据与执行历史已保留。`)
      setStrategyRefreshKey((key) => key + 1)
      onNotify?.('当前策略已清空', '诊断数据、关键词、文章库存和执行历史未删除')
    } catch (error) {
      setStrategyMessage(error instanceof Error ? error.message : '策略清空失败')
    } finally {
      setStrategyClearing(false)
    }
  }

  function toggleCandidateSelection(candidate: StrategyCandidate) {
    if (!isSelectableCandidate(candidate)) return
    setSelectedCandidateIds((current) => {
      const next = new Set(current)
      if (next.has(candidate.id)) next.delete(candidate.id)
      else next.add(candidate.id)
      return next
    })
  }

  async function handleSavePlan() {
    if (!businessId || planSaving || strategyClearing || planIssue) return
    setPlanSaving(true)
    try {
      const saved = await saveStrategyPlan(businessId, actionBudget, siteQuotas, Array.from(selectedCandidateIds))
      setStrategyMessage(`今日计划已保存：预算 ${saved.action_budget} 条，已选 ${saved.selected_candidate_ids.length} 条。`)
      setSelectedStrategyIds(new Set())
      setStrategyRefreshKey((key) => key + 1)
      onNotify?.('今日计划已保存', `${saved.selected_candidate_ids.length} 条策略等待审核执行`)
    } catch (error) {
      setStrategyMessage(error instanceof Error ? error.message : '今日计划保存失败')
    } finally {
      setPlanSaving(false)
    }
  }

  async function handleContentAudit() {
    if (auditRunning || !businessId) return
    setAuditRunning(true)
    setAuditMessage('正在同步所有站点并检查文章字段…')
    try {
      const result = await scanContentAudit(businessId, 100, 200)
      setAuditReport(result)
      const aiMessage = result.data_sources?.ai?.status === 'completed'
        ? `AI 复核 ${result.data_sources.ai.reviewed} 条，已保存 ${result.data_sources.ai.persisted || 0} 条`
        : `AI 未完成（${result.data_sources?.ai?.status || '未执行'}）`
      setAuditMessage(`扫描完成：${result.summary.articles} 篇文章、${result.summary.page_clusters || 0} 个页面簇；发现 ${result.summary.update_candidates} 个更新候选、${result.summary.new_candidates} 个新文候选、${result.summary.hold_candidates} 个 Hold，其中 ${result.summary.coverage_review_clusters || 0} 个簇需要复核正文覆盖；${aiMessage}。`)
      onNotify?.('全站内容扫描完成', `扫描 ${result.summary.articles} 篇文章`)
    } catch (error) {
      setAuditMessage(error instanceof Error ? error.message : '全站内容扫描失败')
    } finally {
      setAuditRunning(false)
    }
  }

  async function handleReviewStrategy(taskId: string, approved: boolean) {
    if (strategyReviewing || selectedExecuting) return
    if (approved && isHoldStrategy(strategies.data?.find((strategy) => strategy.id === taskId))) {
      setStrategyMessage('Hold 策略不能通过，请先补齐证据并重新生成策略。')
      return
    }
    setStrategyReviewing(taskId)
    try {
      const result = await reviewSeoStrategy(taskId, approved, approved)
      setStrategyMessage(approved ? `策略已通过并${result.status === 'running' ? '开始执行' : '进入执行队列'}。` : '策略已驳回。')
      setStrategyRefreshKey((key) => key + 1)
      if (approved) {
        onNotify?.('策略已开始执行', result.status === 'running' ? '后台任务已启动' : '并发已满，任务正在排队')
      }
    } catch (error) {
      setStrategyMessage(error instanceof Error ? error.message : '策略审核失败')
    } finally {
      setStrategyReviewing(null)
    }
  }

  function toggleStrategySelection(taskId: string) {
    setSelectedStrategyIds((current) => {
      const next = new Set(current)
      if (next.has(taskId)) next.delete(taskId)
      else next.add(taskId)
      return next
    })
  }

  function toggleAllStrategies() {
    setSelectedStrategyIds((current) => {
      const allSelected = selectableStrategies.length > 0 && selectableStrategies.every((strategy) => current.has(strategy.id))
      const next = new Set(current)
      for (const strategy of selectableStrategies) {
        if (allSelected) next.delete(strategy.id)
        else next.add(strategy.id)
      }
      return next
    })
  }

  async function handleExecuteSelected() {
    if (selectedExecuting || !selectedStrategies.length) return
    setSelectedExecuting(true)
    setStrategyMessage(`正在逐条批准 ${selectedStrategies.length} 条策略…`)
    let approved = 0
    const failed: string[] = []
    for (const strategy of selectedStrategies) {
      try {
        await reviewSeoStrategy(strategy.id, true, true)
        approved += 1
      } catch {
        failed.push(strategy.title)
      }
    }
    setStrategyMessage(`已批准并启动 ${approved} 条策略${failed.length ? `；${failed.length} 条启动失败` : ''}。`)
    if (approved) onNotify?.('已启动选中策略', `${approved} 条策略已进入后台执行`)
    setSelectedStrategyIds(new Set())
    setSelectedExecuting(false)
    setStrategyRefreshKey((key) => key + 1)
  }

  async function handleCancelStrategy(taskId: string) {
    if (strategyCanceling) return
    setStrategyCanceling(taskId)
    try {
      await cancelSeoStrategy(taskId)
      setStrategyMessage('已从执行队列移除，关键词保留为待分析状态。')
      setStrategyRefreshKey((key) => key + 1)
    } catch (error) {
      setStrategyMessage(error instanceof Error ? error.message : '移除队列失败')
    } finally {
      setStrategyCanceling(null)
    }
  }

  async function handleRetryStrategy(taskId: string) {
    if (strategyRetrying) return
    setStrategyRetrying(taskId)
    setStrategyMessage('正在重新执行；QA 通过后将更新线上旧文。')
    try {
      const result = await executeSeoStrategy(taskId)
      setStrategyMessage(result.ok ? '重新执行完成。' : result.error || '重新执行失败。')
      setStrategyRefreshKey((key) => key + 1)
    } catch (error) {
      setStrategyMessage(error instanceof Error ? error.message : '重新执行失败')
    } finally {
      setStrategyRetrying(null)
    }
  }

  async function handleStopStrategy(taskId: string) {
    if (strategyStopping) return
    setStrategyStopping(taskId)
    try {
      const result = await stopSeoStrategy(taskId)
      if (!result.ok) throw new Error(result.error || '执行任务已结束')
      setStrategyMessage('已停止执行。')
      setStrategyRefreshKey((key) => key + 1)
    } catch (error) {
      setStrategyMessage(error instanceof Error ? error.message : '停止执行失败')
    } finally {
      setStrategyStopping(null)
    }
  }

  return (
      <section className="page strategy-page" data-screen-label="今日策略">
        <div>
          <h1>今日策略</h1>
          <p>按下面五步完成策略闭环；扫描和分析由 AI 完成，外站写入仍由你确认，发布后继续观察效果。</p>
        </div>

        <div className="flow" aria-label="今日策略操作步骤">
          <div className="flow__step"><div className="flow__icon"><span className="msr">travel_explore</span></div><div><div className="flow__title">1. 扫描站点</div><div className="flow__sub">读取文章和数据</div></div></div>
          <div className="flow__connector" />
          <div className="flow__step"><div className="flow__icon"><span className="msr">auto_awesome</span></div><div><div className="flow__title">2. 生成候选</div><div className="flow__sub">AI 给出完整建议</div></div></div>
          <div className="flow__connector" />
          <div className="flow__step"><div className="flow__icon"><span className="msr">checklist</span></div><div><div className="flow__title">3. 保存计划</div><div className="flow__sub">你决定今天做什么</div></div></div>
          <div className="flow__connector" />
          <div className="flow__step"><div className="flow__icon"><span className="msr">play_circle</span></div><div><div className="flow__title">4. 审核执行</div><div className="flow__sub">后台持续运行</div></div></div>
          <div className="flow__connector" />
          <div className="flow__step"><div className="flow__icon"><span className="msr">monitoring</span></div><div><div className="flow__title">5. 效果复盘</div><div className="flow__sub">基线对比与结论</div></div></div>
        </div>

        <div className="card strategy-review-card strategy-step-card">
          <div className="card__title">
            <span>1. 扫描当前业务</span>
            <div className="content-card__tags">
              {auditReport && <span className="tag tag--blue">已扫描 {auditReport.summary.sites} 个站点</span>}
            </div>
          </div>
          <div className="strategy-review-card__toolbar">
            <select className="input" value={businessId} onChange={(event) => { setBusinessId(event.target.value); setAuditReport(null); setStrategyFilters({}) }} aria-label="当前策略业务" style={{ maxWidth: 220 }}>
              {!businessIds.length && <option value="">没有已启用业务</option>}
              {businessIds.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
            <button className="btn btn--primary" type="button" onClick={() => void handleContentAudit()} disabled={auditRunning || !businessId}>
              <span className="msr">{auditRunning ? 'progress_activity' : 'travel_explore'}</span>
              {auditRunning ? '扫描中…' : '扫描全部站点'}
            </button>
            <span className="btn-caption">当前业务：{businessId || '未设置'}；参与站点：{currentStrategySites.map((site) => site.name).join('、') || '无'}；已排除 {Math.max(0, publishSites.length - currentStrategySites.length)} 个站点。</span>
          </div>
          {auditMessage && <div className="strategy-review-card__message">{auditMessage}</div>}
        </div>

        <div className="card strategy-review-card strategy-step-card">
          <div className="card__title">
            <span>2. 生成并选择候选</span>
            <div className="content-card__tags">
              <span className="tag tag--blue">候选 {candidates.data?.total || 0} 条</span>
              <span className="tag tag--blue">GSC / GA4 真实数据</span>
            </div>
          </div>
          <div className="strategy-review-card__toolbar">
            <button className="btn btn--primary" type="button" onClick={() => void handleGenerateStrategies()} disabled={strategyRunning || strategyClearing || !businessId}>
              <span className="msr">{strategyRunning ? 'progress_activity' : 'auto_awesome'}</span>
              {strategyRunning ? '生成中…' : '生成 / 刷新候选池'}
            </button>
            <button className="btn btn--ghost" type="button" onClick={() => void handleClearStrategies()} disabled={strategyClearing || strategyRunning || planSaving || selectedExecuting || !!strategyReviewing || !businessId}>
              <span className="msr">delete_sweep</span>
              {strategyClearing ? '清空中…' : '清空当前策略'}
            </button>
            <span className="btn-caption">数据库当前显示：候选 {candidates.data?.total || 0} 条，今日计划 {todayPlan.data?.selected_candidate_ids.length || 0} 条，待审核 {strategies.data?.length || 0} 条。</span>
          </div>
          <div className="content-section-label">
            <strong>2. 完整候选池</strong>
            <span>AI 发现的全部策略建议；Hold 单列且不可选</span>
          </div>
          {!!candidates.data?.total && (
            <details className="content-audit-review-item__details">
              <summary>筛选候选</summary>
              <div className="strategy-review-card__filters" aria-label="策略筛选">
                <input value={strategyFilters.search || ''} onChange={(event) => setStrategyFilters((current) => ({ ...current, search: event.target.value }))} placeholder="按关键词筛选" aria-label="按关键词筛选" />
                <select value={strategyFilters.siteId || ''} onChange={(event) => setStrategyFilters((current) => ({ ...current, siteId: event.target.value }))} aria-label="按站点筛选">
                  <option value="">全部站点</option>
                  {currentStrategySites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
                </select>
                <select value={strategyFilters.strategyType || ''} onChange={(event) => setStrategyFilters((current) => ({ ...current, strategyType: event.target.value }))} aria-label="按策略类型筛选">
                  <option value="">全部策略类型</option><option value="new_article">新写文章</option><option value="update_article">更新旧文</option>
                </select>
                <select value={strategyFilters.priority || ''} onChange={(event) => setStrategyFilters((current) => ({ ...current, priority: event.target.value }))} aria-label="按优先级筛选">
                  <option value="">全部优先级</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option><option value="Hold">Hold</option>
                </select>
                {Object.values(strategyFilters).some(Boolean) && <button className="btn btn--ghost btn--xs" type="button" onClick={() => setStrategyFilters({})}>清除筛选</button>}
              </div>
            </details>
          )}
          {candidates.loading && <div className="agent-empty">正在加载候选池…</div>}
          {!candidates.loading && candidates.error && <div className="strategy-review-card__error">{candidates.error}</div>}
          {!candidates.loading && !candidates.error && !filteredCandidates.length && <div className="agent-empty">当前业务暂无有效候选。请先完成站点扫描，再点击“生成 / 刷新候选池”；系统不会自动执行空计划。</div>}
          {!!availableCandidates.length && (
            <div className="strategy-review-list strategy-review-list--scroll" tabIndex={0} aria-label="可选策略候选列表">
              <div className="strategy-review-card__subhead">可选候选 · {availableCandidates.length} 条</div>
              {availableCandidates.map((candidate) => (
                <div className={`strategy-review-item${selectedCandidateIds.has(candidate.id) ? ' is-selected' : ''}`} key={candidate.id}>
                  <label className="strategy-review-item__select" title="选入今日计划">
                    <input
                      type="checkbox"
                      checked={selectedCandidateIds.has(candidate.id)}
                      disabled={planSaving}
                      onChange={() => toggleCandidateSelection(candidate)}
                      aria-label={`选入今日计划：${candidate.title}`}
                    />
                  </label>
                  <div className="strategy-review-item__main">
                    <div className="strategy-review-item__head">
                      <span className={`tag tag--${candidate.strategy_type === 'update_article' ? 'green' : 'gold'}`}>{candidate.strategy_type === 'update_article' ? '更新旧文' : candidate.strategy_type === 'new_article' ? '新写文章' : '其他 SEO'}</span>
                      <strong>{candidate.title}</strong>
                    </div>
                    <div className="strategy-review-item__meta">站点：{candidate.site_name || '未分配'} · 关键词：{candidate.query} · {candidate.priority}</div>
                    <div className="strategy-review-item__action">建议：{candidate.recommended_action}</div>
                    <details className="content-audit-review-item__details"><summary>查看判断依据</summary><div>{candidate.reason}</div></details>
                  </div>
                </div>
              ))}
            </div>
          )}
          {!!holdCandidates.length && (
            <details className="strategy-collapsible">
              <summary>Hold / 不可选 · {holdCandidates.length} 条</summary>
              <div className="strategy-review-list strategy-hold-list">
              {holdCandidates.map((candidate) => (
                <div className="strategy-review-item is-hold" key={candidate.id}>
                  <div className="strategy-review-item__main">
                    <div className="strategy-review-item__head"><span className="tag tag--gray">{candidate.candidate_status || 'Hold'}</span><strong>{candidate.title}</strong></div>
                    <div className="strategy-review-item__meta">站点：{candidate.site_name || '未分配'} · 关键词：{candidate.query} · {candidate.priority}</div>
                    <div className="strategy-review-item__reason">当前证据不足，暂不进入今日计划。</div>
                    <details className="content-audit-review-item__details"><summary>查看阻塞原因</summary><div>{candidate.reason}</div></details>
                  </div>
                </div>
              ))}
              </div>
            </details>
          )}
        </div>

        <div className="card strategy-review-card strategy-step-card">
          <div className="card__title">
            <span>3. 保存并审核今日计划</span>
            <span className="tag tag--gold">今日计划 {strategies.data?.length || 0} 条</span>
          </div>
          <div className="strategy-plan-editor">
            <div className="strategy-plan-editor__head">
              <label>
                今日总预算
                <input type="number" min="0" max="200" step="1" value={actionBudget} onChange={(event) => setActionBudget(Math.max(0, Math.min(200, Math.floor(Number(event.target.value) || 0))))} />
                条
              </label>
              <span>已选 {selectedCandidates.length} 条；预算可为 0，不会为了凑数选弱候选。</span>
              <button className="btn btn--primary btn--xs" type="button" disabled={planSaving || !!planIssue || todayPlan.loading} onClick={() => void handleSavePlan()}>
                {planSaving ? '保存中…' : '保存今日计划'}
              </button>
            </div>
            <details className="content-audit-review-item__details">
              <summary>按站点设置配额（可选）</summary>
              <div className="strategy-quota-grid" aria-label="各站点今日配额">
                {currentStrategySites.map((site) => (
                  <label key={site.id}>
                    <span>{site.name}</span>
                    <input
                      type="number"
                      min="0"
                      max="200"
                      step="1"
                      value={siteQuotas[site.id] ?? 0}
                      onChange={(event) => setSiteQuotas((current) => ({ ...current, [site.id]: Math.max(0, Math.min(200, Math.floor(Number(event.target.value) || 0))) }))}
                      aria-label={`${site.name} 今日配额`}
                    />
                    条
                  </label>
                ))}
              </div>
            </details>
            {planIssue && <div className="strategy-review-card__error">{planIssue}</div>}
            {todayPlan.error && <div className="strategy-review-card__error">{todayPlan.error}</div>}
          </div>
          <div className="content-section-label">
            <strong>待审核动作</strong>
            <span>保存后的策略才能在这里审核执行；刷新页面不会丢失</span>
          </div>
          {!!strategies.data?.length && (
            <div className="strategy-selection-bar">
              <label>
                <input
                  type="checkbox"
                  checked={selectableStrategies.length > 0 && selectableStrategies.every((strategy) => selectedStrategyIds.has(strategy.id))}
                  onChange={toggleAllStrategies}
                  disabled={!selectableStrategies.length || selectedExecuting}
                />
                选择当前可执行策略
              </label>
              <span>已选 {selectedStrategies.length} 条；Hold 不可选</span>
              <button className="btn btn--primary btn--xs" type="button" disabled={!selectedStrategies.length || selectedExecuting || !!strategyReviewing} onClick={() => void handleExecuteSelected()}>
                {selectedExecuting ? '正在批准并启动…' : '立即执行选中策略'}
              </button>
            </div>
          )}
          {strategyMessage && <div className="strategy-review-card__message">{strategyMessage}</div>}
          {strategies.loading && <div className="agent-empty">正在加载今日计划…</div>}
          {!strategies.loading && strategies.error && <div className="strategy-review-card__error">{strategies.error}</div>}
          {!strategies.loading && !strategies.error && !(strategies.data || []).length && <div className="agent-empty">今日计划为空。从候选池勾选后保存即可。</div>}
          {!!strategies.data?.length && (
            <div className="strategy-review-list strategy-review-list--plan" tabIndex={0} aria-label="今日待审核策略列表">
              {Object.entries(groupedStrategies).map(([group, items]) => (
                <div key={group}>
                  <div className="strategy-review-card__subhead">{strategyGroupLabel(group)} · {items.length} 条</div>
                  {items.map((strategy) => (
                    <div className={`strategy-review-item${!isHoldStrategy(strategy) && selectedStrategyIds.has(strategy.id) ? ' is-selected' : ''}${isHoldStrategy(strategy) ? ' is-hold' : ''}`} key={strategy.id}>
                      <label className="strategy-review-item__select" title={isHoldStrategy(strategy) ? 'Hold 策略不可执行' : '选中策略'}>
                        <input
                          type="checkbox"
                          checked={!isHoldStrategy(strategy) && selectedStrategyIds.has(strategy.id)}
                          disabled={isHoldStrategy(strategy) || selectedExecuting}
                          onChange={() => toggleStrategySelection(strategy.id)}
                          aria-label={`选择策略：${strategy.title}`}
                        />
                      </label>
                      <div className="strategy-review-item__main">
                        <div className="strategy-review-item__head">
                          <span className={`tag tag--${strategy.strategy_type === 'update_article' ? 'green' : 'gold'}`}>{strategy.strategy_type === 'update_article' ? '更新旧文' : strategy.strategy_type === 'new_article' ? '新写文章' : '其他 SEO'}</span>
                          <strong>{strategy.title}</strong>
                        </div>
                        <div className="strategy-review-item__meta">站点：{strategy.site_name || '未分配'} · 关键词：{strategy.query} · {strategy.priority}</div>
                        <div className="strategy-review-item__reason">{strategy.reason}</div>
                        <div className="strategy-review-item__action">建议：{strategy.recommended_action}</div>
                        {!!strategy.internal_link_plan?.length && <div className="strategy-review-item__action">内链：将关联 {strategy.internal_link_plan.length} 篇同站点内容</div>}
                      </div>
                      <div className="strategy-review-item__actions">
                        <button className="btn btn--primary btn--xs" type="button" disabled={isHoldStrategy(strategy) || !!strategyReviewing || selectedExecuting} title={isHoldStrategy(strategy) ? 'Hold 策略不可执行' : undefined} onClick={() => void handleReviewStrategy(strategy.id, true)}>立即执行</button>
                        <button className="btn btn--ghost btn--xs" type="button" disabled={!!strategyReviewing || selectedExecuting} onClick={() => void handleReviewStrategy(strategy.id, false)}>驳回</button>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="strategy-results-grid">
          <div className="card strategy-review-card strategy-step-card">
            <div className="card__title">
              <span>4. 执行状态</span>
              <span className="tag tag--blue">队列 {activeExecutionStrategies.length} 条</span>
            </div>
            {!!activeExecutionStrategies.length ? (
              <div className="strategy-review-list strategy-execution-list" tabIndex={0} aria-label="策略执行状态列表">
              {activeExecutionStrategies.map((strategy) => (
                <div className="strategy-review-item" key={strategy.id}>
                  <div className="strategy-review-item__main">
                    <div className="strategy-review-item__head">
                      <span className={`tag tag--${strategy.execution_status === 'done' ? 'green' : strategy.execution_status === 'failed' || strategy.execution_status === 'blocked' ? 'pink' : 'gold'}`}>
                        {strategy.execution_status === 'done' ? '已完成' : strategy.execution_status === 'running' ? '执行中' : strategy.execution_status === 'failed' || strategy.execution_status === 'blocked' ? '执行失败' : '待执行'}
                      </span>
                      <strong>{strategy.title}</strong>
                    </div>
                    <div className="strategy-review-item__meta">站点：{strategy.site_name || '未分配'} · 关键词：{strategy.query}{strategy.execution_run_after ? ` · 计划 ${new Date(strategy.execution_run_after).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}` : ''}{strategy.execution_status === 'running' && strategy.execution_started_at ? ` · 已运行 ${formatElapsed(strategy.execution_started_at)}` : ''}{strategy.execution_error ? ` · ${strategy.execution_error}` : ''}</div>
                    {!!strategy.execution_logs?.length && (
                      <details className="automation-panel__hint">
                        <summary>执行链路日志（{strategy.execution_logs.length}）</summary>
                        {strategy.execution_logs.map((log, index) => (
                          <div key={`${log.at || index}-${log.stage || ''}`}>{log.at ? new Date(log.at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—'} · {log.stage || 'unknown'}{log.message ? ` · ${log.message}` : ''}</div>
                        ))}
                      </details>
                    )}
                  </div>
                  <div className="strategy-review-item__actions">
                    {strategy.execution_status === 'failed' && (
                      <button className="btn btn--primary btn--xs" type="button" disabled={strategyRetrying === strategy.id} title="将重新生成并检查，QA 通过后更新线上旧文" onClick={() => void handleRetryStrategy(strategy.id)}>
                        {strategyRetrying === strategy.id ? '重新执行中…' : '重新执行'}
                      </button>
                    )}
                    {strategy.execution_status === 'running' && (
                      <button className="btn btn--ghost btn--xs" type="button" disabled={strategyStopping === strategy.id} onClick={() => void handleStopStrategy(strategy.id)}>
                        {strategyStopping === strategy.id ? '停止中…' : '停止执行'}
                      </button>
                    )}
                    {['queued', 'failed', 'blocked'].includes(strategy.execution_status || '') && (
                      <button className="btn btn--ghost btn--xs" type="button" disabled={strategyCanceling === strategy.id} onClick={() => void handleCancelStrategy(strategy.id)}>
                        {strategyCanceling === strategy.id ? '移除中…' : '移除队列'}
                      </button>
                    )}
                  </div>
                </div>
              ))}
              </div>
            ) : (
              <div className="timeline__desc">当前没有正在执行或等待处理的策略。</div>
            )}
          </div>

          <div className="card strategy-step-card">
            <div className="card__title">
              <span>最近执行结果</span>
              <span className="tag tag--gray">真实任务</span>
            </div>
            <div className="strategy-recent-list" tabIndex={0} aria-label="最近内容任务结果列表">
              {recentContentTasks.length ? recentContentTasks.slice(0, 10).map((item) => (
                <div className="brief-row" key={`${item.id}-${item.status}`}>
                  <div className="brief-row__icon" style={{ background: item.status === 'done' ? '#e8eedf' : '#f9e4de' }}>
                    <span className="msr" style={{ color: item.status === 'done' ? '#6f8449' : '#a14a3c' }}>{item.status === 'done' ? 'check_circle' : 'error'}</span>
                  </div>
                  <div className="brief-row__main">
                    <div className="brief-row__top"><span className="brief-row__title">{item.title}</span></div>
                    <div className="strategy-review-item__meta">{item.finished_at ? new Date(item.finished_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '尚未结束'}{item.error_message ? ` · ${item.error_message}` : ''}</div>
                  </div>
                  <span className={`brief-row__status ${item.status === 'done' ? 'brief-row__status--green' : 'brief-row__status--pink'}`}>{item.status === 'done' ? '已完成' : '需处理'}</span>
                </div>
              )) : (
                <div className="timeline__desc">暂无内容任务执行记录。</div>
              )}
            </div>
          </div>
        </div>

        <div className="card strategy-review-card strategy-step-card" id="strategy-effects">
          <div className="card__title">
            <span>5. 效果观察</span>
            <div className="content-card__tags">
              {EFFECT_STATUSES.map((group) => <span className={`tag tag--${group.tone}`} key={group.status}>{group.label} {(effects.data ?? []).filter((item) => item.status === group.status).length}</span>)}
            </div>
          </div>
          <div className={effectsWithoutBaseline ? 'strategy-effect-notice strategy-effect-notice--warning' : 'strategy-effect-notice'}>
            {effectsWithoutBaseline
              ? `${effectsWithoutBaseline} 条策略没有执行前基线，不能称为完整闭环，也不能可靠判断策略效果。`
              : effects.data?.length
                ? '所有观察项均已保存执行前基线，可结合后续检查判断效果。'
                : '尚无效果观察记录。策略执行并保存执行前基线后，才会进入完整闭环。'}
          </div>
          {effects.loading && <div className="agent-empty">正在加载策略效果…</div>}
          {!effects.loading && effects.error && <div className="strategy-review-card__error">{effects.error}</div>}
          {!effects.loading && !effects.error && !(effects.data ?? []).length && <div className="agent-empty">当前业务暂无可观察策略。</div>}
          {EFFECT_STATUSES.map((group) => {
            const items = (effects.data ?? []).filter((effect) => effect.status === group.status)
            if (!items.length) return null
            return (
              <section className="strategy-effect-group" key={group.status} aria-label={`${group.label}策略`}>
                <div className="strategy-review-card__subhead">{group.label} · {items.length} 条</div>
                <div className="strategy-effect-grid">
                  {items.map((effect) => {
                    const checkpoint = latestCheckpoint(effect)
                    const baselineMetrics = effectMetrics(effect.baseline)
                    const checkpointMetrics = effectMetrics(checkpoint)
                    const outcomeMetrics = effectMetrics(effect.outcome)
                    const outcomeText = typeof effect.outcome === 'string' ? (EFFECT_STATUSES.find((item) => item.status === effect.outcome)?.label || effect.outcome) : ''
                    const checkpointAt = checkpoint && String(checkpoint.checked_at || checkpoint.captured_at || checkpoint.created_at || effect.updated_at)
                    const targetUrl = effect.target_url && /^https?:\/\//i.test(effect.target_url) ? effect.target_url : ''
                    return (
                      <article className="strategy-review-item" key={effect.id}>
                        <div className="strategy-review-item__main">
                          <div className="strategy-review-item__head">
                            <span className={`tag tag--${group.tone}`}>{group.label}</span>
                            <strong>{effect.query || effect.strategy_fingerprint}</strong>
                          </div>
                          <div className="strategy-review-item__meta">站点：{effect.site_name || effect.site_id || '未记录'} · 动作：{effect.action_type || '未记录'}</div>
                          <div className="strategy-effect-item__section">
                            <strong>执行前基线</strong>
                            {baselineMetrics.length ? <div className="strategy-effect-item__metrics">{baselineMetrics.slice(0, 8).map(([label, value]) => <span key={`${label}-${value}`}>{label}：{value}</span>)}</div> : <span className="strategy-effect-item__missing">未记录，当前不可判定完整闭环</span>}
                          </div>
                          <div className="strategy-effect-item__section">
                            <strong>最近检查{checkpointAt ? ` · ${formatEffectDate(checkpointAt)}` : ''}</strong>
                            {checkpointMetrics.length ? <div className="strategy-effect-item__metrics">{checkpointMetrics.slice(0, 8).map(([label, value]) => <span key={`${label}-${value}`}>{label}：{value}</span>)}</div> : <span>尚未检查</span>}
                          </div>
                          {(outcomeText || outcomeMetrics.length > 0) && <div className="strategy-effect-item__section"><strong>效果结论</strong>{outcomeText || <div className="strategy-effect-item__metrics">{outcomeMetrics.slice(0, 8).map(([label, value]) => <span key={`${label}-${value}`}>{label}：{value}</span>)}</div>}</div>}
                          <div className="strategy-review-item__meta">下次检查：{formatEffectDate(effect.next_check_at)}{effect.cooldown_until ? ` · 冷却至 ${formatEffectDate(effect.cooldown_until)}` : ''}</div>
                          <div className="strategy-review-item__action">目标 URL：{targetUrl ? <a href={targetUrl} target="_blank" rel="noreferrer">{targetUrl}</a> : effect.target_url || '未记录'}</div>
                        </div>
                      </article>
                    )
                  })}
                </div>
              </section>
            )
          })}
        </div>
      </section>
  )
}
