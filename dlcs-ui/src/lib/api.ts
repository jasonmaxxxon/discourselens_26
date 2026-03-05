import type {
  AnalysisJsonResponse,
  BuildMetaResponse,
  CasebookCreatePayload,
  CasebookListResponse,
  ClusterGraphResponse,
  ClaimsResponse,
  CommentsByPostResponse,
  CommentsSearchResponse,
  ClustersResponse,
  CreateJobPayload,
  EvidenceResponse,
  JobSummary,
  JobStatus,
  OpsKpiResponse,
  OverviewTelemetryResponse,
  PhenomenonDetail,
  PhenomenonListItem,
  PhenomenonSignalsResponse,
  PostItem,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) || "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  path: string;
  transient: boolean;
  traceId?: string;
  reasonCode?: string;
  sourceStatus?: string;

  constructor(
    message: string,
    opts: { status: number; path: string; transient: boolean; traceId?: string; reasonCode?: string; sourceStatus?: string }
  ) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status;
    this.path = opts.path;
    this.transient = opts.transient;
    this.traceId = opts.traceId;
    this.reasonCode = opts.reasonCode;
    this.sourceStatus = opts.sourceStatus;
  }
}

function isTransientStatus(status: number): boolean {
  return status === 429 || status >= 500;
}

function compactServerError(raw: string): string {
  if (/resource temporarily unavailable/i.test(raw)) {
    return "資料來源暫時不可用，請稍後重試。";
  }
  if (raw.length > 260) return `${raw.slice(0, 260)}...`;
  return raw;
}

export function isDegradedApiError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.transient || error.status === 0 || error.sourceStatus === "pending";
  }
  const text = String(error || "");
  return /temporarily unavailable|failed to fetch|networkerror|timeout|econn|503|502|504/i.test(text);
}

type RequestMetaResult<T> = {
  data: T;
  degraded: boolean;
  requestId?: string;
  sourceStatus?: string;
};

type ParsedErrorMeta = {
  detail: string;
  reasonCode?: string;
  traceId?: string;
  sourceStatus?: string;
};

function parseJsonObject(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object") return parsed as Record<string, unknown>;
    return null;
  } catch {
    return null;
  }
}

function parseErrorMeta(raw: string, response: Response): ParsedErrorMeta {
  const parsed = parseJsonObject(raw);
  const detail =
    typeof parsed?.detail === "string"
      ? parsed.detail
      : typeof parsed?.error === "string"
        ? parsed.error
        : raw;
  const traceFromBody = typeof parsed?.trace_id === "string" ? parsed.trace_id : undefined;
  const reasonCode =
    typeof parsed?.reason_code === "string"
      ? parsed.reason_code
      : typeof parsed?.reason === "string"
        ? parsed.reason
        : undefined;
  const sourceStatus = typeof parsed?.status === "string" ? parsed.status : undefined;
  const traceId = traceFromBody || response.headers.get("x-request-id") || undefined;
  return {
    detail,
    reasonCode,
    traceId,
    sourceStatus,
  };
}

function sourceStatusFromPayload(payload: unknown): string | undefined {
  if (!payload || typeof payload !== "object") return undefined;
  const raw = (payload as Record<string, unknown>).status;
  return typeof raw === "string" ? raw : undefined;
}

function withTraceSuffix(message: string, traceId?: string): string {
  if (!traceId) return message;
  return `${message} (trace: ${traceId})`;
}

export function formatApiError(error: unknown): string {
  if (error instanceof ApiError) {
    const suffix = error.traceId ? ` [trace ${error.traceId}]` : "";
    return `${error.message}${suffix}`;
  }
  return String(error || "Unknown error");
}

async function requestWithMeta<T>(path: string, init?: RequestInit): Promise<RequestMetaResult<T>> {
  const url = `${API_BASE}${path}`;
  const method = (init?.method || "GET").toUpperCase();

  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
    });
  } catch (e) {
    throw new ApiError(`${method} ${path} failed: ${compactServerError(String(e))}`, {
      status: 0,
      path,
      transient: true,
    });
  }

  if (!res.ok) {
    const text = await res.text();
    const parsed = parseErrorMeta(text, res);
    throw new ApiError(withTraceSuffix(`${res.status} ${res.statusText}: ${compactServerError(parsed.detail)}`, parsed.traceId), {
      status: res.status,
      path,
      transient: isTransientStatus(res.status),
      traceId: parsed.traceId,
      reasonCode: parsed.reasonCode,
      sourceStatus: parsed.sourceStatus,
    });
  }

  const body = (await res.json()) as T;
  return {
    data: body,
    degraded: res.headers.get("x-ops-degraded") === "1",
    requestId: res.headers.get("x-request-id") || undefined,
    sourceStatus: sourceStatusFromPayload(body),
  };
}

async function request<T>(path: string, init?: RequestInit, retries = 1): Promise<T> {
  const url = `${API_BASE}${path}`;
  const method = (init?.method || "GET").toUpperCase();
  let lastError = "Request failed";

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    let res: Response;
    try {
      res = await fetch(url, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          ...(init?.headers || {}),
        },
      });
    } catch (e) {
      const err = new ApiError(`${method} ${path} failed: ${compactServerError(String(e))}`, {
        status: 0,
        path,
        transient: true,
      });
      if (method === "GET" && attempt < retries) {
        await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
        lastError = err.message;
        continue;
      }
      throw err;
    }

    if (res.ok) {
      return (await res.json()) as T;
    }

    const text = await res.text();
    const parsed = parseErrorMeta(text, res);
    lastError = withTraceSuffix(`${res.status} ${res.statusText}: ${compactServerError(parsed.detail)}`, parsed.traceId);

    if (!(method === "GET" && isTransientStatus(res.status) && attempt < retries)) {
      throw new ApiError(lastError, {
        status: res.status,
        path,
        transient: isTransientStatus(res.status),
        traceId: parsed.traceId,
        reasonCode: parsed.reasonCode,
        sourceStatus: parsed.sourceStatus,
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
  }

  throw new ApiError(lastError, {
    status: 0,
    path,
    transient: true,
  });
}

export const api = {
  getBuildMeta: () => request<BuildMetaResponse>("/api/_meta/build", undefined, 0),
  getPosts: () => request<PostItem[]>("/api/posts", undefined, 2),
  getPostsMeta: () => requestWithMeta<PostItem[]>("/api/posts"),
  getOverviewTelemetry: (window = "24h") =>
    request<OverviewTelemetryResponse>(`/api/overview/telemetry?window=${encodeURIComponent(window)}`),
  getOpsKpi: (range = "30") => request<OpsKpiResponse>(`/api/ops/kpi?range=${encodeURIComponent(range)}`),
  getAnalysisJson: (postId: string) => request<AnalysisJsonResponse>(`/api/analysis-json/${postId}`),
  getClusters: (postId: string) => request<ClustersResponse>(`/api/clusters?post_id=${postId}&limit=12&sample_limit=10`),
  getClusterGraph: (postId: string) =>
    request<ClusterGraphResponse>(`/api/clusters/${encodeURIComponent(postId)}/graph`),
  getClaims: (postId: string) => request<ClaimsResponse>(`/api/claims?post_id=${postId}&limit=200`),
  getEvidence: (postId: string) => request<EvidenceResponse>(`/api/evidence?post_id=${postId}&limit=400`),
  getCommentsByPost: (postId: string, opts?: { limit?: number; offset?: number; sort?: "likes" | "time" }) => {
    const query = new URLSearchParams();
    if (typeof opts?.limit === "number") query.set("limit", String(opts.limit));
    if (typeof opts?.offset === "number") query.set("offset", String(opts.offset));
    if (opts?.sort) query.set("sort", opts.sort);
    const suffix = query.toString();
    return request<CommentsByPostResponse>(
      `/api/comments/by-post/${encodeURIComponent(postId)}${suffix ? `?${suffix}` : ""}`
    );
  },
  searchComments: (opts?: { q?: string; author_handle?: string; post_id?: string; limit?: number }) => {
    const query = new URLSearchParams();
    if (opts?.q) query.set("q", opts.q);
    if (opts?.author_handle) query.set("author_handle", opts.author_handle);
    if (opts?.post_id) query.set("post_id", opts.post_id);
    if (typeof opts?.limit === "number") query.set("limit", String(opts.limit));
    const suffix = query.toString();
    return request<CommentsSearchResponse>(`/api/comments/search${suffix ? `?${suffix}` : ""}`);
  },
  listCasebook: (opts?: { post_id?: string; limit?: number }) => {
    const query = new URLSearchParams();
    if (opts?.post_id) query.set("post_id", opts.post_id);
    if (typeof opts?.limit === "number") query.set("limit", String(opts.limit));
    const suffix = query.toString();
    return request<CasebookListResponse>(`/api/casebook${suffix ? `?${suffix}` : ""}`);
  },
  createCasebookEntry: (payload: CasebookCreatePayload) =>
    request<{ status: string; id?: string; created_at?: string }>("/api/casebook", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listJobs: () => request<JobStatus[]>("/api/jobs/?limit=20"),
  listJobsMeta: () => requestWithMeta<JobStatus[]>("/api/jobs/?limit=20"),
  getJob: (jobId: string) => request<JobStatus>(`/api/jobs/${jobId}`),
  getJobSummary: (jobId: string) => request<JobSummary>(`/api/jobs/${jobId}/summary`),
  getJobSummaryMeta: (jobId: string) => requestWithMeta<JobSummary>(`/api/jobs/${jobId}/summary`),
  cancelJob: (jobId: string) => request<JobStatus>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
  createJob: (payload: CreateJobPayload) =>
    request<JobStatus>("/api/jobs/", { method: "POST", body: JSON.stringify(payload) }),
  listPhenomena: (opts?: { status?: string; q?: string; limit?: number }) => {
    const query = new URLSearchParams();
    if (opts?.status) query.set("status", opts.status);
    if (opts?.q) query.set("q", opts.q);
    if (typeof opts?.limit === "number") query.set("limit", String(opts.limit));
    const suffix = query.toString();
    return request<PhenomenonListItem[]>(`/api/library/phenomena${suffix ? `?${suffix}` : ""}`);
  },
  getPhenomenon: (phenomenonId: string, limit = 20) =>
    request<PhenomenonDetail>(`/api/library/phenomena/${encodeURIComponent(phenomenonId)}?limit=${limit}`),
  getPhenomenonSignals: (phenomenonId: string, window = "24h") =>
    request<PhenomenonSignalsResponse>(
      `/api/library/phenomena/${encodeURIComponent(phenomenonId)}/signals?window=${encodeURIComponent(window)}`
    ),
  promotePhenomenon: (phenomenonId: string) =>
    request<{ ok?: boolean; id?: string; status?: string }>(
      `/api/library/phenomena/${encodeURIComponent(phenomenonId)}/promote`,
      { method: "POST" }
    ),
  submitReview: (payload: Record<string, unknown>) =>
    request<{ ok?: boolean; id?: string }>("/api/reviews", { method: "POST", body: JSON.stringify(payload) }),
};
