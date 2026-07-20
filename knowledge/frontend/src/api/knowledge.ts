import type {
  BatchDiscoveryRequest,
  BatchPreviewResponse,
  BatchRun,
  BatchRunItem,
  Claim,
  Document,
  ImportDocumentRequest,
  ImportDocumentResponse,
  ImportUrlRequest,
  KnowledgePack,
  Overview,
  KnowledgeClaim,
  QualityRun,
  QualityRunItem,
  RetrieveRequest,
  ReviewClaimRequest,
  Source,
  StartQualityRunRequest,
  MarketSignal,
  SignalImportRequest,
  SignalImportResponse,
  SignalOverview,
  SignalCrawlJob,
  SignalCrawlJobRequest,
  SignalCrawlPreview,
  SignalCrawlRun,
} from "../types/knowledge";

interface ListResponse<T> {
  items: T[];
}

interface ClaimsResponse extends ListResponse<Claim> {
  total: number;
}

interface SignalsResponse extends ListResponse<MarketSignal> {
  total: number;
}

interface OverviewResponse {
  sources: number;
  documents: number;
  claims: {
    pending: number;
    approved: number;
    rejected: number;
  };
  usages: number;
}

interface RetrieveResponse {
  items: KnowledgePack["items"];
  knowledge_pack: {
    query: string;
    filters: {
      channel: KnowledgePack["channel"];
      market: string | null;
      language_code: string;
    };
  };
}

const API_BASE_URL = (import.meta.env.VITE_KNOWLEDGE_API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");
const KNOWLEDGE_PATH = "/api/v1/knowledge";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${KNOWLEDGE_PATH}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: unknown; message?: unknown };
      const detail = body.detail ?? body.message;
      if (typeof detail === "string") {
        message = detail;
      } else if (Array.isArray(detail)) {
        message = detail
          .map((item) => typeof item === "object" && item !== null && "msg" in item ? String(item.msg) : String(item))
          .join("；");
      }
    } catch {
      // The status code remains useful when the server returns a non-JSON error.
    }
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as T;
}

export const knowledgeApi = {
  overview: async (): Promise<Overview> => {
    const data = await request<OverviewResponse>("/overview");
    return {
      sources: data.sources,
      documents: data.documents,
      pending_claims: data.claims.pending,
      approved_claims: data.claims.approved,
      rejected_claims: data.claims.rejected,
    };
  },
  sources: async () => (await request<ListResponse<Source>>("/sources")).items,
  documents: async () => (await request<ListResponse<Document>>("/documents")).items,
  pendingClaims: async () =>
    (await request<ListResponse<Claim>>("/claims?review_status=pending")).items,
  claimsByReviewStatus: async (
    reviewStatus: "approved" | "rejected",
    filters: { query?: string; sourceId?: string; dateFrom?: string; dateTo?: string; limit: number; offset: number },
  ) => {
    const params = new URLSearchParams({
      review_status: reviewStatus,
      limit: String(filters.limit),
      offset: String(filters.offset),
    });
    if (filters.query?.trim()) params.set("query", filters.query.trim());
    if (filters.sourceId) params.set("source_id", filters.sourceId);
    if (filters.dateFrom) params.set("date_from", filters.dateFrom);
    if (filters.dateTo) params.set("date_to", filters.dateTo);
    return request<ClaimsResponse>(`/claims?${params.toString()}`);
  },
  claimsByQuality: async (qualityStatus: string, reviewStatus?: string) => {
    const params = new URLSearchParams({ quality_status: qualityStatus });
    if (reviewStatus) params.set("review_status", reviewStatus);
    return (await request<ListResponse<Claim>>(`/claims?${params.toString()}`)).items;
  },
  signalOverview: () => request<SignalOverview>("/signals/overview"),
  signals: async (filters: { query?: string; sourceId?: string; contentKind?: string; dateFrom?: string; dateTo?: string; limit: number; offset: number }) => {
    const params = new URLSearchParams({ limit: String(filters.limit), offset: String(filters.offset) });
    if (filters.query?.trim()) params.set("query", filters.query.trim());
    if (filters.sourceId) params.set("source_id", filters.sourceId);
    if (filters.contentKind) params.set("content_kind", filters.contentKind);
    if (filters.dateFrom) params.set("date_from", filters.dateFrom);
    if (filters.dateTo) params.set("date_to", filters.dateTo);
    return request<SignalsResponse>(`/signals?${params.toString()}`);
  },
  importSignals: (payload: SignalImportRequest) =>
    request<SignalImportResponse>("/signals/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  previewSignalCrawl: (payload: SignalCrawlJobRequest) =>
    request<SignalCrawlPreview>("/signals/crawl/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  signalCrawlJobs: async () =>
    (await request<ListResponse<SignalCrawlJob>>("/signals/crawl/jobs")).items,
  createSignalCrawlJob: (payload: SignalCrawlJobRequest) =>
    request<SignalCrawlJob>("/signals/crawl/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  runSignalCrawlJob: (jobId: string) =>
    request<SignalCrawlRun>(`/signals/crawl/jobs/${encodeURIComponent(jobId)}/run`, { method: "POST" }),
  importDocument: (payload: ImportDocumentRequest) =>
    request<ImportDocumentResponse>("/documents/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  importUrl: (payload: ImportUrlRequest) =>
    request<ImportDocumentResponse>("/documents/import-url", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  previewBatch: (payload: BatchDiscoveryRequest) =>
    request<BatchPreviewResponse>("/batch/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  startBatch: (payload: BatchDiscoveryRequest) =>
    request<BatchRun>("/batch/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  batchRun: (runId: string) =>
    request<BatchRun>(`/batch/runs/${encodeURIComponent(runId)}`),
  batchRunItems: async (runId: string) =>
    (await request<ListResponse<BatchRunItem>>(`/batch/runs/${encodeURIComponent(runId)}/items`)).items,
  cancelBatch: (runId: string) =>
    request<BatchRun>(`/batch/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
    }),
  startQualityRun: (payload: StartQualityRunRequest) =>
    request<QualityRun>("/quality/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  latestQualityRun: () => request<QualityRun>("/quality/runs/latest"),
  qualityRun: (runId: string) =>
    request<QualityRun>(`/quality/runs/${encodeURIComponent(runId)}`),
  qualityRunItems: async (runId: string) =>
    (await request<ListResponse<QualityRunItem>>(`/quality/runs/${encodeURIComponent(runId)}/items`)).items,
  applyQualityRun: (runId: string) =>
    request<QualityRun>(`/quality/runs/${encodeURIComponent(runId)}/apply`, { method: "POST" }),
  cancelQualityRun: (runId: string) =>
    request<QualityRun>(`/quality/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),
  restoreQualityClaim: (claimId: string) =>
    request<Claim>(`/claims/${encodeURIComponent(claimId)}/quality/restore`, { method: "POST" }),
  reviewClaim: (claimId: string, payload: ReviewClaimRequest) =>
    request<KnowledgeClaim>(`/claims/${encodeURIComponent(claimId)}/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  retrieve: async (payload: RetrieveRequest): Promise<KnowledgePack> => {
    const data = await request<RetrieveResponse>("/retrieve", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return {
      query: data.knowledge_pack.query,
      channel: data.knowledge_pack.filters.channel,
      market: data.knowledge_pack.filters.market,
      language_code: data.knowledge_pack.filters.language_code,
      items: data.items,
    };
  },
};

export function getApiBaseUrl(): string {
  return API_BASE_URL;
}
