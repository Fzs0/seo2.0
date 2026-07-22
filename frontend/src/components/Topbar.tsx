interface TopbarProps {
  dateLabel: string
  notifications?: Array<{ id: string; title: string; detail?: string; time: string }>
  businessId?: string
  businessIds?: string[]
  onBusinessChange?: (businessId: string) => void
}

export function Topbar({ dateLabel, notifications = [], businessId = '', businessIds = [], onBusinessChange }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar__context">
        <span className="topbar__context-title">SEO Agent Workbench</span>
        <span className="topbar__context-hint">策略操作系统</span>
      </div>
      <div className="topbar__spacer" />
      <label className="topbar__business">
        <span className="msr">business_center</span>
        <select value={businessId} onChange={(event) => onBusinessChange?.(event.target.value)} disabled={!businessIds.length} aria-label="当前业务">
          {!businessIds.length && <option value="">暂无已建档业务</option>}
          {businessIds.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
      </label>
      <details className="topbar__notify">
        <summary className="topbar__bell" aria-label="通知状态">
        <span className="msr">notifications</span>
          {notifications.length > 0 && <span className="topbar__bell-dot" />}
        </summary>
        <div className="topbar__notify-panel">
          {notifications.length ? notifications.slice(0, 6).map((item) => (
            <div className="topbar__notify-item" key={item.id}>
              <div className="topbar__notify-title">{item.title}</div>
              {item.detail && <div className="topbar__notify-detail">{item.detail}</div>}
              <div className="topbar__notify-time">{item.time}</div>
            </div>
          )) : <div className="topbar__notify-empty">暂无通知</div>}
        </div>
      </details>
      <div className="topbar__date">
        <span className="msr">calendar_month</span>
        {dateLabel}
      </div>
    </header>
  )
}
