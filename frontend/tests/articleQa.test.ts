import assert from 'node:assert/strict'
import test from 'node:test'

import { decodeArticleQa } from '../src/data/articleQa.ts'

test('decodes a canonical QA checklist', () => {
  const assessment = decodeArticleQa([
    { key: 'title_length', ok: true, value: 58 },
    { key: 'sources_present', ok: false },
  ])

  assert.equal(assessment.state, 'valid')
  assert.equal(assessment.passed, false)
  assert.deepEqual(assessment.checks, [
    { key: 'title_length', ok: true, value: 58 },
    { key: 'sources_present', ok: false },
  ])
})

test('converts a legacy QA envelope without losing summary data', () => {
  const assessment = decodeArticleQa({
    ok: true,
    checks: {
      word_count: true,
      images_present: true,
    },
    word_count: 1778,
    image_count: 2,
    content_sha256: 'abc123',
  })

  assert.equal(assessment.state, 'legacy')
  assert.equal(assessment.passed, true)
  assert.deepEqual(assessment.checks, [
    { key: 'images_present', ok: true },
    { key: 'word_count', ok: true, value: 1778 },
  ])
  assert.equal(assessment.summary.content_sha256, 'abc123')
})

test('marks unknown QA shapes invalid instead of treating them as empty', () => {
  const assessment = decodeArticleQa({ unexpected: 'shape' })

  assert.equal(assessment.state, 'invalid')
  assert.equal(assessment.passed, false)
  assert.deepEqual(assessment.checks, [])
  assert.match(assessment.message || '', /格式/)
})

test('marks a conflicting legacy summary invalid', () => {
  const assessment = decodeArticleQa({
    ok: true,
    checks: {
      title_length: true,
      sources_present: false,
    },
  })

  assert.equal(assessment.state, 'invalid')
  assert.equal(assessment.passed, false)
  assert.match(assessment.message || '', /冲突/)
})

test('marks a non-boolean canonical summary result invalid', () => {
  const assessment = decodeArticleQa(
    [{ key: 'title_length', ok: true }],
    { summary: { ok: 'yes' } },
  )

  assert.equal(assessment.state, 'invalid')
  assert.equal(assessment.passed, false)
  assert.match(assessment.message || '', /布尔/)
})
