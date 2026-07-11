import { DataGuard } from '@/components/StateBlock'
import { useSites } from '@/hooks/useData'
import type { Site } from '@/types/domain'

const TYPE_LABEL: Record<string, string> = {
  main: '主站',
  blog: '博客站',
  wp: 'WordPress',
}

const TYPE_TONE: Record<string, 'gold' | 'blue' | 'violet'> = {
  main: 'gold',
  blog: 'blue',
  wp: 'violet',
}

const PRIORITY_META: Record<string, { label: string; tone: 'gold' | 'pink' | 'blue' | 'green'; width: number; fill: string }> = {
  commercial: { label: '高', tone: 'gold', width: 85, fill: '#c9a03c' },
  educational: { label: '中高', tone: 'pink', width: 70, fill: '#d77e6c' },
  scenario: { label: '中', tone: 'pink', width: 45, fill: '#d77e6c' },
  comparison: { label: '中高', tone: 'green', width: 68, fill: '#93a96c' },
}

function roleKey(role: Site['content_role']) {
  if (role?.includes('主站') || role?.includes('commercial')) return 'commercial'
  if (role?.includes('对比') || role?.includes('评测') || role?.includes('comparison')) return 'comparison'
  if (role?.includes('场景') || role?.includes('人群') || role?.includes('scenario')) return 'scenario'
  return 'educational'
}

function iconFor(role: string) {
  switch (role) {
    case 'commercial':
      return 'workspace_premium'
    case 'educational':
      return 'menu_book'
    case 'comparison':
      return 'balance'
    case 'scenario':
      return 'folder'
    default:
      return 'apartment'
  }
}

function iconBg(role: string) {
  switch (role) {
    case 'commercial':
      return '#f7edd3'
    case 'educational':
      return '#e4ebf3'
    case 'scenario':
      return '#f9e4de'
    case 'comparison':
      return '#eaefdc'
    default:
      return '#f4eee3'
  }
}

function iconColor(role: string) {
  switch (role) {
    case 'commercial':
      return '#b98f2e'
    case 'educational':
      return '#5f84a8'
    case 'scenario':
      return '#c4685a'
    case 'comparison':
      return '#7e9150'
    default:
      return '#17161a'
  }
}

export function SitesPage() {
  const sites = useSites()
  return (
    <>
      <section className="page" data-screen-label="站点管理">
        <div>
          <h1>站点管理</h1>
          <p>
            AI 已完成站点健康检查与内容路由，请确认站点分配策略。
          </p>
        </div>

        <div className="kpi-row">
          <div className="kpi kpi--gold">
            <div className="kpi__head">
              <span className="kpi__label">活跃站点</span>
              <span className="msr kpi__icon kpi__icon--gold">apartment</span>
            </div>
            <div className="kpi__value">{sites.data?.length ?? 0}</div>
            <div className="kpi__delta">较昨日 +1</div>
          </div>
          <div className="kpi kpi--blue">
            <div className="kpi__head">
              <span className="kpi__label">路由正常</span>
              <span className="msr msr-fill kpi__icon kpi__icon--blue">verified_user</span>
            </div>
            <div className="kpi__value">96%</div>
            <div className="kpi__delta">较昨日 +2%</div>
          </div>
          <div className="kpi kpi--pink">
            <div className="kpi__head">
              <span className="kpi__label">待确认分配</span>
              <span className="msr kpi__icon kpi__icon--pink">help</span>
            </div>
            <div className="kpi__value">5</div>
            <div className="kpi__delta">较昨日 -1</div>
          </div>
          <div className="kpi kpi--green">
            <div className="kpi__head">
              <span className="kpi__label">API 健康</span>
              <span className="msr msr-fill kpi__icon kpi__icon--green">cloud_done</span>
            </div>
            <div className="kpi__value">98%</div>
            <div className="kpi__delta">较昨日 +0.6%</div>
          </div>
        </div>

        <div className="card">
          <div className="card__title">
            <span>站点列表</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="chip">
                <span className="msr">expand_more</span>
                全部状态
              </span>
              <div
                style={{
                  display: 'flex',
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  overflow: 'hidden',
                }}
              >
                <span
                  style={{
                    padding: '6px 9px',
                    display: 'flex',
                    alignItems: 'center',
                    background: 'var(--ink-900)',
                  }}
                >
                  <span className="msr" style={{ fontSize: 15, color: '#ffffff' }}>
                    grid_view
                  </span>
                </span>
                <span
                  style={{
                    padding: '6px 9px',
                    display: 'flex',
                    alignItems: 'center',
                  }}
                >
                  <span className="msr" style={{ fontSize: 15, color: 'var(--ink-400)' }}>
                    list
                  </span>
                </span>
              </div>
            </div>
          </div>

          <DataGuard
            loading={sites.loading}
            error={sites.error}
            empty={!sites.data || sites.data.length === 0}
            emptyTitle="暂无站点"
            emptyHint="通过 /api/v1/sites 添加站点后会展示在此"
          >
            <div className="site-grid">
              {(sites.data ?? []).map((s) => {
                const tone = TYPE_TONE[s.site_type] ?? 'blue'
                const role = roleKey(s.content_role)
                const prio = PRIORITY_META[role]
                const isWarn = s.status === 'review'
                return (
                  <div className="site-card" key={s.id}>
                    <div className="site-card__head">
                      <div
                        className="site-card__icon"
                        style={{ background: iconBg(role) }}
                      >
                        <span
                          className="msr"
                          style={{
                            color: iconColor(role),
                            fontSize: 18,
                          }}
                        >
                          {iconFor(role)}
                        </span>
                      </div>
                      <div>
                        <span className={`tag tag--${tone}`}>{TYPE_LABEL[s.site_type] ?? s.site_type}</span>
                        <div className="site-card__name">{s.name}</div>
                      </div>
                    </div>
                    <div className="site-card__meta">
                      <span className="site-card__meta-label">域名</span>
                      <span className="site-card__meta-value">{s.domain}</span>
                      <span className="site-card__meta-label">市场 / 语言</span>
                      <span className="site-card__meta-value">
                        {s.market || '未设置'} / {s.language_code?.toUpperCase() || '未设置'}
                      </span>
                      <span className="site-card__meta-label">AI 分配</span>
                      <span
                        className={
                          'site-card__meta-value ' +
                          (isWarn
                            ? 'site-card__meta-value--warn'
                            : 'site-card__meta-value--ok')
                        }
                      >
                        ● {isWarn ? '发布节奏偏低' : '路由正常'}
                      </span>
                      <span className="site-card__meta-label">内容优先级</span>
                      <span className="site-card__priority">
                        <span
                          className="site-card__priority-label"
                          style={{
                            color:
                              prio.tone === 'gold'
                                ? '#8a6d1f'
                                : prio.tone === 'pink'
                                ? '#a14a3c'
                                : prio.tone === 'blue'
                                ? '#3f6182'
                                : '#5c6e33',
                          }}
                        >
                          {prio.label}
                        </span>
                        <span className="site-card__priority-bar">
                          <span
                            className="site-card__priority-fill"
                            style={{
                              width: `${prio.width}%`,
                              background: prio.fill,
                            }}
                          />
                        </span>
                      </span>
                    </div>
                  </div>
                )
              })}
              <div className="site-card site-card--placeholder">
                <span className="msr">add_circle</span>
                <span className="site-add__title">站点接入占位</span>
                <span className="site-add__hint">POST /api/v1/sites 接入后展示</span>
              </div>
            </div>
          </DataGuard>
        </div>

        <div className="card">
          <div className="card__title">AI 路由建议</div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: 12,
              marginTop: 14,
            }}
          >
            <RouteSuggest
              icon="target"
              color="gold"
              title="商业意图给主站"
              desc="将高商业意图关键词优先分配到主站商业页，提升转化效率。"
            />
            <RouteSuggest
              icon="menu_book"
              color="blue"
              title="知识词给博客 A"
              desc="将教程与知识类关键词优先分配到博客 A，强化权威与覆盖。"
            />
            <RouteSuggest
              icon="balance"
              color="green"
              title="对比词给博客 C"
              desc="将对比与评测类关键词优先分配到博客 C，承接用户决策需求。"
            />
          </div>
        </div>
      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="card__title">站点分配摘要</div>
          <div className="ai-list">
            <div className="ai-list__item">
              <div className="ai-list__dot ai-list__dot--gold">
                <span className="msr">flag</span>
              </div>
              <div>
                <div className="ai-list__label">当前主线</div>
                <div className="ai-list__title">商业价值优先</div>
                <div className="ai-list__desc">聚焦高转化商业内容，提升整体 ROI。</div>
              </div>
            </div>
            <div className="ai-list__item">
              <div className="ai-list__dot ai-list__dot--pink">
                <span className="msr">warning</span>
              </div>
              <div>
                <div className="ai-list__label">异常提醒</div>
                <div className="ai-list__title">博客 B 发布节奏偏低</div>
                <div className="ai-list__desc">近 7 天仅发布 2 篇内容，低于平均水平。</div>
              </div>
            </div>
            <div className="ai-list__item">
              <div className="ai-list__dot ai-list__dot--gold">
                <span className="msr">lightbulb</span>
              </div>
              <div>
                <div className="ai-list__label">建议动作</div>
                <div className="ai-list__title">把 7 个场景词转入博客 B</div>
                <div className="ai-list__desc">相关关键词已有搜索需求，建议尽快补充内容。</div>
              </div>
            </div>
            <div className="ai-list__item">
              <div className="ai-list__dot ai-list__dot--blue">
                <span className="msr">description</span>
              </div>
              <div>
                <div className="ai-list__label">需要确认</div>
                <div className="ai-list__title">2 个目标 URL 尚未存在</div>
                <div className="ai-list__desc">请确认是否创建或调整路由规则。</div>
              </div>
            </div>
          </div>
          <div className="btn--block btn--block-static" role="note">
            站点路由建议待接口接入
            <span className="msr">hourglass_empty</span>
          </div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
              fontSize: 11,
              color: 'var(--ink-400)',
              marginTop: 9,
            }}
          >
            上次路由更新时间：2 小时前
            <span className="msr" style={{ fontSize: 13 }}>sync</span>
          </div>
        </div>
      </aside>
    </>
  )
}

function RouteSuggest({
  icon,
  color,
  title,
  desc,
}: {
  icon: string
  color: 'gold' | 'blue' | 'green'
  title: string
  desc: string
}) {
  const bg =
    color === 'gold' ? '#f7edd3' : color === 'blue' ? '#e4ebf3' : '#eaefdc'
  const ic =
    color === 'gold' ? '#b98f2e' : color === 'blue' ? '#5f84a8' : '#7e9150'
  return (
    <div
      style={{
        background: bg,
        borderRadius: 14,
        padding: 16,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span className="msr" style={{ fontSize: 18, color: ic }}>
          {icon}
        </span>
        <span style={{ fontSize: 13, fontWeight: 700 }}>{title}</span>
      </div>
      <div
        style={{
          fontSize: 11.5,
          color: 'var(--ink-700)',
          marginTop: 8,
          lineHeight: 1.55,
        }}
      >
        {desc}
      </div>
    </div>
  )
}
