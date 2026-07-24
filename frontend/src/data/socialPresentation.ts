export type SocialJobTone = 'success' | 'warning' | 'info' | 'danger'

export type SocialJobPresentation = {
  label: string
  title: string
  guidance: string
  tone: SocialJobTone
}

const contentTypeLabels: Record<string, string> = {
  short_post: '短帖',
  discussion_post: '社区帖子',
  answer_post: '问答内容',
  video: '视频',
  short_video: '短视频',
  reel: 'Reel',
}

export function socialContentTypeLabel(contentType: string) {
  return contentTypeLabels[contentType] || contentType
}

export function socialEnvironmentLabel(displayName: string, containerCode: string) {
  const serial = displayName.match(/(\d+)\s*$/)?.[1]
  return `Hubstudio ${serial || containerCode}`
}

export function describeSocialJob(
  status: string,
  errorMessage?: string | null,
): SocialJobPresentation {
  if (status === 'awaiting_review') {
    return {
      label: '可发布',
      title: '内容已回填并完成检查',
      guidance: '将包含在上方的“全部发布”操作中。',
      tone: 'success',
    }
  }
  if (status === 'pending') {
    return {
      label: '准备中',
      title: '正在打开页面并填写内容',
      guidance: '页面每 5 秒自动刷新，无需重复操作。',
      tone: 'info',
    }
  }

  const error = (errorMessage || '').toLowerCase()
  if (
    error.includes('account is suspended')
    || error.includes('account suspended')
    || error.includes('account is disabled')
  ) {
    return {
      label: '需要处理',
      title: '账号已被平台停用',
      guidance: '请先在平台完成申诉或账号恢复；恢复前无法自动回填和发布。',
      tone: 'danger',
    }
  }
  if (
    error.includes('login is required')
    || error.includes('sign in')
    || error.includes('log in')
  ) {
    return {
      label: '需要处理',
      title: '账号需要登录',
      guidance: '请在对应 Hubstudio 环境完成登录后重新回填。',
      tone: 'warning',
    }
  }
  if (error.includes('captcha') || error.includes('verification')) {
    return {
      label: '需要处理',
      title: '平台要求人工验证',
      guidance: '请在对应页面完成验证码或安全验证后重新回填。',
      tone: 'warning',
    }
  }
  if (error.includes('readtimeout') || error.includes('timeout')) {
    return {
      label: '需要处理',
      title: '浏览器响应超时',
      guidance: '确认环境仍在线且页面没有卡住，然后重新回填。',
      tone: 'danger',
    }
  }
  if (error.includes('returned http 500')) {
    return {
      label: '需要处理',
      title: '自动化服务执行异常',
      guidance: '本次回填没有完成；服务恢复后重新回填即可。',
      tone: 'danger',
    }
  }
  if (error.includes('publish button was not found')) {
    return {
      label: '需要处理',
      title: '未找到发布按钮',
      guidance: '平台页面结构可能发生变化，需要重新打开页面后再回填。',
      tone: 'warning',
    }
  }
  if (
    error.includes('could not be verified')
    || error.includes('did not verify')
  ) {
    return {
      label: '需要处理',
      title: '发布结果未确认',
      guidance: '请先检查账号内是否已经发布，避免重复提交；确认后再重新回填。',
      tone: 'warning',
    }
  }
  if (
    error.includes('rejected the publish action')
    || error.includes('something went wrong')
    || error.includes('try again later')
  ) {
    return {
      label: '需要处理',
      title: '平台暂时拒绝操作',
      guidance: '通常是平台限流或临时错误，请稍后重新回填。',
      tone: 'warning',
    }
  }
  return {
    label: '需要处理',
    title: '自动回填未完成',
    guidance: '展开技术详情查看原因，处理后再重新回填。',
    tone: 'danger',
  }
}
