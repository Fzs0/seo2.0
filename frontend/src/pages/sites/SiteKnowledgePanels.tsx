import type { SiteIndexScanResult, SiteKnowledgeProfile } from '@/types/domain'
import { hasKnowledgeData } from './sitePageModel'

export function SiteIndexScanCard({
  profile,
  result,
  scanning,
  onFiles,
}: {
  profile?: SiteKnowledgeProfile | null
  result?: SiteIndexScanResult
  scanning: boolean
  onFiles: (files: File[]) => void
}) {
  const persisted = profile?.index_scan
  const index = result?.index || persisted
  const summary = result?.seo_audit.summary || persisted?.summary
  const issues = result?.seo_audit.issues || persisted?.issues || []
  const productHints = result?.seo_audit.product_hints || persisted?.product_hints || profile?.products || []
  return (
    <div style={{ background: 'var(--paper-100)', border: '1px solid var(--border)', borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <strong style={{ fontSize: 12 }}>主站索引与 SEO 审查</strong>
        <span className={`tag tag--${summary ? (summary.issues ? 'gold' : 'green') : 'blue'}`}>{summary ? '已扫描' : '未扫描'}</span>
      </div>
      <div style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.5, marginTop: 6 }}>
        上传新的索引时默认覆盖旧库存；一次选择多个文件时，首个文件覆盖、后续文件继续合并。支持 XML、TXT 和 GZip 压缩索引。
      </div>
      {index && summary && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6, marginTop: 10 }}>
            <ScanMetric label="索引 URL" value={index.indexed_urls} />
            <ScanMetric label="已扫描" value={index.scanned_urls} />
            <ScanMetric label="可读取" value={summary.ok} />
            <ScanMetric label="问题页面" value={summary.issues} />
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>
            <span className="chip">缺 title {summary.missing_title}</span>
            <span className="chip">缺 description {summary.missing_description}</span>
            <span className="chip">缺 H1 {summary.missing_h1}</span>
            <span className="chip">重复 title {summary.duplicate_title}</span>
          </div>
          {productHints.length > 0 && <div style={{ color: 'var(--ink-500)', fontSize: 11, marginTop: 8 }}>识别到的产品：{productHints.slice(0, 6).join('、')}</div>}
          {issues.length > 0 && <div style={{ marginTop: 8, display: 'grid', gap: 4 }}>
            {issues.slice(0, 3).map((issue) => (
              <div key={issue.url} style={{ fontSize: 10.5, color: 'var(--ink-500)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={issue.url}>
                <strong style={{ color: '#a14a3c' }}>{issue.issues.join('、')}</strong> · {issue.url}
              </div>
            ))}
            {issues.length > 3 && <div style={{ fontSize: 10.5, color: 'var(--ink-400)' }}>还有 {issues.length - 3} 个问题页面</div>}
          </div>}
          <div style={{ color: 'var(--ink-400)', fontSize: 10.5, marginTop: 8 }}>文件：{index.files?.map((item) => item.filename).join('、') || index.filename}{index.nested_sitemaps ? ` · 展开 ${index.nested_sitemaps} 个子索引` : ''}</div>
        </>
      )}
      <div style={{ marginTop: 10 }}>
        <label className="btn btn--primary btn--xs" style={{ display: 'inline-flex', cursor: scanning ? 'wait' : 'pointer' }}>
          <span className="msr">{scanning ? 'progress_activity' : 'upload_file'}</span>{scanning ? '扫描中…' : summary ? '重新上传并扫描' : '上传索引并扫描'}
          <input
            type="file"
            multiple
            accept=".xml,.txt,.gz,text/xml,text/plain,application/gzip"
            disabled={scanning}
            style={{ display: 'none' }}
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files || [])
              event.currentTarget.value = ''
              if (files.length) onFiles(files)
            }}
          />
        </label>
      </div>
    </div>
  )
}

function ScanMetric({ label, value }: { label: string; value: number }) {
  return <div style={{ background: '#fff', borderRadius: 8, padding: '6px 7px' }}><div style={{ color: 'var(--ink-400)', fontSize: 10 }}>{label}</div><strong style={{ fontSize: 15 }}>{value}</strong></div>
}

export function SiteKnowledgeCard({
  profile,
  generating,
  onGenerate,
  onEdit,
  onConfirm,
  clearing,
  onClear,
  onClearAll,
}: {
  profile?: SiteKnowledgeProfile | null
  generating: boolean
  onGenerate: () => void
  onEdit: () => void
  onConfirm: () => void
  clearing: boolean
  onClear: () => void
  onClearAll: () => void
}) {
  const activeProfile = hasKnowledgeData(profile) ? profile : null
  const status = activeProfile?.status === 'confirmed' ? '已确认' : activeProfile ? '待确认' : '未生成'
  const topics = activeProfile?.in_scope_topics || []
  return (
    <div style={{ background: 'var(--paper-100)', border: '1px solid var(--border)', borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <strong style={{ fontSize: 12 }}>站点知识</strong>
        <span className={`tag tag--${activeProfile?.status === 'confirmed' ? 'green' : activeProfile ? 'gold' : 'blue'}`}>{status}</span>
      </div>
      {activeProfile ? <>
        <div style={{ fontSize: 12, fontWeight: 700, marginTop: 8 }}>{activeProfile.positioning || '定位待补充'}</div>
        <div style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.5, marginTop: 4 }}>{activeProfile.audience || '目标受众待确认'}</div>
        {topics.length > 0 && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>{topics.slice(0, 6).map((topic) => <span className="chip" key={topic}>{topic}</span>)}</div>}
        <div style={{ color: 'var(--ink-400)', fontSize: 10.5, marginTop: 8 }}>依据：{activeProfile.evidence?.length || 0} 条站点、文章和关键词证据</div>
      </> : <div style={{ color: 'var(--ink-500)', fontSize: 11, marginTop: 7 }}>读取现有文章和关键词后，自动整理这个站点应该写什么。</div>}
      <div style={{ display: 'flex', gap: 7, marginTop: 10 }}>
        <button className="btn btn--primary btn--xs" type="button" onClick={onGenerate} disabled={generating}><span className="msr">{generating ? 'progress_activity' : 'auto_awesome'}</span>{generating ? '生成中…' : activeProfile ? '重新生成' : '生成知识'}</button>
        {activeProfile && <button className="btn btn--ghost btn--xs" type="button" onClick={onEdit}>编辑</button>}
        {activeProfile && activeProfile.status !== 'confirmed' && <button className="btn btn--ghost btn--xs" type="button" onClick={onConfirm}>确认使用</button>}
      </div>
      {activeProfile && <div style={{ display: 'flex', gap: 7, marginTop: 7 }}>
        <button className="btn btn--ghost btn--xs" type="button" onClick={onClear} disabled={clearing}>{clearing ? '清空中…' : '清空画像（保留扫描）'}</button>
        <button className="btn btn--ghost btn--xs" type="button" onClick={onClearAll} disabled={clearing} style={{ color: '#a14a3c' }}>全部清空</button>
      </div>}
    </div>
  )
}
