const viteEnv = (import.meta as ImportMeta & {
  env?: { VITE_API_BASE_URL?: string }
}).env

export const API_BASE = (viteEnv?.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

async function readJsonResponse<T>(response: Response, path: string): Promise<T> {
  const contentType = response.headers.get('content-type')?.toLowerCase() || ''
  if (!contentType.includes('json')) {
    const body = await response.text()
    const isHtml = contentType.includes('text/html')
      || /^\s*(?:<!doctype\s+html|<html\b)/i.test(body)
    if (isHtml) {
      throw new Error(
        `后端返回了网页而不是 JSON：${API_BASE}${path}。请检查后端地址或前端 /api 代理配置。`,
      )
    }
    throw new Error(
      `后端返回了无法识别的响应（${contentType || '未提供 Content-Type'}）：${API_BASE}${path}`,
    )
  }
  try {
    return await response.json() as T
  } catch {
    throw new Error(`后端返回的 JSON 格式无效：${API_BASE}${path}`)
  }
}

export async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return readJsonResponse<T>(response, path)
}

export async function postJson<T>(path: string, body: unknown, method: 'POST' | 'PUT' = 'POST'): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!response.ok) {
        const detail = await response.text()
        throw new Error(detail || `${response.status} ${response.statusText}`)
      }
      return readJsonResponse<T>(response, path)
    } catch (error) {
      if (attempt === 0 && error instanceof TypeError) {
        await new Promise((resolve) => window.setTimeout(resolve, 300))
        continue
      }
      if (error instanceof TypeError) {
        throw new Error(`后端连接中断：${API_BASE}${path}。请确认后端服务正在运行。`)
      }
      throw error
    }
  }
  throw new Error('请求后端失败')
}
