import { useEffect, useState } from 'react'
import {
  activateCustomConnector,
  configureOemAppsConnector,
  listCustomConnectors,
  syncCustomConnectorProducts,
  syncOemAppsCollections,
  testCustomConnector,
} from '@/data/connectors'
import type { CustomConnector } from '@/data/connectors'
import type { Site } from '@/types/domain'

export function SiteOemAppsConnectorCard({ site }: { site: Site }) {
  const [connector, setConnector] = useState<CustomConnector | null>(null)
  const [token, setToken] = useState('')
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState<'verify' | 'sync' | null>(null)
  const [message, setMessage] = useState('')

  async function refreshConnector() {
    const result = await listCustomConnectors(site.id)
    setConnector(result.items.find((item) => item.config?.adapter === 'oemapps') || null)
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setConnector(null)
    setMessage('')
    void listCustomConnectors(site.id)
      .then((result) => {
        if (!cancelled) setConnector(result.items.find((item) => item.config?.adapter === 'oemapps') || null)
      })
      .catch((error) => {
        if (!cancelled) setMessage(error instanceof Error ? `无法读取商品连接器：${error.message}` : '无法读取商品连接器')
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [site.id])

  async function verifyAndActivate() {
    const trimmedToken = token.trim()
    if (!connector && !trimmedToken) {
      window.alert('请填写 OEMApps 站点 Token。它只会加密保存到专用商品连接器，不会覆盖通用站点 API 配置。')
      return
    }
    setAction('verify')
    setMessage('')
    try {
      const saved = trimmedToken ? await configureOemAppsConnector(site.id, trimmedToken) : connector
      if (!saved) throw new Error('未找到商品连接器')
      const tested = await testCustomConnector(saved.id)
      if (!tested.ok) throw new Error(tested.errors?.join('；') || '商品映射验证未通过')
      const active = await activateCustomConnector(saved.id)
      setConnector(active)
      setToken('')
      setMessage(`已验证并启用：可读取 ${tested.mapped_items ?? tested.total_items ?? 0} 个商品样本。下一步可同步商品与分类。`)
    } catch (error) {
      setMessage(error instanceof Error ? `验证失败：${error.message}` : '验证失败')
      void refreshConnector().catch(() => undefined)
    } finally {
      setAction(null)
    }
  }

  async function syncCatalog() {
    if (!connector || connector.status !== 'active') return
    setAction('sync')
    setMessage('')
    try {
      const products = await syncCustomConnectorProducts(connector.id)
      const collections = await syncOemAppsCollections(connector.id)
      setMessage(`同步完成：商品 ${products.items_upserted} 个，分类 ${collections.collections_upserted} 个。`)
    } catch (error) {
      setMessage(error instanceof Error ? `同步失败：${error.message}` : '同步失败')
    } finally {
      setAction(null)
    }
  }

  const status = connector?.status || '未接入'
  const tone = status === 'active' ? 'green' : status === 'verified' ? 'gold' : status === '未接入' ? 'blue' : 'pink'
  const canSync = connector?.status === 'active'
  return (
    <div style={{ background: 'var(--paper-100)', border: '1px solid var(--border)', borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <strong style={{ fontSize: 12 }}>OEMApps 商品与分类连接器</strong>
        <span className={`tag tag--${tone}`}>{loading ? '读取中' : status === 'active' ? '已启用' : status}</span>
      </div>
      <div style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.55, marginTop: 6 }}>
        专供 ExDivo、Avinoti 这类 OEMApps 主站。商品 Token 与通用站点 API 凭据独立加密保存；验证通过后才允许同步，不会向站点写入任何内容。
      </div>
      {!loading && (
        <>
          {connector && <div style={{ color: 'var(--ink-400)', fontSize: 10.5, marginTop: 7 }}>连接器：{connector.name} · 当前版本 v{connector.current_version}{connector.verified_at ? ' · 已验证' : ''}</div>}
          <label style={{ display: 'grid', gap: 5, fontSize: 11, marginTop: 10 }}>
            <span>{connector ? '替换 OEMApps Token（留空则仅重新验证）' : 'OEMApps 站点 Token'}</span>
            <input className="input" type="password" autoComplete="new-password" value={token} onChange={(event) => setToken(event.target.value)} placeholder={connector ? '可选：填写后会替换旧 Token' : '粘贴站点 Token'} disabled={action !== null} />
          </label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 10 }}>
            <button className="btn btn--primary btn--xs" type="button" onClick={() => void verifyAndActivate()} disabled={action !== null}>
              <span className="msr">{action === 'verify' ? 'progress_activity' : 'verified_user'}</span>{action === 'verify' ? '验证中…' : connector ? '重新验证并启用' : '保存并验证'}
            </button>
            {canSync && <button className="btn btn--ghost btn--xs" type="button" onClick={() => void syncCatalog()} disabled={action !== null}>
              <span className="msr">{action === 'sync' ? 'progress_activity' : 'inventory_2'}</span>{action === 'sync' ? '同步中…' : '同步商品与分类'}
            </button>}
          </div>
        </>
      )}
      {message && <div style={{ color: message.includes('失败') || message.includes('无法') ? '#a14a3c' : '#5c6e33', fontSize: 11, lineHeight: 1.55, marginTop: 9 }}>{message}</div>}
    </div>
  )
}
