import { useCallback, useEffect, useState } from "react";
import { getApiBaseUrl, knowledgeApi } from "./api/knowledge";
import type { Claim, Document, Overview, Source } from "./types/knowledge";
import { LibraryWorkspace } from "./workspaces/LibraryWorkspace";
import { RetrievalWorkspace } from "./workspaces/RetrievalWorkspace";
import { ReviewWorkspace } from "./workspaces/ReviewWorkspace";
import { SignalsWorkspace } from "./workspaces/SignalsWorkspace";
import { errorMessage } from "./workspaces/shared";

type Workspace = "library" | "signals" | "review" | "lab";

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

export default App;
