import assert from 'node:assert/strict'
import test from 'node:test'

import {
  describeSocialJob,
  socialContentTypeLabel,
  socialEnvironmentLabel,
} from '../src/data/socialPresentation.ts'

test('turns executor failures into actionable user-facing guidance', () => {
  assert.deepEqual(
    describeSocialJob('manual_required', 'social executor returned HTTP 500'),
    {
      label: '需要处理',
      title: '自动化服务执行异常',
      guidance: '本次回填没有完成；服务恢复后重新回填即可。',
      tone: 'danger',
    },
  )
  assert.equal(
    describeSocialJob('manual_required', 'YouTube login is required').title,
    '账号需要登录',
  )
  assert.equal(
    describeSocialJob(
      'manual_required',
      'Published Quora post URL could not be verified',
    ).title,
    '发布结果未确认',
  )
  assert.equal(
    describeSocialJob(
      'manual_required',
      'Instagram account is suspended and requires account recovery',
    ).title,
    '账号已被平台停用',
  )
})

test('labels ready and pending jobs by the action the user can take', () => {
  assert.equal(describeSocialJob('awaiting_review', null).label, '可发布')
  assert.equal(describeSocialJob('pending', null).label, '准备中')
})

test('uses human-readable environment and content labels', () => {
  assert.equal(socialEnvironmentLabel('Instagram 2875', '1729002472'), 'Hubstudio 2875')
  assert.equal(socialEnvironmentLabel('Instagram', '1729002472'), 'Hubstudio 1729002472')
  assert.equal(socialContentTypeLabel('short_video'), '短视频')
  assert.equal(socialContentTypeLabel('discussion_post'), '社区帖子')
})
