import { useEffect, useState } from 'react'
import {
  executeShopifyProductSeo,
  getShopifyProductSeo,
  previewShopifyProductSeo,
  syncShopifyProducts,
  type ShopifyProductSeoItem,
} from '@/data/sites'
import type { Site } from '@/types/domain'

type Draft = { title: string; description: string; previewToken?: string }

export function SiteShopifyProductSeoCard({ site }: { site: Site }) {
  const [items, setItems] = useState<ShopifyProductSeoItem[]>([])
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [missingOnly, setMissingOnly] = useState(true)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState<number | 'sync' | null>(null)
  const [message, setMessage] = useState('')

  function load(signal: AbortSignal) {
    setLoading(true)
    return getShopifyProductSeo(site.id, missingOnly, signal)
      .then((result) => {
        setItems(result.items)
        setDrafts(Object.fromEntries(result.items.map((item) => [item.id, {
          title: item.meta_title || '',
          description: item.meta_description || '',
        }])))
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const controller = new AbortController()
    setItems([])
    setDrafts({})
    setMessage('')
    void load(controller.signal).catch((error) => {
      if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : '无法读取商品 SEO')
    })
    return () => controller.abort()
  }, [site.id, missingOnly])

  function change(id: number, patch: Partial<Draft>) {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], ...patch, previewToken: undefined } }))
  }

  async function sync() {
    setWorking('sync')
    setMessage('')
    try {
      const result = await syncShopifyProducts(site.id)
      setMessage(`同步完成：读取 ${result.received} 个商品，其中 ${result.missing_seo} 个缺少 SEO 标题或描述。`)
      const controller = new AbortController()
      await load(controller.signal)
    } catch (error) {
      setMessage(error instanceof Error ? `同步失败：${error.message}` : '同步失败')
    } finally {
      setWorking(null)
    }
  }

  async function preview(item: ShopifyProductSeoItem) {
    const draft = drafts[item.id]
    setWorking(item.id)
    setMessage('')
    try {
      const result = await previewShopifyProductSeo(site.id, {
        product_id: item.id,
        expected_updated_at: item.source_updated_at,
        meta_title: draft.title,
        meta_description: draft.description,
      })
      setDrafts((current) => ({ ...current, [item.id]: { ...current[item.id], previewToken: result.preview_token } }))
      setMessage(`“${item.title}”已生成绑定当前版本的预览；确认差异后才能写入。`)
    } catch (error) {
      setMessage(error instanceof Error ? `预览失败：${error.message}` : '预览失败')
    } finally {
      setWorking(null)
    }
  }

  async function execute(item: ShopifyProductSeoItem) {
    const draft = drafts[item.id]
    if (!draft.previewToken || !window.confirm(`只修改“${item.title}”的 SEO 标题和描述，确认写入 Shopify？`)) return
    setWorking(item.id)
    setMessage('')
    try {
      await executeShopifyProductSeo(site.id, {
        product_id: item.id,
        expected_updated_at: item.source_updated_at,
        meta_title: draft.title,
        meta_description: draft.description,
        preview_token: draft.previewToken,
        request_id: crypto.randomUUID(),
      })
      setMessage(`“${item.title}”的 SEO 标题和描述已写入并回读验证。`)
      const controller = new AbortController()
      await load(controller.signal)
    } catch (error) {
      setMessage(error instanceof Error ? `写入失败：${error.message}` : '写入失败')
    } finally {
      setWorking(null)
    }
  }

  return (
    <div style={{ background: 'var(--paper-100)', border: '1px solid var(--border)', borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 12 }}>Shopify 商品 SEO 工作台</strong>
        <div style={{ display: 'flex', gap: 7 }}>
          <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={missingOnly} onChange={(event) => setMissingOnly(event.target.checked)} />
            仅看缺失项
          </label>
          <button className="btn btn--ghost btn--xs" type="button" onClick={() => void sync()} disabled={working !== null}>
            {working === 'sync' ? '同步中…' : '同步商品'}
          </button>
        </div>
      </div>
      <div style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.55, marginTop: 6 }}>
        写入请求严格只包含商品 ID、SEO Title 和 Meta Description，不包含 Handle、变体、价格、库存或 SKU。Shopify 不使用 Meta Keywords；图片 Alt 当前只审计、不在此处写入。
      </div>
      {loading && <div style={{ fontSize: 11, marginTop: 10 }}>读取中…</div>}
      {!loading && items.length === 0 && <div style={{ fontSize: 11, marginTop: 10 }}>暂无商品。请先同步，或关闭“仅看缺失项”。</div>}
      {items.map((item) => {
        const draft = drafts[item.id] || { title: '', description: '' }
        return (
          <div key={item.id} style={{ borderTop: '1px solid var(--border)', marginTop: 10, paddingTop: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <strong style={{ fontSize: 11.5 }}>{item.title}</strong>
              <span className="tag tag--gold">{item.seo_audit?.images_missing_alt || 0} 张图缺 Alt</span>
            </div>
            <input className="input" style={{ marginTop: 7 }} value={draft.title} maxLength={70} placeholder="SEO Title（最多 70 字符）" onChange={(event) => change(item.id, { title: event.target.value })} disabled={working !== null} />
            <textarea className="input" style={{ marginTop: 7, minHeight: 64 }} value={draft.description} maxLength={320} placeholder="Meta Description（最多 320 字符）" onChange={(event) => change(item.id, { description: event.target.value })} disabled={working !== null} />
            <div style={{ display: 'flex', gap: 7, marginTop: 7 }}>
              <button className="btn btn--ghost btn--xs" type="button" onClick={() => void preview(item)} disabled={working !== null || !draft.title.trim() || !draft.description.trim()}>
                {working === item.id ? '处理中…' : '预览并锁定版本'}
              </button>
              {draft.previewToken && <button className="btn btn--primary btn--xs" type="button" onClick={() => void execute(item)} disabled={working !== null}>确认写入</button>}
            </div>
          </div>
        )
      })}
      {message && <div style={{ color: message.includes('失败') ? '#a14a3c' : '#5c6e33', fontSize: 11, marginTop: 9 }}>{message}</div>}
    </div>
  )
}
