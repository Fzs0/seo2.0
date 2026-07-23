import { useState } from 'react'
import type { Site, SiteKnowledgeProfile } from '@/types/domain'
import {
  articlePathsForForm,
  isBusinessMainForm,
  splitLines,
  type BusinessConfirmation,
  type BusinessDiscoveryResult,
  type BusinessOnboardingForm,
  type SiteForm,
} from './sitePageModel'

export function DiagnosticItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="site-diagnostics__item">
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  )
}

export function BusinessDiscoveryEditor({
  sites,
  selectedSiteId,
  result,
  scanning,
  confirmation,
  confirming,
  onDiscover,
  onConfirmationChange,
  onConfirm,
  onManual,
  onClose,
}: {
  sites: Site[]
  selectedSiteId: string
  result: BusinessDiscoveryResult | null
  scanning: boolean
  confirmation: BusinessConfirmation
  confirming: boolean
  onDiscover: (siteId: string) => void
  onConfirmationChange: (key: keyof BusinessConfirmation, value: string | boolean) => void
  onConfirm: () => void
  onManual: () => void
  onClose: () => void
}) {
  const profile = result?.knowledge_profile
  const policy = profile?.generation_policy
  const field = (key: keyof Pick<BusinessConfirmation, 'business_id' | 'market' | 'language_code'>, label: string, hint?: string) => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}>
      <span>{label}</span>
      <input className="input" value={String(confirmation[key] ?? '')} onChange={(event) => onConfirmationChange(key, event.target.value)} />
      {hint && <span style={{ color: 'var(--ink-400)', fontSize: 10.5 }}>{hint}</span>}
    </label>
  )
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 22, background: 'rgba(23,22,20,.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: 'min(760px, 100%)', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="card__title"><span>从已有站点新增业务</span><button className="icon-btn" type="button" onClick={onClose}>close</button></div>
        <p style={{ color: 'var(--ink-500)', fontSize: 12, lineHeight: 1.65, marginTop: 8 }}>系统会读取公开 sitemap、站内页面和已导入文章，再由 AI 生成一份待确认资料卡。预览不会写入业务资料、发布文章或自动启用策略。</p>
        <div style={{ display: 'grid', gap: 8, marginTop: 14 }}>
          {!sites.length && <div className="agent-empty">没有未归属业务的站点。已有站点可先解除原业务归属，或使用手动创建。</div>}
          {sites.map((site) => (
            <button key={site.id} type="button" className={`btn ${selectedSiteId === site.id ? 'btn--primary' : 'btn--ghost'}`} style={{ justifyContent: 'flex-start', textAlign: 'left' }} onClick={() => onDiscover(site.id)} disabled={scanning}>
              <span className="msr">{site.site_type === 'shopify' ? 'storefront' : 'language'}</span>
              {scanning && selectedSiteId === site.id ? '正在扫描并分析…' : `${site.name} · ${site.site_type} · ${site.base_url || site.domain}`}
            </button>
          ))}
        </div>
        {result && profile && (
          <>
            <div className="site-diagnostics site-diagnostics--ok" style={{ marginTop: 16 }}>
              <div className="site-diagnostics__head"><strong>扫描完成</strong><span className="tag tag--green">草案</span></div>
              <div className="site-diagnostics__grid">
                <DiagnosticItem label="发现 URL" value={`${result.scan.indexed_urls} 个；实际扫描 ${result.scan.scanned_urls} 个`} />
                <DiagnosticItem label="既有文章 / 产品记录" value={`${result.evidence.existing_posts} / ${result.evidence.product_records}`} />
                <DiagnosticItem label="页面问题" value={`${result.scan.issues} 个`} />
                <DiagnosticItem label="风险等级" value={policy?.risk_level || '待确认'} />
              </div>
            </div>
            <div style={{ display: 'grid', gap: 7, marginTop: 14, fontSize: 12, lineHeight: 1.6 }}>
              <div><strong>AI 定位：</strong>{profile.positioning || '未识别'}</div>
              <div><strong>受众：</strong>{profile.audience || '未识别'}</div>
              <div><strong>建议主题：</strong>{profile.in_scope_topics?.join('、') || '未识别'}</div>
              <div><strong>产品/服务线索：</strong>{profile.products?.join('、') || result.scan.product_hints.join('、') || '未识别'}</div>
              <div style={{ color: 'var(--ink-500)' }}>{result.recommended_next_step}</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 16 }}>
              {field('business_id', '业务 ID *', '已根据站点 Key 预填，可修改')}
              {field('market', '目标市场', '仅在启用关键词策略时必填，例如 US')}
              {field('language_code', '语言代码', '例如 en / de / zh')}
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12, paddingTop: 20 }}><input type="checkbox" checked={confirmation.strategy_enabled} onChange={(event) => onConfirmationChange('strategy_enabled', event.target.checked)} /><span>我已核对资料卡，立即启用策略。</span></label>
            </div>
            {result.candidate_targets.length > 0 && <label style={{ display: 'grid', gap: 5, fontSize: 12, marginTop: 12 }}><span>候选承接页</span><select className="input" value={confirmation.target_url} onChange={(event) => onConfirmationChange('target_url', event.target.value)}>{result.candidate_targets.map((target) => <option value={target.url} key={target.url}>{target.type} · {target.title || target.url}</option>)}</select><span style={{ color: 'var(--ink-400)', fontSize: 10.5 }}>来自公开页面的标题和 H1；选择即确认它可作为文章内链承接页。医疗效果、安全或治疗说法仍必须有批准来源。</span></label>}
          </>
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onManual}>手动创建</button><div style={{ display: 'flex', gap: 8 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button>{result && <button className="btn btn--primary" type="button" onClick={onConfirm} disabled={confirming}>{confirming ? '确认中…' : (['regulated', 'ymyl'].includes(String(policy?.risk_level)) && !(policy?.allowed_sources || []).length ? '保存业务草案' : '确认业务资料卡')}</button>}</div></div>
      </div>
    </div>
  )
}

export function BusinessOnboardingEditor({
  form,
  saving,
  onChange,
  onSave,
  onClose,
}: {
  form: BusinessOnboardingForm
  saving: boolean
  onChange: (key: keyof BusinessOnboardingForm, value: string | boolean) => void
  onSave: () => void
  onClose: () => void
}) {
  const field = (key: keyof BusinessOnboardingForm, label: string, hint?: string, type = 'text') => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}>
      <span>{label}</span>
      <input className="input" type={type} value={String(form[key] ?? '')} onChange={(event) => onChange(key, event.target.value)} />
      {hint && <span style={{ color: 'var(--ink-400)', fontSize: 10.5 }}>{hint}</span>}
    </label>
  )
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 22, background: 'rgba(23,22,20,.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: 'min(760px, 100%)', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="card__title"><span>新增业务</span><button className="icon-btn" type="button" onClick={onClose}>close</button></div>
        <p style={{ color: 'var(--ink-500)', fontSize: 12, lineHeight: 1.65, marginTop: 8 }}>先创建业务草案。产品和分类接口同步完成后，AI 会自动生成定位、受众、主题与内容安全规则；此处不会扫描 sitemap，也不会启用策略。</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
          {field('business_name', '业务名称 *', '例如 HealthyOxy；系统会根据主站域名自动生成内部业务标识')}
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>主站类型</span><select className="input" value={form.site_type} onChange={(event) => onChange('site_type', event.target.value)}><option value="shopify">Shopify 商业主站</option><option value="main">其他商业主站</option><option value="wp">WordPress 内容站</option><option value="blog">博客站</option><option value="other">其他</option></select></label>
          <div style={{ gridColumn: '1 / -1' }}>{field('base_url', '主站网址 *', '例如 https://example.com；用于后续关联产品与分类接口')}</div>
        </div>
        <div style={{ marginTop: 14, padding: 12, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--paper-100)', fontSize: 12, lineHeight: 1.65, color: 'var(--ink-500)' }}>
          <strong style={{ color: 'var(--ink-700)' }}>创建后会做什么</strong>
          <div>1. 接入并同步产品、分类和产品页数据</div>
          <div>2. AI 生成业务资料卡与内容边界</div>
          <div>3. 你确认资料卡后，才可以启用关键词和文章策略</div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button><button className="btn btn--primary" type="button" onClick={onSave} disabled={saving}>{saving ? '创建中…' : '创建业务草案'}</button></div>
      </div>
    </div>
  )
}

export function SiteEditor({
  form,
  configuredKeys,
  saving,
  onChange,
  onSave,
  onClose,
}: {
  form: SiteForm
  configuredKeys: string[]
  saving: boolean
  onChange: (key: keyof SiteForm, value: string | boolean) => void
  onSave: () => void
  onClose: () => void
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const isWordPress = form.connector_type === 'wordpress'
  const isShopify = form.connector_type === 'shopify' || form.site_type === 'shopify'
  const articlePaths = articlePathsForForm(form)
  const field = (key: keyof SiteForm, label: string, type = 'text') => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}>
      <span>{label}</span>
      <input className="input" type={type} value={String(form[key] ?? '')} onChange={(event) => onChange(key, event.target.value)} />
    </label>
  )
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 20, background: 'rgba(23,22,20,.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: 'min(620px, 100%)', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="card__title"><span>编辑站点配置：{form.name}</span><button className="icon-btn" type="button" onClick={onClose}>close</button></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
          {field('name', '站点名称')}
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>站点类型</span><select className="input" value={form.site_type} onChange={(event) => onChange('site_type', event.target.value)}>{isBusinessMainForm(form) && <><option value="main">商业主站</option><option value="shopify">Shopify 商业主站</option></>}<option value="blog">博客</option><option value="wp">WordPress</option><option value="other">其他</option></select></label>
          {field('base_url', '公开站点网址')}
          <div style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>所属业务</span><div className="input" style={{ display: 'flex', alignItems: 'center', color: 'var(--ink-500)' }}>{form.business_id || '未选择业务'}</div></div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <input type="checkbox" checked={form.strategy_enabled} onChange={(event) => onChange('strategy_enabled', event.target.checked)} />
            <span>参与所属业务的策略扫描与规划</span>
          </label>
        </div>
        <div style={{ marginTop: 18, fontWeight: 700, fontSize: 13 }}>文章连接</div>
        {isShopify ? (
          <div style={{ marginTop: 6, color: 'var(--ink-500)', fontSize: 12 }}>Shopify 由已安装的应用授权连接；无需在这里填写文章接口地址或 Token。</div>
        ) : (
          <>
            <div style={{ marginTop: 6, color: 'var(--ink-500)', fontSize: 12 }}>已保存的密钥不会回显；留空表示保留原配置，填写新值后替换。</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
              {isWordPress ? <>{field('username', 'WordPress 用户名')}{field('applicationPassword', 'Application Password', 'password')}</> : <>{field('api_base_url', '文章接口地址')}{field('tokenA', '站点 Token', 'password')}</>}
            </div>
            {articlePaths.isPreset && <div style={{ marginTop: 8, color: 'var(--ink-500)', fontSize: 12 }}>已识别为 OEMApps：文章读取和发布均使用 <code>/posts</code>，仅 Token 因站点而异，无需手填路径。</div>}
          </>
        )}
        <div style={{ marginTop: 16 }}>
          <button className="btn btn--ghost btn--xs" type="button" onClick={() => setAdvancedOpen((value) => !value)}>{advancedOpen ? '收起高级配置' : '高级配置'}</button>
        </div>
        {advancedOpen && <>
          <div style={{ marginTop: 8, color: 'var(--ink-500)', fontSize: 12 }}>通常不需要修改。站点 Key、市场/语种和内容边界会由业务归属、产品接口和 AI 资料卡补全。</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
            {field('site_key', '站点 Key')}
            <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>连接器类型</span><select className="input" value={form.connector_type} onChange={(event) => onChange('connector_type', event.target.value)}><option value="custom_openapi">Custom OpenAPI</option><option value="wordpress">WordPress REST</option><option value="shopify">Shopify Admin</option></select></label>
            {field('domain', '技术域名（通常无需修改）')}
            {!isShopify && !isWordPress && field('openApiKey', 'OpenAPI Key（旧协议更新用）', 'password')}
            {!isShopify && !isWordPress && field('tokenB', '备用站点 Token', 'password')}
            {!articlePaths.isPreset && !isShopify && <>{field('articlesPath', '读取文章路径')}{field('publishPath', '发布文章路径')}</>}
            {field('market', '市场，例如 US / DE')}
            {field('language_code', '语言，例如 en / de')}
            {field('google_gl', 'Google 国家参数，例如 us')}
            {field('google_hl', 'Google 语言参数，例如 en')}
            {field('semrush_database', 'Semrush 数据库，例如 us')}
            {field('content_role', '内容角色')}
            {field('content_scope', '内容范围')}
            {field('notes', '备注')}
          </div>
          <div style={{ marginTop: 10, color: 'var(--ink-500)', fontSize: 12 }}>当前已保存字段：{configuredKeys.length ? configuredKeys.join('、') : '未检测或未配置'}</div>
        </>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button><button className="btn btn--primary" type="button" onClick={onSave} disabled={saving}>{saving ? '保存中…' : '保存配置'}</button></div>
      </div>
    </div>
  )
}


export function KnowledgeEditor({
  site,
  profile,
  saving,
  onChange,
  onSave,
  onConfirm,
  onClose,
}: {
  site: Site
  profile: SiteKnowledgeProfile
  saving: boolean
  onChange: (profile: SiteKnowledgeProfile) => void
  onSave: () => void
  onConfirm: () => void
  onClose: () => void
}) {
  const policy = profile.generation_policy || {}
  const updatePolicy = (patch: Partial<NonNullable<SiteKnowledgeProfile['generation_policy']>>) => onChange({ ...profile, generation_policy: { ...policy, ...patch } })
  const textField = (key: 'positioning' | 'audience' | 'tone', label: string) => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>{label}</span><textarea className="input" rows={2} value={profile[key]} onChange={(event) => onChange({ ...profile, [key]: event.target.value })} /></label>
  )
  const listField = (key: keyof Pick<SiteKnowledgeProfile, 'products' | 'in_scope_topics' | 'out_of_scope_topics' | 'content_types' | 'conversion_goals' | 'editorial_rules'>, label: string) => (
    <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>{label}（一行一项）</span><textarea className="input" rows={3} value={profile[key].join('\n')} onChange={(event) => onChange({ ...profile, [key]: event.target.value.split(/\n|[,，]/).map((item) => item.trim()).filter(Boolean) })} /></label>
  )
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 21, background: 'rgba(23,22,20,.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: 'min(700px, 100%)', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="card__title"><span>确认站点知识：{site.name}</span><button className="icon-btn" type="button" onClick={onClose}>close</button></div>
        <p style={{ color: 'var(--ink-500)', fontSize: 12, lineHeight: 1.6, marginTop: 8 }}>这里决定 AI 后续为这个站点选择哪些主题、内容类型和转化方向。确认前可以直接修改。</p>
        <div style={{ display: 'grid', gap: 12, marginTop: 14 }}>
          {textField('positioning', '站点定位')}
          {textField('audience', '目标受众')}
          {textField('tone', '写作语气')}
          {listField('products', '产品或服务')}
          {listField('in_scope_topics', '应该持续写的主题')}
          {listField('out_of_scope_topics', '暂不应该写的主题')}
          {listField('content_types', '内容类型')}
          {listField('conversion_goals', '转化目标')}
          {listField('editorial_rules', '编辑规则')}
        </div>
        <div style={{ marginTop: 18, fontWeight: 700, fontSize: 13 }}>生文安全规则</div>
        <p style={{ color: 'var(--ink-500)', fontSize: 11, lineHeight: 1.55, marginTop: 6 }}>健康、金融和法律等业务必须保留受监管/高风险等级，并添加与目标市场匹配的官方或专业来源；系统会在缺来源时阻止生成。</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>站点角色</span><select className="input" value={policy.site_role || ''} onChange={(event) => updatePolicy({ site_role: event.target.value })}><option value="">由站点类型判断</option><option value="commercial">商业主站</option><option value="local_service">本地服务站</option><option value="editorial">内容站</option></select></label>
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}><span>风险等级</span><select className="input" value={policy.risk_level || 'standard'} onChange={(event) => updatePolicy({ risk_level: event.target.value })}><option value="low">低</option><option value="standard">标准</option><option value="regulated">受监管</option><option value="ymyl">高风险（医疗/金融/法律）</option></select></label>
        </div>
        <label style={{ display: 'grid', gap: 5, fontSize: 12, marginTop: 12 }}><span>已核验的允许来源（一行一个 URL）</span><textarea className="input" rows={3} value={(policy.allowed_sources || []).map((item) => item.url).filter(Boolean).join('\n')} onChange={(event) => updatePolicy({ allowed_sources: splitLines(event.target.value).filter((url) => /^https:\/\//i.test(url)).map((url) => ({ url, label: url, source_type: 'approved' })) })} /><span style={{ color: 'var(--ink-400)', fontSize: 10.5 }}>只填写适用于当前目标市场、且已由你核验的来源；不要把搜索结果或竞品页面当作来源。</span></label>
        {profile.evidence.length > 0 && <div style={{ marginTop: 14, color: 'var(--ink-500)', fontSize: 11 }}>数据依据：{profile.evidence.map((item) => item.fact).filter(Boolean).join('；')}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}><button className="btn btn--ghost" type="button" onClick={onClose}>取消</button><button className="btn btn--ghost" type="button" onClick={onSave} disabled={saving}>保存草稿</button><button className="btn btn--primary" type="button" onClick={onConfirm} disabled={saving}>{saving ? '保存中…' : '确认并用于策略'}</button></div>
      </div>
    </div>
  )
}
