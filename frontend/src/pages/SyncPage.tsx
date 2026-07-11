import { Bars, Sparkline } from '@/components/Charts'
import { DataGuard } from '@/components/StateBlock'
import { useDashboard, usePipelineStages, useSyncLog } from '@/hooks/useData'
import type { SyncLogEntry } from '@/types/domain'

const SOURCE_LABEL: Record<SyncLogEntry['source_type'], string> = {
  gsc: 'Google Search Console',
  ga4: 'Google Analytics 4',
}

const STATUS_META: Record<SyncLogEntry['status'], { label: string; tone: 'green' | 'pink' | 'gold'; icon: string }> = {
  success: { label: '成功', tone: 'green', icon: 'check_circle' },
  failed: { label: '失败', tone: 'pink', icon: 'error' },
  running: { label: '运行中', tone: 'gold', icon: 'progress_activity' },
}

function formatNumber(n: number) {
  return new Intl.NumberFormat('en-US').format(n)
}

function formatTime(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(
    d.getHours(),
  ).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function formatDuration(ms: number) {
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

export function SyncPage() {
  const logs = useSyncLog()
  const dashboard = useDashboard('site-main-shop')
  const pipeline = usePipelineStages()
  const totalRows = logs.data?.reduce((sum, log) => sum + log.rows_written, 0) ?? 0
  const failedCount = logs.data?.filter((log) => log.status === 'failed').length ?? 0
  const lastLog = logs.data?.[0]

  return (
    <>
      <section className="page" data-screen-label="同步状态">
        <div>
          <h1>同步状态</h1>
          <p>
            展示 GSC / GA4 同步记录与数据链路健康度；页面仅作只读监控，后续写入能力由接口联调接入。
          </p>
        </div>

        <div className="kpi-row">
          <div className="kpi kpi--blue">
            <div className="kpi__head">
              <span className="kpi__label">最近同步</span>
              <span className="msr kpi__icon kpi__icon--blue">sync</span>
            </div>
            <div className="kpi__value kpi__value--sm">{lastLog ? formatTime(lastLog.finished_at) : '—'}</div>
            <div className="kpi__delta">{lastLog ? SOURCE_LABEL[lastLog.source_type] : '暂无记录'}</div>
            <div className="kpi__spark">
              <Sparkline values={[8, 12, 10, 16, 15, 21, 19]} width={86} height={28} color="#7c9ec1" />
            </div>
          </div>
          <div className="kpi kpi--green">
            <div className="kpi__head">
              <span className="kpi__label">写入行数</span>
              <span className="msr kpi__icon kpi__icon--green">database</span>
            </div>
            <div className="kpi__value">{formatNumber(totalRows)}</div>
            <div className="kpi__delta">最近同步批次合计</div>
            <div className="kpi__spark">
              <Bars values={[12, 16, 14, 18, 24, 22, 30]} width={86} height={32} color="#93a96c" />
            </div>
          </div>
          <div className="kpi kpi--gold">
            <div className="kpi__head">
              <span className="kpi__label">数据源</span>
              <span className="msr kpi__icon kpi__icon--gold">hub</span>
            </div>
            <div className="kpi__value">2</div>
            <div className="kpi__delta">GSC + GA4</div>
          </div>
          <div className="kpi kpi--pink">
            <div className="kpi__head">
              <span className="kpi__label">异常批次</span>
              <span className="msr kpi__icon kpi__icon--pink">warning</span>
            </div>
            <div className="kpi__value">{failedCount}</div>
            <div className="kpi__delta">需要排查但不在前端修复</div>
          </div>
        </div>

        <DataGuard
          loading={logs.loading}
          error={logs.error}
          empty={!logs.data || logs.data.length === 0}
          emptyTitle="暂无同步记录"
          emptyHint="后端同步任务写入日志后会展示在此"
        >
          <div className="card">
            <div className="card__title">
              <div className="card__title-icon">
                <span className="msr">history</span>
                同步日志
              </div>
              <span className="card__title-tag">只读日志</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(logs.data ?? []).map((log) => {
                const status = STATUS_META[log.status]
                const pct = log.rows_fetched > 0 ? Math.round((log.rows_written / log.rows_fetched) * 100) : 0
                return (
                  <div className="link-row" key={log.id}>
                    <div className={`link-row__icon ai-list__dot--${status.tone}`}>
                      <span className="msr">{status.icon}</span>
                    </div>
                    <div className="link-row__name">
                      <h4>{SOURCE_LABEL[log.source_type]}</h4>
                      <p>{log.trigger === 'schedule' ? '计划任务' : '人工触发记录'}</p>
                    </div>
                    <span className={`link-row__status link-row__status--${status.tone === 'green' ? 'ok' : status.tone === 'gold' ? 'gold' : 'warn'}`}>
                      {status.label}
                    </span>
                    <div className="link-row__progress">
                      <div className="link-row__bar">
                        <div
                          className="link-row__bar-fill"
                          style={{
                            width: `${Math.max(0, Math.min(100, pct))}%`,
                            background:
                              status.tone === 'green'
                                ? 'var(--green-400)'
                                : status.tone === 'gold'
                                ? 'var(--brand-deep)'
                                : 'var(--orange-400)',
                          }}
                        />
                      </div>
                      <span className="link-row__pct">{pct}%</span>
                    </div>
                    <div className="link-row__time">
                      <div className="link-row__time-label">完成时间</div>
                      <div className="link-row__time-value">{formatTime(log.finished_at)}</div>
                    </div>
                    <div className="link-row__result">
                      <div className="link-row__result-label">写入 / 耗时</div>
                      <div className="link-row__result-value">
                        {formatNumber(log.rows_written)} · {formatDuration(log.duration_ms)}
                      </div>
                    </div>
                    <span className="tag tag--gray">{log.error_message ?? log.id}</span>
                  </div>
                )
              })}
            </div>
          </div>
        </DataGuard>

        <DataGuard
          loading={dashboard.loading}
          error={dashboard.error}
          empty={!dashboard.data}
          emptyTitle="暂无数据链路信息"
        >
          {dashboard.data && (
            <div className="content-grid-2">
              <div className="card">
                <div className="card__title">
                  <div className="card__title-icon">
                    <span className="msr">query_stats</span>
                    GSC 最近同步
                  </div>
                </div>
                {dashboard.data.lastSync
                  .filter((row) => row.source_type === 'gsc')
                  .map((row) => (
                    <SourceCard
                      key={`${row.source_type}-${row.started_at}`}
                      title="Search Console"
                      icon="search"
                      tone="blue"
                      rows={row.rows_written}
                      started={row.started_at}
                      finished={row.finished_at}
                      status={row.status}
                    />
                  ))}
              </div>
              <div className="card">
                <div className="card__title">
                  <div className="card__title-icon">
                    <span className="msr">monitoring</span>
                    GA4 最近同步
                  </div>
                </div>
                {dashboard.data.lastSync
                  .filter((row) => row.source_type === 'ga4')
                  .map((row) => (
                    <SourceCard
                      key={`${row.source_type}-${row.started_at}`}
                      title="Analytics 4"
                      icon="analytics"
                      tone="gold"
                      rows={row.rows_written}
                      started={row.started_at}
                      finished={row.finished_at}
                      status={row.status}
                    />
                  ))}
              </div>
            </div>
          )}
        </DataGuard>

        <DataGuard
          loading={pipeline.loading}
          error={pipeline.error}
          empty={!pipeline.data || pipeline.data.length === 0}
          emptyTitle="暂无流程数据"
        >
          <div className="footer-pipeline">
            {(pipeline.data ?? []).slice(0, 3).map((stage, index) => (
              <div key={stage.key} style={{ display: 'contents' }}>
                <div className={`pipeline__item pipeline__item--${stage.status === 'done' ? 'green' : 'gold'}`}>
                  <span className="msr">{stage.status === 'done' ? 'check_circle' : 'progress_activity'}</span>
                  <div>
                    <div className="pipeline__label">{stage.label}</div>
                    <div className="pipeline__sub">{stage.status === 'done' ? '已完成' : '进行中'}</div>
                  </div>
                </div>
                {index < 2 && <div className="pipeline__divider" />}
              </div>
            ))}
            <div className="footer-pipeline__time">页面展示 mock 数据，后续由 API hooks 替换。</div>
          </div>
        </DataGuard>
      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="ai-card__head ai-card__head--blue">
            <span className="msr">timeline</span>
            今日同步时间线
          </div>
          <div className="timeline">
            <TimelineItem time="03:00" icon="search" color="blue" title="GSC 计划同步" desc="写入搜索查询、点击、展示与平均排名数据。" />
            <TimelineItem time="03:04" icon="analytics" color="gold" title="GA4 计划同步" desc="写入渠道、会话、参与率与转化数据。" />
            <TimelineItem time="13:50" icon="sync" color="green" title="最近批次完成" desc="两类数据源均已完成写入，供前端只读展示。" />
            <TimelineItem time="待处理" icon="warning" color="pink" title="异常批次保留" desc="GA4 quota exceeded 仅作为日志展示，不在前端重试。" last />
          </div>
        </div>

        <div className="suggest">
          <span className="msr suggest__icon">info</span>
          <div className="suggest__body">
            <div className="suggest__title">接口联调提示</div>
            <div className="suggest__desc">页面通过 useSyncLog / useDashboard 获取数据，替换 hook 即可接入真实接口。</div>
          </div>
        </div>
      </aside>
    </>
  )
}

function SourceCard({
  title,
  icon,
  tone,
  rows,
  started,
  finished,
  status,
}: {
  title: string
  icon: string
  tone: 'blue' | 'gold'
  rows: number
  started: string
  finished: string
  status: string
}) {
  return (
    <div className="rule-row" style={{ cursor: 'default' }}>
      <div className={`rule-row__icon ai-list__dot--${tone}`}>
        <span className="msr">{icon}</span>
      </div>
      <div className="rule-row__main">
        <div className="rule-row__title">{title}</div>
        <div className="rule-row__sub">
          {formatTime(started)} → {formatTime(finished)}
        </div>
      </div>
      <span className="tag tag--green">{status}</span>
      <span className="tbl-strong">{formatNumber(rows)} 行</span>
    </div>
  )
}

function TimelineItem({
  time,
  icon,
  color,
  title,
  desc,
  last,
}: {
  time: string
  icon: string
  color: 'pink' | 'green' | 'gold' | 'blue' | 'orange'
  title: string
  desc: string
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
      </div>
    </div>
  )
}
