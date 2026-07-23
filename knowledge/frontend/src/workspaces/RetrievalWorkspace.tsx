import { FormEvent, useState } from "react";
import { knowledgeApi } from "../api/knowledge";
import type { Channel, KnowledgePack, RetrieveRequest } from "../types/knowledge";
import { channelLabels, EmptyState, errorMessage, LoadingRows, PageHeading } from "./shared";

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

export { RetrievalWorkspace };
