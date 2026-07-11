import { useMemo, useState } from 'react'
import { Sidebar } from '@/components/Sidebar'
import { Topbar } from '@/components/Topbar'
import { DashboardPage } from '@/pages/DashboardPage'
import { OpportunitiesPage } from '@/pages/OpportunitiesPage'
import { SerpPage } from '@/pages/SerpPage'
import { KeywordsPage } from '@/pages/KeywordsPage'
import { BriefPage } from '@/pages/BriefPage'
import { ContentPage } from '@/pages/ContentPage'
import { ArticlesPage } from '@/pages/ArticlesPage'
import { SitesPage } from '@/pages/SitesPage'
import { AnalyticsPage } from '@/pages/AnalyticsPage'
import { RulesPage } from '@/pages/RulesPage'
import { SyncPage } from '@/pages/SyncPage'
import { AgentWorkbenchPage, type AgentView } from '@/pages/AgentWorkbenchPage'

type PageId =
  | 'dashboard'
  | 'opportunities'
  | 'serp'
  | 'keywords'
  | 'brief'
  | 'content'
  | 'articles'
  | 'sites'
  | 'analytics'
  | 'rules'
  | 'sync'

/** The top tab set is the same for every page — it scopes the current
 *  project / market / dataset. Field naming matches the backend's
 *  `positioning.defaultProject` & `market` query params. */
const WORK_VIEWS = [
  { id: 'command', label: '总控' },
  { id: 'assets', label: '资产集群' },
  { id: 'opportunities', label: '机会队列' },
  { id: 'execution', label: '执行队列' },
  { id: 'risk', label: '风险治理' },
  { id: 'review', label: '数据复盘' },
]

function todayLabel() {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(new Date())
}

export default function App() {
  const [page, setPage] = useState<PageId>('dashboard')
  const [workView, setWorkView] = useState<AgentView>('command')
  const [notifications, setNotifications] = useState<Array<{ id: string; title: string; detail?: string; time: string }>>([])

  function notify(title: string, detail?: string) {
    const time = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date())
    setNotifications((items) => [{ id: crypto.randomUUID(), title, detail, time }, ...items].slice(0, 20))
  }

  const pageNode = useMemo(() => {
    switch (page) {
      case 'dashboard':
        return <AgentWorkbenchPage view={workView} onNotify={notify} />
      case 'opportunities':
        return <OpportunitiesPage />
      case 'serp':
        return <SerpPage />
      case 'keywords':
        return <KeywordsPage onNotify={notify} />
      case 'brief':
        return <BriefPage />
      case 'content':
        return <ContentPage onNotify={notify} />
      case 'articles':
        return <ArticlesPage />
      case 'sites':
        return <SitesPage />
      case 'analytics':
        return <AnalyticsPage />
      case 'rules':
        return <RulesPage />
      case 'sync':
        return <SyncPage />
      default:
        return null
    }
  }, [page, workView])

  return (
    <div className="app-shell">
      <Sidebar currentPage={page} onNavigate={(id) => setPage(id as PageId)} />
      <div className="app-main">
        <Topbar
          tabs={WORK_VIEWS}
          currentTab={workView}
          onTabChange={(id) => {
            setWorkView(id as AgentView)
            setPage('dashboard')
          }}
          dateLabel={todayLabel()}
          notifications={notifications}
        />
        <main className="app-content">{pageNode}</main>
      </div>
    </div>
  )
}
