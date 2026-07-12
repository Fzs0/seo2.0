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
import { AgentWorkbenchPage } from '@/pages/AgentWorkbenchPage'

type PageId =
  | 'agent-command'
  | 'agent-assets'
  | 'agent-opportunities'
  | 'agent-execution'
  | 'agent-review'
  | 'agent-risk'
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

function todayLabel() {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(new Date())
}

export default function App() {
  const [page, setPage] = useState<PageId>('agent-command')
  const [notifications, setNotifications] = useState<Array<{ id: string; title: string; detail?: string; time: string }>>([])

  function notify(title: string, detail?: string) {
    const time = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date())
    setNotifications((items) => [{ id: crypto.randomUUID(), title, detail, time }, ...items].slice(0, 20))
  }

  const pageNode = useMemo(() => {
    switch (page) {
      case 'agent-command':
        return <AgentWorkbenchPage view="command" onNotify={notify} />
      case 'agent-assets':
        return <AgentWorkbenchPage view="assets" onNotify={notify} />
      case 'agent-opportunities':
        return <AgentWorkbenchPage view="opportunities" onNotify={notify} />
      case 'agent-execution':
        return <AgentWorkbenchPage view="execution" onNotify={notify} />
      case 'agent-review':
        return <AgentWorkbenchPage view="review" onNotify={notify} />
      case 'agent-risk':
        return <AgentWorkbenchPage view="risk" onNotify={notify} />
      case 'dashboard':
        return <AgentWorkbenchPage view="command" onNotify={notify} />
      case 'opportunities':
        return <OpportunitiesPage />
      case 'serp':
        return <SerpPage />
      case 'keywords':
        return <KeywordsPage onNotify={notify} onOpenContent={() => setPage('content')} />
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
  }, [page])

  return (
    <div className="app-shell">
      <Sidebar currentPage={page} onNavigate={(id) => setPage(id as PageId)} />
      <div className="app-main">
        <Topbar dateLabel={todayLabel()} notifications={notifications} />
        <main className="app-content">{pageNode}</main>
      </div>
    </div>
  )
}
