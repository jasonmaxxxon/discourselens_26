import type { CasebookCreatePayload, CasebookItem, CommentItem } from "./types";

type BuildSnapshotInput = {
  postId: string;
  comment: CommentItem;
  comments: CommentItem[];
  commentsTotal?: number | null;
  windowT0?: string | null;
  windowT1?: string | null;
  filters?: {
    author?: string | null;
    cluster_key?: number | null;
    query?: string | null;
    sort?: string | null;
  };
  analystNote?: string | null;
};

function asTs(raw: string | null | undefined): number | null {
  if (!raw) return null;
  const ts = new Date(raw).getTime();
  return Number.isFinite(ts) ? ts : null;
}

function toIso(ms: number): string {
  return new Date(ms).toISOString();
}

function hhmm(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleTimeString("zh-Hant-HK", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

function deriveBucket(input: BuildSnapshotInput): { t0Ms: number; t1Ms: number } {
  const parsedT0 = asTs(input.windowT0 || null);
  const parsedT1 = asTs(input.windowT1 || null);
  if (parsedT0 !== null && parsedT1 !== null && parsedT1 > parsedT0) {
    return { t0Ms: parsedT0, t1Ms: parsedT1 };
  }
  const center = asTs(input.comment.created_at || null) ?? Date.now();
  const width = 15 * 60 * 1000;
  return { t0Ms: center - width, t1Ms: center + width };
}

function inRange(ts: number | null, t0Ms: number, t1Ms: number): boolean {
  return ts !== null && ts >= t0Ms && ts < t1Ms;
}

function momentumPct(curr: number, prev: number): number | null {
  if (prev <= 0) return null;
  return Number((((curr - prev) / prev) * 100).toFixed(1));
}

function dominantClusterFromComments(rows: CommentItem[], totalComments: number): { clusterId: number | null; share: number | null } {
  if (!totalComments) return { clusterId: null, share: null };
  const counts = new Map<number, number>();
  for (const row of rows) {
    const ck = Number(row.cluster_key);
    if (!Number.isFinite(ck)) continue;
    counts.set(ck, (counts.get(ck) || 0) + 1);
  }
  if (!counts.size) return { clusterId: null, share: null };
  let topCluster: number | null = null;
  let topCount = 0;
  for (const [clusterId, count] of counts.entries()) {
    if (count > topCount) {
      topCluster = clusterId;
      topCount = count;
    }
  }
  return {
    clusterId: topCluster,
    share: Number(((topCount / totalComments) * 100).toFixed(2)),
  };
}

export function buildCasebookSnapshot(input: BuildSnapshotInput): CasebookCreatePayload {
  const { t0Ms, t1Ms } = deriveBucket(input);
  const width = Math.max(60_000, t1Ms - t0Ms);
  const prevT0Ms = t0Ms - width;
  const prevT1Ms = t0Ms;

  const bucketComments = input.comments.filter((row) => inRange(asTs(row.created_at || null), t0Ms, t1Ms));
  const prevBucketComments = input.comments.filter((row) => inRange(asTs(row.created_at || null), prevT0Ms, prevT1Ms));

  const dominant = dominantClusterFromComments(bucketComments, bucketComments.length);
  const momentum = momentumPct(bucketComments.length, prevBucketComments.length);
  const commentsLoaded = input.comments.length;
  const commentsTotal = typeof input.commentsTotal === "number" && Number.isFinite(input.commentsTotal)
    ? Math.max(0, Math.trunc(input.commentsTotal))
    : null;
  const isTruncated = commentsTotal !== null ? commentsTotal > commentsLoaded : false;

  return {
    evidence_id: String(input.comment.id || ""),
    comment_id: String(input.comment.id || ""),
    evidence_text: String(input.comment.text || ""),
    post_id: String(input.postId || ""),
    captured_at: new Date().toISOString(),
    bucket: {
      t0: toIso(t0Ms),
      t1: toIso(t1Ms),
    },
    metrics_snapshot: {
      bucket_comment_count: bucketComments.length,
      prev_bucket_comment_count: prevBucketComments.length,
      momentum_pct: momentum,
      dominant_cluster_id: dominant.clusterId,
      dominant_cluster_share: dominant.share,
    },
    coverage: {
      comments_loaded: commentsLoaded,
      comments_total: commentsTotal,
      is_truncated: isTruncated,
    },
    summary_version: "casebook_summary_v1",
    filters: {
      author: input.filters?.author || null,
      cluster_key:
        typeof input.filters?.cluster_key === "number" && Number.isFinite(input.filters.cluster_key)
          ? input.filters.cluster_key
          : null,
      query: input.filters?.query || null,
      sort: input.filters?.sort || null,
    },
    analyst_note: input.analystNote || null,
  };
}

function velocityLine(item: Pick<CasebookItem, "metrics_snapshot">): string {
  const snap = item.metrics_snapshot;
  if (snap.momentum_pct === null) {
    return `Velocity Change: baseline=0 (${snap.bucket_comment_count} vs ${snap.prev_bucket_comment_count})`;
  }
  const sign = snap.momentum_pct >= 0 ? "+" : "";
  return `Velocity Change: ${sign}${Math.round(snap.momentum_pct)}% vs previous window`;
}

function dominantLine(item: Pick<CasebookItem, "metrics_snapshot">): string {
  const snap = item.metrics_snapshot;
  if (snap.dominant_cluster_id === null || snap.dominant_cluster_share === null) {
    return "Dominant Cluster: n/a";
  }
  return `Dominant Cluster: #${snap.dominant_cluster_id} (${Math.round(snap.dominant_cluster_share)}%)`;
}

export function renderCasebookSummary(item: CasebookItem): string[] {
  const totalText = item.coverage.comments_total === null ? "unknown" : String(item.coverage.comments_total);
  const scopeLine = item.coverage.is_truncated
    ? `Based on partial dataset (${item.coverage.comments_loaded} / ${totalText} comments loaded)`
    : `Dataset scope: full (${item.coverage.comments_loaded} / ${totalText} comments loaded)`;
  return [
    `Time Window: ${hhmm(item.bucket.t0)}-${hhmm(item.bucket.t1)}`,
    velocityLine(item),
    dominantLine(item),
    "Evidence Captured: 1 item",
    scopeLine,
  ];
}

export function casebookToJson(items: CasebookItem[]): string {
  return JSON.stringify(
    {
      version: "casebook-memory-loop-v1",
      exported_at: new Date().toISOString(),
      count: items.length,
      items,
    },
    null,
    2
  );
}

export function casebookToCsv(items: CasebookItem[]): string {
  const head = [
    "id",
    "post_id",
    "evidence_id",
    "comment_id",
    "captured_at",
    "bucket_t0",
    "bucket_t1",
    "bucket_comment_count",
    "prev_bucket_comment_count",
    "momentum_pct",
    "dominant_cluster_id",
    "dominant_cluster_share",
    "comments_loaded",
    "comments_total",
    "is_truncated",
    "summary_version",
    "filter_author",
    "filter_cluster_key",
    "filter_query",
    "filter_sort",
    "analyst_note",
  ];
  const rows = items.map((item) => [
    item.id,
    item.post_id,
    item.evidence_id,
    item.comment_id,
    item.captured_at,
    item.bucket.t0,
    item.bucket.t1,
    item.metrics_snapshot.bucket_comment_count,
    item.metrics_snapshot.prev_bucket_comment_count,
    item.metrics_snapshot.momentum_pct ?? "",
    item.metrics_snapshot.dominant_cluster_id ?? "",
    item.metrics_snapshot.dominant_cluster_share ?? "",
    item.coverage.comments_loaded,
    item.coverage.comments_total ?? "",
    item.coverage.is_truncated ? "true" : "false",
    item.summary_version,
    item.filters.author ?? "",
    item.filters.cluster_key ?? "",
    (item.filters.query || "").replace(/[\r\n]+/g, " ").replace(/,/g, " "),
    item.filters.sort ?? "",
    (item.analyst_note || "").replace(/[\r\n]+/g, " ").replace(/,/g, " "),
  ]);
  return [head, ...rows].map((r) => r.join(",")).join("\n");
}

export function downloadTextFile(filename: string, content: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
