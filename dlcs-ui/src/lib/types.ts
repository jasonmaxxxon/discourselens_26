export type PostItem = {
  id: string;
  snippet: string;
  url?: string | null;
  created_at?: string | null;
  author?: string | null;
  like_count?: number | null;
  reply_count?: number | null;
  view_count?: number | null;
  share_count?: number | null;
  repost_count?: number | null;
  has_analysis?: boolean;
  analysis_is_valid?: boolean | null;
  analysis_version?: string | null;
  phenomenon_id?: string | null;
};

export type BuildMetaResponse = {
  status?: "ok";
  build_sha?: string;
  build_time?: string;
  env?: string;
  version?: string;
};

export type AnalysisJsonResponse = {
  status?: "ready" | "pending" | "empty" | "not_found";
  reason_code?: string | null;
  trace_id?: string;
  analysis_json: Record<string, unknown>;
  analysis_is_valid?: boolean | null;
  analysis_invalid_reason?: string | null;
  analysis_missing_keys?: string[] | null;
};

export type ClusterSample = {
  id?: string;
  text?: string;
  author_handle?: string;
  like_count?: number;
  reply_count?: number;
  created_at?: string;
};

export type ClusterItem = {
  cluster_key: number;
  label: string;
  summary: string;
  size: number;
  share?: number | null;
  keywords?: string[];
  sample_total?: number;
  samples: ClusterSample[];
  engagement?: {
    likes?: number;
    replies?: number;
    like_share?: number | null;
    engagement_share?: number | null;
    likes_per_comment?: number | null;
  };
  coords?: { x: number; y: number };
  label_source?: string;
  cip?: {
    label_confidence?: number;
    label_unstable?: boolean;
  } | null;
};

export type ClustersResponse = {
  status?: "ready" | "pending" | "empty" | "not_found";
  reason_code?: string | null;
  trace_id?: string;
  clusters: ClusterItem[];
  total_comments: number;
};

export type ClaimItem = {
  id: string;
  text: string;
  status?: string;
  confidence?: number;
  cluster_key?: number | null;
  primary_cluster_key?: number | null;
  claim_type?: string;
  scope?: string;
  created_at?: string;
};

export type ClaimsResponse = {
  status?: "ready" | "pending" | "empty" | "not_found";
  reason_code?: string | null;
  trace_id?: string;
  claims: ClaimItem[];
  audit?: {
    verdict?: string;
    kept_claims_count?: number;
    dropped_claims_count?: number;
    total_claims_count?: number;
    created_at?: string;
  };
};

export type EvidenceItem = {
  id: string;
  evidence_type?: string;
  evidence_id?: string | number;
  locator_key?: string;
  claim_id?: string;
  claim_text?: string;
  claim_status?: string;
  cluster_key?: number;
  author_handle?: string;
  like_count?: number;
  text?: string;
  created_at?: string;
};

export type EvidenceResponse = {
  status?: "ready" | "pending" | "empty" | "not_found" | "error";
  reason?: string | null;
  reason_code?: string | null;
  trace_id?: string;
  post_id?: string;
  items: EvidenceItem[];
  claims: ClaimItem[];
};

export type CommentItem = {
  id: string;
  post_id?: string;
  text?: string | null;
  author_handle?: string | null;
  like_count?: number | null;
  reply_count?: number | null;
  cluster_key?: number | null;
  created_at?: string | null;
};

export type CommentsByPostResponse = {
  post_id: string;
  total: number;
  items: CommentItem[];
};

export type CommentsSearchResponse = {
  items: CommentItem[];
};

export type CasebookBucket = {
  t0: string;
  t1: string;
};

export type CasebookMetricsSnapshot = {
  bucket_comment_count: number;
  prev_bucket_comment_count: number;
  momentum_pct: number | null;
  dominant_cluster_id: number | null;
  dominant_cluster_share: number | null;
};

export type CasebookCoverage = {
  comments_loaded: number;
  comments_total: number | null;
  is_truncated: boolean;
};

export type CasebookFilters = {
  author: string | null;
  cluster_key: number | null;
  query: string | null;
  sort: string | null;
};

export type CasebookCreatePayload = {
  evidence_id: string;
  comment_id: string;
  evidence_text: string;
  post_id: string;
  captured_at: string;
  bucket: CasebookBucket;
  metrics_snapshot: CasebookMetricsSnapshot;
  coverage: CasebookCoverage;
  summary_version: "casebook_summary_v1";
  filters: CasebookFilters;
  analyst_note?: string | null;
};

export type CasebookItem = {
  id: string;
  evidence_id: string;
  comment_id: string;
  evidence_text: string;
  post_id: string;
  captured_at: string;
  bucket: CasebookBucket;
  metrics_snapshot: CasebookMetricsSnapshot;
  coverage: CasebookCoverage;
  summary_version: "casebook_summary_v1";
  filters: CasebookFilters;
  analyst_note?: string | null;
  created_at?: string | null;
};

export type CasebookListResponse = {
  items: CasebookItem[];
};

export type OpsKpiResponse = {
  status?: "ready" | "pending" | "empty";
  reason_code?: string | null;
  trace_id?: string;
  range_days: number;
  generated_at: string;
  summary: Record<string, number | null>;
  trends: Array<Record<string, string | number | null>>;
};

export type JobItem = {
  id: string;
  target_id: string;
  status: string;
  stage: string;
  result_post_id?: string | null;
  error_log?: string | null;
  updated_at: string;
};

export type JobStatus = {
  id: string;
  status: string;
  pipeline_type: string;
  mode: string;
  total_count: number;
  processed_count: number;
  success_count: number;
  failed_count: number;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
  input_config: Record<string, unknown>;
  error_summary?: string | null;
  items?: JobItem[];
};

export type CreateJobPayload = {
  pipeline_type: string;
  mode: string;
  input_config: Record<string, unknown>;
};

export type JobSummary = {
  job_id: string;
  pipeline_type?: string | null;
  status: string;
  total_count: number;
  processed_count: number;
  success_count: number;
  failed_count: number;
  last_item_updated_at?: string | null;
  last_heartbeat_at?: string | null;
  degraded?: boolean;
};

export type PhenomenonListItem = {
  id: string;
  canonical_name?: string | null;
  description?: string | null;
  status?: string | null;
  total_posts?: number | null;
  last_seen_at?: string | null;
};

export type PhenomenonDetail = {
  status?: "ready" | "pending" | "empty" | "not_found";
  reason_code?: string | null;
  trace_id?: string;
  meta: {
    id: string;
    canonical_name?: string | null;
    description?: string | null;
    status?: string | null;
  };
  stats: {
    total_posts?: number | null;
    total_likes?: number | null;
    last_seen_at?: string | null;
  };
  recent_posts: Array<{
    id: string;
    created_at?: string | null;
    snippet?: string | null;
    like_count?: number | null;
    phenomenon_status?: string | null;
  }>;
};

export type OverviewTelemetryBucket = {
  ts_hour: string;
  drift_score: number;
  baseline: number;
  sample_n: number;
};

export type OverviewMomentumEvent = {
  ts: string;
  level: "good" | "warn" | "bad" | "info" | "neutral";
  actor: string;
  action: string;
  ref_type?: string;
  ref_id?: string;
};

export type OverviewTelemetryResponse = {
  status?: "ready" | "pending" | "empty";
  reason_code?: string | null;
  trace_id?: string;
  window: string;
  drift_buckets: OverviewTelemetryBucket[];
  momentum_events: OverviewMomentumEvent[];
  active_context: {
    job_id?: string | null;
    post_id?: string | null;
    phenomenon_id?: string | null;
  };
  meta: {
    generated_at: string;
    degraded?: boolean;
    source?: string[];
  };
};

export type ClusterGraphNode = {
  id: string;
  cluster_key: number;
  weight: number;
  label?: string;
  share?: number | null;
  coords?: { x?: number | null; y?: number | null };
  metrics?: Record<string, unknown>;
  cip?: Record<string, unknown> | null;
};

export type ClusterGraphLink = {
  source: string;
  target: string;
  weight: number;
  type?: string;
};

export type ClusterGraphResponse = {
  status?: "ready" | "pending" | "empty" | "not_found";
  reason_code?: string | null;
  trace_id?: string;
  post_id: string;
  nodes: ClusterGraphNode[];
  links: ClusterGraphLink[];
  coords?: Array<{ id: string; x: number; y: number }>;
  meta?: {
    run_id?: string | null;
    generated_at?: string;
    layout_version?: string;
    degraded?: boolean;
    source?: string[];
  };
};

export type PhenomenonSignalItem = {
  signal_id: string;
  title: string;
  strength_pct: number;
  source_type: string;
  source_ref: string;
  evidence_count: number;
  last_seen?: string | null;
};

export type PhenomenonSignalsResponse = {
  status?: "ready" | "pending" | "empty" | "not_found";
  reason_code?: string | null;
  trace_id?: string;
  phenomenon_id: string;
  window: string;
  occurrence_timeline: Array<{
    ts_hour: string;
    post_count: number;
    comment_count: number;
    risk_max: number;
  }>;
  related_signals: PhenomenonSignalItem[];
  supporting_refs?: {
    latest_post_id?: string | null;
    latest_run_id?: string | null;
  };
  meta?: {
    computed_at?: string;
    version?: string;
    degraded?: boolean;
  };
};
