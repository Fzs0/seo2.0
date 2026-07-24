import { useEffect, useMemo, useState } from 'react'
import { useBusinessScope } from '@/businessScope'
import {
  confirmPreparedSocialJobs,
  createSocialExtensionPairing,
  getSocialExtensionDevices,
  getSocialPublishJobs,
  type SocialBatchResult,
  type SocialExtensionDevice,
  type SocialExtensionPairing,
  type SocialPublishJob,
} from '@/data/social'
import {
  describeSocialJob,
  socialContentTypeLabel,
  socialEnvironmentLabel,
  type SocialJobTone,
} from '@/data/socialPresentation'

const platformLabels: Record<string, string> = {
  x: 'X',
  reddit: 'Reddit',
  quora: 'Quora',
  youtube: 'YouTube',
  tiktok: 'TikTok',
  instagram: 'Instagram',
  facebook: 'Facebook',
}

const EXTENSION_ONLINE_WINDOW_MS = 45_000
const toneClasses: Record<SocialJobTone, string> = {
  success: 'tag--green',
  warning: 'tag--gold',
  info: 'tag--blue',
  danger: 'tag--pink',
}

type PlatformJobGroup = {
  platform: string
  jobs: SocialPublishJob[]
}

type EnvironmentJobGroup = {
  containerCode: string
  label: string
  jobs: SocialPublishJob[]
  platforms: PlatformJobGroup[]
}

function extensionPresence(lastSeenAt?: string | null) {
  if (!lastSeenAt) return { online: false, detail: '尚未收到心跳' }
  const lastSeen = new Date(lastSeenAt)
  const elapsed = Date.now() - lastSeen.getTime()
  if (Number.isNaN(lastSeen.getTime())) return { online: false, detail: '心跳时间未知' }
  if (elapsed <= EXTENSION_ONLINE_WINDOW_MS) return { online: true, detail: '刚刚同步' }
  if (elapsed < 60 * 60 * 1000) {
    return { online: false, detail: `${Math.max(1, Math.floor(elapsed / 60_000))} 分钟前同步` }
  }
  return {
    online: false,
    detail: `${new Intl.DateTimeFormat('zh-CN', {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(lastSeen)} 同步`,
  }
}

function extensionEnvironmentLabel(device: SocialExtensionDevice) {
  const name = device.environment_name || device.display_name
  const serial = name.match(/(?:Hubstudio\s*)?(\d+)$/i)?.[1]
  return serial ? `环境 ${serial}` : name
}

function formatJobTime(value?: string) {
  if (!value) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function SocialJobStatus({ job }: { job: SocialPublishJob }) {
  const presentation = describeSocialJob(job.status, job.error_message)
  return (
    <div className="social-job-status">
      <div className="social-job-status__headline">
        <span className={`tag ${toneClasses[presentation.tone]}`}>{presentation.label}</span>
        <b>{presentation.title}</b>
      </div>
      <p>{presentation.guidance}</p>
      {job.error_message && (
        <details className="social-job-status__details">
          <summary>查看技术详情</summary>
          <code>{job.error_message}</code>
        </details>
      )}
    </div>
  )
}

function SocialPlatformStatus({ group }: { group: PlatformJobGroup }) {
  if (group.jobs.length === 1) return <SocialJobStatus job={group.jobs[0]} />
  const ready = group.jobs.filter((job) => job.status === 'awaiting_review').length
  const pending = group.jobs.filter((job) => job.status === 'pending').length
  const needsAttention = group.jobs.length - ready - pending
  return (
    <div className="social-job-status">
      <div className="social-job-status__counts">
        {ready > 0 && <span className="tag tag--green">{ready} 条可发布</span>}
        {pending > 0 && <span className="tag tag--blue">{pending} 条准备中</span>}
        {needsAttention > 0 && <span className="tag tag--pink">{needsAttention} 条需处理</span>}
      </div>
      <p>同一平台有多条内容，已合并展示；可发布内容仍会正常包含在“全部发布”中。</p>
      <details className="social-job-history">
        <summary>查看全部 {group.jobs.length} 条任务</summary>
        <div>
          {group.jobs.map((job) => {
            const presentation = describeSocialJob(job.status, job.error_message)
            return (
              <article key={job.id}>
                <div>
                  <b>{job.content_title || `${platformLabels[job.platform] || job.platform} 内容`}</b>
                  <small>{formatJobTime(job.created_at)}</small>
                </div>
                <span className={`tag ${toneClasses[presentation.tone]}`}>{presentation.label}</span>
                <p>{presentation.title}：{presentation.guidance}</p>
                {job.error_message && <code>{job.error_message}</code>}
              </article>
            )
          })}
        </div>
      </details>
    </div>
  )
}

export function SocialPublishingPage() {
  const { businessId } = useBusinessScope()
  const [jobs, setJobs] = useState<SocialPublishJob[]>([])
  const [loading, setLoading] = useState(true)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<SocialBatchResult | null>(null)
  const [containerCode, setContainerCode] = useState('')
  const [pairing, setPairing] = useState<SocialExtensionPairing | null>(null)
  const [devices, setDevices] = useState<SocialExtensionDevice[]>([])
  const [pairingBusy, setPairingBusy] = useState(false)
  const activeJobs = useMemo(
    () => jobs.filter((job) => ['awaiting_review', 'manual_required', 'pending'].includes(job.status)),
    [jobs],
  )
  const preparedJobs = useMemo(
    () => activeJobs.filter((job) => job.status === 'awaiting_review'),
    [activeJobs],
  )
  const attentionJobs = useMemo(
    () => activeJobs.filter((job) => job.status === 'manual_required'),
    [activeJobs],
  )
  const pendingJobs = useMemo(
    () => activeJobs.filter((job) => job.status === 'pending'),
    [activeJobs],
  )
  const environments = useMemo(
    () => new Set(activeJobs.map((job) => job.container_code)).size,
    [activeJobs],
  )
  const environmentGroups = useMemo<EnvironmentJobGroup[]>(() => {
    const groups = new Map<string, {
      containerCode: string
      label: string
      jobs: SocialPublishJob[]
      platforms: Map<string, SocialPublishJob[]>
    }>()
    for (const job of activeJobs) {
      let environment = groups.get(job.container_code)
      if (!environment) {
        environment = {
          containerCode: job.container_code,
          label: socialEnvironmentLabel(job.display_name, job.container_code),
          jobs: [],
          platforms: new Map(),
        }
        groups.set(job.container_code, environment)
      }
      environment.jobs.push(job)
      const platformJobs = environment.platforms.get(job.platform) || []
      platformJobs.push(job)
      environment.platforms.set(job.platform, platformJobs)
    }
    return Array.from(groups.values()).map((environment) => ({
      containerCode: environment.containerCode,
      label: environment.label,
      jobs: environment.jobs,
      platforms: Array.from(environment.platforms.entries()).map(([platform, platformJobs]) => ({
        platform,
        jobs: platformJobs,
      })),
    }))
  }, [activeJobs])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setResult(null)
    if (!businessId) {
      setJobs([])
      setLoading(false)
      return () => controller.abort()
    }
    const load = () => {
      getSocialPublishJobs(businessId, controller.signal)
        .then((payload) => setJobs(payload.items))
        .catch((reason: Error) => {
          if (reason.name !== 'AbortError') setError(reason.message)
        })
        .finally(() => setLoading(false))
      getSocialExtensionDevices(businessId, controller.signal)
        .then((payload) => setDevices(payload.items))
        .catch(() => undefined)
    }
    load()
    const timer = window.setInterval(load, 5000)
    return () => {
      window.clearInterval(timer)
      controller.abort()
    }
  }, [businessId])

  async function publishAll() {
    if (!businessId || !preparedJobs.length || publishing) return
    setPublishing(true)
    setError('')
    setResult(null)
    try {
      const payload = await confirmPreparedSocialJobs(
        businessId,
        preparedJobs.map((job) => job.id),
      )
      setResult(payload)
      const completed = new Set(payload.items.filter((item) => item.ok).map((item) => item.job_id))
      setJobs((items) => items.filter((item) => !completed.has(item.id)))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '批量发布失败')
    } finally {
      setPublishing(false)
    }
  }

  async function generatePairingCode() {
    if (!businessId || !containerCode.trim() || pairingBusy) return
    setPairingBusy(true)
    setError('')
    try {
      setPairing(await createSocialExtensionPairing(businessId, containerCode.trim()))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法生成扩展配对码')
    } finally {
      setPairingBusy(false)
    }
  }

  return (
    <section className="social-publishing">
      <header className="page-header">
        <div>
          <div className="eyebrow">SOCIAL DELIVERY</div>
          <h1>社媒发布</h1>
          <p>一次提交当前业务下所有已经检查完成的 Hubstudio 社媒草稿。</p>
        </div>
      </header>

      <div className="card social-publishing__summary">
        <div className="social-publishing__summary-metrics">
          <div className="is-ready">
            <b>{preparedJobs.length}</b>
            <span>已回填，可发布</span>
          </div>
          <div className={attentionJobs.length ? 'is-attention' : ''}>
            <b>{attentionJobs.length}</b>
            <span>需要处理</span>
          </div>
          <div>
            <b>{pendingJobs.length}</b>
            <span>正在准备</span>
          </div>
          <div>
            <b>{environments}</b>
            <span>Hubstudio 环境</span>
          </div>
        </div>
        <div className="social-publishing__summary-action">
          <button
            type="button"
            className="btn btn--primary social-publishing__publish"
            disabled={!preparedJobs.length || loading || publishing}
            onClick={() => void publishAll()}
          >
            {publishing
              ? '正在逐项发布…'
              : preparedJobs.length
                ? `全部发布（${preparedJobs.length}）`
                : '暂无可发布内容'}
          </button>
          <small>
            {preparedJobs.length
              ? `只会发布上方标记为“可发布”的 ${preparedJobs.length} 条内容。`
              : attentionJobs.length
                ? `还有 ${attentionJobs.length} 条内容尚未完成回填，不会触发误发布。`
                : '等待内容完成回填和检查后，发布按钮会自动启用。'}
          </small>
        </div>
      </div>

      <div className="card social-extension-pairing">
        <div>
          <div className="eyebrow">HUBSTUDIO EXTENSION</div>
          <h2>连接本地发布扩展</h2>
          <p>输入环境对应的 containerCode，生成十分钟有效的一次性配对码。</p>
        </div>
        <div className="social-extension-pairing__form">
          <input
            value={containerCode}
            onChange={(event) => setContainerCode(event.target.value)}
            placeholder="例如：1729002472"
            aria-label="Hubstudio containerCode"
          />
          <button
            type="button"
            className="btn btn--primary"
            disabled={!containerCode.trim() || pairingBusy}
            onClick={() => void generatePairingCode()}
          >
            {pairingBusy ? '生成中…' : '生成配对码'}
          </button>
        </div>
        {pairing && (
          <div className="social-extension-pairing__code">
            <b>{pairing.pairing_code}</b>
            <span>请在当前 Hubstudio 环境的扩展弹窗中输入；配对后立即失效。</span>
          </div>
        )}
        <div className="social-extension-pairing__devices">
          {devices.length
            ? devices.map((device) => {
                const presence = extensionPresence(device.last_seen_at)
                return (
                  <article className="social-extension-device" key={device.container_code}>
                    <div className="social-extension-device__header">
                      <span
                        className={`social-extension-device__dot ${presence.online ? 'is-online' : ''}`}
                        aria-hidden="true"
                      />
                      <b>{extensionEnvironmentLabel(device)}</b>
                      <span className={`tag ${presence.online ? 'tag--green' : 'tag--blue'}`}>
                        {presence.online ? '在线' : '离线'}
                      </span>
                    </div>
                    <small>containerCode：{device.container_code}</small>
                    <small>最近心跳：{presence.detail}</small>
                    {(device.paired_instances || 0) > 1 && (
                      <small className="social-extension-device__notice">
                        已合并 {device.paired_instances} 条重复连接记录
                      </small>
                    )}
                  </article>
                )
              })
            : <span className="tag tag--blue">暂无已配对扩展</span>}
        </div>
      </div>

      {error && <div className="article-chart__error">{error}</div>}
      {result && (
        <div className={`social-publishing__result ${result.failed ? 'has-error' : ''}`}>
          本次共 {result.total} 项：成功 {result.succeeded} 项，失败 {result.failed} 项。
          <div className="social-publishing__result-list">
            {result.items.map((item) => {
              const job = jobs.find((candidate) => candidate.id === item.job_id)
              return (
                <div className="social-publishing__result-row" key={item.job_id}>
                  <span className={`tag ${item.ok ? 'tag--green' : 'tag--pink'}`}>
                    {item.ok ? '成功' : '失败'}
                  </span>
                  <b>{job ? `${job.display_name} · ${platformLabels[job.platform] || job.platform}` : item.job_id}</b>
                  <small>{item.ok ? item.post_url || '已发布' : item.error || item.status}</small>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="card social-publishing__list">
        <header className="social-publishing__list-header">
          <div>
            <h2>发布任务</h2>
            <p>按 Hubstudio 环境和平台整理；技术错误已转换为可操作的说明。</p>
          </div>
          <span className="tag tag--blue">每 5 秒自动刷新</span>
        </header>
        {loading && <div className="agent-empty">正在读取待发布内容…</div>}
        {!loading && !activeJobs.length && <div className="agent-empty">当前没有需要发布或处理的社媒内容。</div>}
        {environmentGroups.map((environment) => {
          const ready = environment.jobs.filter((job) => job.status === 'awaiting_review').length
          const pending = environment.jobs.filter((job) => job.status === 'pending').length
          const needsAttention = environment.jobs.length - ready - pending
          return (
            <section className="social-publishing__environment" key={environment.containerCode}>
              <header>
                <div>
                  <span className="msr">desktop_windows</span>
                  <div>
                    <h3>{environment.label}</h3>
                    <small>containerCode：{environment.containerCode}</small>
                  </div>
                </div>
                <div className="social-publishing__environment-counts">
                  <span>{environment.jobs.length} 条任务</span>
                  {ready > 0 && <span className="tag tag--green">{ready} 可发布</span>}
                  {pending > 0 && <span className="tag tag--blue">{pending} 准备中</span>}
                  {needsAttention > 0 && <span className="tag tag--pink">{needsAttention} 需处理</span>}
                </div>
              </header>
              <div className="social-publishing__platforms">
                {environment.platforms.map((platformGroup) => {
                  const firstJob = platformGroup.jobs[0]
                  return (
                    <article className="social-publishing__platform" key={platformGroup.platform}>
                      <div className="social-publishing__platform-meta">
                        <span className="tag tag--blue">
                          {platformLabels[platformGroup.platform] || platformGroup.platform}
                        </span>
                        <div>
                          <b>
                            {platformGroup.jobs.length > 1
                              ? `${platformGroup.jobs.length} 条内容`
                              : firstJob.content_title
                                || `${platformLabels[firstJob.platform] || firstJob.platform} 内容`}
                          </b>
                          <small>
                            {socialContentTypeLabel(firstJob.content_type)}
                            {platformGroup.jobs.length === 1 ? ` · ${formatJobTime(firstJob.created_at)}` : ''}
                          </small>
                        </div>
                      </div>
                      <SocialPlatformStatus group={platformGroup} />
                    </article>
                  )
                })}
              </div>
            </section>
          )
        })}
      </div>
    </section>
  )
}
