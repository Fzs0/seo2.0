import { useState, type ReactNode } from 'react'
import type { PipelineStep } from '@/data/articles'
import type { Site } from '@/types/domain'

export type ArticleResultData = {
  status?: 'done' | 'failed' | string
  steps: PipelineStep[]
  article?: {
    id: string
    site_id?: string | null
    title: string
    status: string
    slug?: string | null
    language_code?: string | null
    market?: string | null
    primary_keyword?: string | null
    meta_title?: string | null
    meta_description?: string | null
  }
  brief?: { source?: string; aiEnhanced?: boolean; aiMeta?: Record<string, unknown>; text?: string }
  outline?: string
  content?: string
  contentPreview?: string
  savedTo?: { table?: string; articleId?: string }
  serp?: { id?: string | null; source?: string; status?: string }
  qa?: Array<{ key: string; ok: boolean }>
  provider?: string
  model?: string
  contentLength?: number
}

type ResultTab = 'article' | 'brief' | 'outline' | 'pipeline' | 'meta' | 'seo'

function formatCount(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function splitFrontmatter(source: string) {
  let normalized = (source || '').replace(/\r\n?/g, '\n')
  const wrapped = normalized.match(/^```(?:markdown|md)?\n([\s\S]*?)\n```\s*$/i)
  if (wrapped) normalized = wrapped[1]
  const meta: Record<string, string> = {}
  let lines = normalized.split('\n')
  if (normalized.startsWith('---\n')) {
    const end = lines.findIndex((line, index) => index > 0 && line.trim() === '---')
    if (end >= 0) {
      for (const line of lines.slice(1, end)) {
        const match = line.match(/^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$/)
        if (!match) continue
        const value = match[2].trim()
        meta[match[1]] = value.length >= 2 && value[0] === value[value.length - 1] && ['"', "'"].includes(value[0])
          ? value.slice(1, -1)
          : value
      }
      lines = lines.slice(end + 1)
    }
  }
  const known = ['title', 'meta title', 'meta description', 'url slug', 'primary keyword', 'secondary keywords', 'last updated', 'evidence needed', 'content qa checklist']
  const output: string[] = []
  let foundMetadata = false
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    const heading = line.match(/^\s*#{2,6}\s+(.+?)\s*$/)
    const direct = line.match(/^\s*(Meta Title|Meta Description|URL Slug|Primary Keyword|Secondary Keywords|Last Updated)\s*:\s*(.*)$/i)
    const label = (heading?.[1] || direct?.[1] || '').trim().toLowerCase()
    const key = known.find((item) => label === item || label.startsWith(`${item}:`))
    if (!key) {
      output.push(line)
      index += 1
      continue
    }
    foundMetadata = true
    const inline = heading?.[1]?.includes(':') ? heading[1].split(':').slice(1).join(':').trim() : direct?.[2]?.trim() || ''
    const values = inline ? [inline] : []
    index += 1
    while (index < lines.length && !/^\s*#{1,6}\s+/.test(lines[index])) {
      if (lines[index].trim()) values.push(lines[index].trim())
      index += 1
    }
    const metadataKey = key.replace(/ /g, '_')
    if (values.length && !meta[metadataKey]) meta[metadataKey] = values.join(' ').replace(/^['"]|['"]$/g, '')
  }
  const firstH1 = output.findIndex((line) => /^\s*#\s+/.test(line))
  const body = foundMetadata && firstH1 >= 0 ? output.slice(firstH1) : output
  return { body: body.join('\n').replace(/^\n+/, '').trim(), meta }
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
  onSyncSeoMetadata,
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
  onSyncSeoMetadata?: () => Promise<void>
}) {
  const [localTab, setLocalTab] = useState<ResultTab>('article')
  const activeTab = tab || localTab
  const selectTab = onTabChange || setLocalTab
  const parsedContent = splitFrontmatter(result.content || result.contentPreview || '')
  const content = parsedContent.body || '暂无文章正文'
  const metaTitle = result.article?.meta_title || parsedContent.meta.title || result.article?.title || ''
  const metaDescription = result.article?.meta_description || parsedContent.meta.meta_description || ''
  const passedQa = (result.qa || []).filter((item) => item.ok).length
  const tabLabels: Array<[ResultTab, string]> = [
    ['article', '文章正文'],
    ['brief', '增强 Brief'],
    ['outline', '文章大纲'],
    ['pipeline', '执行过程'],
    ['meta', 'SEO 元数据'],
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
          {activeTab === 'article' && <MarkdownContent source={content} />}
          {activeTab === 'brief' && <MarkdownContent source={result.brief?.text || '暂无 Brief 内容'} />}
          {activeTab === 'outline' && <MarkdownContent source={result.outline || '暂无文章大纲'} />}
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
          {activeTab === 'meta' && (
            <div className="article-dialog__info-grid">
              <InfoItem label="Meta Title" value={metaTitle || '未生成'} />
              <InfoItem label="Meta Description" value={metaDescription || '未生成'} />
              <InfoItem label="Focus Keyword" value={result.article?.primary_keyword || '未设置'} />
              <InfoItem label="Slug" value={result.article?.slug || '未生成'} />
              <InfoItem label="语种" value={`${result.article?.market || '未知'} / ${result.article?.language_code || '未知'}`} />
            </div>
          )}
          {activeTab === 'seo' && (
            <div className="article-dialog__info-grid">
              <InfoItem label="保存位置" value={`${result.savedTo?.table || 'seo_agent.articles'} / ${result.savedTo?.articleId || result.article?.id || '未知'}`} />
              <InfoItem label="Brief 来源" value={result.brief?.aiEnhanced ? 'AI 增强' : '已保存 Brief'} />
              <InfoItem label="模型厂商 / 模型" value={`${result.provider || 'unknown'} / ${result.model || 'unknown'}`} />
              <InfoItem label="SERP 状态" value={`${result.serp?.source || '未使用'} · ${result.serp?.status || '未知'}`} />
              <InfoItem label="QA 结果" value={`${passedQa}/${result.qa?.length || 0} 项通过`} />
              <InfoItem label="文章状态" value={result.article?.status || '未知'} />
              {(result.qa || []).map((item) => <InfoItem key={item.key} label={`QA · ${item.key}`} value={item.ok ? '通过' : '未通过'} />)}
            </div>
          )}
        </div>

        <div className="article-dialog__foot">
          {onSyncSeoMetadata && (
            <button className="btn btn--primary btn--seo-metadata-sync" type="button" disabled={publishing} title="只同步页面标题与元描述，不会改动正文或调用 AI" onClick={() => void onSyncSeoMetadata()}>
              {publishing ? '同步中…' : '同步 SEO 元数据'}
            </button>
          )}
          {onPublish && (
            <>
              <span className="article-dialog__target-site">目标站点：{publishSites.find((site) => site.id === targetSiteId)?.name || '未分配'}</span>
              <select className="input" value={targetSiteId} onChange={(event) => onSiteChange?.(event.target.value)} aria-label="选择发布站点">
                <option value="">选择发布站点</option>
                {publishSites.map((site) => (
                  <option key={site.id} value={site.id}>
                    {site.name} · {site.publish_adapter || site.site_type}{site.publish_ready ? '' : ' · 未就绪'}
                  </option>
                ))}
              </select>
              {!publishSites.length && <div className="article-dialog__publish-message">没有与文章语种匹配的可用站点</div>}
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

type MarkdownBlock =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'quote'; text: string }
  | { type: 'code'; language: string; text: string }
  | { type: 'table'; headers: string[]; rows: string[][] }

function MarkdownContent({ source }: { source: string }) {
  return (
    <div className="article-dialog__markdown">
      {parseMarkdown(source).map((block, index) => {
        if (block.type === 'heading') {
          const content = <InlineMarkdown text={block.text} />
          if (block.level === 1) return <h1 key={index}>{content}</h1>
          if (block.level === 2) return <h2 key={index}>{content}</h2>
          if (block.level === 3) return <h3 key={index}>{content}</h3>
          return <h4 key={index}>{content}</h4>
        }
        if (block.type === 'paragraph') {
          return <p key={index}><InlineMarkdown text={block.text} /></p>
        }
        if (block.type === 'list') {
          const List = block.ordered ? 'ol' : 'ul'
          return <List key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}><InlineMarkdown text={item} /></li>)}</List>
        }
        if (block.type === 'quote') {
          return <blockquote key={index}><InlineMarkdown text={block.text} /></blockquote>
        }
        if (block.type === 'code') {
          return <pre className="article-dialog__markdown-code" key={index}><code>{block.text}</code></pre>
        }
        if (block.type === 'table') {
          return (
            <div className="article-dialog__markdown-table" key={index}>
              <table>
                <thead><tr>{block.headers.map((cell, cellIndex) => <th key={cellIndex}><InlineMarkdown text={cell} /></th>)}</tr></thead>
                <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}><InlineMarkdown text={cell} /></td>)}</tr>)}</tbody>
              </table>
            </div>
          )
        }
        return null
      })}
    </div>
  )
}

function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const blocks: MarkdownBlock[] = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) {
      index += 1
      continue
    }
    const fence = line.match(/^\s*```(.*)$/)
    if (fence) {
      const code: string[] = []
      index += 1
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) code.push(lines[index++])
      if (index < lines.length) index += 1
      blocks.push({ type: 'code', language: fence[1].trim(), text: code.join('\n') })
      continue
    }
    const heading = line.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/)
    if (heading) {
      blocks.push({ type: 'heading', level: Math.min(heading[1].length, 4), text: heading[2] })
      index += 1
      continue
    }
    if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) {
      index += 1
      continue
    }
    if (index + 1 < lines.length && line.includes('|') && isTableDivider(lines[index + 1])) {
      const headers = splitTableRow(line)
      const rows: string[][] = []
      index += 2
      while (index < lines.length && lines[index].trim() && lines[index].includes('|')) {
        rows.push(splitTableRow(lines[index++]))
      }
      blocks.push({ type: 'table', headers, rows })
      continue
    }
    const listItem = line.match(/^\s*([-+*]|\d+[.)])\s+(.+)$/)
    if (listItem) {
      const ordered = /^\d/.test(listItem[1])
      const items = [listItem[2]]
      index += 1
      while (index < lines.length) {
        const next = lines[index].match(/^\s*([-+*]|\d+[.)])\s+(.+)$/)
        if (!next || /^\d/.test(next[1]) !== ordered) break
        items.push(next[2])
        index += 1
      }
      blocks.push({ type: 'list', ordered, items })
      continue
    }
    if (/^\s*>/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ''))
      blocks.push({ type: 'quote', text: quote.join('\n') })
      continue
    }
    const paragraph = [line.trim()]
    index += 1
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) paragraph.push(lines[index++].trim())
    blocks.push({ type: 'paragraph', text: paragraph.join('\n') })
  }
  return blocks
}

function isBlockStart(lines: string[], index: number) {
  const line = lines[index]
  return /^\s*(?:#{1,6}\s|```|>|(?:---+|\*\*\*+|___+)\s*$)/.test(line) || /^\s*([-+*]|\d+[.)])\s+/.test(line) || (line.includes('|') && index + 1 < lines.length && isTableDivider(lines[index + 1]))
}

function isTableDivider(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line)
}

function splitTableRow(line: string) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

function InlineMarkdown({ text }: { text: string }) {
  const nodes: ReactNode[] = []
  const pattern = /(!?\[[^\]]+\]\([^\s)]+\)|`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g
  let cursor = 0
  let match: RegExpExecArray | null
  let key = 0
  while ((match = pattern.exec(text))) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index))
    const token = match[0]
    const image = token.match(/^!\[([^\]]*)\]\(([^\s)]+)\)$/)
    const link = token.match(/^\[([^\]]+)\]\(([^\s)]+)\)$/)
    if (image && isSafeUrl(image[2])) nodes.push(<img key={key++} src={image[2]} alt={image[1]} loading="lazy" />)
    else if (link && isSafeUrl(link[2])) nodes.push(<a key={key++} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>)
    else if (token.startsWith('`')) nodes.push(<code key={key++}>{token.slice(1, -1)}</code>)
    else if (token.startsWith('**') || token.startsWith('__')) nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>)
    else nodes.push(<em key={key++}>{token.slice(1, -1)}</em>)
    cursor = match.index + token.length
  }
  if (cursor < text.length) nodes.push(text.slice(cursor))
  return nodes
}

function isSafeUrl(url: string) {
  return /^(?:https?:|mailto:|\/|#)/i.test(url)
}
