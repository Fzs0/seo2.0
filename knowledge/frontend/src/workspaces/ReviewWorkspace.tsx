import { useEffect, useState } from "react";
import { knowledgeApi } from "../api/knowledge";
import type {
  Claim,
  QualityDecision,
  QualityRun,
  QualityRunItem,
  ReviewDecision,
} from "../types/knowledge";
import {
  BatchMetric,
  channelLabels,
  EmptyState,
  errorMessage,
  formatDate,
  formatStructuredList,
  LoadingCard,
  PageHeading,
} from "./shared";

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

export { ReviewWorkspace };
