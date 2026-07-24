import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  resetKey: string
  onLeave: () => void
}

interface State {
  error: Error | null
}

export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('page_render_failed', error, info.componentStack)
  }

  componentDidUpdate(previous: Props) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <section className="page">
        <div className="state-block state-block--error" role="alert">
          <span className="msr">error</span>
          <div>
            <strong>当前页面发生异常</strong>
            <p>页面已被隔离，导航和其他工作区仍可使用。</p>
            {import.meta.env.DEV && <p>{this.state.error.message}</p>}
          </div>
          <button className="btn btn--ghost" type="button" onClick={() => this.setState({ error: null })}>
            重试当前页面
          </button>
          <button className="btn btn--primary" type="button" onClick={this.props.onLeave}>
            返回今日策略
          </button>
        </div>
      </section>
    )
  }
}
