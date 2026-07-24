interface NavItem {
  id: string
  label: string
  icon: string
}

interface NavSection {
  label?: string
  items: NavItem[]
}

const navSections: NavSection[] = [
  {
    label: '日常工作',
    items: [
      { id: 'content', label: '今日策略', icon: 'task_alt' },
      { id: 'keywords', label: '关键词', icon: 'key' },
      { id: 'articles', label: '文章', icon: 'article' },
      { id: 'main-site-content', label: '主站内容', icon: 'storefront' },
      { id: 'social-publishing', label: '社媒发布', icon: 'campaign' },
      { id: 'analytics', label: '数据复盘', icon: 'monitoring' },
    ],
  },
  {
    label: '设置',
    items: [
      { id: 'sites', label: '站点', icon: 'domain' },
    ],
  },
]

interface SidebarProps {
  currentPage: string
  onNavigate: (page: string) => void
}

export function Sidebar({ currentPage, onNavigate }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="sidebar__brand">
        <div className="sidebar__brand-bars" aria-hidden="true">
          <span /> <span /> <span />
        </div>
        <span className="sidebar__brand-name">SEO Agent Workbench</span>
      </div>
      <nav className="nav">
        {navSections.map((section, idx) => (
          <div key={section.label || idx}>
            {section.label && <div className="nav-section-label">{section.label}</div>}
            {section.items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={'nav-item' + (currentPage === item.id ? ' is-active' : '')}
                aria-current={currentPage === item.id ? 'page' : undefined}
                onClick={() => onNavigate(item.id)}
              >
                <span className="msr" aria-hidden="true">{item.icon}</span>
                {item.label}
              </button>
            ))}
          </div>
        ))}
      </nav>
    </aside>
  )
}
