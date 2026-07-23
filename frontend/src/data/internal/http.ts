export const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
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
      return response.json() as Promise<T>
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
