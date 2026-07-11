import { BarDecor, Donut } from '@/components/Charts'
import { DataGuard } from '@/components/StateBlock'
import { usePipelineStages, useStandardRules } from '@/hooks/useData'

const RULE_ROWS = [
  {
    icon: 'workspace_premium',
    color: 'gold',
    title: '商业价值优先',
    desc: '高商业意图关键词优先承接到主站商业页，减少低价值主题消耗。',
    weight: 78,
  },
  {
    icon: 'fact_check',
    color: 'blue',
    title: '内容质量门槛',
    desc: 'Brief 必须覆盖搜索意图、FAQ、内链与引用来源，缺项进入人工复核。',
    weight: 82,
  },
  {
    icon: 'shield',
    color: 'pink',
    title: '风险保守策略',
    desc: '涉及医疗、金融或强声明内容时，提升引用和人工复核权重。',
    weight: 65,
  },
  {
    icon: 'speed',
    color: 'green',
    title: '生产节奏控制',
    desc: '结合站点产能与历史表现分配任务，避免单站短期内容过密。',
    weight: 71,
  },
] as const

const CHANGE_ROWS = [
  {
    icon: 'link',
    color: 'gold',
    title: '内链锚文本覆盖不足',
    desc: '部分商业页 Brief 尚未覆盖目标锚文本规则。',
    tag: '待人工复核',
  },
  {
    icon: 'quiz',
    color: 'blue',
    title: 'FAQ 模块权重上调',
    desc: 'SERP 中 FAQ 结果占比提升，建议在结构清单中提高覆盖率。',
    tag: '观察中',
  },
  {
    icon: 'warning',
    color: 'pink',
    title: '高风险声明需引用来源',
    desc: '强事实性表述需要保留 reference.sources，避免无依据内容。',
    tag: '风险提醒',
  },
] as const

export function RulesPage() {
  const rules = useStandardRules()
  const pipeline = usePipelineStages()

  return (
    <>
      <section className="page" data-screen-label="规则方向">
        <div>
          <h1>规则方向</h1>
          <p>
            只读展示 seo-standard 当前策略权重与风险提醒；真实保存、审批或发布能力等待后端接口接入。
          </p>
        </div>

        <DataGuard
          loading={rules.loading}
          error={rules.error}
          empty={!rules.data}
          emptyTitle="暂无规则数据"
          emptyHint="后端标准接口接入后会展示规则方向"
        >
          {rules.data && (
            <>
              <div className="kpi-row">
                <div className="kpi kpi--gold">
                  <div className="kpi__head">
                    <span className="kpi__label">商业价值权重</span>
                    <span className="msr kpi__icon kpi__icon--gold">workspace_premium</span>
                  </div>
                  <div className="kpi__value">{rules.data.businessValue}%</div>
                  <div className="kpi__delta">用于关键词与站点分配排序</div>
                  <div className="kpi__spark">
                    <BarDecor width={82} height={34} gradient="gold" />
                  </div>
                </div>
                <div className="kpi kpi--blue">
                  <div className="kpi__head">
                    <span className="kpi__label">内容质量门槛</span>
                    <span className="msr kpi__icon kpi__icon--blue">fact_check</span>
                  </div>
                  <div className="kpi__value">{rules.data.contentQuality}%</div>
                  <div className="kpi__delta">Brief 结构完整度参考</div>
                  <div className="kpi__spark">
                    <Donut value={rules.data.contentQuality} size={54} color="#7c9ec1" />
                  </div>
                </div>
                <div className="kpi kpi--pink">
                  <div className="kpi__head">
                    <span className="kpi__label">风险约束</span>
                    <span className="msr kpi__icon kpi__icon--pink">shield</span>
                  </div>
                  <div className="kpi__value">{rules.data.riskConstraints}</div>
                  <div className="kpi__delta">需要人工复核的规则项</div>
                </div>
                <div className="kpi kpi--green">
                  <div className="kpi__head">
                    <span className="kpi__label">活跃规则</span>
                    <span className="msr kpi__icon kpi__icon--green">rule</span>
                  </div>
                  <div className="kpi__value">{rules.data.activeRules}</div>
                  <div className="kpi__delta">{rules.data.ruleGroups} 个规则组</div>
                </div>
              </div>

              <div className="content-grid-2">
                <div className="card">
                  <div className="card__title">
                    <div className="card__title-icon">
                      <span className="msr">tune</span>
                      策略权重
                    </div>
                    <span className="card__title-tag">只读</span>
                  </div>
                  {RULE_ROWS.map((row) => (
                    <div className="range-row" key={row.title}>
                      <div className={`range-row__icon ai-list__dot--${row.color}`}>
                        <span className="msr">{row.icon}</span>
                      </div>
                      <div className="range-row__name">
                        <h4>{row.title}</h4>
                        <p>{row.desc}</p>
                      </div>
                      <div className="range-row__slider" aria-label={`${row.title} ${row.weight}%`}>
                        <span>低</span>
                        <div className="bar" role="presentation">
                          <div
                            className={`bar__fill bar__fill--${row.color}`}
                            style={{ width: `${row.weight}%` }}
                          />
                        </div>
                        <span>{row.weight}%</span>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="card">
                  <div className="card__title">
                    <div className="card__title-icon">
                      <span className="msr">rule_folder</span>
                      规则复核队列
                    </div>
                    <span className="card__title-tag">{rules.data.pendingChanges} 项待看</span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {CHANGE_ROWS.map((row) => (
                      <div className="rule-row" key={row.title}>
                        <div className={`rule-row__icon ai-list__dot--${row.color}`}>
                          <span className="msr">{row.icon}</span>
                        </div>
                        <div className="rule-row__main">
                          <div className="rule-row__title">{row.title}</div>
                          <div className="rule-row__sub">{row.desc}</div>
                        </div>
                        <span className={`tag tag--${row.color}`}>{row.tag}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <DataGuard
                loading={pipeline.loading}
                error={pipeline.error}
                empty={!pipeline.data || pipeline.data.length === 0}
                emptyTitle="暂无流程数据"
              >
                <div className="card">
                  <div className="card__title">规则应用流程</div>
                  <div className="flow">
                    {(pipeline.data ?? []).map((stage, index) => (
                      <div key={stage.key} style={{ display: 'contents' }}>
                        <div className={'flow__step' + (stage.status === 'running' ? ' flow__step--active' : '')}>
                          <div className={`flow__icon ai-list__dot--${stage.status === 'done' ? 'green' : stage.status === 'running' ? 'gold' : 'blue'}`}>
                            <span className="msr">
                              {stage.status === 'done'
                                ? 'check'
                                : stage.status === 'running'
                                ? 'progress_activity'
                                : 'radio_button_unchecked'}
                            </span>
                          </div>
                          <div>
                            <div className="flow__title">{stage.label}</div>
                            <div className={stage.status === 'running' ? 'flow__sub flow__sub--gold' : 'flow__sub'}>
                              {stage.status === 'done' ? '已完成' : stage.status === 'running' ? '进行中' : '等待中'}
                            </div>
                          </div>
                        </div>
                        {index < (pipeline.data?.length ?? 0) - 1 && (
                          <>
                            <div className="flow__connector" />
                            <div className="flow__node" />
                            <div className="flow__connector" />
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                  <div className="flow-caption">当前页面仅展示流程状态，不触发规则保存或发布。</div>
                </div>
              </DataGuard>
            </>
          )}
        </DataGuard>
      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="ai-card__head">
            <span className="msr msr-fill">auto_awesome</span>
            AI 规则摘要
          </div>
          <div className="ai-list">
            <SummaryItem
              icon="workspace_premium"
              color="gold"
              label="主策略"
              title="商业价值优先"
              desc="优先覆盖高商业意图词，避免内容资源分散。"
            />
            <SummaryItem
              icon="fact_check"
              color="blue"
              label="质量门槛"
              title="结构完整度需要稳定"
              desc="Brief 应包含搜索意图、FAQ、引用和内链建议。"
            />
            <SummaryItem
              icon="warning"
              color="pink"
              label="风险提醒"
              title="强声明内容必须复核"
              desc="缺少来源或证据时仅展示为待人工复核状态。"
            />
            <SummaryItem
              icon="schedule"
              color="green"
              label="生产节奏"
              title="保持站点产能平衡"
              desc="博客站与主站的节奏差异会影响路由建议。"
            />
          </div>
        </div>

        <div className="suggest">
          <span className="msr suggest__icon">visibility</span>
          <div className="suggest__body">
            <div className="suggest__title">只读预览</div>
            <div className="suggest__desc">此页不提供写入、确认或发布操作，便于后续接口联调替换数据。</div>
          </div>
        </div>
      </aside>
    </>
  )
}

function SummaryItem({
  icon,
  color,
  label,
  title,
  desc,
}: {
  icon: string
  color: 'gold' | 'pink' | 'blue' | 'green' | 'orange'
  label: string
  title: string
  desc: string
}) {
  return (
    <div className="ai-list__item">
      <div className={`ai-list__dot ai-list__dot--${color}`}>
        <span className="msr">{icon}</span>
      </div>
      <div>
        <div className="ai-list__label">{label}</div>
        <div className="ai-list__title">{title}</div>
        <div className="ai-list__desc">{desc}</div>
      </div>
    </div>
  )
}
