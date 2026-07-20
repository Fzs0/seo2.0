import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { getApiBaseUrl, knowledgeApi } from "./api/knowledge";
import type {
  BatchDiscoveryMode,
  BatchDiscoveryRequest,
  BatchPreviewResponse,
  BatchRun,
  BatchRunItem,
  Channel,
  Claim,
  Document,
  ImportDocumentRequest,
  ImportDocumentResponse,
  ImportUrlRequest,
  KnowledgePack,
  MarketSignal,
  Overview,
  QualityDecision,
  QualityRun,
  QualityRunItem,
  RetrieveRequest,
  ReviewDecision,
  SignalContentKind,
  SignalCrawlJob,
  SignalCrawlJobRequest,
  SignalCrawlPreview,
  SignalImportRequest,
  SignalOverview,
  Source,
} from "./types/knowledge";

type Workspace = "library" | "signals" | "review" | "lab";
type ArchiveStatus = Extract<ReviewDecision, "approved" | "rejected">;
type ArchiveFilters = { query: string; sourceId: string; dateFrom: string; dateTo: string };

const ARCHIVE_PAGE_SIZE = 12;
const EMPTY_ARCHIVE_FILTERS: ArchiveFilters = { query: "", sourceId: "", dateFrom: "", dateTo: "" };

const channelLabels: Record<Channel, string> = {
  seo: "SEO",
  social: "社交媒体",
  forum: "论坛",
  video: "视频",
  visual: "图文",
};

const emptyImport: ImportDocumentRequest = {
  source_name: "",
  canonical_url: "",
  title: "",
  channel: "seo",
  content_type: "article",
  language_code: "en",
  market: "US",
  author: null,
  published_at: null,
  raw_content: "",
  rights_confirmed: false,
};

const emptyUrlImport: ImportUrlRequest = {
  url: "",
  rights_confirmed: false,
  channel: "seo",
  market: "",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生了未知错误，请稍后重试。";
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("zh-CN");
}

function formatStructuredList(items: unknown[]): string {
  if (items.length === 0) return "未限定";
  return items.map((item) => typeof item === "string" ? item : JSON.stringify(item)).join("；");
}

function App() {
  const [workspace, setWorkspace] = useState<Workspace>("library");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const loadWorkspace = useCallback(async () => {
    setInitialLoading(true);
    setLoadError("");
    try {
      const [nextOverview, nextSources, nextDocuments, nextClaims] = await Promise.all([
        knowledgeApi.overview(),
        knowledgeApi.sources(),
        knowledgeApi.documents(),
        knowledgeApi.pendingClaims(),
      ]);
      setOverview(nextOverview);
      setSources(nextSources);
      setDocuments(nextDocuments);
      setClaims(nextClaims);
    } catch (error) {
      setLoadError(errorMessage(error));
    } finally {
      setInitialLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  return (
    <div className="app-shell">
      <Sidebar workspace={workspace} onSelect={setWorkspace} />
      <main className="main-content">
        <header className="topbar">
          <div>
            <span className="eyebrow">品牌发现力</span>
            <strong>Discovery Intelligence</strong>
          </div>
          <div className="api-status" title={getApiBaseUrl()}>
            <span aria-hidden="true" /> API · {new URL(getApiBaseUrl()).port || "80"}
          </div>
        </header>

        {loadError && (
          <div className="alert error" role="alert">
            <div><strong>无法加载知识库</strong><p>{loadError}</p></div>
            <button className="button secondary" onClick={() => void loadWorkspace()}>重试</button>
          </div>
        )}

        {workspace === "library" && (
          <LibraryWorkspace
            overview={overview}
            sources={sources}
            documents={documents}
            loading={initialLoading}
            onImported={loadWorkspace}
          />
        )}
        {workspace === "review" && (
          <ReviewWorkspace
            claims={claims}
            loading={initialLoading}
            onReload={loadWorkspace}
            onReviewed={async (claimId) => {
              setClaims((current) => current.filter((claim) => claim.id !== claimId));
              try {
                setOverview(await knowledgeApi.overview());
              } catch {
                // The completed review remains visible through the queue update.
              }
            }}
          />
        )}
        {workspace === "signals" && <SignalsWorkspace />}
        {workspace === "lab" && <RetrievalWorkspace />}
      </main>
    </div>
  );
}

function Sidebar({ workspace, onSelect }: { workspace: Workspace; onSelect: (value: Workspace) => void }) {
  const links: Array<{ id: Workspace; icon: string; label: string; hint: string }> = [
    { id: "library", icon: "▤", label: "发现力知识", hint: "来源与方法" },
    { id: "signals", icon: "◌", label: "需求信号", hint: "用户与市场" },
    { id: "review", icon: "✓", label: "知识校准", hint: "证据与边界" },
    { id: "lab", icon: "⌕", label: "策略检索", hint: "任务知识包" },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>
        <div><strong>发现力</strong><small>Brand discoverability</small></div>
      </div>
      <nav aria-label="知识系统工作区">
        <p className="nav-label">工作区</p>
        {links.map((link) => (
          <button
            key={link.id}
            className={`nav-link ${workspace === link.id ? "active" : ""}`}
            onClick={() => onSelect(link.id)}
            aria-current={workspace === link.id ? "page" : undefined}
          >
            <span className="nav-icon" aria-hidden="true">{link.icon}</span>
            <span><strong>{link.label}</strong><small>{link.hint}</small></span>
          </button>
        ))}
      </nav>
      <div className="sidebar-note">
        <span className="pulse" aria-hidden="true" />
        <div><strong>证据优先</strong><small>只有经校准的知识进入策略</small></div>
      </div>
    </aside>
  );
}

function PageHeading({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return (
    <div className="page-heading">
      <div><h1>{title}</h1><p>{description}</p></div>
      {action}
    </div>
  );
}

function LibraryWorkspace({
  overview,
  sources,
  documents,
  loading,
  onImported,
}: {
  overview: Overview | null;
  sources: Source[];
  documents: Document[];
  loading: boolean;
  onImported: () => Promise<void>;
}) {
  const [showImporter, setShowImporter] = useState(false);
  const [showBatchImporter, setShowBatchImporter] = useState(false);
  const [claimArchiveStatus, setClaimArchiveStatus] = useState<ArchiveStatus | null>(null);
  const [archivedClaims, setArchivedClaims] = useState<Claim[]>([]);
  const [archiveFilters, setArchiveFilters] = useState<ArchiveFilters>(EMPTY_ARCHIVE_FILTERS);
  const [archivePage, setArchivePage] = useState(0);
  const [archiveTotal, setArchiveTotal] = useState(0);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [archiveError, setArchiveError] = useState("");
  const [form, setForm] = useState<ImportDocumentRequest>(emptyImport);
  const [urlForm, setUrlForm] = useState<ImportUrlRequest>(emptyUrlImport);
  const [submitting, setSubmitting] = useState(false);
  const [importMode, setImportMode] = useState<"url" | "manual" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [lastResult, setLastResult] = useState<ImportDocumentResponse | null>(null);

  const documentCounts = useMemo(() => {
    const counts = new Map<string, number>();
    documents.forEach((document) => counts.set(document.source_id, (counts.get(document.source_id) ?? 0) + 1));
    return counts;
  }, [documents]);

  function applyImportResult(result: ImportDocumentResponse) {
    setLastResult(result);
    setNotice(result.duplicate
      ? "检测到重复文档：已保留原记录，没有重复生成知识候选。"
      : `导入成功，已生成 ${result.claims_created} 条待审核知识候选。`);
  }

  async function loadClaimArchive(status: ArchiveStatus, page: number, filters: ArchiveFilters) {
    setClaimArchiveStatus(status);
    setArchiveLoading(true);
    setArchiveError("");
    try {
      const result = await knowledgeApi.claimsByReviewStatus(status, {
        query: filters.query,
        sourceId: filters.sourceId,
        dateFrom: filters.dateFrom,
        dateTo: filters.dateTo,
        limit: ARCHIVE_PAGE_SIZE,
        offset: page * ARCHIVE_PAGE_SIZE,
      });
      setArchivedClaims(result.items);
      setArchiveTotal(result.total);
      setArchivePage(page);
    } catch (loadError) {
      setArchivedClaims([]);
      setArchiveTotal(0);
      setArchiveError(`无法加载${status === "approved" ? "已批准" : "已拒绝"}知识：${errorMessage(loadError)}`);
    } finally {
      setArchiveLoading(false);
    }
  }

  function openClaimArchive(status: ArchiveStatus) {
    void loadClaimArchive(status, 0, archiveFilters);
  }

  function applyArchiveFilters(filters: ArchiveFilters) {
    setArchiveFilters(filters);
    if (claimArchiveStatus) void loadClaimArchive(claimArchiveStatus, 0, filters);
  }

  function changeArchivePage(page: number) {
    if (claimArchiveStatus) void loadClaimArchive(claimArchiveStatus, page, archiveFilters);
  }

  async function submitUrlImport(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setImportMode("url");
    setError("");
    setNotice("");
    setLastResult(null);
    try {
      const payload: ImportUrlRequest = {
        url: urlForm.url.trim(),
        rights_confirmed: urlForm.rights_confirmed,
        channel: urlForm.channel,
        language_code: urlForm.language_code?.trim() || undefined,
        market: urlForm.market?.trim() || undefined,
        source_name: urlForm.source_name?.trim() || undefined,
      };
      const result = await knowledgeApi.importUrl(payload);
      applyImportResult(result);
      if (!result.duplicate) setUrlForm(emptyUrlImport);
      await onImported();
    } catch (submitError) {
      setError(`URL 导入失败：${errorMessage(submitError)}`);
    } finally {
      setSubmitting(false);
      setImportMode(null);
    }
  }

  async function submitManualImport(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setImportMode("manual");
    setError("");
    setNotice("");
    setLastResult(null);
    try {
      const payload = {
        ...form,
        canonical_url: form.canonical_url.trim(),
        author: form.author?.trim() || null,
        published_at: form.published_at ? `${form.published_at}T00:00:00Z` : null,
      };
      const result = await knowledgeApi.importDocument(payload);
      applyImportResult(result);
      if (!result.duplicate) setForm(emptyImport);
      await onImported();
    } catch (submitError) {
      setError(errorMessage(submitError));
    } finally {
      setSubmitting(false);
      setImportMode(null);
    }
  }

  return (
    <section>
      <PageHeading
        title="品牌发现力知识库"
        description="沉淀帮助品牌被发现、理解、信任和选择的可追溯知识。"
        action={(
          <div className="heading-actions">
            <button className="button secondary" onClick={() => { setShowBatchImporter(false); setShowImporter((value) => !value); }}>{showImporter ? "收起单篇" : "+ 单篇 URL"}</button>
            <button className="button primary" onClick={() => { setShowImporter(false); setShowBatchImporter((value) => !value); }}>{showBatchImporter ? "收起批量" : "+ 批量来源"}</button>
          </div>
        )}
      />

      <div className="stats-grid" aria-label="品牌发现力知识统计">
        <Stat label="知识来源" value={overview?.sources} hint="已登记的公开出处" loading={loading} />
        <Stat label="沉淀内容" value={overview?.documents} hint="已去重保存的文档" loading={loading} />
        <Stat label="待校准" value={overview?.pending_claims} hint="需要核对证据与边界" tone="orange" loading={loading} />
        <Stat label="可用知识" value={overview?.approved_claims} hint="点击查看可进入策略的知识" tone="green" loading={loading} onClick={() => void openClaimArchive("approved")} />
        <Stat label="已排除" value={overview?.rejected_claims} hint="不进入策略检索" tone="muted" loading={loading} onClick={() => void openClaimArchive("rejected")} />
      </div>

      <DiscoveryMap />

      {claimArchiveStatus && (
        <ClaimArchive
          status={claimArchiveStatus}
          claims={archivedClaims}
          loading={archiveLoading}
          error={archiveError}
          filters={archiveFilters}
          page={archivePage}
          pageSize={ARCHIVE_PAGE_SIZE}
          total={archiveTotal}
          sources={sources}
          onBack={() => setClaimArchiveStatus(null)}
          onRefresh={() => void loadClaimArchive(claimArchiveStatus, archivePage, archiveFilters)}
          onFiltersChange={applyArchiveFilters}
          onPageChange={changeArchivePage}
        />
      )}

      {showImporter && (
        <div className="card importer">
          <form onSubmit={(event) => void submitUrlImport(event)}>
            <div className="section-heading"><div><h2>从公开来源沉淀知识</h2><p>抓取并清洗正文，生成可校准、可追溯的知识候选。</p></div><span className="badge safety">抓取 + 候选生成</span></div>
            <label className="url-field">公开文章 URL<input type="url" required autoFocus value={urlForm.url} onChange={(e) => setUrlForm({ ...urlForm, url: e.target.value })} placeholder="https://example.com/article" /></label>
            <details className="import-options">
              <summary>可选设置</summary>
              <div className="form-grid compact-grid">
                <label>来源名称<input value={urlForm.source_name ?? ""} onChange={(e) => setUrlForm({ ...urlForm, source_name: e.target.value })} placeholder="默认使用网站名称" /></label>
                <label>渠道<select value={urlForm.channel} onChange={(e) => setUrlForm({ ...urlForm, channel: e.target.value as Channel })}>{Object.entries(channelLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                <label>语言代码<input value={urlForm.language_code ?? ""} onChange={(e) => setUrlForm({ ...urlForm, language_code: e.target.value })} placeholder="留空自动识别" /></label>
                <label>市场<input value={urlForm.market ?? ""} onChange={(e) => setUrlForm({ ...urlForm, market: e.target.value })} placeholder="例如 US（可选）" /></label>
              </div>
            </details>
            <label className="rights-check"><input type="checkbox" required checked={urlForm.rights_confirmed} onChange={(e) => setUrlForm({ ...urlForm, rights_confirmed: e.target.checked })} /><span><strong>我确认有权保存并在内部使用这份资料</strong><small>只支持公开 HTTP(S) 页面；本机、内网和登录页面不会被访问。</small></span></label>
            {submitting && importMode === "url" && (
              <div className="import-progress" role="status" aria-live="polite">
                <span className="progress-spinner" aria-hidden="true" />
                <div><strong>正在抓取正文并生成候选</strong><small>服务端会依次完成网页抓取、正文清洗和知识提取，可能需要几十秒，请勿关闭页面。</small></div>
              </div>
            )}
            {error && <p className="form-message error" role="alert">{error}</p>}
            {notice && <p className="form-message success" role="status">{notice}</p>}
            {lastResult && (
              <div className="import-result" aria-label="导入结果">
                <span>文章：<strong>{lastResult.document.title}</strong></span>
                <span>提取方式：<strong>{lastResult.extraction_method === "ai" ? `AI${lastResult.model ? ` · ${lastResult.model}` : ""}` : lastResult.extraction_method === "deterministic_fallback" ? "规则兜底" : "未返回"}</strong></span>
                <span>知识候选：<strong>{lastResult.claims_created} 条</strong></span>
                {lastResult.warning && <p className="result-warning">注意：{lastResult.warning}</p>}
              </div>
            )}
            <div className="form-actions"><button type="button" className="button secondary" onClick={() => setShowImporter(false)}>取消</button><button className="button primary" disabled={submitting}>{submitting && importMode === "url" ? "正在抓取并生成候选…" : "抓取并生成知识候选"}</button></div>
          </form>

          <details className="manual-fallback">
            <summary>网页无法抓取？改为手动沉淀正文</summary>
            <form onSubmit={(event) => void submitManualImport(event)}>
              <div className="section-heading"><div><h2>手动沉淀内容</h2><p>用于登录页面、强动态页面或网站阻止自动访问时。</p></div><span className="badge">备用方式</span></div>
              <div className="form-grid">
                <label>来源名称<input required value={form.source_name} onChange={(e) => setForm({ ...form, source_name: e.target.value })} placeholder="例如 官方博客" /></label>
                <label>文章标题<input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="输入文档标题" /></label>
                <label>原文 URL<input type="url" required value={form.canonical_url} onChange={(e) => setForm({ ...form, canonical_url: e.target.value })} placeholder="https://example.com/article" /></label>
                <label>渠道<select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value as Channel })}>{Object.entries(channelLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                <label>内容类型<select value={form.content_type} onChange={(e) => setForm({ ...form, content_type: e.target.value })}><option value="article">文章</option><option value="guide">指南</option><option value="case_study">案例</option><option value="transcript">转录稿</option><option value="post">帖子</option></select></label>
                <label>语言代码<input required value={form.language_code} onChange={(e) => setForm({ ...form, language_code: e.target.value })} placeholder="en" /></label>
                <label>市场<input required value={form.market} onChange={(e) => setForm({ ...form, market: e.target.value })} placeholder="US" /></label>
                <label>作者（可选）<input value={form.author ?? ""} onChange={(e) => setForm({ ...form, author: e.target.value })} /></label>
                <label>发布日期（可选）<input type="date" value={form.published_at ?? ""} onChange={(e) => setForm({ ...form, published_at: e.target.value })} /></label>
                <label className="full-span">正文或 Markdown<textarea required rows={10} value={form.raw_content} onChange={(e) => setForm({ ...form, raw_content: e.target.value })} placeholder="请粘贴你有权保存和使用的完整正文……" /></label>
              </div>
              <label className="rights-check"><input type="checkbox" required checked={form.rights_confirmed} onChange={(e) => setForm({ ...form, rights_confirmed: e.target.checked })} /><span><strong>我确认有权保存并在内部使用这份资料</strong><small>外部内容仅作为不可信数据处理，不会被当作系统指令执行。</small></span></label>
              <div className="form-actions"><button className="button secondary" disabled={submitting}>{submitting && importMode === "manual" ? "正在分析…" : "手动导入并生成候选"}</button></div>
            </form>
          </details>
        </div>
      )}

      {showBatchImporter && <BatchSourceWorkspace onImported={onImported} />}

      <div className="content-grid">
        <section className="card">
          <div className="section-heading"><div><h2>知识来源</h2><p>按渠道沉淀的授权来源</p></div><span className="badge">{sources.length} 个来源</span></div>
          {loading ? <LoadingRows /> : sources.length === 0 ? <EmptyState title="还没有知识来源" text="完成第一次 URL 导入后，来源会显示在这里。" /> : (
            <div className="table-wrap"><table><thead><tr><th>来源</th><th>渠道</th><th>文档</th><th>状态</th></tr></thead><tbody>{sources.map((source) => <tr key={source.id}><td><strong>{source.name}</strong><small>{source.domain ?? "手动来源"}</small></td><td><span className="badge channel">{channelLabels[source.channel]}</span></td><td>{source.document_count ?? documentCounts.get(source.id) ?? 0}</td><td><span className={`status-dot ${source.status === "active" ? "online" : ""}`} />{source.status}</td></tr>)}</tbody></table></div>
          )}
        </section>
        <section className="card">
          <div className="section-heading"><div><h2>最新沉淀内容</h2><p>正文已清洗，并按内容哈希去重</p></div><span className="badge">{documents.length} 篇文档</span></div>
          {loading ? <LoadingRows /> : documents.length === 0 ? <EmptyState title="还没有沉淀内容" text="粘贴第一篇公开文章 URL，开始建立品牌发现力知识库。" /> : (
            <div className="document-list">{documents.map((document) => <article className="document-row" key={document.id}><div><span className="badge channel">{document.content_type}</span><h3>{document.title}</h3><p>{document.market} · {document.language_code} · {formatDate(document.published_at ?? document.created_at)}</p></div><div className="document-count"><strong>{document.claim_count ?? "—"}</strong><small>知识候选</small></div></article>)}</div>
          )}
        </section>
      </div>
    </section>
  );
}

type BatchDateMode = "1" | "2" | "3" | "5" | "custom";
const BATCH_RUN_STORAGE_KEY = "knowledge.batchRunId";

const batchRunLabels: Record<BatchRun["status"], string> = {
  queued: "等待开始",
  running: "正在运行",
  completed: "已完成",
  failed: "运行失败",
  cancelling: "正在取消",
  cancelled: "已取消",
};

function BatchSourceWorkspace({ onImported }: { onImported: () => Promise<void> }) {
  const [form, setForm] = useState<Omit<BatchDiscoveryRequest, "years" | "date_from">>({
    seed_url: "",
    source_name: "",
    discovery_mode: "auto",
    include_unknown_dates: false,
    max_articles: 100,
    channel: "seo",
    language_code: "",
    market: "",
    rights_confirmed: false,
  });
  const [dateMode, setDateMode] = useState<BatchDateMode>("1");
  const [dateFrom, setDateFrom] = useState("");
  const [preview, setPreview] = useState<BatchPreviewResponse | null>(null);
  const [run, setRun] = useState<BatchRun | null>(null);
  const [runItems, setRunItems] = useState<BatchRunItem[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [error, setError] = useState("");

  const activeRun = run && ["queued", "running", "cancelling"].includes(run.status);

  useEffect(() => {
    const runId = window.localStorage.getItem(BATCH_RUN_STORAGE_KEY);
    if (!runId) return;
    let cancelled = false;
    void Promise.all([
      knowledgeApi.batchRun(runId),
      knowledgeApi.batchRunItems(runId),
    ]).then(([savedRun, savedItems]) => {
      if (!cancelled) {
        setRun(savedRun);
        setRunItems(savedItems);
      }
    }).catch(() => window.localStorage.removeItem(BATCH_RUN_STORAGE_KEY));
    return () => { cancelled = true; };
  }, []);

  function updateForm(patch: Partial<typeof form>) {
    setForm((current) => ({ ...current, ...patch }));
    setPreview(null);
    setError("");
  }

  function buildSpec(): BatchDiscoveryRequest | null {
    const base: BatchDiscoveryRequest = {
      ...form,
      seed_url: form.seed_url.trim(),
      source_name: form.source_name.trim(),
      language_code: form.language_code?.trim() || undefined,
      market: form.market?.trim() || undefined,
    };
    if (dateMode === "custom") {
      if (!dateFrom) return null;
      base.date_from = `${dateFrom}T00:00:00Z`;
    } else {
      base.years = Number(dateMode) as 1 | 2 | 3 | 5;
    }
    return base;
  }

  async function previewBatch(event: FormEvent) {
    event.preventDefault();
    const spec = buildSpec();
    if (!spec) {
      setError("请选择自定义起始日期。");
      return;
    }
    setPreviewing(true);
    setError("");
    setRun(null);
    setRunItems([]);
    window.localStorage.removeItem(BATCH_RUN_STORAGE_KEY);
    try {
      setPreview(await knowledgeApi.previewBatch(spec));
    } catch (previewError) {
      setPreview(null);
      setError(`批量预览失败：${errorMessage(previewError)}`);
    } finally {
      setPreviewing(false);
    }
  }

  async function startBatch() {
    const spec = buildSpec();
    if (!spec || !preview) return;
    setStarting(true);
    setError("");
    try {
      const nextRun = await knowledgeApi.startBatch(spec);
      setRun(nextRun);
      setRunItems([]);
      window.localStorage.setItem(BATCH_RUN_STORAGE_KEY, nextRun.id);
    } catch (startError) {
      setError(`无法启动批次：${errorMessage(startError)}`);
    } finally {
      setStarting(false);
    }
  }

  async function refreshRun() {
    if (!run) return;
    setRefreshing(true);
    try {
      const [nextRun, nextItems] = await Promise.all([
        knowledgeApi.batchRun(run.id),
        knowledgeApi.batchRunItems(run.id),
      ]);
      const justCompleted = nextRun.status === "completed" && run.status !== "completed";
      setRun(nextRun);
      setRunItems(nextItems);
      if (justCompleted) await onImported();
    } catch (refreshError) {
      setError(`无法刷新批次：${errorMessage(refreshError)}`);
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (!activeRun || !run) return;
    const timer = window.setInterval(() => void refreshRun(), 3000);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);

  async function cancelRun() {
    if (!run) return;
    setCancelling(true);
    setError("");
    try {
      setRun(await knowledgeApi.cancelBatch(run.id));
      setConfirmingCancel(false);
    } catch (cancelError) {
      setError(`无法取消批次：${errorMessage(cancelError)}`);
    } finally {
      setCancelling(false);
    }
  }

  const terminalCount = run ? run.counts.succeeded + run.counts.skipped + run.counts.failed + run.counts.cancelled : 0;
  const selectedCount = run ? terminalCount + run.counts.queued + run.counts.processing : 0;
  const progress = selectedCount > 0 ? Math.min(100, Math.round((terminalCount / selectedCount) * 100)) : 0;
  const attentionItems = runItems.filter((item) => item.status === "failed" || item.status === "skipped" || item.status === "retry");

  return (
    <section className="card batch-workspace">
      <div className="section-heading">
        <div><h2>批量来源</h2><p>从任意公开站点的入口、站点地图或 Feed 发现文章；预览确认后才会开始抓取和 AI 分析。</p></div>
        <span className="badge safety">先预览，再启动</span>
      </div>

      <form onSubmit={(event) => void previewBatch(event)}>
        <div className="form-grid batch-main-fields">
          <label className="batch-seed">Seed URL<input type="url" required value={form.seed_url} onChange={(e) => updateForm({ seed_url: e.target.value })} placeholder="https://example.com/blog/ 或 sitemap.xml" /></label>
          <label>来源名称<input required value={form.source_name} onChange={(e) => updateForm({ source_name: e.target.value })} placeholder="例如 官方博客" /></label>
          <label>发现模式<select value={form.discovery_mode} onChange={(e) => updateForm({ discovery_mode: e.target.value as BatchDiscoveryMode })}><option value="auto">自动识别</option><option value="sitemap">Sitemap</option><option value="feed">RSS / Atom Feed</option></select></label>
          <label>最大文章数<input type="number" required min={1} max={5000} value={form.max_articles} onChange={(e) => updateForm({ max_articles: Number(e.target.value) })} /></label>
        </div>

        <fieldset className="date-range">
          <legend>时间范围</legend>
          <div className="date-options">
            {(["1", "2", "3", "5"] as const).map((years) => <label key={years}><input type="radio" name="batch-date" checked={dateMode === years} onChange={() => { setDateMode(years); setPreview(null); }} />最近 {years} 年</label>)}
            <label><input type="radio" name="batch-date" checked={dateMode === "custom"} onChange={() => { setDateMode("custom"); setPreview(null); }} />自定义起始日期</label>
            {dateMode === "custom" && <input aria-label="自定义起始日期" type="date" required value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPreview(null); }} />}
          </div>
          <p>发布时间或修改时间任一满足范围，即视为符合时间条件。</p>
        </fieldset>

        <details className="import-options batch-options">
          <summary>更多筛选设置</summary>
          <div className="form-grid compact-grid">
            <label>渠道<select value={form.channel} onChange={(e) => updateForm({ channel: e.target.value as Channel })}>{Object.entries(channelLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>语言代码<input value={form.language_code ?? ""} onChange={(e) => updateForm({ language_code: e.target.value })} placeholder="留空自动识别" /></label>
            <label>市场<input value={form.market ?? ""} onChange={(e) => updateForm({ market: e.target.value })} placeholder="例如 US（可选）" /></label>
          </div>
        </details>

        <label className="batch-check"><input type="checkbox" checked={form.include_unknown_dates} onChange={(e) => updateForm({ include_unknown_dates: e.target.checked })} /><span><strong>包含日期未知的文章</strong><small>开启后，无法识别发布时间和修改时间的文章也会进入预览与抓取。</small></span></label>
        <label className="rights-check"><input type="checkbox" required checked={form.rights_confirmed} onChange={(e) => updateForm({ rights_confirmed: e.target.checked })} /><span><strong>我确认有权批量保存并在内部使用这个来源的资料</strong><small>只访问公开 HTTP(S) 页面；本机、内网和登录页面不会被访问。</small></span></label>

        {error && <p className="form-message error" role="alert">{error}</p>}
        <div className="form-actions"><button className="button secondary" disabled={previewing || Boolean(activeRun)}>{previewing ? "正在发现文章…" : "预览可导入文章"}</button></div>
      </form>

      {previewing && <div className="import-progress" role="status"><span className="progress-spinner" aria-hidden="true" /><div><strong>正在发现并筛选文章</strong><small>系统会检查 Sitemap、Feed 或站点入口；这一步不会保存正文或调用 AI。</small></div></div>}

      {preview && !run && (
        <section className="batch-preview" aria-label="批量导入预览">
          <div className="batch-metrics">
            <BatchMetric label="发现" value={preview.discovered_total} />
            <BatchMetric label="符合时间" value={preview.eligible_total} />
            <BatchMetric label="日期未知" value={preview.unknown_date_total} />
            <BatchMetric label="本次选择" value={preview.selected_total} tone="selected" />
          </div>
          <p className="discovery-summary">发现方式：<strong>{preview.discovery_mode === "sitemap" ? "Sitemap" : preview.discovery_mode === "feed" ? "RSS / Atom Feed" : "自动识别"}</strong></p>
          {preview.warnings.length > 0 && <div className="batch-warnings"><strong>预览警告</strong>{preview.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>}
          {preview.sample_items.length > 0 && (
            <div className="batch-samples"><h3>文章样例</h3>{preview.sample_items.map((item) => <article key={item.url}><div><strong>{item.title || "预览阶段暂未抓取标题"}</strong><a href={item.url} target="_blank" rel="noreferrer">{item.url}</a></div><span>{formatDate(item.published_at ?? item.modified_at)} · {item.date_status === "published" ? "发布时间" : item.date_status === "modified" ? "修改时间" : "日期未知"}</span></article>)}</div>
          )}
          <div className="batch-confirm"><p><strong>确认启动后</strong>系统会依次抓取正文、清洗内容并调用 AI 生成待审核知识卡片。</p><button className="button primary" disabled={starting || preview.selected_total === 0} onClick={() => void startBatch()}>{starting ? "正在创建批次…" : `确认启动 ${preview.selected_total} 篇`}</button></div>
        </section>
      )}

      {run && (
        <section className="batch-run" aria-label="批次进度">
          <div className="batch-run-head"><div><span className={`batch-status ${run.status}`}>{batchRunLabels[run.status]}</span><strong>批次 {run.id.slice(0, 8)}</strong><small>更新于 {new Date(run.updated_at).toLocaleTimeString("zh-CN")}</small></div><div><button className="button secondary" disabled={refreshing} onClick={() => void refreshRun()}>{refreshing ? "正在刷新…" : "刷新"}</button>{activeRun && <button className="button danger" disabled={cancelling} onClick={() => setConfirmingCancel(true)}>取消批次</button>}</div></div>
          {confirmingCancel && activeRun && <div className="cancel-confirm"><p><strong>确认取消这个批次？</strong>尚未处理的 URL 将被取消；已经完成的文章和知识候选不会回滚。</p><div><button className="button secondary" onClick={() => setConfirmingCancel(false)}>继续运行</button><button className="button danger" disabled={cancelling} onClick={() => void cancelRun()}>{cancelling ? "正在取消…" : "确认取消剩余 URL"}</button></div></div>}
          <div className="progress-track" aria-label={`批次进度 ${progress}%`}><span style={{ width: `${progress}%` }} /></div>
          <div className="batch-funnel">
            <BatchMetric label="已发现" value={run.counts.discovered} />
            <BatchMetric label="排队" value={run.counts.queued} />
            <BatchMetric label="处理中" value={run.counts.processing} tone="selected" />
            <BatchMetric label="成功" value={run.counts.succeeded} tone="success" />
            <BatchMetric label="跳过" value={run.counts.skipped} />
            <BatchMetric label="失败" value={run.counts.failed} tone="danger" />
            <BatchMetric label="取消" value={run.counts.cancelled} />
          </div>
          {run.error && <p className="form-message error" role="alert">批次失败：{run.error}</p>}
          {run.warnings.length > 0 && <div className="batch-warnings"><strong>运行警告</strong>{run.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>}
          {attentionItems.length > 0 && <details className="batch-issues"><summary>查看失败、跳过或重试项（{attentionItems.length}）</summary><div>{attentionItems.slice(0, 20).map((item) => <article key={item.id}><span className={`item-status ${item.status}`}>{item.status === "failed" ? "失败" : item.status === "skipped" ? "跳过" : "重试"}</span><a href={item.url} target="_blank" rel="noreferrer">{item.url}</a><small>{item.error || `已尝试 ${item.attempts} 次`}</small></article>)}</div></details>}
        </section>
      )}
    </section>
  );
}

function BatchMetric({ label, value, tone = "" }: { label: string; value: number; tone?: string }) {
  return <div className={`batch-metric ${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}

function Stat({ label, value, hint, tone = "blue", loading, onClick }: { label: string; value?: number; hint: string; tone?: string; loading: boolean; onClick?: () => void }) {
  const content = <><span>{label}</span><strong>{loading ? "—" : (value ?? 0)}</strong><small>{hint}</small></>;
  if (!onClick) return <div className={`stat ${tone}`}>{content}</div>;
  return <button type="button" className={`stat stat-button ${tone}`} disabled={loading} onClick={onClick} aria-label={`${label}：${value ?? 0}，${hint}`}>{content}</button>;
}

function DiscoveryMap() {
  const pillars = [
    { index: "01", title: "被发现", text: "需求、关键词、Prompt 与分发触点" },
    { index: "02", title: "被理解", text: "内容、结构与技术可读性" },
    { index: "03", title: "被信任", text: "证据、经验、权威与口碑" },
    { index: "04", title: "被选择", text: "点击、转化、ROI 与反馈" },
  ];

  return (
    <section className="discovery-map" aria-label="品牌发现力地图">
      <div className="discovery-map-heading"><div><span className="eyebrow">Knowledge compass</span><h2>品牌发现力地图</h2><p>导入、校准和检索都围绕一个目标：让目标用户发现、理解、信任并选择品牌。</p></div><span className="badge safety">知识导航</span></div>
      <div className="discovery-pillars">{pillars.map((pillar) => <article key={pillar.index}><span>{pillar.index}</span><div><strong>{pillar.title}</strong><p>{pillar.text}</p></div></article>)}</div>
    </section>
  );
}

function ClaimArchive({
  status,
  claims,
  loading,
  error,
  filters,
  page,
  pageSize,
  total,
  sources,
  onBack,
  onRefresh,
  onFiltersChange,
  onPageChange,
}: {
  status: ArchiveStatus;
  claims: Claim[];
  loading: boolean;
  error: string;
  filters: ArchiveFilters;
  page: number;
  pageSize: number;
  total: number;
  sources: Source[];
  onBack: () => void;
  onRefresh: () => void;
  onFiltersChange: (filters: ArchiveFilters) => void;
  onPageChange: (page: number) => void;
}) {
  const approved = status === "approved";
  const title = approved ? "可用策略知识" : "已排除知识";
  const description = approved
    ? "这些知识已经通过人工校准，可进入品牌发现策略与 Agent 检索。"
    : "这些知识已被人工或质量门禁排除，不会进入策略检索。";
  const [draftFilters, setDraftFilters] = useState(filters);
  const [rejectionNotes, setRejectionNotes] = useState<Record<string, string>>({});
  const [workingClaim, setWorkingClaim] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const firstItem = total === 0 ? 0 : page * pageSize + 1;
  const lastItem = Math.min((page + 1) * pageSize, total);

  useEffect(() => {
    setDraftFilters(filters);
  }, [filters]);

  function submitFilters(event: FormEvent) {
    event.preventDefault();
    onFiltersChange(draftFilters);
  }

  function clearFilters() {
    setDraftFilters(EMPTY_ARCHIVE_FILTERS);
    onFiltersChange(EMPTY_ARCHIVE_FILTERS);
  }

  async function rejectApprovedClaim(claim: Claim) {
    if (!window.confirm("拒绝后，这条知识会立即从 Agent 检索结果中移除。确定继续吗？")) return;
    setWorkingClaim(claim.id);
    setActionError("");
    try {
      await knowledgeApi.reviewClaim(claim.id, {
        decision: "rejected",
        reviewer: "local-user",
        note: rejectionNotes[claim.id]?.trim() || "人工撤回已批准知识",
      });
      await onRefresh();
    } catch (error) {
      setActionError(`拒绝失败：${errorMessage(error)}`);
    } finally {
      setWorkingClaim(null);
    }
  }

  return (
    <section className={`card claim-archive ${approved ? "approved-archive" : "rejected-archive"}`} aria-label={title}>
      <div className="section-heading">
        <div><h2>{title}</h2><p>{description}</p></div>
        <div className="archive-actions"><button className="button secondary" onClick={onRefresh} disabled={loading}>{loading ? "正在加载…" : "刷新"}</button><button className="button secondary" onClick={onBack}>收起</button></div>
      </div>
      <form className="archive-filters" onSubmit={submitFilters}>
        <label className="archive-search">搜索知识、文档或来源<input value={draftFilters.query} onChange={(event) => setDraftFilters({ ...draftFilters, query: event.target.value })} placeholder="例如：search intent" /></label>
        <label>来源<select value={draftFilters.sourceId} onChange={(event) => setDraftFilters({ ...draftFilters, sourceId: event.target.value })}><option value="">全部来源</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
        <label>入库日期从<input type="date" value={draftFilters.dateFrom} onChange={(event) => setDraftFilters({ ...draftFilters, dateFrom: event.target.value })} /></label>
        <label>到<input type="date" value={draftFilters.dateTo} onChange={(event) => setDraftFilters({ ...draftFilters, dateTo: event.target.value })} /></label>
        <div className="archive-filter-actions"><button className="button primary" type="submit" disabled={loading}>应用筛选</button><button className="button secondary" type="button" onClick={clearFilters} disabled={loading}>清除</button></div>
      </form>
      {error && <p className="form-message error" role="alert">{error}</p>}
      {actionError && <p className="form-message error" role="alert">{actionError}</p>}
      {loading ? <LoadingRows /> : claims.length === 0 ? <EmptyState title={`还没有${title}`} text={approved ? "批准后的知识会出现在这里。" : "被拒绝的知识会出现在这里。"} /> : (
        <div className="archive-list">
          {claims.map((claim) => (
            <article className="archive-item" key={claim.id}>
              <div className="archive-item-head"><span className={`quality-badge ${approved ? "keep" : "reject"}`}>{approved ? "已批准" : "已拒绝"}</span><span>{formatDate(claim.reviewed_at ?? claim.created_at)}</span></div>
              <h3>{claim.statement}</h3>
              {claim.recommended_action && <p className="archive-action"><strong>建议动作：</strong>{claim.recommended_action}</p>}
              {claim.evidence.length > 0 && <div className="archive-evidence"><strong>证据</strong>{claim.evidence.map((evidence) => <p key={evidence.id}>“{evidence.excerpt}” <span>{evidence.locator || "原文"}</span></p>)}</div>}
              <div className="archive-meta"><span>{claim.source.name}</span><strong>{claim.document.title}</strong>{claim.reviewed_by && <span>校准：{claim.reviewed_by}</span>}{claim.review_note && <span>备注：{claim.review_note}</span>}</div>
              {approved && <div className="archive-review-action"><label>撤回原因（可选）<textarea rows={2} value={rejectionNotes[claim.id] ?? ""} onChange={(event) => setRejectionNotes((state) => ({ ...state, [claim.id]: event.target.value }))} placeholder="例如：复核后发现证据不足或结论不适用" /></label><button type="button" className="button danger" disabled={workingClaim === claim.id} onClick={() => void rejectApprovedClaim(claim)}>{workingClaim === claim.id ? "正在拒绝…" : "拒绝这条已批准知识"}</button></div>}
            </article>
          ))}
        </div>
      )}
      {!loading && total > 0 && <div className="archive-pagination"><span>显示 {firstItem}–{lastItem} 条，共 {total} 条 · 第 {page + 1} / {totalPages} 页</span><div><button className="button secondary" disabled={page === 0 || loading} onClick={() => onPageChange(page - 1)}>上一页</button><button className="button secondary" disabled={page + 1 >= totalPages || loading} onClick={() => onPageChange(page + 1)}>下一页</button></div></div>}
    </section>
  );
}

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

const QUALITY_RUN_STORAGE_KEY = "knowledge.latestQualityRunId";

const qualityDecisionLabels: Record<QualityDecision, string> = {
  unreviewed: "尚未预审",
  keep: "建议保留",
  reject: "建议筛除",
  uncertain: "留给人工",
  error: "预审异常",
};

function AIQualityPanel({ onApplied }: { onApplied: () => Promise<void> }) {
  const [limitDocuments, setLimitDocuments] = useState(20);
  const [includeReviewed, setIncludeReviewed] = useState(false);
  const [run, setRun] = useState<QualityRun | null>(null);
  const [items, setItems] = useState<QualityRunItem[]>([]);
  const [screenedClaims, setScreenedClaims] = useState<Claim[]>([]);
  const [starting, setStarting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [restoring, setRestoring] = useState<Record<string, boolean>>({});
  const [confirmApply, setConfirmApply] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [error, setError] = useState("");

  const activeRun = run && ["queued", "running", "cancelling"].includes(run.status);

  async function loadScreenedClaims() {
    try {
      const claims = await knowledgeApi.claimsByQuality("reject", "rejected");
      setScreenedClaims(claims.filter((claim) =>
        claim.review_status === "rejected" && claim.reviewed_by?.startsWith("ai-quality:")
      ));
    } catch {
      // The primary review queue remains usable if this secondary list fails.
    }
  }

  async function loadQualityRun(runId: string, showSpinner = true) {
    if (showSpinner) setRefreshing(true);
    try {
      const [nextRun, nextItems] = await Promise.all([
        knowledgeApi.qualityRun(runId),
        knowledgeApi.qualityRunItems(runId),
      ]);
      setRun(nextRun);
      setItems(nextItems);
      setError("");
      return nextRun;
    } catch (loadError) {
      setError(`无法恢复 AI 预审任务：${errorMessage(loadError)}`);
      return null;
    } finally {
      if (showSpinner) setRefreshing(false);
    }
  }

  useEffect(() => {
    void loadScreenedClaims();
    try {
      const runId = window.localStorage.getItem(QUALITY_RUN_STORAGE_KEY);
      if (runId) {
        void loadQualityRun(runId);
      } else {
        void knowledgeApi.latestQualityRun().then((latestRun) => {
          try {
            window.localStorage.setItem(QUALITY_RUN_STORAGE_KEY, latestRun.id);
          } catch {
            // The durable run is still available from the API for this session.
          }
          return loadQualityRun(latestRun.id);
        }).catch(() => undefined);
      }
    } catch {
      // localStorage may be unavailable in privacy-restricted browser contexts.
      void knowledgeApi.latestQualityRun()
        .then((latestRun) => loadQualityRun(latestRun.id))
        .catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    if (!activeRun || !run) return;
    const timer = window.setInterval(() => void loadQualityRun(run.id, false), 3000);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);

  async function startQualityRun() {
    setStarting(true);
    setError("");
    setConfirmApply(false);
    try {
      const nextRun = await knowledgeApi.startQualityRun({
        limit_documents: limitDocuments,
        include_reviewed: includeReviewed,
      });
      setRun(nextRun);
      setItems([]);
      try {
        window.localStorage.setItem(QUALITY_RUN_STORAGE_KEY, nextRun.id);
      } catch {
        // The run remains visible for this page session.
      }
    } catch (startError) {
      setError(`无法启动 AI 预审：${errorMessage(startError)}`);
    } finally {
      setStarting(false);
    }
  }

  async function applySafeRejections() {
    if (!run) return;
    setApplying(true);
    setError("");
    try {
      setRun(await knowledgeApi.applyQualityRun(run.id));
      setConfirmApply(false);
      await Promise.all([loadScreenedClaims(), onApplied()]);
    } catch (applyError) {
      setError(`应用安全拒绝失败：${errorMessage(applyError)}`);
    } finally {
      setApplying(false);
    }
  }

  async function cancelQualityRun() {
    if (!run) return;
    setCancelling(true);
    setError("");
    try {
      setRun(await knowledgeApi.cancelQualityRun(run.id));
      setConfirmCancel(false);
    } catch (cancelError) {
      setError(`取消 AI 预审失败：${errorMessage(cancelError)}`);
    } finally {
      setCancelling(false);
    }
  }

  async function restoreClaim(claimId: string) {
    setRestoring((current) => ({ ...current, [claimId]: true }));
    setError("");
    try {
      await knowledgeApi.restoreQualityClaim(claimId);
      setScreenedClaims((current) => current.filter((claim) => claim.id !== claimId));
      await onApplied();
    } catch (restoreError) {
      setError(`恢复待审失败：${errorMessage(restoreError)}`);
    } finally {
      setRestoring((current) => ({ ...current, [claimId]: false }));
    }
  }

  const allResults = items.flatMap((item) => item.results.map((result) => ({ ...result, document: item })));
  const decisionCounts = {
    keep: allResults.filter((result) => result.effective_decision === "keep").length,
    reject: allResults.filter((result) => result.effective_decision === "reject").length,
    uncertain: allResults.filter((result) => result.effective_decision === "uncertain").length,
    error: items.filter((item) => item.status === "failed").length,
  };
  const processed = run ? run.counts.completed + run.counts.failed + run.counts.cancelled : 0;
  const total = run ? processed + run.counts.queued + run.counts.processing : 0;
  const progress = total > 0 ? Math.round((processed / total) * 100) : 0;

  return (
    <section className="card quality-panel">
      <div className="section-heading"><div><h2>AI 预审</h2><p>先做 dry-run：AI 只提出筛选建议，不会批准知识，也不会改变人工审核状态。</p></div><span className="badge safety">人工批准门禁不变</span></div>
      <div className="quality-policy"><strong>预审重点筛除</strong><span>文章摘要</span><span>研究规模</span><span>孤立易失数字</span><span>宣传语</span><span>无复用行动</span><small>文档级会先识别离题、非文章、抓取噪声和纯工具 UI；冲突、歧义和边界不清的内容标记为 uncertain，继续交给人工。</small></div>
      <div className="quality-controls">
        <label>最多文档数<input type="number" min={1} max={1000} value={limitDocuments} onChange={(e) => setLimitDocuments(Number(e.target.value))} /></label>
        <label className="quality-check"><input type="checkbox" checked={includeReviewed} onChange={(e) => setIncludeReviewed(e.target.checked)} /><span>包含已预审文档</span></label>
        <button className="button primary" disabled={starting || Boolean(activeRun)} onClick={() => void startQualityRun()}>{starting ? "正在创建 dry-run…" : "启动 AI dry-run"}</button>
      </div>
      {error && <p className="form-message error" role="alert">{error}</p>}

      {run && (
        <div className="quality-run">
          <div className="quality-run-head"><div><span className={`quality-run-status ${run.status}`}>{run.status === "applied" ? "已应用安全拒绝" : run.status === "completed" ? "dry-run 已完成" : run.status === "running" ? "正在预审" : run.status === "queued" ? "等待开始" : run.status === "failed" ? "运行失败" : run.status === "cancelled" ? "已取消" : "正在取消"}</span><strong>任务 {run.id.slice(0, 8)}</strong><small>文档 {processed}/{total} · {formatDate(run.updated_at)}</small></div><div><button className="button secondary" disabled={refreshing} onClick={() => void loadQualityRun(run.id)}>{refreshing ? "正在刷新…" : "刷新"}</button>{activeRun && <button className="button danger" onClick={() => setConfirmCancel(true)}>取消</button>}</div></div>
          {confirmCancel && activeRun && <div className="cancel-confirm"><p><strong>确认取消 AI 预审？</strong>尚未处理的文档会取消；已经完成的 dry-run 结果不会自动应用，也不会改变人工审核状态。</p><div><button className="button secondary" onClick={() => setConfirmCancel(false)}>继续运行</button><button className="button danger" disabled={cancelling} onClick={() => void cancelQualityRun()}>{cancelling ? "正在取消…" : "确认取消剩余文档"}</button></div></div>}
          <div className="progress-track" aria-label={`预审进度 ${progress}%`}><span style={{ width: `${progress}%` }} /></div>
          <div className="quality-metrics"><BatchMetric label="文档完成" value={run.counts.completed} /><BatchMetric label="建议保留" value={decisionCounts.keep} tone="success" /><BatchMetric label="安全拒绝" value={decisionCounts.reject} tone="danger" /><BatchMetric label="留给人工" value={decisionCounts.uncertain} tone="selected" /><BatchMetric label="异常" value={decisionCounts.error} /></div>
          {run.error && <p className="form-message error">预审失败：{run.error}</p>}

          {items.some((item) => item.document_decision || item.document_reason_codes.length > 0) && <div className="document-quality"><h3>文档级判断</h3>{items.filter((item) => item.document_decision || item.document_reason_codes.length > 0).slice(0, 8).map((item) => <article key={item.id}><div><span className={`quality-badge ${item.document_decision || "uncertain"}`}>{item.document_decision ? qualityDecisionLabels[item.document_decision] : "文档判断"}</span><strong>{item.document_title}</strong></div><p>{item.document_rationale || "该文档已完成页面类型和主题相关性检查。"}</p><div>{item.document_reason_codes.map((reason) => <span className="reason-code" key={reason}>{reason}</span>)}</div></article>)}</div>}

          {allResults.length > 0 && <div className="quality-samples">{(["keep", "reject", "uncertain"] as const).map((decision) => <section key={decision}><h3>{qualityDecisionLabels[decision]} · {decisionCounts[decision]}</h3>{allResults.filter((result) => result.effective_decision === decision).slice(0, 4).map((result) => <article key={result.claim_id}><small>{result.document.document_title}</small><strong>{result.statement}</strong><p>{result.rationale}</p><div className="quality-scores"><span>效用分 {Math.round(result.utility_score * 100)}</span><span>预审置信度 {Math.round(result.reviewer_confidence * 100)}%</span></div><div>{result.reason_codes.map((reason) => <span className="reason-code" key={reason}>{reason}</span>)}</div></article>)}</section>)}</div>}
          {items.some((item) => item.status === "failed") && <div className="quality-errors"><strong>异常样例</strong>{items.filter((item) => item.status === "failed").slice(0, 5).map((item) => <p key={item.id}><span>{item.document_title}</span>{item.error || "AI 预审未完成，可稍后重新运行。"}</p>)}</div>}

          {run.status === "completed" && <div className="quality-apply"><div><strong>dry-run 已完成，人工状态尚未改变</strong><p>只会应用后端安全策略判定的高置信明显垃圾；keep 不等于 approved，uncertain 始终保留给人工。</p></div><button className="button danger" disabled={run.counts.auto_rejects === 0} onClick={() => setConfirmApply(true)}>应用 {run.counts.auto_rejects} 条安全拒绝</button></div>}
          {confirmApply && run.status === "completed" && <div className="apply-confirm"><p><strong>再次确认应用安全拒绝</strong>这会将高置信明显垃圾从 pending 移出。其他卡片仍由人工审核，之后可逐张恢复。</p><div><button className="button secondary" onClick={() => setConfirmApply(false)}>取消</button><button className="button danger" disabled={applying} onClick={() => void applySafeRejections()}>{applying ? "正在应用…" : `确认筛除 ${run.counts.auto_rejects} 条`}</button></div></div>}
        </div>
      )}

      {screenedClaims.length > 0 && <details className="screened-claims"><summary>查看 AI 已筛除卡片（{screenedClaims.length}）</summary><div>{screenedClaims.map((claim) => <article key={claim.id}><div><span className="quality-badge reject">已筛除</span><strong>{claim.statement}</strong><small>{claim.document.title}</small></div><div>{claim.quality_reasons?.map((reason) => <span className="reason-code" key={reason}>{reason}</span>)}</div><button className="button secondary" disabled={restoring[claim.id]} onClick={() => void restoreClaim(claim.id)}>{restoring[claim.id] ? "正在恢复…" : "恢复待审"}</button></article>)}</div></details>}
    </section>
  );
}

function QualitySummary({ claim }: { claim: Claim }) {
  const status = claim.quality_status ?? "unreviewed";
  if (status === "unreviewed") return null;
  return <div className="claim-quality"><div><span className={`quality-badge ${status}`}>{qualityDecisionLabels[status]}</span>{claim.quality_utility_score != null && <span>效用分 {Math.round(claim.quality_utility_score * 100)}</span>}{claim.quality_reviewer_confidence != null && <span>预审置信度 {Math.round(claim.quality_reviewer_confidence * 100)}%</span>}</div>{claim.quality_note && <p>{claim.quality_note}</p>}{claim.quality_reasons && claim.quality_reasons.length > 0 && <div>{claim.quality_reasons.map((reason) => <span className="reason-code" key={reason}>{reason}</span>)}</div>}{status === "keep" && <small>AI 建议保留不等于批准，仍需人工核对证据。</small>}</div>;
}

function ReviewWorkspace({ claims, loading, onReviewed, onReload }: { claims: Claim[]; loading: boolean; onReviewed: (claimId: string) => Promise<void>; onReload: () => Promise<void> }) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [working, setWorking] = useState<Record<string, ReviewDecision | undefined>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  async function review(claim: Claim, decision: ReviewDecision) {
    setWorking((state) => ({ ...state, [claim.id]: decision }));
    setErrors((state) => ({ ...state, [claim.id]: "" }));
    try {
      await knowledgeApi.reviewClaim(claim.id, { decision, reviewer: "local-user", note: notes[claim.id]?.trim() || null });
      await onReviewed(claim.id);
    } catch (error) {
      setErrors((state) => ({ ...state, [claim.id]: errorMessage(error) }));
    } finally {
      setWorking((state) => ({ ...state, [claim.id]: undefined }));
    }
  }

  return (
    <section>
      <PageHeading title="知识校准" description="核对证据、适用条件与例外，让可信知识进入品牌发现策略。" action={<span className="queue-count">{claims.length} 条待校准</span>} />
      <div className="review-banner"><span aria-hidden="true">◎</span><p><strong>证据门禁已启用</strong>pending 和 rejected 候选永远不会进入策略检索。批准前请核对原文、证据位置和适用条件。</p></div>
      <AIQualityPanel onApplied={onReload} />
      {loading ? <div className="review-grid"><LoadingCard /><LoadingCard /></div> : claims.length === 0 ? <div className="card"><EmptyState title="校准队列已清空" text="新导入的文档生成候选后，会在这里等待人工校准。" /></div> : (
        <div className="review-grid">{claims.map((claim) => (
          <article className="card claim-card" key={claim.id}>
            <div className="claim-top"><div><span className="badge channel">{channelLabels[claim.channel]}</span><span className="badge">{claim.knowledge_type}</span></div><span className="confidence">提取置信度 {(claim.confidence * 100).toFixed(0)}%</span></div>
            <h2>{claim.statement}</h2>
            <QualitySummary claim={claim} />
            {claim.recommended_action && <p className="recommended"><strong>建议动作</strong>{claim.recommended_action}</p>}
            <dl className="conditions"><div><dt>适用条件</dt><dd>{formatStructuredList(claim.conditions)}</dd></div><div><dt>例外</dt><dd>{claim.exceptions.length ? formatStructuredList(claim.exceptions) : "暂无"}</dd></div></dl>
            <div className="evidence-box"><span>证据</span>{claim.evidence.length ? claim.evidence.map((evidence) => <blockquote key={evidence.id}>“{evidence.excerpt}”<cite>{evidence.locator || "原文"}</cite></blockquote>) : <p>该候选暂未提供证据摘录。</p>}</div>
            <div className="provenance"><div><strong>{claim.source.name}</strong><span>{claim.document.title}</span></div>{claim.document.canonical_url && <a href={claim.document.canonical_url} target="_blank" rel="noreferrer">查看原文 ↗</a>}</div>
            <label className="review-note">校准备注（可选）<textarea rows={2} value={notes[claim.id] ?? ""} onChange={(e) => setNotes((state) => ({ ...state, [claim.id]: e.target.value }))} placeholder="记录保留或排除的依据" /></label>
            {errors[claim.id] && <p className="form-message error" role="alert">{errors[claim.id]}</p>}
            <div className="review-actions"><button className="button danger" disabled={Boolean(working[claim.id])} onClick={() => void review(claim, "rejected")}>{working[claim.id] === "rejected" ? "正在排除…" : "排除"}</button><button className="button approve" disabled={Boolean(working[claim.id])} onClick={() => void review(claim, "approved")}>{working[claim.id] === "approved" ? "正在校准…" : "校准并开放策略"}</button></div>
          </article>
        ))}</div>
      )}
    </section>
  );
}

function RetrievalWorkspace() {
  const [form, setForm] = useState<RetrieveRequest>({ query: "", channel: "seo", language_code: "en", limit: 5 });
  const [pack, setPack] = useState<KnowledgePack | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function retrieve(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      setPack(await knowledgeApi.retrieve(form));
    } catch (retrieveError) {
      setError(errorMessage(retrieveError));
      setPack(null);
    } finally {
      setLoading(false);
    }
  }

  const preview = pack ? {
    query: pack.query,
    filters: { channel: pack.channel, market: pack.market, language_code: pack.language_code },
    claims: pack.items.map((item) => ({
      claim_id: item.claim.id,
      statement: item.claim.statement,
      evidence: item.evidence.map((evidence) => ({ excerpt: evidence.excerpt, locator: evidence.locator })),
      source: item.source.name,
      score: item.score,
    })),
  } : null;

  return (
    <section>
      <PageHeading title="策略检索" description="验证已校准知识，并预览任务 Agent 将使用的可追溯知识包。" />
      <div className="lab-grid">
        <form className="card lab-form" onSubmit={(event) => void retrieve(event)}>
          <div className="section-heading"><div><h2>检索条件</h2><p>按渠道和语言查找可用策略知识</p></div></div>
          <label>任务问题<textarea required rows={5} value={form.query} onChange={(e) => setForm({ ...form, query: e.target.value })} placeholder="例如：如何判断关键词的搜索意图？" /></label>
          <div className="field-pair"><label>渠道<select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value as Channel })}>{Object.entries(channelLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>返回条数<input type="number" min={1} max={20} value={form.limit} onChange={(e) => setForm({ ...form, limit: Number(e.target.value) })} /></label></div>
          <label>语言<input required value={form.language_code} onChange={(e) => setForm({ ...form, language_code: e.target.value })} /></label>
          <div className="approved-only"><span>✓</span><p><strong>Calibrated only</strong>服务端只会返回已通过人工校准的知识。</p></div>
          {error && <p className="form-message error" role="alert">{error}</p>}
          <button className="button primary wide" disabled={loading}>{loading ? "正在检索知识库…" : "检索策略知识"}</button>
        </form>

        <div className="results-column">
          <section className="card results-card">
            <div className="section-heading"><div><h2>检索结果</h2><p>{pack ? `${channelLabels[pack.channel]} · 所有市场 / ${pack.language_code}` : "运行检索后显示可追溯结果"}</p></div>{pack && <span className="badge">{pack.items.length} 条知识</span>}</div>
            {loading ? <LoadingRows /> : !pack ? <EmptyState title="等待第一次检索" text="填写左侧任务问题，查看 Agent 实际会获得的策略知识。" /> : pack.items.length === 0 ? <EmptyState title="没有匹配的可用知识" text="尝试调整关键词或过滤条件，也可以先沉淀并校准相关来源。" /> : (
              <div className="result-list">{pack.items.map((item, index) => <article className="result-item" key={item.claim.id}><div className="result-number">{String(index + 1).padStart(2, "0")}</div><div className="result-body"><div className="result-top"><span className="badge channel">{item.claim.knowledge_type}</span><strong>score {item.score.toFixed(3)}</strong></div><h3>{item.claim.statement}</h3>{item.match_reason && <p className="match-reason">命中原因：{item.match_reason}</p>}<div className="result-evidence">{item.evidence.map((evidence) => <p key={evidence.id}>“{evidence.excerpt}” <span>{evidence.locator || "原文"}</span></p>)}</div><div className="result-source"><span>{item.source.name}</span><strong>{item.document.title}</strong>{item.document.canonical_url && <a href={item.document.canonical_url} target="_blank" rel="noreferrer">原文 ↗</a>}</div></div></article>)}</div>
            )}
          </section>
          {preview && <section className="card pack-preview"><div className="section-heading"><div><h2>KnowledgePack 预览</h2><p>后台接入时可直接消费的可追溯数据</p></div><span className="badge safety">不可信数据区</span></div><pre>{JSON.stringify(preview, null, 2)}</pre></section>}
        </div>
      </div>
    </section>
  );
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="empty"><span aria-hidden="true">◇</span><strong>{title}</strong><p>{text}</p></div>;
}

function LoadingRows() {
  return <div className="loading-rows" aria-label="正在加载"><span /><span /><span /></div>;
}

function LoadingCard() {
  return <div className="card loading-card" aria-label="正在加载"><span /><span /><span /><span /></div>;
}

export default App;
