import { useEffect, useMemo, useRef, useState } from 'react'
import { Sidebar } from '@/components/Sidebar'
import { Topbar } from '@/components/Topbar'
import { KeywordsPage } from '@/pages/KeywordsPage'
import { ContentPage } from '@/pages/ContentPage'
import { ArticlesPage } from '@/pages/ArticlesPage'
import { SitesPage } from '@/pages/SitesPage'
import { AnalyticsPage } from '@/pages/AnalyticsPage'
import { MainSiteContentPage } from '@/pages/MainSiteContentPage'
import { useAutomationStatus } from '@/data/automation'
import { BusinessScopeProvider, useBusinessScope } from '@/businessScope'

type PageId =
  | 'content'
  | 'keywords'
  | 'articles'
  | 'sites'
  | 'analytics'
  | 'main-site-content'

function todayLabel() {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(new Date())
}

export default function App() {
  return <BusinessScopeProvider><AppShell /></BusinessScopeProvider>
}

function AppShell() {
  const [page, setPage] = useState<PageId>('content')
  const [notifications, setNotifications] = useState<Array<{ id: string; title: string; detail?: string; time: string }>>([])
  const [taskRefreshKey, setTaskRefreshKey] = useState(0)
  const { businessId, businessIds, setBusinessId } = useBusinessScope()
  const execution = useAutomationStatus(taskRefreshKey, businessId || undefined)
  const seenTasks = useRef<Set<string> | null>(null)

  function notify(title: string, detail?: string) {
    const time = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date())
    setNotifications((items) => [{ id: crypto.randomUUID(), title, detail, time }, ...items].slice(0, 20))
  }

  useEffect(() => {
    const timer = window.setInterval(() => setTaskRefreshKey((key) => key + 1), 5000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!execution.data) return
    const current = new Set(execution.data.recent.map((item) => item.id))
    if (seenTasks.current) {
      execution.data.recent
        .filter((item) => !seenTasks.current?.has(item.id))
        .forEach((item) => notify(
          item.task_type === 'keyword_review'
            ? (item.status === 'done' ? 'AI 关键词分析完成' : 'AI 关键词分析失败')
            : (item.status === 'done' ? '内容任务执行完成' : '内容任务执行失败'),
          item.status === 'done' ? item.title : `${item.title}${item.error_message ? `：${item.error_message}` : ''}`,
        ))
    }
    seenTasks.current = current
  }, [execution.data])

  const pageNode = useMemo(() => {
    switch (page) {
      case 'content':
        return <ContentPage onNotify={notify} />
      case 'keywords':
        return <KeywordsPage onNotify={notify} onOpenContent={() => setPage('content')} />
      case 'articles':
        return <ArticlesPage />
      case 'sites':
        return <SitesPage />
      case 'analytics':
        return <AnalyticsPage />
      case 'main-site-content':
        return <MainSiteContentPage />
      default:
        return null
    }
  }, [page])

  return (
    <div className="app-shell">
      <Sidebar currentPage={page} onNavigate={(id) => setPage(id as PageId)} />
      <div className="app-main">
        <Topbar dateLabel={todayLabel()} notifications={notifications} businessId={businessId} businessIds={businessIds} onBusinessChange={setBusinessId} />
        <main className="app-content">{pageNode}</main>
      </div>
    </div>
  )
}
