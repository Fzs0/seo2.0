import assert from 'node:assert/strict'
import test from 'node:test'

import { postJson } from '../src/data/internal/http.ts'

test('reports an HTML API response without exposing a JSON syntax error', async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })
  globalThis.fetch = async () => new Response(
    '<!DOCTYPE html><html><body>frontend fallback</body></html>',
    {
      status: 200,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    },
  )

  await assert.rejects(
    postJson('/api/v1/social/publish-jobs/confirm-batch', {
      business_id: 'exdivo',
      job_ids: ['job-1'],
    }),
    /后端返回了网页而不是 JSON/,
  )
})
