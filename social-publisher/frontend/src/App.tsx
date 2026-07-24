import { useState } from 'react'

import { BusinessScopeProvider } from '@/businessScope'
import { SocialPublishingPage } from '@/pages/SocialPublishingPage'

const STORAGE_KEY = 'exdivo-social-publisher-business'

export default function App() {
  const [businessId, setBusinessId] = useState(
    () => window.localStorage.getItem(STORAGE_KEY) || 'exdivo',
  )

  function updateBusiness(value: string) {
    setBusinessId(value)
    window.localStorage.setItem(STORAGE_KEY, value)
  }

  return (
    <BusinessScopeProvider businessId={businessId}>
      <div className="publisher-shell">
        <aside className="publisher-sidebar">
          <div className="publisher-brand">
            <span>EXDIVO</span>
            <b>Social Publisher</b>
          </div>
          <label>
            当前业务
            <input
              aria-label="当前业务"
              value={businessId}
              onChange={(event) => updateBusiness(event.target.value.trim())}
              placeholder="exdivo"
            />
          </label>
          <p>macOS 本地发布器</p>
          <small>最终发布仍需点击页面中的“全部发布”。</small>
        </aside>
        <main className="publisher-main">
          <SocialPublishingPage />
        </main>
      </div>
    </BusinessScopeProvider>
  )
}
