import { Sparkline, BarDecor } from '@/components/Charts'
import { DataGuard } from '@/components/StateBlock'
import { useGscOpportunities } from '@/hooks/useData'

interface OppRow {
  i: string
  icon: string
  iconBg: string
  iconColor: string
  title: string
  desc: string
  cat: string
  catBg: string
  catColor: string
  conf: string
  gain: string
}

const OPP_ROWS: OppRow[] = [
  {
    i: '01',
    icon: 'star',
    iconBg: '#f7edd3',
    iconColor: '#b98f2e',
    title: '高转化关键词池',
    desc: '围绕商业意图最高的核心词布局，预期带来稳定的转化流量。',
    cat: '商业价值',
    catBg: '#f7edd3',
    catColor: '#8a6d1f',
    conf: '92%',
    gain: '+45.6K',
  },
  {
    i: '02',
    icon: 'trending_up',
    iconBg: '#f9e4de',
    iconColor: '#c4685a',
    title: 'FAQ 型结果占比上升',
    desc: 'SERP 中 FAQ 模块占比提升，建议在内容中强化 FAQ 覆盖。',
    cat: '内容结构',
    catBg: '#e4ebf3',
    catColor: '#3f6182',
    conf: '86%',
    gain: '+18.4K',
  },
  {
    i: '03',
    icon: 'my_location',
    iconBg: '#eaefdc',
    iconColor: '#7e9150',
    title: '低竞争长尾机会',
    desc: '21 个低竞争长尾词排名阻力小，建议优先布局内容。',
    cat: '低竞争',
    catBg: '#eaefdc',
    catColor: '#4e6428',
    conf: '88%',
    gain: '+12.7K',
  },
  {
    i: '04',
    icon: 'flag',
    iconBg: '#e4ebf3',
    iconColor: '#5f84a8',
    title: '对比词空缺',
    desc: 'SERP 对比类结果页面较少，存在结构化机会。',
    cat: '对比内容',
    catBg: '#f7e7d9',
    catColor: '#c97b3f',
    conf: '81%',
    gain: '+9.2K',
  },
  {
    i: '05',
    icon: 'campaign',
    iconBg: '#f7e7d9',
    iconColor: '#c97b3f',
    title: '商业类聚合页机会',
    desc: 'Best x tools 列表型结果主导搜索，集合页内容收益明显。',
    cat: '集合页',
    catBg: '#f7edd3',
    catColor: '#8a6d1f',
    conf: '85%',
    gain: '+22.8K',
  },
]

export function OpportunitiesPage() {
  const opps = useGscOpportunities()

  return (
    <>
      <section className="page" data-screen-label="机会洞察">
        <div>
          <h1>机会洞察</h1>
          <p>AI 已筛出最值得投入的增长机会，请确认优先级与资源分配。</p>
        </div>

        <div className="kpi-row">
          <div className="kpi kpi--gold">
            <div className="kpi__head">
              <div className="kpi__icon-wrap kpi__icon-wrap--gold">
                <span className="msr msr-fill">star</span>
              </div>
              <span className="kpi__label">新增机会</span>
            </div>
            <div className="kpi__value">126</div>
            <div className="kpi__delta">较昨日 +18</div>
            <div className="kpi__spark">
              <BarDecor width={80} height={34} gradient="gold" />
            </div>
          </div>
          <div className="kpi kpi--pink">
            <div className="kpi__head">
              <div className="kpi__icon-wrap kpi__icon-wrap--pink">
                <span className="msr">trending_up</span>
              </div>
              <span className="kpi__label">访问潜力</span>
            </div>
            <div className="kpi__value">+45.6K</div>
            <div className="kpi__delta">月访问</div>
            <div className="kpi__spark">
              <Sparkline
                values={[6, 9, 7, 13, 11, 19, 22, 26]}
                width={86}
                height={28}
                color="#d77e6c"
                showEndDot
              />
            </div>
          </div>
          <div className="kpi kpi--green">
            <div className="kpi__head">
              <div className="kpi__icon-wrap kpi__icon-wrap--green">
                <span className="msr">my_location</span>
              </div>
              <span className="kpi__label">低竞争机会</span>
            </div>
            <div className="kpi__value">21</div>
            <div className="kpi__delta">占全部 17%</div>
            <div className="kpi__spark">
              <svg width={86} height={30} viewBox="0 0 86 30">
                <path
                  d="M0 30 Q14 12 28 20 Q42 28 56 14 Q70 2 86 12 L86 30 Z"
                  fill="#b7c48e"
                  opacity={0.7}
                />
              </svg>
            </div>
          </div>
          <div className="kpi kpi--blue">
            <div className="kpi__head">
              <div className="kpi__icon-wrap kpi__icon-wrap--blue">
                <span className="msr">task_alt</span>
              </div>
              <span className="kpi__label">待你确认</span>
            </div>
            <div className="kpi__value">7</div>
            <div className="kpi__delta">需要决策</div>
            <div
              style={{
                position: 'absolute',
                right: 16,
                bottom: 14,
                display: 'flex',
                gap: 5,
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: '#7c9ec1',
                }}
              />
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: '#a9c0d6',
                }}
              />
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: '#cbd9e6',
                }}
              />
            </div>
          </div>
        </div>

        <DataGuard
          loading={opps.loading}
          error={opps.error}
          empty={!opps.data || opps.data.length === 0}
          emptyTitle="暂未抓到机会"
          emptyHint="等待 GSC 数据同步完成后展示"
        >
          <div className="opp-list">
            {OPP_ROWS.map((r) => (
              <div className="opp-row" key={r.i}>
                <div className="opp-row__i">{r.i}</div>
                <div
                  className="opp-row__icon"
                  style={{ background: r.iconBg }}
                >
                  <span className="msr" style={{ color: r.iconColor }}>
                    {r.icon}
                  </span>
                </div>
                <div className="opp-row__main">
                  <div className="opp-row__title">{r.title}</div>
                  <div className="opp-row__desc">{r.desc}</div>
                </div>
                <div className="opp-row__cell">
                  <div className="opp-row__label">机会类型</div>
                  <span
                    className="tag"
                    style={{
                      marginTop: 5,
                      background: r.catBg,
                      color: r.catColor,
                    }}
                  >
                    {r.cat}
                  </span>
                </div>
                <div className="opp-row__cell opp-row__cell--sm">
                  <div className="opp-row__label">AI 置信度</div>
                  <div className="opp-row__value">
                    <span className="msr msr-fill">verified_user</span>
                    {r.conf}
                  </div>
                </div>
                <div className="opp-row__cell opp-row__cell--sm">
                  <div className="opp-row__label">预估收益</div>
                  <div className="opp-row__value">{r.gain}</div>
                  <div className="opp-row__sub">月访问</div>
                </div>
                <div className="opp-row__actions">
                  <span className="tag tag--gold">
                    <span className="msr" style={{ fontSize: 12 }}>insights</span>
                    建议查看
                  </span>
                  <span className="tag tag--gray">只读</span>
                </div>
              </div>
            ))}
            <div className="opp-more" role="note">
              查看全部机会
              <span className="msr">expand_more</span>
            </div>
          </div>
        </DataGuard>
      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="ai-card__head">
            <span className="msr msr-fill">auto_awesome</span>
            AI 机会摘要
          </div>
          <div className="ai-list">
            <SummaryRow
              icon="emoji_events"
              color="gold"
              label="最大机会"
              title="高转化关键词池"
              desc="预估带来最高访问潜力，建议优先推进。"
            />
            <SummaryRow
              icon="description"
              color="pink"
              label="建议动作"
              title="先复核 3 个商业页 Brief"
              desc="可快速补齐核心商业关键词覆盖缺口。"
            />
            <SummaryRow
              icon="group"
              color="blue"
              label="资源建议"
              title="内容生产优先级上调"
              desc="当前内容团队产能可支持快速推进。"
            />
            <SummaryRow
              icon="warning"
              color="orange"
              label="风险提醒"
              title="竞争页更新频率升高"
              desc="多个核心词竞争对手近期更新活跃。"
            />
          </div>
          <div className="btn--block btn--block-static" role="note">
            本周机会计划待接口接入
            <span className="msr">hourglass_empty</span>
          </div>
          <div className="btn-caption">当前仅展示基于 mock 数据的只读摘要</div>
        </div>
      </aside>
    </>
  )
}

function SummaryRow({
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