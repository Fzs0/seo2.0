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
const PROJECT_TABS = [
  { id: 'default', label: '默认项目' },
  { id: 'blog-a', label: '博客 A · 知识教程' },
  { id: 'blog-b', label: '博客 B · 场景方案' },
  { id: 'blog-c', label: '博客 C · 对比评测' },
  { id: 'main-shop', label: '主站 · 商业页' },
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
  const [projectTab, setProjectTab] = useState('default')
  const [notifications, setNotifications] = useState<Array<{ id: string; title: string; detail?: string; time: string }>>([])

  function notify(title: string, detail?: string) {
    const time = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date())
    setNotifications((items) => [{ id: crypto.randomUUID(), title, detail, time }, ...items].slice(0, 20))
  }

  const pageNode = useMemo(() => {
    switch (page) {
      case 'dashboard':
        return <DashboardPage project={projectTab} />
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
  }, [page, projectTab])

  return (
    <div className="app-shell">
      <Sidebar currentPage={page} onNavigate={(id) => setPage(id as PageId)} />
      <div className="app-main">
        <Topbar
          tabs={PROJECT_TABS}
          currentTab={projectTab}
          onTabChange={setProjectTab}
          dateLabel={todayLabel()}
          notifications={notifications}
        />
        <main className="app-content">{pageNode}</main>
      </div>
    </div>
  )
}
