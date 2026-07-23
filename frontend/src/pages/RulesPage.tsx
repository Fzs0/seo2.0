import { DataGuard } from '@/components/StateBlock'
import { useStandardRules } from '@/data/contentStrategy'
export function RulesPage() {
  const rules = useStandardRules()
  const standard = rules.data?.standard
  const thresholds = standard?.scoring?.thresholds ?? {}
  const signals = Object.entries(standard?.signals ?? {})
  const modules = standard?.articleBriefTemplate?.modules ?? []
  const gates = standard?.globalPlanning?.hardGates ?? []

  return (
    <section className="page" data-screen-label="规则配置">
      <div className="page-heading-row">
        <div>
          <h1>规则配置</h1>
          <p>查看当前生效的 SEO 标准、评分阈值、信号词和生产门禁。此页只读，修改仍通过后端配置流程完成。</p>
        </div>
        {rules.data && <div className="page-heading-meta"><span className="tag tag--green">{rules.data.healthy ? '已加载' : '不健康'}</span><span className="tag tag--gray">v{rules.data.version} · {rules.data.source}</span></div>}
      </div>

      <DataGuard loading={rules.loading} error={rules.error} empty={!standard} emptyTitle="暂无规则配置" emptyHint="后端标准接口未返回可用配置" minHeight={180}>
        {standard && (
          <>
            <div className="kpi-row">
              <Metric label="规则版本" value={rules.data?.version || '—'} sub={standard.name || '当前标准'} tone="gold" />
              <Metric label="评分上限" value={String(standard.scoring?.maxScore ?? '—')} sub={(standard.scoring?.components ?? []).join(' · ') || '未配置评分组件'} tone="blue" />
              <Metric label="Brief 模块" value={String(modules.length)} sub="写作前检查项" tone="green" />
              <Metric label="硬门禁" value={String(gates.length)} sub="阻止错误生产的条件" tone="pink" />
            </div>

            <div className="content-grid-2">
              <div className="card">
                <div className="card__title"><div className="card__title-icon"><span className="msr">score</span>评分阈值</div><span className="card__title-tag">真实配置</span></div>
                <div className="rule-config-list">
                  {Object.entries(thresholds).map(([key, value]) => <div className="rule-config-row" key={key}><span className="tag tag--gold">{key}</span><strong>{value}</strong><span>进入对应优先级队列</span></div>)}
                  {!Object.keys(thresholds).length && <div className="agent-empty">暂无评分阈值。</div>}
                </div>
              </div>

              <div className="card">
                <div className="card__title"><div className="card__title-icon"><span className="msr">flag</span>分类信号</div><span className="card__title-tag">{signals.length} 组</span></div>
                <div className="rule-config-list">
                  {signals.map(([name, values]) => <div className="rule-config-row rule-config-row--stack" key={name}><strong>{name}</strong><span>{values.length ? values.join(' · ') : '暂无信号词'}</span></div>)}
                  {!signals.length && <div className="agent-empty">暂无分类信号。</div>}
                </div>
              </div>
            </div>

            <div className="content-grid-2">
              <div className="card">
                <div className="card__title"><div className="card__title-icon"><span className="msr">checklist</span>Brief 检查模块</div><span className="card__title-tag">{modules.length} 项</span></div>
                <div className="rule-config-list">
                  {modules.map((module) => <div className="rule-config-row rule-config-row--stack" key={module.key || module.label}><strong>{module.label || module.key || '未命名模块'}</strong><span>{module.field || '已配置'}</span></div>)}
                  {!modules.length && <div className="agent-empty">暂无 Brief 模块。</div>}
                </div>
              </div>

              <div className="card">
                <div className="card__title"><div className="card__title-icon"><span className="msr">gpp_maybe</span>生产硬门禁</div><span className="card__title-tag">{gates.length} 项</span></div>
                <div className="rule-config-list">
                  {gates.map((gate) => <div className="rule-config-row rule-config-row--stack" key={gate.key}><div><strong>{gate.label}</strong><span className="tag tag--pink">{gate.priority || '未分级'}</span></div><span>{gate.blocker || '触发后暂停对应任务'}</span></div>)}
                  {!gates.length && <div className="agent-empty">暂无硬门禁。</div>}
                </div>
              </div>
            </div>
          </>
        )}
      </DataGuard>
    </section>
  )
}

function Metric({ label, value, sub, tone }: { label: string; value: string; sub: string; tone: 'gold' | 'blue' | 'green' | 'pink' }) {
  return <div className={`kpi kpi--${tone}`}><div className="kpi__head"><span className="kpi__label">{label}</span><span className="msr kpi__icon">tune</span></div><div className="kpi__value kpi__value--sm">{value}</div><div className="kpi__delta">{sub}</div></div>
}
