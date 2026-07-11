import { Sparkline, Bars } from '@/components/Charts'
import { DataGuard } from '@/components/StateBlock'
import { useGscOpportunities } from '@/hooks/useData'

interface SerpRow {
  icon: string
  color: 'pink' | 'gold' | 'blue'
  keyword: string
  intent: string
  posFrom: number
  posTo: number
  deltaText: string
  deltaUp: boolean
  sparkColor: string
}

const SERP_ROWS: SerpRow[] = [
  {
    icon: 'search',
    color: 'pink',
    keyword: 'best project management software',
    intent: '商业类词',
    posFrom: 12,
    posTo: 8,
    deltaText: '↑ 4',
    deltaUp: true,
    sparkColor: '#d77e6c',
  },
  {
    icon: 'language',
    color: 'gold',
    keyword: 'team collaboration tools',
    intent: '商业类词',
    posFrom: 7,
    posTo: 6,
    deltaText: '↑ 1',
    deltaUp: true,
    sparkColor: '#c9a03c',
  },
  {
    icon: 'timer',
    color: 'blue',
    keyword: 'online gantt chart',
    intent: '工具类词',
    posFrom: 15,
    posTo: 14,
    deltaText: '↑ 1',
    deltaUp: true,
    sparkColor: '#7c9ec1',
  },
]

interface CompetitorRow {
  icon: string
  color: 'pink' | 'gold' | 'blue' | 'green'
  title: string
  sub: string
  count: number
}

const COMP_ROWS: CompetitorRow[] = [
  { icon: 'update', color: 'pink', title: '页面内容更新', sub: '近 7 天内有更新', count: 26 },
  { icon: 'title', color: 'gold', title: '标题调整', sub: '标题发生变化', count: 18 },
  { icon: 'quiz', color: 'blue', title: 'FAQ 增加', sub: '新增问题与答案', count: 12 },
  { icon: 'link', color: 'green', title: '内链增强', sub: '内链数量明显提升', count: 9 },
]

export function SerpPage() {
  const gsc = useGscOpportunities()
  return (
    <>
      <section className="page" data-screen-label="SERP 洞察">
        <div>
          <h1>SERP 洞察</h1>
          <p>AI 正在观察搜索结果变化，识别竞争格局与内容缺口。</p>
        </div>

        <div className="kpi-row">
          <div className="kpi kpi--pink">
            <div className="kpi__head">
              <span className="kpi__label">SERP 波动</span>
              <span className="msr kpi__icon kpi__icon--pink">monitoring</span>
            </div>
            <div className="kpi__value">18</div>
            <div className="kpi__delta">较昨日 +5</div>
            <div className="kpi__spark">
              <Sparkline
                values={[6, 9, 7, 13, 11, 19, 22, 26]}
                width={86}
                height={26}
                color="#d77e6c"
              />
            </div>
          </div>
          <div className="kpi kpi--gold">
            <div className="kpi__head">
              <span className="kpi__label">竞争页更新</span>
              <span className="msr kpi__icon kpi__icon--gold">bolt</span>
            </div>
            <div className="kpi__value">42</div>
            <div className="kpi__delta">较昨日 +12</div>
            <div className="kpi__spark">
              <Bars values={[6, 8, 10, 12, 14, 18, 22, 28]} width={86} height={32} color="#d9b25c" />
            </div>
          </div>
          <div className="kpi kpi--blue">
            <div className="kpi__head">
              <span className="kpi__label">内容缺口</span>
              <span className="msr kpi__icon kpi__icon--blue">space_dashboard</span>
            </div>
            <div className="kpi__value">16</div>
            <div className="kpi__delta">较昨日 +3</div>
            <div
              style={{
                position: 'absolute',
                left: 18,
                right: 18,
                bottom: 14,
                height: 6,
                borderRadius: 999,
                background: 'rgba(23,22,20,0.08)',
              }}
            >
              <div
                style={{
                  width: '42%',
                  height: '100%',
                  borderRadius: 999,
                  background: '#7c9ec1',
                }}
              />
            </div>
          </div>
          <div className="kpi kpi--orange">
            <div className="kpi__head">
              <span className="kpi__label">风险关键词</span>
              <span className="msr kpi__icon kpi__icon--orange">warning</span>
            </div>
            <div className="kpi__value">7</div>
            <div className="kpi__delta">较昨日 +2</div>
            <div className="kpi__spark">
              <svg width={86} height={26} viewBox="0 0 86 26">
                <polyline
                  points="0,20 14,16 28,18 42,10 56,13 70,5 86,8"
                  fill="none"
                  stroke="#d08a4f"
                  strokeWidth={2}
                  strokeDasharray="1 6"
                  strokeLinecap="round"
                />
                <circle cx={14} cy={16} r={2.5} fill="#d08a4f" />
                <circle cx={42} cy={10} r={2.5} fill="#d08a4f" />
                <circle cx={70} cy={5} r={2.5} fill="#d08a4f" />
              </svg>
            </div>
          </div>
        </div>

        <DataGuard
          loading={gsc.loading}
          error={gsc.error}
          empty={!gsc.data || gsc.data.length === 0}
          emptyTitle="暂无 SERP 数据"
          emptyHint="等待 GSC 与 SERP API 同步完成"
        >
          <div className="serp-grid">
            {/* Search result changes card */}
            <div className="card">
              <div className="card__title">
                <div className="card__title-icon">
                  搜索结果变化
                  <span className="msr" style={{ color: 'var(--ink-300)' }}>
                    info
                  </span>
                </div>
              </div>
              {SERP_ROWS.map((row, idx) => (
                <div
                  className="serp-row"
                  key={row.keyword}
                  style={idx === SERP_ROWS.length - 1 ? { borderBottom: 'none' } : {}}
                >
                  <div className={`serp-row__icon serp-row__icon--${row.color}`}>
                    <span className="msr">{row.icon}</span>
                  </div>
                  <div className="serp-row__main">
                    <div className="serp-row__title">{row.keyword}</div>
                    <div className="serp-row__sub">{row.intent}</div>
                  </div>
                  <div className="serp-row__metric">
                    <div className="serp-row__metric-label">平均排名</div>
                    <div className="serp-row__metric-value">
                      {row.posFrom} → {row.posTo}
                    </div>
                  </div>
                  <Sparkline
                    values={[22, 18, 20, 13, 16, 8, 10, 3]}
                    width={72}
                    height={26}
                    color={row.sparkColor}
                  />
                  <span
                    className={
                      'serp-row__delta ' +
                      (row.deltaUp ? 'serp-row__delta--up' : 'serp-row__delta--down')
                    }
                  >
                    {row.deltaText}
                  </span>
                </div>
              ))}
              <div className="more-link" role="note">
                查看更多关键词趋势
                <span className="msr">chevron_right</span>
              </div>
            </div>

            {/* Competitor moves */}
            <div className="card">
              <div className="card__title">竞争者动作</div>
              {COMP_ROWS.map((row, idx) => (
                <div
                  className="serp-row"
                  key={row.title}
                  style={
                    idx === COMP_ROWS.length - 1 ? { borderBottom: 'none' } : {}
                  }
                >
                  <div
                    className={`serp-row__icon serp-row__icon--${row.color}`}
                    style={{ borderRadius: 10 }}
                  >
                    <span className="msr">{row.icon}</span>
                  </div>
                  <div className="serp-row__main">
                    <div className="serp-row__title">{row.title}</div>
                    <div className="serp-row__sub">{row.sub}</div>
                  </div>
                  <span className="serp-row__count">{row.count}</span>
                  <span className="msr serp-row__chev">chevron_right</span>
                </div>
              ))}
              <div className="more-link" role="note">
                查看完整竞品动态
                <span className="msr">chevron_right</span>
              </div>
            </div>
          </div>
        </DataGuard>

        <div className="serp-grid-3">
          <JudgeCard
            eyebrow="AI 判断 1"
            color="pink"
            title="竞争页在 7 天内集中更新"
            range="32 个关键词"
            conf="91%"
            action="建议动作：加快内容更新节奏"
            cta="优先处理"
          />
          <JudgeCard
            eyebrow="AI 判断 2"
            color="gold"
            title="FAQ 型结果占比上升"
            range="18 个关键词"
            conf="86%"
            action="建议动作：优化 FAQ 结构与覆盖"
            cta="结构优化"
          />
          <JudgeCard
            eyebrow="AI 判断 3"
            color="blue"
            title="商业页更容易获得点击"
            range="24 个关键词"
            conf="89%"
            action="建议动作：强化商业价值与对比内容"
            cta="内容策略"
          />
        </div>

        <div className="footer-pipeline">
          <div className="pipeline__item pipeline__item--green">
            <span className="msr msr-fill">check_circle</span>
            <div>
              <div className="pipeline__label">同步正常</div>
              <div className="pipeline__sub">所有数据已同步</div>
            </div>
          </div>
          <div className="pipeline__divider" />
          <div className="pipeline__item pipeline__item--gold">
            <span className="msr">description</span>
            <div>
              <div className="pipeline__label">Brief 生成中</div>
              <div className="pipeline__sub">今日生成 48 / 60</div>
            </div>
          </div>
          <div className="pipeline__divider" />
          <div className="pipeline__item pipeline__item--green">
            <span className="msr">account_tree</span>
            <div>
              <div className="pipeline__label">站点分配完成</div>
              <div className="pipeline__sub">路由正常，规则已生效</div>
            </div>
          </div>
          <div className="footer-pipeline__time">数据更新时间：13 分钟前</div>
        </div>
      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="ai-card__head ai-card__head--blue">
            <span className="msr">calendar_today</span>
            今日 SERP 变化
          </div>
          <div className="timeline">
            <SerpTimelineItem
              time="09:20"
              icon="monitoring"
              color="pink"
              title="核心词排名波动"
              desc="多个核心词排名出现明显波动，其中 12 个关键词下降超过 5 位"
              tags={[
                { label: '高波动', tone: 'pink' },
                { label: '信息类词', tone: 'gray' },
              ]}
            />
            <SerpTimelineItem
              time="10:40"
              icon="quiz"
              color="gold"
              title="竞品 FAQ 增加"
              desc="竞品新增 FAQ 模块，覆盖更多长尾问题"
              tags={[{ label: '结构变化', tone: 'gray' }]}
            />
            <SerpTimelineItem
              time="13:10"
              icon="shopping_cart"
              color="blue"
              title="商业结果占比上升"
              desc="商业类结果占比提升至 68%，点击集中在前 3 位"
              tags={[{ label: '商业意图增强', tone: 'gold' }]}
            />
            <SerpTimelineItem
              time="16:00"
              icon="flag"
              color="orange"
              title="建议复核 Brief 结构"
              desc="部分 Brief 缺少 FAQ 覆盖与对比模块，建议优化"
              tags={[{ label: '需人工确认', tone: 'pink' }]}
              last
            />
          </div>
          <div className="btn--block btn--block-static" role="note">
            完整 SERP 报告待接口接入
            <span className="msr">hourglass_empty</span>
          </div>
        </div>

        <div className="suggest">
          <span className="msr suggest__icon">lightbulb</span>
          <div className="suggest__body">
            <div className="suggest__title">AI 建议摘要</div>
            <div className="suggest__desc">
              建议：复核第 1 项，观察第 2 项，确认第 3 项规则
            </div>
          </div>
          <span className="msr suggest__chev">chevron_right</span>
        </div>
      </aside>
    </>
  )
}

function JudgeCard({
  eyebrow,
  color,
  title,
  range,
  conf,
  action,
  cta,
}: {
  eyebrow: string
  color: 'pink' | 'gold' | 'blue'
  title: string
  range: string
  conf: string
  action: string
  cta: string
}) {
  return (
    <div className={`judge judge--${color}`}>
      <div className="judge__eyebrow">{eyebrow}</div>
      <div className="judge__title">{title}</div>
      <div className="judge__stats">
        <div>
          <div className="judge__stat-label">影响范围</div>
          <div className="judge__stat-value">{range}</div>
        </div>
        <div>
          <div className="judge__stat-label">AI 置信度</div>
          <div className="judge__stat-value">{conf}</div>
        </div>
      </div>
      <div className="judge__action">{action}</div>
      <div className="judge__cta">
        <span className="tag tag--gray">建议动作</span>
        <span className={`tag tag--${color}`}>{cta}</span>
      </div>
    </div>
  )
}

function SerpTimelineItem({
  time,
  icon,
  color,
  title,
  desc,
  tags,
  last,
}: {
  time: string
  icon: string
  color: 'pink' | 'green' | 'gold' | 'blue' | 'orange'
  title: string
  desc: string
  tags: Array<{ label: string; tone: 'pink' | 'gray' | 'gold' }>
  last?: boolean
}) {
  return (
    <div className="timeline__item">
      <div className="timeline__time">{time}</div>
      <div className="timeline__rail">
        <div className={`timeline__dot ai-list__dot--${color}`}>
          <span className="msr">{icon}</span>
        </div>
        {!last && <div className="timeline__line" />}
      </div>
      <div className="timeline__body">
        <div className="timeline__title">{title}</div>
        <div className="timeline__desc">{desc}</div>
        <div className="timeline__tags">
          {tags.map((t) => (
            <span key={t.label} className={`tag tag--${t.tone}`}>
              {t.label}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}