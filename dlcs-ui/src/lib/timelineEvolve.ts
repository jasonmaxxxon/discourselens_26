import type { CommentItem, EvidenceItem } from "./types";

export type TimeRange = {
  t0Ms: number;
  t1Ms: number;
};

export type TimelineBucket = {
  bucket_start_iso: string;
  bucket_end_iso: string;
  comment_count: number;
  evidence_count: number;
  like_sum: number;
  reply_sum: number;
  engagement_sum: number;
  distinct_authors: number;
  momentum_score: number;
  drift_score: number;
  anomaly_flag: boolean;
  top_cluster_key?: number;
};

export type MomentumSignal = {
  latest_bucket_momentum: number;
  momentum_delta_1: number;
  velocity_ratio_1: number;
  trend: "up" | "flat" | "down";
};

export type DriftSignal = {
  latest_drift: number;
  drift_delta_1: number;
  author_churn_ratio: number;
  cluster_mix_shift: number;
};

export type SufficiencyFlags = {
  has_min_comments: boolean;
  has_min_evidence: boolean;
  has_multi_bucket: boolean;
  has_cluster_mix: boolean;
  sufficient_for_compare: boolean;
  warnings: string[];
};

export type TimelineModel = {
  range: { t0: string; t1: string };
  bucket_minutes: number;
  buckets: TimelineBucket[];
  momentum: MomentumSignal;
  drift: DriftSignal;
  sufficiency: SufficiencyFlags;
};

const MIN_COMMENTS_TOTAL = 30;
const MIN_EVIDENCE_TOTAL = 8;
const MIN_BUCKETS = 4;
const MIN_COMMENTS_PER_BUCKET_FOR_FLAG = 5;
const DEFAULT_RANGE_MS = 24 * 60 * 60 * 1000;

type DraftBucket = {
  startMs: number;
  endMs: number;
  commentCount: number;
  evidenceCount: number;
  likeSum: number;
  replySum: number;
  authors: Set<string>;
  commentAuthors: Set<string>;
  evidenceByCluster: Map<number, number>;
};

function asTimestamp(iso: unknown): number | null {
  if (!iso) return null;
  const t = new Date(String(iso)).getTime();
  return Number.isFinite(t) ? t : null;
}

function pct85(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * 0.85) - 1));
  return sorted[idx] || 0;
}

function minMaxNorm(values: number[]): number[] {
  if (!values.length) return [];
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    if (value < min) min = value;
    if (value > max) max = value;
  }
  const span = max - min;
  if (!Number.isFinite(span) || span <= 0) return values.map(() => 0.5);
  return values.map((value) => (value - min) / span);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function autoBucketMinutes(rangeMs: number): number {
  const windowMinutes = Math.max(1, Math.ceil(rangeMs / 60000));
  let bucket = 5;
  if (windowMinutes > 90 && windowMinutes <= 360) bucket = 15;
  else if (windowMinutes > 360 && windowMinutes <= 1440) bucket = 60;
  else if (windowMinutes > 1440) bucket = 180;

  const count = Math.ceil(windowMinutes / bucket);
  if (count < 8) return Math.max(1, Math.floor(bucket / 2));
  if (count > 36) return bucket * 2;
  return bucket;
}

function deriveRange(comments: CommentItem[], evidence: EvidenceItem[], nowMs: number): TimeRange {
  let minTs = Number.POSITIVE_INFINITY;
  let maxTs = Number.NEGATIVE_INFINITY;
  for (const row of comments) {
    const ts = asTimestamp(row.created_at);
    if (ts === null) continue;
    if (ts < minTs) minTs = ts;
    if (ts > maxTs) maxTs = ts;
  }
  for (const row of evidence) {
    const ts = asTimestamp(row.created_at);
    if (ts === null) continue;
    if (ts < minTs) minTs = ts;
    if (ts > maxTs) maxTs = ts;
  }
  if (!Number.isFinite(minTs) || !Number.isFinite(maxTs) || maxTs <= minTs) {
    return { t0Ms: nowMs - DEFAULT_RANGE_MS, t1Ms: nowMs };
  }
  return { t0Ms: minTs, t1Ms: maxTs };
}

function toDistribution(map: Map<number, number>): Map<number, number> {
  let total = 0;
  for (const v of map.values()) total += v;
  const out = new Map<number, number>();
  if (total <= 0) return out;
  for (const [k, v] of map.entries()) out.set(k, v / total);
  return out;
}

function jsDistance(a: Map<number, number>, b: Map<number, number>): number {
  const keys = new Set<number>([...a.keys(), ...b.keys()]);
  if (!keys.size) return 0;
  const m = new Map<number, number>();
  for (const key of keys) {
    m.set(key, ((a.get(key) || 0) + (b.get(key) || 0)) / 2);
  }
  const kl = (p: Map<number, number>, q: Map<number, number>) => {
    let sum = 0;
    for (const key of keys) {
      const pv = p.get(key) || 0;
      if (pv <= 0) continue;
      const qv = Math.max(1e-9, q.get(key) || 0);
      sum += pv * Math.log2(pv / qv);
    }
    return sum;
  };
  const js = 0.5 * kl(a, m) + 0.5 * kl(b, m);
  return Math.sqrt(Math.max(0, js));
}

function topClusterKey(clusterMap: Map<number, number>): number | undefined {
  let key: number | undefined;
  let maxCount = -1;
  for (const [cluster, count] of clusterMap.entries()) {
    if (count > maxCount) {
      key = cluster;
      maxCount = count;
    }
  }
  return key;
}

function parseClusterKey(raw: unknown): number | null {
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

export function buildTimelineModel(input: {
  comments: CommentItem[];
  evidence: EvidenceItem[];
  t0?: string | null;
  t1?: string | null;
  bucketMinutes?: number | "auto";
  nowMs?: number;
}): TimelineModel {
  const comments = input.comments || [];
  const evidence = input.evidence || [];
  const nowMs = typeof input.nowMs === "number" ? input.nowMs : Date.now();

  const parsedT0 = asTimestamp(input.t0);
  const parsedT1 = asTimestamp(input.t1);
  const fallbackRange = deriveRange(comments, evidence, nowMs);
  const t0Ms = parsedT0 ?? fallbackRange.t0Ms;
  const t1Ms = parsedT1 ?? fallbackRange.t1Ms;
  const safeT1Ms = t1Ms > t0Ms ? t1Ms : t0Ms + 5 * 60000;
  const rangeMs = Math.max(60000, safeT1Ms - t0Ms);

  const bucketMinutes =
    typeof input.bucketMinutes === "number" && input.bucketMinutes > 0
      ? input.bucketMinutes
      : autoBucketMinutes(rangeMs);
  const bucketMs = bucketMinutes * 60000;
  const bucketCount = Math.max(1, Math.ceil(rangeMs / bucketMs));

  const draft: DraftBucket[] = Array.from({ length: bucketCount }).map((_, idx) => {
    const startMs = t0Ms + idx * bucketMs;
    const endMs = Math.min(safeT1Ms, startMs + bucketMs);
    return {
      startMs,
      endMs,
      commentCount: 0,
      evidenceCount: 0,
      likeSum: 0,
      replySum: 0,
      authors: new Set<string>(),
      commentAuthors: new Set<string>(),
      evidenceByCluster: new Map<number, number>(),
    };
  });

  const indexForTs = (ts: number): number | null => {
    if (ts < t0Ms || ts > safeT1Ms) return null;
    const idx = Math.floor((ts - t0Ms) / bucketMs);
    if (idx < 0) return null;
    return Math.min(bucketCount - 1, idx);
  };

  for (const row of comments) {
    const ts = asTimestamp(row.created_at);
    if (ts === null) continue;
    const idx = indexForTs(ts);
    if (idx === null) continue;
    const b = draft[idx];
    b.commentCount += 1;
    b.likeSum += Number(row.like_count || 0);
    b.replySum += Number(row.reply_count || 0);
    const author = String(row.author_handle || "").trim();
    if (author) {
      b.authors.add(author);
      b.commentAuthors.add(author);
    }
  }

  for (const row of evidence) {
    const ts = asTimestamp(row.created_at);
    if (ts === null) continue;
    const idx = indexForTs(ts);
    if (idx === null) continue;
    const b = draft[idx];
    b.evidenceCount += 1;
    const author = String(row.author_handle || "").trim();
    if (author) b.authors.add(author);
    const cluster = parseClusterKey(row.cluster_key);
    if (cluster !== null) {
      b.evidenceByCluster.set(cluster, (b.evidenceByCluster.get(cluster) || 0) + 1);
    }
  }

  const engagementPerMin = draft.map((b) => (b.likeSum + b.replySum) / Math.max(1, bucketMinutes));
  const commentCounts = draft.map((b) => b.commentCount);
  const replyPerComment = draft.map((b) => b.replySum / Math.max(1, b.commentCount));

  const nE = minMaxNorm(engagementPerMin);
  const nC = minMaxNorm(commentCounts);
  const nR = minMaxNorm(replyPerComment);

  const momentumValues = draft.map((_, i) => clamp(0.5 * nE[i] + 0.3 * nC[i] + 0.2 * nR[i], 0, 1));
  const momentumScores = momentumValues.map((m) => Math.round(m * 100));

  const maxMomentumDelta = momentumScores.reduce((acc, score, idx) => {
    if (idx === 0) return acc;
    return Math.max(acc, Math.abs(score - momentumScores[idx - 1]));
  }, 0);

  const driftScores: number[] = [];
  const churnRatios: number[] = [];
  const mixShifts: number[] = [];

  for (let i = 0; i < draft.length; i += 1) {
    if (i === 0) {
      churnRatios.push(0);
      mixShifts.push(0);
      driftScores.push(0);
      continue;
    }
    const prev = draft[i - 1];
    const cur = draft[i];
    const newAuthors = [...cur.authors].filter((a) => !prev.authors.has(a)).length;
    const churnRatio = newAuthors / Math.max(1, cur.authors.size);
    churnRatios.push(churnRatio);

    const prevDist = toDistribution(prev.evidenceByCluster);
    const curDist = toDistribution(cur.evidenceByCluster);
    const mixShift = jsDistance(prevDist, curDist);
    mixShifts.push(mixShift);

    const delta = Math.abs(momentumScores[i] - momentumScores[i - 1]);
    const v = maxMomentumDelta > 0 ? delta / maxMomentumDelta : 0;
    const driftRaw = clamp(0.45 * mixShift + 0.35 * churnRatio + 0.2 * v, 0, 1);
    driftScores.push(Math.round(driftRaw * 100));
  }

  const momentumP85 = pct85(momentumScores);
  const driftP85 = pct85(driftScores);

  const totalComments = draft.reduce((sum, b) => sum + b.commentCount, 0);
  const totalEvidence = draft.reduce((sum, b) => sum + b.evidenceCount, 0);
  const hasClusterMix = draft.some((b) => b.evidenceByCluster.size > 1);
  const hasMultiBucket = draft.length >= MIN_BUCKETS;
  const hasMinComments = totalComments >= MIN_COMMENTS_TOTAL;
  const hasMinEvidence = totalEvidence >= MIN_EVIDENCE_TOTAL;
  const warnings: string[] = [];
  if (!hasMinComments) warnings.push(`Low sample: comments < ${MIN_COMMENTS_TOTAL}`);
  if (!hasMinEvidence) warnings.push(`Low sample: evidence < ${MIN_EVIDENCE_TOTAL}`);
  if (!hasMultiBucket) warnings.push(`Low sample: buckets < ${MIN_BUCKETS}`);
  if (!hasClusterMix) warnings.push("Cluster mix unavailable or sparse");
  const sufficientForCompare = hasMinComments && hasMinEvidence && hasMultiBucket;

  const buckets: TimelineBucket[] = draft.map((b, idx) => ({
    bucket_start_iso: new Date(b.startMs).toISOString(),
    bucket_end_iso: new Date(b.endMs).toISOString(),
    comment_count: b.commentCount,
    evidence_count: b.evidenceCount,
    like_sum: b.likeSum,
    reply_sum: b.replySum,
    engagement_sum: b.likeSum + b.replySum,
    distinct_authors: b.authors.size,
    momentum_score: momentumScores[idx],
    drift_score: driftScores[idx],
    anomaly_flag:
      sufficientForCompare &&
      b.commentCount >= MIN_COMMENTS_PER_BUCKET_FOR_FLAG &&
      (momentumScores[idx] >= momentumP85 || driftScores[idx] >= driftP85),
    top_cluster_key: topClusterKey(b.evidenceByCluster),
  }));

  const lastMomentum = momentumScores[momentumScores.length - 1] || 0;
  const prevMomentum = momentumScores[momentumScores.length - 2] || lastMomentum;
  const lastVelocity = engagementPerMin[engagementPerMin.length - 1] || 0;
  const prevVelocity = engagementPerMin[engagementPerMin.length - 2] || lastVelocity;
  const velocityRatio = prevVelocity > 0 ? lastVelocity / prevVelocity : 1;
  const momentumDelta = lastMomentum - prevMomentum;

  const lastDrift = driftScores[driftScores.length - 1] || 0;
  const prevDrift = driftScores[driftScores.length - 2] || lastDrift;
  const lastChurn = churnRatios[churnRatios.length - 1] || 0;
  const lastMix = mixShifts[mixShifts.length - 1] || 0;

  return {
    range: {
      t0: new Date(t0Ms).toISOString(),
      t1: new Date(safeT1Ms).toISOString(),
    },
    bucket_minutes: bucketMinutes,
    buckets,
    momentum: {
      latest_bucket_momentum: lastMomentum,
      momentum_delta_1: momentumDelta,
      velocity_ratio_1: Number.isFinite(velocityRatio) ? Number(velocityRatio.toFixed(2)) : 1,
      trend: momentumDelta > 4 ? "up" : momentumDelta < -4 ? "down" : "flat",
    },
    drift: {
      latest_drift: lastDrift,
      drift_delta_1: lastDrift - prevDrift,
      author_churn_ratio: Number(lastChurn.toFixed(3)),
      cluster_mix_shift: Number(lastMix.toFixed(3)),
    },
    sufficiency: {
      has_min_comments: hasMinComments,
      has_min_evidence: hasMinEvidence,
      has_multi_bucket: hasMultiBucket,
      has_cluster_mix: hasClusterMix,
      sufficient_for_compare: sufficientForCompare,
      warnings,
    },
  };
}

export function filterCommentsByRange(comments: CommentItem[], t0?: string | null, t1?: string | null): CommentItem[] {
  const start = asTimestamp(t0);
  const end = asTimestamp(t1);
  if (start === null && end === null) return comments;
  const lower = start ?? Number.NEGATIVE_INFINITY;
  const upper = end ?? Number.POSITIVE_INFINITY;
  return comments.filter((row) => {
    const ts = asTimestamp(row.created_at);
    if (ts === null) return false;
    return ts >= lower && ts <= upper;
  });
}
