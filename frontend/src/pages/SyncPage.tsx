import { useEffect, useState } from 'react'
import { Bars, Sparkline } from '@/components/Charts'
import { DataGuard } from '@/components/StateBlock'
import { useAnalyticsSources, useDashboard, useSites, useSyncLog } from '@/hooks/useData'
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
  const [siteId, setSiteId] = useState<string>()
  const [refreshKey, setRefreshKey] = useState(0)
  const sites = useSites(refreshKey)
  const logs = useSyncLog(siteId, refreshKey)
  const sources = useAnalyticsSources(refreshKey)
  const dashboard = useDashboard(siteId, refreshKey)
  useEffect(() => {
    if (!siteId && sites.data?.[0]?.id) setSiteId(sites.data[0].id)
  }, [siteId, sites.data])

  const selectedSite = sites.data?.find((site) => site.id === siteId)
  const totalRows = logs.data?.reduce((sum, log) => sum + log.rows_written, 0) ?? 0
  const failedCount = logs.data?.filter((log) => log.status === 'failed').length ?? 0
  const lastLog = logs.data?.[0]
  const latestBySource = new Map((logs.data ?? []).map((log) => [log.source_type, log]))
  const successCount = logs.data?.filter((log) => log.status === 'success').length ?? 0

  return (
    <>
      <section className="page" data-screen-label="同步状态">
        <div>
          <h1>同步状态</h1>
          <p>
            展示真实 GSC / GA4 同步记录与数据链路健康度。
          </p>
        </div>
        <div className="page-heading-actions">
          <label className="select-control">
            <span>当前站点</span>
            <select value={siteId || ''} onChange={(event) => setSiteId(event.target.value || undefined)} disabled={!sites.data?.length}>
              {!sites.data?.length && <option value="">暂无站点</option>}
              {sites.data?.map((site) => <option value={site.id} key={site.id}>{site.name}</option>)}
            </select>
          </label>
          <button type="button" className="btn btn--ghost" onClick={() => setRefreshKey((key) => key + 1)}>
            <span className="msr">refresh</span>刷新日志
          </button>
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
            <div className="kpi__value">{sources.data?.length ?? 0}</div>
            <div className="kpi__delta">来自真实数据源配置</div>
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
          error={logs.error || sites.error || sources.error}
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
                      <p>{log.trigger === 'schedule' || log.trigger === 'scheduled' ? '计划任务' : '人工触发记录'}</p>
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
                    <span className="tag tag--gray">
                      {log.error_message || (log.range_start && log.range_end ? `${log.range_start} ~ ${log.range_end}` : '无错误')}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        </DataGuard>

        <DataGuard
          loading={dashboard.loading}
          error={dashboard.error || sites.error}
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

        <div className="footer-pipeline">
          <PipelineItem tone="gold" icon="hub" label="已配置数据源" value={`${sources.data?.length ?? 0} 个`} />
          <div className="pipeline__divider" />
          <PipelineItem tone="blue" icon="database" label="当前站点日志" value={`${logs.data?.length ?? 0} 条`} />
          <div className="pipeline__divider" />
          <PipelineItem tone="green" icon="verified" label="成功批次" value={`${successCount} 条`} />
          <div className="footer-pipeline__time">{selectedSite ? `当前站点：${selectedSite.name}` : '尚未选择站点'}</div>
        </div>
      </section>

      <aside className="rail">
        <div className="ai-card">
          <div className="ai-card__head ai-card__head--blue">
            <span className="msr">timeline</span>
            今日同步时间线
          </div>
          <div className="timeline">
            {(['gsc', 'ga4'] as const).map((sourceType, index) => {
              const log = latestBySource.get(sourceType)
              return (
                <TimelineItem
                  key={sourceType}
                  time={log ? formatTime(log.started_at) : '暂无'}
                  icon={sourceType === 'gsc' ? 'search' : 'analytics'}
                  color={log?.status === 'failed' ? 'pink' : index === 0 ? 'blue' : 'gold'}
                  title={`${sourceType.toUpperCase()} 最近同步`}
                  desc={log ? `${STATUS_META[log.status].label}，写入 ${formatNumber(log.rows_written)} 行` : '暂无同步记录'}
                  last={index === 1}
                />
              )
            })}
          </div>
        </div>

        <div className="suggest">
          <span className="msr suggest__icon">info</span>
          <div className="suggest__body">
            <div className="suggest__title">接口联调提示</div>
            <div className="suggest__desc">数据来自同步日志、站点 Dashboard 与数据源配置接口；失败原因直接展示后端返回信息。</div>
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
      <span className={`tag tag--${status === 'failed' ? 'pink' : status === 'running' ? 'gold' : 'green'}`}>
        {STATUS_META[status as SyncLogEntry['status']]?.label || status}
      </span>
      <span className="tbl-strong">{formatNumber(rows)} 行</span>
    </div>
  )
}

function PipelineItem({
  tone,
  icon,
  label,
  value,
}: {
  tone: 'blue' | 'gold' | 'green'
  icon: string
  label: string
  value: string
}) {
  return (
    <div className={`pipeline__item pipeline__item--${tone}`}>
      <span className="msr">{icon}</span>
      <div>
        <div className="pipeline__label">{label}</div>
        <div className="pipeline__sub">{value}</div>
      </div>
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
