import { useEffect, useState } from 'react'
import {
  activateShopifyConnection,
  configureShopifyConnection,
  getShopifyConnection,
  testShopifyConnection,
  type ShopifyConnection,
} from '@/data/sites'
import type { Site } from '@/types/domain'

function shopDomainFrom(site: Site) {
  return String(site.domain || '').replace(/^https?:\/\//i, '').split('/')[0].toLowerCase()
}

export function SiteShopifyConnectorCard({ site }: { site: Site }) {
  const [connection, setConnection] = useState<ShopifyConnection | null>(null)
  const [shopDomain, setShopDomain] = useState(shopDomainFrom(site))
  const [blogHandle, setBlogHandle] = useState('news')
  const [apiVersion, setApiVersion] = useState('2026-07')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setMessage('')
    setConnection(null)
    setShopDomain(shopDomainFrom(site))
    setBlogHandle('news')
    setApiVersion('2026-07')
    setClientId('')
    setClientSecret('')
    void getShopifyConnection(site.id, controller.signal)
      .then((value) => {
        setConnection(value)
        setShopDomain(value.shop_domain || shopDomainFrom(site))
        setBlogHandle(value.blog_handle || 'news')
        setApiVersion(value.api_version || '2026-07')
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setMessage(error instanceof Error ? `无法读取 Shopify 连接：${error.message}` : '无法读取 Shopify 连接')
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [site.id])

  async function saveVerifyActivate() {
    const replacingCredentials = Boolean(clientId.trim() || clientSecret.trim())
    if (replacingCredentials && (!clientId.trim() || !clientSecret.trim())) {
      setMessage('客户端 ID 和客户端密钥必须一起填写。')
      return
    }
    setWorking(true)
    setMessage('')
    try {
      const saved = await configureShopifyConnection(site.id, {
        shop_domain: shopDomain.trim(),
        blog_handle: blogHandle.trim(),
        api_version: apiVersion.trim(),
        ...(replacingCredentials ? { client_id: clientId.trim(), client_secret: clientSecret.trim() } : {}),
      })
      setConnection(saved)
      setClientId('')
      setClientSecret('')
      const tested = await testShopifyConnection(site.id)
      if (!tested.ok) throw new Error(tested.error || 'Shopify 验证未通过')
      const active = await activateShopifyConnection(site.id)
      setConnection(active)
      setMessage(`连接已验证并启用。权限：${(tested.scopes || []).join('、') || '已通过检查'}。`)
    } catch (error) {
      setMessage(error instanceof Error ? `连接失败：${error.message}` : '连接失败')
    } finally {
      setWorking(false)
    }
  }

  const status = connection?.status || 'not_configured'
  const tone = status === 'active' ? 'green' : status === 'verified' ? 'gold' : status === 'failed' ? 'pink' : 'blue'
  const hasCredentials = (connection?.configured_secret_names || []).length === 2
  return (
    <div style={{ background: 'var(--paper-100)', border: '1px solid var(--border)', borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <strong style={{ fontSize: 12 }}>Shopify Admin 连接</strong>
        <span className={`tag tag--${tone}`}>{loading ? '读取中' : status === 'active' ? '已启用' : status === 'not_configured' ? '未配置' : status}</span>
      </div>
      <div style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.55, marginTop: 6 }}>
        每个站点独立加密保存凭据。保存后会验证店铺身份、博客和读写权限，只有验证通过并启用后才能读写文章。
      </div>
      {!loading && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8, marginTop: 10 }}>
            <label style={{ display: 'grid', gap: 5, fontSize: 11 }}>
              <span>Shopify 店铺域名</span>
              <input className="input" value={shopDomain} onChange={(event) => setShopDomain(event.target.value)} placeholder="example.myshopify.com" disabled={working} />
            </label>
            <label style={{ display: 'grid', gap: 5, fontSize: 11 }}>
              <span>博客 Handle</span>
              <input className="input" value={blogHandle} onChange={(event) => setBlogHandle(event.target.value)} placeholder="news" disabled={working} />
            </label>
            <label style={{ display: 'grid', gap: 5, fontSize: 11 }}>
              <span>Admin API 版本</span>
              <input className="input" value={apiVersion} onChange={(event) => setApiVersion(event.target.value)} placeholder="2026-07" disabled={working} />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 8, marginTop: 8 }}>
            <label style={{ display: 'grid', gap: 5, fontSize: 11 }}>
              <span>客户端 ID{hasCredentials ? '（留空保留现有值）' : ''}</span>
              <input className="input" type="password" autoComplete="new-password" value={clientId} onChange={(event) => setClientId(event.target.value)} disabled={working} />
            </label>
            <label style={{ display: 'grid', gap: 5, fontSize: 11 }}>
              <span>客户端密钥{hasCredentials ? '（留空保留现有值）' : ''}</span>
              <input className="input" type="password" autoComplete="new-password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} disabled={working} />
            </label>
          </div>
          <button className="btn btn--primary btn--xs" type="button" onClick={() => void saveVerifyActivate()} disabled={working} style={{ marginTop: 10 }}>
            <span className="msr">{working ? 'progress_activity' : 'verified_user'}</span>
            {working ? '保存并验证中…' : hasCredentials ? '保存、重新验证并启用' : '保存、验证并启用'}
          </button>
          {connection?.last_tested_at && <div style={{ color: 'var(--ink-400)', fontSize: 10.5, marginTop: 7 }}>最近验证：{new Date(connection.last_tested_at).toLocaleString()}</div>}
        </>
      )}
      {(message || connection?.last_error) && (
        <div style={{ color: (message || connection?.last_error || '').includes('失败') ? '#a14a3c' : '#5c6e33', fontSize: 11, lineHeight: 1.55, marginTop: 9 }}>
          {message || connection?.last_error}
        </div>
      )}
    </div>
  )
}
