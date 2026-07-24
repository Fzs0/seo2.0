import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  blankBusinessOnboarding,
  SITE_TYPE_OPTIONS,
} from '../src/pages/sites/sitePageModel.ts'

test('business onboarding and site editor share one commercial-main option vocabulary', () => {
  const values = SITE_TYPE_OPTIONS.map((option) => option.value)
  assert.deepEqual(values, ['main', 'shopify', 'wp', 'blog', 'other'])
  assert.equal(
    SITE_TYPE_OPTIONS.find((option) => option.value === 'main')?.label,
    '商业主站（OEMApps / 自建站）',
  )
  assert.equal(blankBusinessOnboarding().site_type, 'main')
})

test('commercial-main configuration describes the shared site API instead of an article-only API', () => {
  const source = readFileSync(new URL('../src/pages/sites/SiteDialogs.tsx', import.meta.url), 'utf8')
  assert.match(source, /站点 API 连接/)
  assert.match(source, /API 基础地址/)
  assert.doesNotMatch(source, />文章连接</)
  assert.doesNotMatch(source, /'文章接口地址'/)
})
