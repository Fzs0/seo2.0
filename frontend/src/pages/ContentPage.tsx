import { useEffect, useState } from 'react'
import {
  createAndStartStrategyRun,
  scanContentAudit,
  useStrategyEffects,
  useStrategyRuns,
  type ContentAuditReport,
} from '@/data/contentStrategy'
import { useSites } from '@/data/sites'
import { useBusinessScope } from '@/businessScope'

function formatDate(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}

const RUN_STATUS: Record<string, string> = {
  queued: '等待启动',
  discovering_sites: '发现站点',
  checking_capabilities: '检查能力',
  gathering_evidence: '收集证据',
  planning: '制定计划',
  refreshing_evidence: '刷新证据',
  replanning: '重新规划',
  awaiting_approval: '等待审批',
  executing: '执行中',
  verifying: '回读验证',
  observing: '效果观察',
  completed: '已完成',
  partial: '部分完成',
  blocked: '已阻塞',
  failed: '失败',
  canceled: '已取消',
}

export function ContentPage({
  onNotify,
}: {
  onNotify?: (title: string, detail?: string) => void
}) {
  const { businessId } = useBusinessScope()
  const sites = useSites()
  const [refreshKey, setRefreshKey] = useState(0)
  const [auditRunning, setAuditRunning] = useState(false)
  const [runStarting, setRunStarting] = useState(false)
  const [message, setMessage] = useState<string>()
  const [auditReport, setAuditReport] = useState<ContentAuditReport | null>(null)
  const runs = useStrategyRuns(refreshKey, businessId || undefined)
  const effects = useStrategyEffects(refreshKey, businessId || undefined)

  const currentSites = (sites.data ?? []).filter(
    (site) =>
      site.business_id === businessId
      && site.status === 'active'
      && site.strategy_enabled,
  )

  useEffect(() => {
    setAuditReport(null)
    setMessage(undefined)
  }, [businessId])

  useEffect(() => {
    if (!(runs.data ?? []).some((run) => !['completed', 'partial', 'blocked', 'failed', 'canceled', 'awaiting_approval'].includes(run.status))) return
    const timer = window.setInterval(() => setRefreshKey((key) => key + 1), 5000)
    return () => window.clearInterval(timer)
  }, [runs.data])

  async function handleAudit() {
    if (!businessId || auditRunning) return
    setAuditRunning(true)
    setMessage('正在同步站点并检查内容证据…')
    try {
      const result = await scanContentAudit(businessId, 100, 200)
      setAuditReport(result)
      setMessage(
        `扫描完成：${result.summary.sites} 个站点、${result.summary.articles} 篇文章；`
        + `${result.summary.update_candidates} 个更新方向、${result.summary.new_candidates} 个新文方向、`
        + `${result.summary.hold_candidates} 个 Hold。`,
      )
      onNotify?.('全站内容扫描完成', `${result.summary.articles} 篇文章`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '全站内容扫描失败')
    } finally {
      setAuditRunning(false)
    }
  }

  async function handleStartRun() {
    if (!businessId || runStarting) return
    setRunStarting(true)
    setMessage('正在创建正式 Strategy Run，并按全部站点制定计划…')
    try {
      const run = await createAndStartStrategyRun(businessId)
      setMessage(
        `Strategy Run 已进入“${RUN_STATUS[run.status] || run.status}”；`
        + '候选和关键词仅作研究证据，只有正式计划中的 Action 才能进入审批。',
      )
      setRefreshKey((key) => key + 1)
      onNotify?.('Strategy Run 已启动', run.run_id)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Strategy Run 启动失败')
    } finally {
      setRunStarting(false)
    }
  }

  return (
    <section className="page strategy-page" data-screen-label="今日策略">
      <div>
        <h1>今日策略</h1>
        <p>系统现只保留一条写执行链：Strategy Run → 正式计划 → Strategy Action → 回读验证 → 效果观察。</p>
      </div>

      <div className="flow" aria-label="统一 SEO 策略流程">
        <div className="flow__step"><div className="flow__icon"><span className="msr">travel_explore</span></div><div><div className="flow__title">1. 收集证据</div><div className="flow__sub">API / GSC / GA4 / SERP</div></div></div>
        <div className="flow__connector" />
        <div className="flow__step"><div className="flow__icon"><span className="msr">account_tree</span></div><div><div className="flow__title">2. 正式计划</div><div className="flow__sub">逐站点自主决策</div></div></div>
        <div className="flow__connector" />
        <div className="flow__step"><div className="flow__icon"><span className="msr">fact_check</span></div><div><div className="flow__title">3. Action 审批</div><div className="flow__sub">预览与能力快照绑定</div></div></div>
        <div className="flow__connector" />
        <div className="flow__step"><div className="flow__icon"><span className="msr">verified</span></div><div><div className="flow__title">4. 执行验证</div><div className="flow__sub">幂等写入与回读</div></div></div>
        <div className="flow__connector" />
        <div className="flow__step"><div className="flow__icon"><span className="msr">monitoring</span></div><div><div className="flow__title">5. 效果观察</div><div className="flow__sub">7 / 14 / 28 / 56 天</div></div></div>
      </div>

      <div className="card strategy-review-card strategy-step-card">
        <div className="card__title">
          <span>当前业务范围</span>
          <span className="tag tag--blue">{currentSites.length} 个活动站点</span>
        </div>
        <div className="strategy-review-card__toolbar">
          <span className="chip"><span className="msr">business_center</span>{businessId || '未选择业务'}</span>
          <button className="btn btn--ghost" type="button" onClick={() => void handleAudit()} disabled={!businessId || auditRunning || runStarting}>
            <span className="msr">{auditRunning ? 'progress_activity' : 'travel_explore'}</span>
            {auditRunning ? '扫描中…' : '刷新内容证据'}
          </button>
          <button className="btn btn--primary" type="button" onClick={() => void handleStartRun()} disabled={!businessId || runStarting || auditRunning}>
            <span className="msr">{runStarting ? 'progress_activity' : 'play_circle'}</span>
            {runStarting ? '启动中…' : '执行今日策略'}
          </button>
        </div>
        <div className="btn-caption">
          覆盖：{currentSites.map((site) => site.name).join('、') || '无活动站点'}。
          “安全上限”只防止失控，不参与正常选题；超出部分会记录为 deferred。
        </div>
        {message && <div className="strategy-review-card__message">{message}</div>}
        {auditReport && (
          <div className="strategy-effect-item__metrics">
            <span>文章：{auditReport.summary.articles}</span>
            <span>更新方向：{auditReport.summary.update_candidates}</span>
            <span>新文方向：{auditReport.summary.new_candidates}</span>
            <span>Hold：{auditReport.summary.hold_candidates}</span>
          </div>
        )}
      </div>

      <div className="card strategy-review-card strategy-step-card">
        <div className="card__title">
          <span>Strategy Runs</span>
          <span className="tag tag--gold">{runs.data?.length || 0} 次</span>
        </div>
        {runs.loading && <div className="agent-empty">正在加载运行记录…</div>}
        {runs.error && <div className="strategy-review-card__error">{runs.error}</div>}
        {!runs.loading && !runs.error && !(runs.data ?? []).length && <div className="agent-empty">当前业务还没有 Strategy Run。</div>}
        {!!runs.data?.length && (
          <div className="strategy-review-list" aria-label="Strategy Run 列表">
            {runs.data.map((run) => (
              <article className="strategy-review-item" key={run.run_id}>
                <div className="strategy-review-item__main">
                  <div className="strategy-review-item__head">
                    <span className={`tag tag--${['failed', 'blocked', 'partial'].includes(run.status) ? 'pink' : run.status === 'completed' ? 'green' : 'gold'}`}>
                      {RUN_STATUS[run.status] || run.status}
                    </span>
                    <strong>{run.run_id}</strong>
                  </div>
                  <div className="strategy-review-item__meta">
                    模式：{run.mode === 'approval_execution' ? '审批后执行' : '只读模拟'} ·
                    创建：{formatDate(run.created_at)} ·
                    计划：{run.plan_id || '尚未生成'}
                  </div>
                  <div className="strategy-review-item__action">
                    本批执行 {run.counts?.execute_now || 0} · 稍后执行 {run.counts?.deferred || 0} ·
                    Hold {run.counts?.hold || 0} · 配置修复 {run.counts?.configuration_repair || 0}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      <div className="card strategy-review-card strategy-step-card" id="strategy-effects">
        <div className="card__title">
          <span>效果观察</span>
          <span className="tag tag--blue">{effects.data?.length || 0} 条</span>
        </div>
        {effects.loading && <div className="agent-empty">正在加载效果记录…</div>}
        {effects.error && <div className="strategy-review-card__error">{effects.error}</div>}
        {!effects.loading && !effects.error && !(effects.data ?? []).length && <div className="agent-empty">暂无效果观察记录。</div>}
        {!!effects.data?.length && (
          <div className="strategy-review-list" aria-label="效果观察列表">
            {effects.data.slice(0, 50).map((effect) => (
              <article className="strategy-review-item" key={effect.id}>
                <div className="strategy-review-item__main">
                  <div className="strategy-review-item__head">
                    <span className="tag tag--blue">{effect.status}</span>
                    <strong>{effect.query || effect.strategy_fingerprint}</strong>
                  </div>
                  <div className="strategy-review-item__meta">
                    站点：{effect.site_name || effect.site_id || '未记录'} ·
                    动作：{effect.action_type || '未记录'} ·
                    下次检查：{formatDate(effect.next_check_at)}
                  </div>
                  <div className="strategy-review-item__action">
                    目标：{effect.target_url || '尚未回填'}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
