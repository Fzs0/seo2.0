import type { ReactNode } from 'react'

interface StateBlockProps {
  icon?: string
  title: string
  hint?: string
}

export function StateBlock({ icon = 'inbox', title, hint }: StateBlockProps) {
  return (
    <div className="state-block">
      <span className="msr" aria-hidden="true">
        {icon}
      </span>
      <div className="state-block__title">{title}</div>
      {hint && <div className="state-block__hint">{hint}</div>}
    </div>
  )
}

interface DataGuardProps {
  loading?: boolean
  error?: string | null
  empty?: boolean
  emptyTitle?: string
  emptyHint?: string
  children: ReactNode
  minHeight?: number
}

export function DataGuard({
  loading,
  error,
  empty,
  emptyTitle = '暂无数据',
  emptyHint,
  children,
  minHeight = 200,
}: DataGuardProps) {
  if (loading) {
    return (
      <div className="state-block" style={{ minHeight }}>
        <span className="msr" aria-hidden="true">
          progress_activity
        </span>
        <div className="state-block__title">数据加载中…</div>
        <div className="state-block__hint">正在拉取最近数据</div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="state-block" style={{ minHeight }}>
        <span className="msr" aria-hidden="true">
          error
        </span>
        <div className="state-block__title">数据加载失败</div>
        <div className="state-block__hint">{error}</div>
      </div>
    )
  }
  if (empty) {
    return (
      <StateBlock
        icon="inbox"
        title={emptyTitle}
        hint={emptyHint || '后端接口尚未提供数据，请稍后再来'}
      />
    )
  }
  return <>{children}</>
}