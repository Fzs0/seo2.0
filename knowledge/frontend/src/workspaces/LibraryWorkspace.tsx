import { FormEvent, useEffect, useMemo, useState } from "react";
import { knowledgeApi } from "../api/knowledge";
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
  Overview,
  ReviewDecision,
  Source,
} from "../types/knowledge";
import {
  BatchMetric,
  channelLabels,
  EmptyState,
  errorMessage,
  formatDate,
  LoadingRows,
  PageHeading,
} from "./shared";

type ArchiveStatus = Extract<ReviewDecision, "approved" | "rejected">;
type ArchiveFilters = { query: string; sourceId: string; dateFrom: string; dateTo: string };

const ARCHIVE_PAGE_SIZE = 12;
const EMPTY_ARCHIVE_FILTERS: ArchiveFilters = { query: "", sourceId: "", dateFrom: "", dateTo: "" };

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

export { LibraryWorkspace };
