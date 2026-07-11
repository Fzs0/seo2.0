
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
    items: [
      { id: 'dashboard', label: '决策总览', icon: 'space_dashboard' },
      { id: 'opportunities', label: '机会洞察', icon: 'auto_awesome' },
      { id: 'serp', label: 'SERP 洞察', icon: 'search' },
      { id: 'keywords', label: '关键词库', icon: 'key' },
    ],
  },
  {
    label: '内容生产',
    items: [
      { id: 'brief', label: 'Brief 工作台', icon: 'description' },
      { id: 'content', label: '内容策略', icon: 'edit_note' },
      { id: 'articles', label: '文章管理', icon: 'article' },
    ],
  },
  {
    label: '资源',
    items: [
      { id: 'sites', label: '站点管理', icon: 'apartment' },
      { id: 'analytics', label: '数据分析', icon: 'monitoring' },
    ],
  },
  {
    label: '配置',
    items: [
      { id: 'rules', label: '规则方向', icon: 'tune' },
      { id: 'sync', label: '同步状态', icon: 'sync' },
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
        <span className="sidebar__brand-name">SEO Workbench</span>
      </div>
      <nav className="nav">
        {navSections.map((section, idx) => (
          <div key={idx}>
            {section.label && (
              <div className="nav-section-label">{section.label}</div>
            )}
            {section.items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={
                  'nav-item' + (currentPage === item.id ? ' is-active' : '')
                }
                onClick={() => onNavigate(item.id)}
              >
                <span className="msr">{item.icon}</span>
                {item.label}
              </button>
            ))}
          </div>
        ))}
      </nav>
    </aside>
  )
}
