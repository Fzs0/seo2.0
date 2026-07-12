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
      { id: 'agent-command', label: '总控台', icon: 'space_dashboard' },
      { id: 'agent-assets', label: '资产地图', icon: 'map' },
      { id: 'agent-opportunities', label: '机会引擎', icon: 'auto_awesome' },
    ],
  },
  {
    label: '执行中心',
    items: [
      { id: 'agent-execution', label: '执行管线', icon: 'route' },
      { id: 'agent-review', label: '数据复盘', icon: 'monitoring' },
    ],
  },
  {
    label: '治理与知识',
    items: [
      { id: 'agent-risk', label: '风险治理', icon: 'shield' },
      { id: 'rules', label: '知识系统', icon: 'menu_book' },
    ],
  },
  {
    label: '操作工具',
    items: [
      { id: 'keywords', label: '关键词库', icon: 'key' },
      { id: 'serp', label: 'SERP 洞察', icon: 'search' },
      { id: 'content', label: '内容生产', icon: 'edit_note' },
      { id: 'articles', label: '文章管理', icon: 'article' },
      { id: 'sites', label: '站点管理', icon: 'domain' },
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
