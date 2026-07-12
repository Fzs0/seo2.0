import { useState } from 'react'
import type { PipelineStep } from '@/hooks/useData'
import type { Site } from '@/types/domain'

export type ArticleResultData = {
  status?: 'done' | 'failed' | string
  steps: PipelineStep[]
  article?: { id: string; title: string; status: string }
  brief?: { source?: string; aiEnhanced?: boolean; aiMeta?: Record<string, unknown>; text?: string }
  outline?: string
  content?: string
  contentPreview?: string
  savedTo?: { table?: string; articleId?: string }
  serp?: { id?: string | null; source?: string; status?: string }
  qa?: Array<{ key: string; ok: boolean }>
  model?: string
  contentLength?: number
}

type ResultTab = 'article' | 'brief' | 'outline' | 'pipeline' | 'seo'

function formatCount(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value)
}

export function ArticleResultDialog({
  result,
  tab,
  onTabChange,
  onClose,
  publishSites = [],
  targetSiteId = '',
  onSiteChange,
  publishing = false,
  publishMessage,
  onPublish,
}: {
  result: ArticleResultData
  tab?: ResultTab
  onTabChange?: (tab: ResultTab) => void
  onClose: () => void
  publishSites?: Site[]
  targetSiteId?: string
  onSiteChange?: (siteId: string) => void
  publishing?: boolean
  publishMessage?: string
  onPublish?: (dryRun: boolean) => Promise<void>
}) {
  const [localTab, setLocalTab] = useState<ResultTab>('article')
  const activeTab = tab || localTab
  const selectTab = onTabChange || setLocalTab
  const content = result.content || result.contentPreview || '暂无文章正文'
  const passedQa = (result.qa || []).filter((item) => item.ok).length
  const tabLabels: Array<[ResultTab, string]> = [
    ['article', '文章正文'],
    ['brief', '增强 Brief'],
    ['outline', '文章大纲'],
    ['pipeline', '执行过程'],
    ['seo', 'SEO 与 QA'],
  ]

  return (
    <div className="article-dialog-backdrop" role="presentation">
      <section className="article-dialog" role="dialog" aria-modal="true" aria-labelledby="article-result-title">
        <div className="article-dialog__head">
          <div>
            <div className="eyebrow">ARTICLE PIPELINE · 已保存</div>
            <h2 id="article-result-title">{result.article?.title || '文章生成结果'}</h2>
            <div className="article-dialog__meta">
              <span className="tag tag--green">{result.status === 'failed' ? '生成失败' : '已保存'}</span>
              <span>{result.contentLength ? `${formatCount(result.contentLength)} 字符` : '正文长度未知'}</span>
              <span>SERP：{result.serp?.source || result.serp?.status || '未使用'}</span>
            </div>
          </div>
          <button className="icon-btn" type="button" aria-label="关闭生成结果" onClick={onClose}>
            <span className="msr">close</span>
          </button>
        </div>

        <div className="article-dialog__tabs" role="tablist" aria-label="文章生成结果">
          {tabLabels.map(([key, label]) => (
            <button key={key} className={activeTab === key ? 'is-active' : ''} type="button" role="tab" aria-selected={activeTab === key} onClick={() => selectTab(key)}>
              {label}
            </button>
          ))}
        </div>

        <div className="article-dialog__body">
          {activeTab === 'article' && <pre className="article-dialog__content">{content}</pre>}
          {activeTab === 'brief' && <pre className="article-dialog__content">{result.brief?.text || '暂无 Brief 内容'}</pre>}
          {activeTab === 'outline' && <pre className="article-dialog__content">{result.outline || '暂无文章大纲'}</pre>}
          {activeTab === 'pipeline' && (
            <div className="article-dialog__steps">
              {result.steps.length ? result.steps.map((step) => (
                <div className="article-dialog__step" key={step.key}>
                  <div className={`article-dialog__step-dot article-dialog__step-dot--${step.status}`}>
                    <span className="msr">{step.status === 'done' ? 'check' : step.status === 'failed' ? 'error' : 'hourglass_top'}</span>
                  </div>
                  <div>
                    <strong>{step.label}</strong>
                    <span>{step.status}</span>
                    {step.message && <p>{step.message}</p>}
                  </div>
                </div>
              )) : <div className="agent-empty">历史文章未保存逐步执行记录。</div>}
            </div>
          )}
          {activeTab === 'seo' && (
            <div className="article-dialog__info-grid">
              <InfoItem label="保存位置" value={`${result.savedTo?.table || 'seo_agent.articles'} / ${result.savedTo?.articleId || result.article?.id || '未知'}`} />
              <InfoItem label="Brief 来源" value={result.brief?.aiEnhanced ? 'AI 增强' : '已保存 Brief'} />
              <InfoItem label="生成模型" value={result.model || '未返回'} />
              <InfoItem label="SERP 状态" value={`${result.serp?.source || '未使用'} · ${result.serp?.status || '未知'}`} />
              <InfoItem label="QA 结果" value={`${passedQa}/${result.qa?.length || 0} 项通过`} />
              <InfoItem label="文章状态" value={result.article?.status || '未知'} />
              {(result.qa || []).map((item) => <InfoItem key={item.key} label={`QA · ${item.key}`} value={item.ok ? '通过' : '未通过'} />)}
            </div>
          )}
        </div>

        <div className="article-dialog__foot">
          {onPublish && (
            <>
              <select className="input" value={targetSiteId} onChange={(event) => onSiteChange?.(event.target.value)} aria-label="选择发布站点">
                <option value="">选择发布站点</option>
                {publishSites.map((site) => (
                  <option key={site.id} value={site.id}>
                    {site.name} · {site.publish_adapter || site.site_type}{site.publish_ready ? '' : ' · 未就绪'}
                  </option>
                ))}
              </select>
              <button className="btn btn--ghost" type="button" disabled={!targetSiteId || publishing} onClick={() => void onPublish(true)}>
                发布预检
              </button>
              <button className="btn btn--primary" type="button" disabled={!targetSiteId || publishing} onClick={() => void onPublish(false)}>
                {publishing ? '发布中…' : '确认发布'}
              </button>
              {publishMessage && <div className="article-dialog__publish-message">{publishMessage}</div>}
            </>
          )}
          <button className="btn btn--ghost" type="button" onClick={onClose}>关闭</button>
        </div>
      </section>
    </div>
  )
}

function InfoItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="article-dialog__info-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
