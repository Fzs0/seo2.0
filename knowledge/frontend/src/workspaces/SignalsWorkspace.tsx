import { FormEvent, useEffect, useState } from "react";
import { knowledgeApi } from "../api/knowledge";
import type {
  MarketSignal,
  SignalContentKind,
  SignalCrawlJob,
  SignalCrawlJobRequest,
  SignalCrawlPreview,
  SignalImportRequest,
  SignalOverview,
  Source,
} from "../types/knowledge";
import {
  EmptyState,
  errorMessage,
  formatDate,
  LoadingRows,
  PageHeading,
} from "./shared";

const signalKindLabels: Record<SignalContentKind, string> = {
  post: "帖子",
  comment: "评论",
  reply: "回复",
  review: "评价",
  video_comment: "视频评论",
  forum_thread: "论坛主题",
  forum_reply: "论坛回复",
  message: "消息",
  other: "其他",
};

type SignalFilters = { query: string; sourceId: string; contentKind: string; dateFrom: string; dateTo: string };
const EMPTY_SIGNAL_FILTERS: SignalFilters = { query: "", sourceId: "", contentKind: "", dateFrom: "", dateTo: "" };
const SIGNAL_PAGE_SIZE = 12;

type CrawlPresetId = "reddit" | "youtube" | "forum" | "custom";

const CRAWL_PRESETS: Record<CrawlPresetId, SignalCrawlJobRequest> = {
  reddit: {
    name: "Reddit 用户需求监测",
    source_name: "Reddit 公开搜索",
    platform: "reddit",
    channel: "social",
    rights_confirmed: false,
    search_url_template: "https://www.reddit.com/search/?q={query}&type=link",
    keywords: [""],
    detail_url_contains: ["/comments/"],
    max_pages: 1,
    max_signals: 50,
    delay_ms: 1000,
    interval_hours: 24,
    enabled: true,
  },
  youtube: {
    name: "YouTube 评论需求监测",
    source_name: "YouTube 公开搜索",
    platform: "youtube",
    channel: "video",
    rights_confirmed: false,
    search_url_template: "https://www.youtube.com/results?search_query={query}",
    keywords: [""],
    detail_url_contains: ["/watch"],
    max_pages: 1,
    max_signals: 50,
    delay_ms: 1000,
    interval_hours: 24,
    enabled: true,
  },
  forum: {
    name: "论坛用户需求监测",
    source_name: "公开论坛搜索",
    platform: "forum",
    channel: "forum",
    rights_confirmed: false,
    search_url_template: "",
    keywords: [""],
    detail_url_contains: ["/thread/"],
    max_pages: 1,
    max_signals: 50,
    delay_ms: 1000,
    interval_hours: 24,
    enabled: true,
  },
  custom: {
    name: "我的市场需求监测",
    source_name: "公开网页搜索",
    platform: "web",
    channel: "social",
    rights_confirmed: false,
    search_url_template: "",
    keywords: [""],
    detail_url_contains: ["/thread/"],
    max_pages: 1,
    max_signals: 50,
    delay_ms: 1000,
    interval_hours: 24,
    enabled: true,
  },
};

const crawlPresetLabels: Record<CrawlPresetId, { title: string; description: string }> = {
  reddit: { title: "Reddit", description: "发现用户真实问题" },
  youtube: { title: "YouTube", description: "查看视频下的评论" },
  forum: { title: "普通论坛", description: "适合行业社区与问答站" },
  custom: { title: "其他公开网页", description: "自己配置一个公开搜索页" },
};

function SignalsWorkspace() {
  const [overview, setOverview] = useState<SignalOverview | null>(null);
  const [signals, setSignals] = useState<MarketSignal[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [filters, setFilters] = useState<SignalFilters>(EMPTY_SIGNAL_FILTERS);
  const [draftFilters, setDraftFilters] = useState<SignalFilters>(EMPTY_SIGNAL_FILTERS);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [crawlNotice, setCrawlNotice] = useState("");
  const [crawlError, setCrawlError] = useState("");
  const [crawlJobs, setCrawlJobs] = useState<SignalCrawlJob[]>([]);
  const [crawlPreview, setCrawlPreview] = useState<SignalCrawlPreview | null>(null);
  const [crawlSubmitting, setCrawlSubmitting] = useState(false);
  const [crawlPreset, setCrawlPreset] = useState<CrawlPresetId>("reddit");
  const [crawlForm, setCrawlForm] = useState<SignalCrawlJobRequest>(CRAWL_PRESETS.reddit);
  const [form, setForm] = useState<SignalImportRequest>({
    source_name: "",
    platform: "",
    channel: "social",
    rights_confirmed: false,
    signals: [{ content_kind: "comment", content: "", language_code: "en", engagement: {}, metadata: {} }],
  });

  async function loadSignals(nextPage: number, nextFilters: SignalFilters = filters) {
    setLoading(true);
    setError("");
    try {
      const [result, nextOverview] = await Promise.all([
        knowledgeApi.signals({ ...nextFilters, limit: SIGNAL_PAGE_SIZE, offset: nextPage * SIGNAL_PAGE_SIZE }),
        knowledgeApi.signalOverview(),
      ]);
      setSignals(result.items);
      setTotal(result.total);
      setOverview(nextOverview);
      setPage(nextPage);
    } catch (loadError) {
      setError(`无法加载市场信号：${errorMessage(loadError)}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadSignals(0, EMPTY_SIGNAL_FILTERS);
    void knowledgeApi.sources().then(setSources).catch(() => undefined);
    void knowledgeApi.signalCrawlJobs().then(setCrawlJobs).catch(() => undefined);
  }, []);

  async function submitSignal(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    setNotice("");
    const signal = form.signals[0];
    try {
      const payload: SignalImportRequest = {
        ...form,
        source_name: form.source_name.trim(),
        platform: form.platform.trim(),
        signals: [{ ...signal, content: signal.content.trim(), published_at: signal.published_at ? `${signal.published_at}T00:00:00Z` : undefined }],
      };
      const result = await knowledgeApi.importSignals(payload);
      setNotice(`已接收 ${result.received} 条信号，新建 ${result.created} 条，重复 ${result.duplicates} 条。`);
      setForm({ ...form, rights_confirmed: false, signals: [{ ...signal, content: "", published_at: undefined }] });
      await loadSignals(0, filters);
    } catch (submitError) {
      setError(`导入市场信号失败：${errorMessage(submitError)}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function previewCrawl() {
    setCrawlSubmitting(true);
    setCrawlError("");
    setCrawlNotice("");
    if (!crawlForm.rights_confirmed) {
      setCrawlError("请先勾选“我确认有权抓取”，系统才会访问公开页面。");
      setCrawlSubmitting(false);
      return;
    }
    if (!crawlForm.search_url_template.trim()) {
      setCrawlError("这个平台还需要一个公开搜索页地址，请打开“高级设置”补充。");
      setCrawlSubmitting(false);
      return;
    }
    try {
      const payload = {
        ...crawlForm,
        name: crawlForm.name.trim(),
        source_name: crawlForm.source_name.trim(),
        platform: crawlForm.platform.trim(),
        search_url_template: crawlForm.search_url_template.trim(),
        keywords: crawlForm.keywords.map((item) => item.trim()).filter(Boolean),
        detail_url_contains: crawlForm.detail_url_contains.map((item) => item.trim()).filter(Boolean),
      };
      const preview = await knowledgeApi.previewSignalCrawl(payload);
      setCrawlPreview(preview);
      setCrawlNotice(preview.total > 0 ? `找到了 ${preview.total} 个公开帖子链接，可以开始追踪。` : "暂时没有找到匹配的帖子链接，请检查关键词或高级设置。 ");
    } catch (previewError) {
      setCrawlError(`预览失败：${errorMessage(previewError)}`);
    } finally {
      setCrawlSubmitting(false);
    }
  }

  async function createCrawlJob(event: FormEvent) {
    event.preventDefault();
    setCrawlSubmitting(true);
    setCrawlError("");
    setCrawlNotice("");
    if (!crawlForm.search_url_template.trim()) {
      setCrawlError("这个平台还需要一个公开搜索页地址，请打开“高级设置”补充。");
      setCrawlSubmitting(false);
      return;
    }
    try {
      const payload = {
        ...crawlForm,
        name: crawlForm.name.trim(),
        source_name: crawlForm.source_name.trim(),
        platform: crawlForm.platform.trim(),
        search_url_template: crawlForm.search_url_template.trim(),
        keywords: crawlForm.keywords.map((item) => item.trim()).filter(Boolean),
        detail_url_contains: crawlForm.detail_url_contains.map((item) => item.trim()).filter(Boolean),
      };
      const job = await knowledgeApi.createSignalCrawlJob(payload);
      setCrawlJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      setCrawlNotice(`已创建“${job.name}”，系统会每天自动检查新讨论。`);
    } catch (createError) {
      setCrawlError(`创建失败：${errorMessage(createError)}`);
    } finally {
      setCrawlSubmitting(false);
    }
  }

  async function runCrawlJob(job: SignalCrawlJob) {
    setCrawlSubmitting(true);
    setCrawlError("");
    try {
      const run = await knowledgeApi.runSignalCrawlJob(job.id);
      if (run.status === "failed") {
        setCrawlError(`任务运行失败：${run.error ?? "暂时无法访问目标网站"}`);
      } else if ((run.counts.signals ?? 0) === 0 && run.warnings.length > 0) {
        setCrawlError(`任务未采集到数据：${run.warnings[0]}`);
      } else {
        const warning = run.warnings.length > 0 ? ` 采集提示：${run.warnings[0]}` : "";
        setCrawlNotice(`任务“${job.name}”已完成：发现 ${run.counts.signals ?? 0} 条，新建 ${run.counts.created ?? 0} 条。${warning}`);
      }
      await loadSignals(0, filters);
      setCrawlJobs(await knowledgeApi.signalCrawlJobs());
    } catch (runError) {
      setCrawlError(`运行失败：${errorMessage(runError)}`);
    } finally {
      setCrawlSubmitting(false);
    }
  }

  function chooseCrawlPreset(preset: CrawlPresetId) {
    setCrawlPreset(preset);
    setCrawlForm({ ...CRAWL_PRESETS[preset], rights_confirmed: crawlForm.rights_confirmed });
    setCrawlPreview(null);
    setCrawlNotice("");
    setCrawlError("");
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    setFilters(draftFilters);
    void loadSignals(0, draftFilters);
  }

  function clearFilters() {
    setDraftFilters(EMPTY_SIGNAL_FILTERS);
    setFilters(EMPTY_SIGNAL_FILTERS);
    void loadSignals(0, EMPTY_SIGNAL_FILTERS);
  }

  const totalPages = Math.max(1, Math.ceil(total / SIGNAL_PAGE_SIZE));
  const signal = form.signals[0];

  return (
    <section>
      <PageHeading title="需求信号" description="统一收集平台帖子、评论、回复和评价，为需求发现、趋势判断和内容选题保留原始证据。" action={<span className="badge safety">平台无关信号层</span>} />
      <div className="signal-note"><strong>现在先建立证据层</strong><span>Reddit 与 YouTube 已支持公开页面采集；论坛或其他平台也可以先导入授权内容，继续复用同一套去重、筛选和分析管线。</span></div>
      <div className="signal-stats" aria-label="需求信号统计">
        <div className="signal-stat"><span>信号总数</span><strong>{overview?.total ?? 0}</strong><small>已保存的帖子与评论</small></div>
        <div className="signal-stat"><span>近 7 天</span><strong>{overview?.last_7_days ?? 0}</strong><small>近期市场讨论活跃度</small></div>
        <div className="signal-stat"><span>近 30 天</span><strong>{overview?.last_30_days ?? 0}</strong><small>趋势分析观察窗口</small></div>
        <div className="signal-stat"><span>平台</span><strong>{overview ? Object.keys(overview.by_platform).length : 0}</strong><small>已接入或导入的平台</small></div>
      </div>

      <section className="card signal-crawl">
        <div className="section-heading"><div><h2>每天帮我发现用户在讨论什么</h2><p>只需要选择平台和输入关键词，系统会自动收集公开讨论，方便后续分析用户需求和写文章。</p></div><span className="badge safety">公开内容</span></div>
        <form className="crawl-form" onSubmit={(event) => void createCrawlJob(event)}>
          <div className="crawl-step"><span className="crawl-step-number">1</span><div><strong>你想去哪里了解用户？</strong><small>先选一个平台，系统会自动准备采集方式。</small></div></div>
          <div className="crawl-platform-grid">{(Object.keys(crawlPresetLabels) as CrawlPresetId[]).map((preset) => <button type="button" key={preset} className={`crawl-platform ${crawlPreset === preset ? "selected" : ""}`} onClick={() => chooseCrawlPreset(preset)}><strong>{crawlPresetLabels[preset].title}</strong><span>{crawlPresetLabels[preset].description}</span></button>)}</div>
          <div className="crawl-step"><span className="crawl-step-number">2</span><div><strong>你想研究什么？</strong><small>一句话或多个关键词都可以，每行一个，例如：pricing、seo workflow、content brief。</small></div></div>
          <label className="crawl-keywords"><span className="sr-only">研究关键词</span><textarea required rows={3} value={crawlForm.keywords.join("\n")} onChange={(event) => setCrawlForm({ ...crawlForm, keywords: event.target.value.split(/[\n,]/) })} placeholder="例如：\ncontent strategy\nseo workflow\ncontent brief" /></label>
          <div className="crawl-step"><span className="crawl-step-number">3</span><div><strong>多久帮你检查一次？</strong><small>系统只会抓取公开页面，并且会自动去重。</small></div></div>
          <label className="crawl-frequency">检查频率<select value={crawlForm.interval_hours} onChange={(event) => setCrawlForm({ ...crawlForm, interval_hours: Number(event.target.value) })}><option value={24}>每天一次</option><option value={12}>每天两次</option><option value={168}>每周一次</option></select></label>
          <details className="crawl-advanced"><summary>高级设置（普通论坛或特殊网站才需要）</summary><div className="form-grid compact-grid"><label>公开搜索页地址<input value={crawlForm.search_url_template} onChange={(event) => setCrawlForm({ ...crawlForm, search_url_template: event.target.value })} placeholder="https://example.com/search?q={query}" /><small>地址中必须包含 <code>{"{query}"}</code>。</small></label><label>帖子链接特征<input value={crawlForm.detail_url_contains.join(", ")} onChange={(event) => setCrawlForm({ ...crawlForm, detail_url_contains: event.target.value.split(",") })} placeholder="/thread/, /discussion/" /></label><label>任务名称<input value={crawlForm.name} onChange={(event) => setCrawlForm({ ...crawlForm, name: event.target.value })} /></label><label>来源名称<input value={crawlForm.source_name} onChange={(event) => setCrawlForm({ ...crawlForm, source_name: event.target.value })} /></label><label>最多抓取页数<input type="number" min={1} max={20} value={crawlForm.max_pages} onChange={(event) => setCrawlForm({ ...crawlForm, max_pages: Number(event.target.value) })} /></label><label>每次最多帖子<input type="number" min={1} max={500} value={crawlForm.max_signals} onChange={(event) => setCrawlForm({ ...crawlForm, max_signals: Number(event.target.value) })} /></label></div></details>
          <label className="rights-check"><input type="checkbox" required checked={crawlForm.rights_confirmed} onChange={(event) => setCrawlForm({ ...crawlForm, rights_confirmed: event.target.checked })} /><span><strong>我确认有权抓取并在内部分析这些公开页面</strong><small>系统不会处理登录墙、验证码、私有内容或绕过反爬限制。</small></span></label>
          {crawlNotice && <p className="form-message success" role="status">{crawlNotice}</p>}
          {crawlError && <p className="form-message error" role="alert">{crawlError}</p>}
          <div className="crawl-actions"><button className="button secondary" type="button" disabled={crawlSubmitting} onClick={() => void previewCrawl()}>先预览结果</button><button className="button primary" disabled={crawlSubmitting}>{crawlSubmitting ? "正在处理…" : "开始每天追踪"}</button></div>
        </form>
        {crawlPreview && <div className="crawl-preview"><strong>预览发现 {crawlPreview.total} 个链接</strong>{crawlPreview.warnings.map((warning) => <span key={warning}>{warning}</span>)}{crawlPreview.urls.slice(0, 8).map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">{url}</a>)}</div>}
        {crawlJobs.length > 0 && <div className="crawl-jobs"><strong>每日任务</strong>{crawlJobs.map((job) => <div className="crawl-job" key={job.id}><div><b>{job.name}</b><span>{job.platform} · {job.keywords.join("、")} · {job.enabled ? "启用" : "停用"}</span><small className={job.last_error ? "crawl-job-error" : "crawl-job-status"}>{job.last_error ? `最近失败：${job.last_error}` : job.last_run_at ? `最近运行：${formatDate(job.last_run_at)}` : "尚未运行"}</small></div><button className="button secondary" type="button" disabled={crawlSubmitting} onClick={() => void runCrawlJob(job)}>立即运行</button></div>)}</div>}
      </section>

      <div className="signal-layout">
        <form className="card signal-import" onSubmit={(event) => void submitSignal(event)}>
          <div className="section-heading"><div><h2>导入一条需求信号</h2><p>适用于先手动验证 Reddit、论坛或视频评论数据。</p></div></div>
          <div className="form-grid compact-grid signal-form-grid">
            <label>来源名称<input required value={form.source_name} onChange={(event) => setForm({ ...form, source_name: event.target.value })} placeholder="例如 r/SEO、YouTube 某频道" /></label>
            <label>平台<input required value={form.platform} onChange={(event) => setForm({ ...form, platform: event.target.value })} placeholder="reddit / youtube / forum" /></label>
            <label>内容类型<select value={signal.content_kind} onChange={(event) => setForm({ ...form, signals: [{ ...signal, content_kind: event.target.value as SignalContentKind }] })}>{Object.entries(signalKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>市场<input value={signal.market ?? ""} onChange={(event) => setForm({ ...form, signals: [{ ...signal, market: event.target.value }] })} placeholder="US" /></label>
            <label>发布日期<input type="date" value={signal.published_at ?? ""} onChange={(event) => setForm({ ...form, signals: [{ ...signal, published_at: event.target.value }] })} /></label>
            <label>原文链接<input type="url" value={signal.canonical_url ?? ""} onChange={(event) => setForm({ ...form, signals: [{ ...signal, canonical_url: event.target.value }] })} placeholder="https://…" /></label>
            <label className="full-span">信号内容<textarea required rows={7} value={signal.content} onChange={(event) => setForm({ ...form, signals: [{ ...signal, content: event.target.value }] })} placeholder="粘贴帖子、评论或用户评价原文……" /></label>
          </div>
          <label className="rights-check"><input type="checkbox" required checked={form.rights_confirmed} onChange={(event) => setForm({ ...form, rights_confirmed: event.target.checked })} /><span><strong>我确认有权保存并在内部分析这份公开资料</strong><small>作者名、互动指标和平台字段只作为不可信数据保存。</small></span></label>
          {notice && <p className="form-message success" role="status">{notice}</p>}
          {error && <p className="form-message error" role="alert">{error}</p>}
          <button className="button primary wide" disabled={submitting}>{submitting ? "正在保存信号…" : "保存市场信号"}</button>
        </form>

        <section className="card signal-library">
          <div className="section-heading"><div><h2>信号库</h2><p>先按来源、类型和时间筛选，再用于后续需求与趋势分析。</p></div><span className="badge">{total} 条</span></div>
          <form className="signal-filters" onSubmit={applyFilters}>
            <label className="signal-search">搜索内容或来源<input value={draftFilters.query} onChange={(event) => setDraftFilters({ ...draftFilters, query: event.target.value })} placeholder="例如：pricing、workflow、alternative" /></label>
            <label>来源<select value={draftFilters.sourceId} onChange={(event) => setDraftFilters({ ...draftFilters, sourceId: event.target.value })}><option value="">全部来源</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
            <label>类型<select value={draftFilters.contentKind} onChange={(event) => setDraftFilters({ ...draftFilters, contentKind: event.target.value })}><option value="">全部类型</option>{Object.entries(signalKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>从<input type="date" value={draftFilters.dateFrom} onChange={(event) => setDraftFilters({ ...draftFilters, dateFrom: event.target.value })} /></label>
            <label>到<input type="date" value={draftFilters.dateTo} onChange={(event) => setDraftFilters({ ...draftFilters, dateTo: event.target.value })} /></label>
            <div className="archive-filter-actions"><button className="button primary" type="submit">筛选</button><button className="button secondary" type="button" onClick={clearFilters}>清除</button></div>
          </form>
          {loading ? <LoadingRows /> : signals.length === 0 ? <EmptyState title="还没有匹配的需求信号" text="导入一条帖子或评论，开始建立用户需求的原始证据。" /> : <div className="signal-list">{signals.map((item) => <article className="signal-item" key={item.id}><div className="signal-item-top"><span className="badge channel">{item.platform}</span><span className="quality-badge">{signalKindLabels[item.content_kind]}</span><time>{formatDate(item.published_at ?? item.created_at)}</time></div>{item.title && <h3>{item.title}</h3>}<p>{item.content}</p><div className="signal-meta"><span>{item.source_name}</span>{item.author_handle && <span>作者：{item.author_handle}</span>}{item.market && <span>市场：{item.market}</span>}{item.canonical_url && <a href={item.canonical_url} target="_blank" rel="noreferrer">查看原文 ↗</a>}</div></article>)}</div>}
          {!loading && total > 0 && <div className="archive-pagination"><span>第 {page + 1} / {totalPages} 页</span><div><button className="button secondary" disabled={page === 0} onClick={() => void loadSignals(page - 1)}>上一页</button><button className="button secondary" disabled={page + 1 >= totalPages} onClick={() => void loadSignals(page + 1)}>下一页</button></div></div>}
        </section>
      </div>
    </section>
  );
}

export { SignalsWorkspace };
