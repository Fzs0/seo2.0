import { useState } from 'react'
import { ArticleResultDialog, type ArticleResultData } from '@/components/ArticleResultDialog'
import { DataGuard } from '@/components/StateBlock'
import { getArticleDetail, publishArticle, syncAllPosts, syncSitePosts, useArticles, usePosts, useSites } from '@/hooks/useData'

function formatDate(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function ArticlesPage() {
  const [refreshKey, setRefreshKey] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [selectedSiteId, setSelectedSiteId] = useState('')
  const [message, setMessage] = useState<string>()
  const [openingArticleId, setOpeningArticleId] = useState<string>()
  const [selectedArticle, setSelectedArticle] = useState<ArticleResultData | null>(null)
  const [articleTargetSiteId, setArticleTargetSiteId] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [publishMessage, setPublishMessage] = useState<string>()
  const articles = useArticles(refreshKey)
  const sites = useSites()
  const posts = usePosts(refreshKey, selectedSiteId || undefined)
  const publishSites = (sites.data ?? []).filter((site) => site.status === 'active')

  async function handleSyncSelected() {
    if (!selectedSiteId || syncing) return
    const site = sites.data?.find((item) => item.id === selectedSiteId)
    setSyncing(true)
    setMessage(`正在读取 ${site?.name || '目标站点'} 的已有文章…`)
    try {
      const result = await syncSitePosts(selectedSiteId, 100)
      setMessage(result.ok ? `${site?.name || '目标站点'} 读取完成：获取 ${result.fetched} 篇，写入 ${result.saved} 篇。` : `${site?.name || '目标站点'} 读取失败：${result.error || '未知错误'}`)
      if (result.ok) setRefreshKey((key) => key + 1)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '站点文章读取失败')
    } finally {
      setSyncing(false)
    }
  }

  async function handleSync() {
    if (syncing) return
    setSyncing(true)
    setMessage('正在同步所有站点文章…')
    try {
      const result = await syncAllPosts(100)
      const failed = result.results.filter((item) => !item.ok).length
      setMessage(`同步完成：写入 ${result.saved} 篇，失败站点 ${failed} 个。`)
      setRefreshKey((key) => key + 1)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '同步失败')
    } finally {
      setSyncing(false)
    }
  }

  async function handleOpenArticle(articleId: string) {
    if (openingArticleId) return
    setOpeningArticleId(articleId)
    try {
      const detail = await getArticleDetail(articleId)
      const parts = detail.article_parts || {}
      const defaultSiteId = detail.site_id && publishSites.some((site) => site.id === detail.site_id)
        ? detail.site_id
        : publishSites.find((site) => site.publish_ready)?.id || publishSites[0]?.id || ''
      setArticleTargetSiteId(defaultSiteId)
      setPublishMessage(undefined)
      setSelectedArticle({
        status: detail.status,
        steps: [],
        article: { id: detail.id, site_id: detail.site_id, title: detail.title, status: detail.status },
        brief: { source: 'saved', text: detail.brief_md || '' },
        outline: typeof parts.outline === 'string' ? parts.outline : '',
        content: detail.content_md || detail.content_html || '',
        savedTo: { table: 'seo_agent.articles', articleId: detail.id },
        serp: { id: detail.serp_snapshot_id, source: detail.serp_snapshot_id ? 'saved' : undefined, status: detail.serp_snapshot_id ? 'available' : 'not-loaded' },
        qa: detail.qa_checklist || [],
        model: detail.generation_model || undefined,
        contentLength: (detail.content_md || detail.content_html || '').length,
      })
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '文章详情读取失败')
    } finally {
      setOpeningArticleId(undefined)
    }
  }

  async function handlePublish(dryRun: boolean) {
    const articleId = selectedArticle?.article?.id
    if (!articleId || publishing) return
    setPublishing(true)
    setPublishMessage(dryRun ? '正在执行发布预检…' : '正在发布文章…')
    try {
      const result = await publishArticle(articleId, articleTargetSiteId, dryRun)
      setPublishMessage(result.ok ? (dryRun ? `预检通过：${result.url || '可以发布'}` : `发布成功：${result.url || result.post_id || '已发布'}`) : `发布失败：${result.error || '未知错误'}`)
      if (result.ok) {
        setSelectedArticle((current) => current ? { ...current, status: dryRun ? 'approved' : 'published', article: current.article ? { ...current.article, status: dryRun ? 'approved' : 'published', site_id: articleTargetSiteId } : current.article } : current)
        if (!dryRun) setRefreshKey((key) => key + 1)
      }
    } catch (error) {
      setPublishMessage(error instanceof Error ? error.message : '发布失败')
    } finally {
      setPublishing(false)
    }
  }

  return (
    <section className="page" data-screen-label="文章管理">
      <div>
        <h1>文章管理</h1>
        <p>这里展示 AI 生成稿及其目标发布站点；下方列表展示从各站点 API 同步回来的已有文章。</p>
        {message && <p>{message}</p>}
      </div>

      <div className="filter-row">
        <select className="chip" value={selectedSiteId} onChange={(event) => setSelectedSiteId(event.target.value)} disabled={sites.loading}>
          <option value="">全部站点</option>
          {sites.data?.map((site) => <option value={site.id} key={site.id}>{site.name}</option>)}
        </select>
        <button className="btn btn--primary" type="button" onClick={() => void handleSyncSelected()} disabled={syncing || !selectedSiteId}>
          <span className="msr">{syncing ? 'progress_activity' : 'download'}</span>
          {syncing ? '读取中…' : '读取当前站点文章'}
        </button>
        <button className="btn btn--primary" type="button" onClick={handleSync} disabled={syncing}>
          <span className="msr">{syncing ? 'progress_activity' : 'sync'}</span>
          {syncing ? '同步中' : '同步所有站点文章'}
        </button>
        <span className="chip chip--active">
          <span className="msr">article</span>
          已同步 {posts.data?.length ?? '—'}
        </span>
      </div>

      <div className="tbl-wrap" style={{ marginBottom: 18 }}>
        <DataGuard
          loading={articles.loading}
          error={articles.error}
          empty={!articles.data || articles.data.length === 0}
          emptyTitle="暂无生成稿件"
          emptyHint="到内容策略页点击“生成 1 篇文章”后会展示在这里"
        >
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 36 }}>#</th>
                <th>生成稿标题</th>
                <th>目标站点</th>
                <th>状态</th>
                <th>关键词</th>
                <th>模型</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(articles.data ?? []).map((article, i) => (
                <tr key={article.id}>
                  <td style={{ color: 'var(--ink-400)' }}>{i + 1}</td>
                  <td>
                    <div className="tbl-strong">{article.title}</div>
                    <div style={{ fontSize: 11, color: 'var(--ink-400)', marginTop: 2 }}>{article.id}</div>
                  </td>
                  <td>
                    <span className="tag tag--blue">{article.site_label || sites.data?.find((site) => site.id === article.site_id)?.name || '未分配站点'}</span>
                    <div style={{ marginTop: 4, color: 'var(--ink-400)', fontSize: 11 }}>{article.site_id || '需要人工选择'}</div>
                  </td>
                  <td><span className="tag tag--green">{article.status}</span></td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{article.primary_keyword || article.keyword_id || '—'}</td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{article.generation_model || '—'}</td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{formatDate(article.created_at)}</td>
                  <td>
                    <button className="btn btn--ghost btn--xs" type="button" onClick={() => void handleOpenArticle(article.id)} disabled={openingArticleId === article.id}>
                      {openingArticleId === article.id ? '读取中…' : '查看并发布'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataGuard>
      </div>

      <div className="tbl-wrap">
        <DataGuard
          loading={posts.loading}
          error={posts.error}
          empty={!posts.data || posts.data.length === 0}
          emptyTitle="暂无站点文章"
          emptyHint="点击“同步所有站点文章”后会写入并展示"
        >
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 36 }}>#</th>
                <th>标题</th>
                <th>来源</th>
                <th>状态</th>
                <th>URL</th>
                <th>发布时间</th>
                <th>抓取时间</th>
              </tr>
            </thead>
            <tbody>
              {(posts.data ?? []).map((post, i) => (
                <tr key={post.id}>
                  <td style={{ color: 'var(--ink-400)' }}>{i + 1}</td>
                  <td>
                    <div className="tbl-strong">{post.title}</div>
                    <div style={{ fontSize: 11, color: 'var(--ink-400)', marginTop: 2 }}>
                      {post.external_id || post.id}
                    </div>
                  </td>
                  <td><span className="tag tag--blue">{post.source}</span></td>
                  <td><span className="tag tag--gray">{post.status || 'unknown'}</span></td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{post.url || post.slug || '—'}</td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{formatDate(post.published_at)}</td>
                  <td style={{ color: 'var(--ink-500)', fontSize: 12 }}>{formatDate(post.fetched_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataGuard>
      </div>

      {selectedArticle && (
        <ArticleResultDialog
          result={selectedArticle}
          onClose={() => setSelectedArticle(null)}
          publishSites={publishSites}
          targetSiteId={articleTargetSiteId}
          onSiteChange={setArticleTargetSiteId}
          publishing={publishing}
          publishMessage={publishMessage}
          onPublish={handlePublish}
        />
      )}
    </section>
  )
}
