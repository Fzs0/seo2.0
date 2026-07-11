import { useState } from 'react'
import { DataGuard } from '@/components/StateBlock'
import { analyzeKeywordStrategy, importSemrushFile, useKeywords } from '@/hooks/useData'
import type { Keyword, Priority } from '@/types/domain'

const INTENT_TAG: Record<string, { label: string; tone: 'pink' | 'gold' | 'blue' | 'green' }> = {
  commercial: { label: '商业', tone: 'pink' },
  transactional: { label: '交易', tone: 'gold' },
  informational: { label: '信息', tone: 'blue' },
  navigational: { label: '导航', tone: 'green' },
  unknown: { label: '未知', tone: 'green' },
}

const PRIORITY_TAG: Record<Priority, { label: string; tone: 'pink' | 'gold' | 'blue' | 'green' }> = {
  high: { label: '高', tone: 'pink' },
  'medium-high': { label: '中高', tone: 'gold' },
  medium: { label: '中', tone: 'blue' },
  'medium-low': { label: '中低', tone: 'green' },
  low: { label: '低', tone: 'green' },
  P0: { label: 'P0', tone: 'pink' },
  P1: { label: 'P1', tone: 'gold' },
  P2: { label: 'P2', tone: 'blue' },
  P3: { label: 'P3', tone: 'green' },
  Hold: { label: 'Hold', tone: 'green' },
}

const STATUS_LABEL: Record<string, string> = {
  planned: '待启动',
  analyzing: '分析中',
  ready: '可生成 Brief',
  brief_ready: 'Brief 已就绪',
  article_drafted: '草稿已生成',
  published: '已发布',
  blocked: '阻塞',
  imported: '已导入',
  analyzed: '已分析',
  queued: '排队中',
  written: '已写稿',
  reviewed: '已复核',
  hold: '暂停',
  dropped: '放弃',
}

function formatNumber(n: number | string | null | undefined) {
  return new Intl.NumberFormat('en-US').format(Number(n ?? 0))
}

function formatConfidence(value: number | string | null | undefined) {
  if (value == null || value === '') return '未给出'
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  return n <= 1 ? `${Math.round(n * 100)}%` : `${Math.round(n)}%`
}

export function KeywordsPage({ onNotify, onOpenContent }: { onNotify?: (title: string, detail?: string) => void; onOpenContent?: () => void }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [importing, setImporting] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeLimit, setAnalyzeLimit] = useState(10)
  const [opportunityType, setOpportunityType] = useState('long_tail')
  const [analysisComplete, setAnalysisComplete] = useState(false)
  const [message, setMessage] = useState<string>()
  const kw = useKeywords(refreshKey, 50)

  async function handleFile(file?: File) {
    if (!file || importing) return
    setImporting(true)
    setMessage(`正在导入并分析 ${file.name}…`)
    try {
      const result = await importSemrushFile(file)
      setMessage(`导入完成：分析 ${formatNumber(result.keywords.length)} 条，写入 ${formatNumber(result.saved)} 条。`)
      setRefreshKey((key) => key + 1)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '导入失败')
    } finally {
      setImporting(false)
    }
  }

  async function handleAiAnalyze() {
    if (analyzing) return
    setAnalyzing(true)
    setAnalysisComplete(false)
    setMessage('正在用 AI 分析前 10 个关键词策略…')
    try {
      const result = await analyzeKeywordStrategy([], analyzeLimit, opportunityType)
      setAnalysisComplete(result.updated > 0)
      setMessage(result.error ? `AI 分析未完成：${result.error}` : `AI 分析完成：更新 ${formatNumber(result.updated)} 条策略。`)
      onNotify?.('AI 关键词分析完成', result.error ? result.error : `更新 ${formatNumber(result.updated)} 条策略`)
      setRefreshKey((key) => key + 1)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'AI 分析失败')
    } finally {
      setAnalyzing(false)
    }
  }

  return (
    <section className="page" data-screen-label="关键词库">
      <div>
        <h1>关键词库</h1>
        <p>
          关键词来源：Semrush / 手工 / GSC。导入 Semrush 文件后会自动分析并写入关键词库。
        </p>
        {message && <p>{message}</p>}
        {analysisComplete && onOpenContent && (
          <button className="btn btn--primary" type="button" onClick={onOpenContent}>
            <span className="msr">arrow_forward</span>
            进入内容策略，生成 Brief
          </button>
        )}
      </div>

      <div className="filter-row">
        <label className="btn btn--primary">
          <span className="msr">{importing ? 'progress_activity' : 'upload_file'}</span>
          {importing ? '导入中' : '导入 Semrush 文件'}
          <input
            type="file"
            accept=".csv,.tsv,.xlsx"
            style={{ display: 'none' }}
            disabled={importing}
            onChange={(event) => {
              void handleFile(event.target.files?.[0])
              event.currentTarget.value = ''
            }}
          />
        </label>
        <button className="btn btn--ghost" type="button" onClick={handleAiAnalyze} disabled={analyzing}>
          <span className="msr">{analyzing ? 'progress_activity' : 'auto_awesome'}</span>
          {analyzing ? 'AI 分析中' : 'AI 分析策略'}
        </button>
        <label className="chip">
          分析数量
          <input
            type="number"
            min={1}
            max={50}
            value={analyzeLimit}
            onChange={(event) => setAnalyzeLimit(Math.max(1, Math.min(50, Number(event.target.value) || 1)))}
            style={{ width: 64, marginLeft: 8, border: 0, background: 'transparent' }}
          />
        </label>
        <select className="chip" value={opportunityType} onChange={(event) => setOpportunityType(event.target.value)}>
          <option value="long_tail">长尾词机会</option>
          <option value="qa">问答型机会</option>
          <option value="low_kd">低 KD 机会</option>
          <option value="">综合机会</option>
        </select>
      </div>

      <div className="filter-row">
        <span className="chip chip--active">
          <span className="msr">all_inclusive</span>
          全部 {kw.data ? formatNumber(kw.data.length) : '—'}
        </span>
        <span className="chip">
          <span className="msr">database</span>
          当前仅加载前 50 条
        </span>
        <span className="chip">
          <span className="msr">flag</span>
          高优先级
        </span>
        <span className="chip">
          <span className="msr">business</span>
          商业意图
        </span>
        <span className="chip">
          <span className="msr">school</span>
          信息意图
        </span>
        <span className="chip">
          <span className="msr">apartment</span>
          主站
        </span>
        <span className="chip">
          <span className="msr">menu_book</span>
          博客 A
        </span>
        <span className="chip">
          <span className="msr">bookmark</span>
          博客 B
        </span>
        <span className="chip">
          <span className="msr">balance</span>
          博客 C
        </span>
        <span className="chip">
          <span className="msr">search</span>
          搜索关键词
        </span>
      </div>

      <div className="tbl-wrap">
        <DataGuard
          loading={kw.loading}
          error={kw.error}
          empty={!kw.data || kw.data.length === 0}
          emptyTitle="暂无关键词"
          emptyHint="请先通过 /api/v1/workflow/import-csv 或 Semrush 导入"
        >
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 36 }}>#</th>
                <th>关键词</th>
                <th>意图</th>
                <th>主题</th>
                <th>站点</th>
                <th className="tbl-num">月搜索量</th>
                <th className="tbl-num">KD</th>
                <th className="tbl-num">CPC</th>
                <th className="tbl-num">Score</th>
                <th>优先级</th>
                <th>状态</th>
                <th>AI 置信度</th>
                <th>AI 策略</th>
              </tr>
            </thead>
            <tbody>
              {(kw.data ?? []).map((k, i) => {
                const intent = INTENT_TAG[k.intent] ?? INTENT_TAG.unknown
                const prio = PRIORITY_TAG[k.priority] ?? PRIORITY_TAG.P3
                const strategy = k.ai_review?.strategy
                return (
                  <tr key={k.id}>
                    <td style={{ color: 'var(--ink-400)' }}>{i + 1}</td>
                    <td className="tbl-strong">{k.keyword}</td>
                    <td>
                      <span className={`tag tag--${intent.tone}`}>{intent.label}</span>
                    </td>
                    <td style={{ color: 'var(--ink-500)' }}>{k.topic_cluster}</td>
                    <td style={{ color: 'var(--ink-500)' }}>{k.assigned_site_label}</td>
                    <td className="tbl-num">{formatNumber(k.volume)}</td>
                    <td className="tbl-num">{k.kd}</td>
                    <td className="tbl-num">${Number(k.cpc ?? 0).toFixed(2)}</td>
                    <td className="tbl-num tbl-strong">{Number(k.score ?? 0).toFixed(1)}</td>
                    <td>
                      <span className={`tag tag--${prio.tone}`}>{prio.label}</span>
                    </td>
                    <td>
                      <span className="tag tag--gray">{STATUS_LABEL[k.status] ?? k.status}</span>
                    </td>
                    <td>
                      <span className={`tag tag--${strategy?.confidence != null && strategy.confidence !== '' ? 'green' : 'gray'}`}>
                        {formatConfidence(strategy?.confidence)}
                      </span>
                    </td>
                    <td style={{ color: 'var(--ink-500)', fontSize: 12, maxWidth: 320 }}>
                      {strategy
                        ? strategy.briefDirection || strategy.strategyReason || strategy.contentAction || '已保存 AI 策略'
                        : '未分析'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </DataGuard>
      </div>
    </section>
  )
}
