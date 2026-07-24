export type ArticleQaState = 'valid' | 'legacy' | 'missing' | 'invalid'

export interface ArticleQaCheck {
  key: string
  ok: boolean
  value?: unknown
  [key: string]: unknown
}

export interface ArticleQaAssessment {
  state: ArticleQaState
  checks: ArticleQaCheck[]
  summary: Record<string, unknown>
  passed: boolean
  message?: string
}

interface ArticleQaServerMeta {
  state?: unknown
  summary?: unknown
  message?: unknown
}

export function decodeArticleQa(
  raw: unknown,
  meta: ArticleQaServerMeta = {},
): ArticleQaAssessment {
  if (meta.summary !== undefined && !isRecord(meta.summary)) {
    return invalidAssessment('QA summary 格式异常')
  }
  const summary = isRecord(meta.summary) ? structuredClone(meta.summary) : {}

  if (raw == null || (Array.isArray(raw) && raw.length === 0)) {
    if (meta.state === 'invalid') {
      return invalidAssessment(
        typeof meta.message === 'string' ? meta.message : 'QA 数据格式异常',
        [],
        summary,
      )
    }
    return {
      state: 'missing',
      checks: [],
      summary,
      passed: false,
      message: '未保存 QA 检查结果',
    }
  }

  if (Array.isArray(raw)) {
    const parsed = canonicalChecks(raw)
    if ('error' in parsed) return invalidAssessment(parsed.error, [], summary)
    const state: ArticleQaState = meta.state === 'legacy' || summary.source_shape === 'legacy_envelope'
      ? 'legacy'
      : 'valid'
    return assessmentFromChecks(parsed.checks, summary, state, meta.message)
  }

  if (isRecord(raw)) {
    if (!isRecord(raw.checks) || Object.keys(raw.checks).length === 0) {
      return invalidAssessment('QA 数据格式异常：对象缺少 checks 布尔映射')
    }
    const entries = Object.entries(raw.checks).sort(([left], [right]) => left.localeCompare(right))
    if (entries.some(([key]) => !key.trim())) {
      return invalidAssessment('QA 数据格式异常：检查项 key 必须是非空字符串')
    }
    if (entries.some(([, value]) => typeof value !== 'boolean')) {
      return invalidAssessment('QA 数据格式异常：legacy checks 的值必须是布尔值')
    }
    const legacySummary: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(raw)) {
      if (key !== 'checks') legacySummary[key] = structuredClone(value)
    }
    Object.assign(legacySummary, summary, { source_shape: 'legacy_envelope' })
    const checks = entries.map(([key, ok]) => {
      const check: ArticleQaCheck = { key: key.trim(), ok: ok as boolean }
      const value = legacySummary[key]
      if (value !== undefined && typeof value !== 'boolean') check.value = structuredClone(value)
      return check
    })
    const passed = checks.length > 0 && checks.every((check) => check.ok)
    if (raw.ok !== undefined && typeof raw.ok !== 'boolean') {
      return invalidAssessment('QA 数据格式异常：legacy ok 必须是布尔值', checks, legacySummary)
    }
    if (typeof raw.ok === 'boolean' && raw.ok !== passed) {
      return invalidAssessment('QA 汇总结果与检查项冲突，已禁止发布', checks, legacySummary)
    }
    return {
      state: 'legacy',
      checks,
      summary: legacySummary,
      passed,
      message: '历史 QA 已转换为规范检查项',
    }
  }

  return invalidAssessment(`QA 数据格式异常：不支持 ${typeof raw}`)
}

function canonicalChecks(raw: unknown[]): { checks: ArticleQaCheck[] } | { error: string } {
  const checks: ArticleQaCheck[] = []
  const keys = new Set<string>()
  for (let index = 0; index < raw.length; index += 1) {
    const item = raw[index]
    if (!isRecord(item)) return { error: `QA 数据格式异常：第 ${index + 1} 项不是对象` }
    if (typeof item.key !== 'string' || !item.key.trim()) {
      return { error: `QA 数据格式异常：第 ${index + 1} 项缺少非空 key` }
    }
    const key = item.key.trim()
    if (keys.has(key)) return { error: `QA 数据格式异常：检查项 ${key} 重复` }
    if (typeof item.ok !== 'boolean') {
      return { error: `QA 数据格式异常：检查项 ${key} 的 ok 必须是布尔值` }
    }
    keys.add(key)
    checks.push({ ...structuredClone(item), key, ok: item.ok })
  }
  return { checks }
}

function assessmentFromChecks(
  checks: ArticleQaCheck[],
  summary: Record<string, unknown>,
  state: ArticleQaState,
  message: unknown,
): ArticleQaAssessment {
  if (!checks.length) {
    return { state: 'missing', checks: [], summary, passed: false, message: '未保存 QA 检查结果' }
  }
  const passed = checks.every((check) => check.ok)
  if ('ok' in summary && typeof summary.ok !== 'boolean') {
    return invalidAssessment('QA summary 的 ok 必须是布尔值', checks, summary)
  }
  if (typeof summary.ok === 'boolean' && summary.ok !== passed) {
    return invalidAssessment('QA 汇总结果与检查项冲突，已禁止发布', checks, summary)
  }
  return {
    state,
    checks,
    summary,
    passed,
    message: typeof message === 'string'
      ? message
      : state === 'legacy' ? '历史 QA 已转换为规范检查项' : undefined,
  }
}

function invalidAssessment(
  message: string,
  checks: ArticleQaCheck[] = [],
  summary: Record<string, unknown> = {},
): ArticleQaAssessment {
  return { state: 'invalid', checks, summary, passed: false, message }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
