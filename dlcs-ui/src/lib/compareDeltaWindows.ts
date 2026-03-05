import type { CommentItem, EvidenceItem } from "./types";

type BucketStats = {
  startMs: number;
  endMs: number;
  commentCount: number;
  evidenceCount: number;
  evidenceByCluster: Map<number, number>;
};

export type CompareDeltaWindow = {
  rank: number;
  t0: string;
  t1: string;
  bucket_minutes: number;
  score: number;
  momentum_delta_pct: number;
  cluster_share_divergence: number;
  evidence_density_delta: number;
  support: {
    baseline_comments: number;
    compare_comments: number;
    baseline_evidence: number;
    compare_evidence: number;
  };
};

function asTimestamp(iso: unknown): number | null {
  if (!iso) return null;
  const ts = new Date(String(iso)).getTime();
  return Number.isFinite(ts) ? ts : null;
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

function parseClusterKey(raw: unknown): number | null {
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function toDistribution(map: Map<number, number>): Map<number, number> {
  let total = 0;
  for (const value of map.values()) total += value;
  const out = new Map<number, number>();
  if (total <= 0) return out;
  for (const [key, value] of map.entries()) {
    out.set(key, value / total);
  }
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
  return Math.sqrt(Math.max(0, 0.5 * kl(a, m) + 0.5 * kl(b, m)));
}

function momentumPct(curr: number, prev: number): number | null {
  if (prev <= 0) return null;
  return ((curr - prev) / prev) * 100;
}

function buildBuckets(
  comments: CommentItem[],
  evidence: EvidenceItem[],
  t0Ms: number,
  t1Ms: number,
  bucketMinutes: number
): BucketStats[] {
  const safeT1Ms = t1Ms > t0Ms ? t1Ms : t0Ms + 5 * 60000;
  const bucketMs = bucketMinutes * 60000;
  const bucketCount = Math.max(1, Math.ceil((safeT1Ms - t0Ms) / bucketMs));
  const draft: BucketStats[] = Array.from({ length: bucketCount }).map((_, idx) => {
    const startMs = t0Ms + idx * bucketMs;
    const endMs = Math.min(safeT1Ms, startMs + bucketMs);
    return {
      startMs,
      endMs,
      commentCount: 0,
      evidenceCount: 0,
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
    draft[idx].commentCount += 1;
  }
  for (const row of evidence) {
    const ts = asTimestamp(row.created_at);
    if (ts === null) continue;
    const idx = indexForTs(ts);
    if (idx === null) continue;
    draft[idx].evidenceCount += 1;
    const cluster = parseClusterKey(row.cluster_key);
    if (cluster !== null) {
      draft[idx].evidenceByCluster.set(cluster, (draft[idx].evidenceByCluster.get(cluster) || 0) + 1);
    }
  }
  return draft;
}

function deriveRangeMs(comments: CommentItem[], evidence: EvidenceItem[]): { minMs: number; maxMs: number } | null {
  let minMs = Number.POSITIVE_INFINITY;
  let maxMs = Number.NEGATIVE_INFINITY;
  for (const row of comments) {
    const ts = asTimestamp(row.created_at);
    if (ts === null) continue;
    if (ts < minMs) minMs = ts;
    if (ts > maxMs) maxMs = ts;
  }
  for (const row of evidence) {
    const ts = asTimestamp(row.created_at);
    if (ts === null) continue;
    if (ts < minMs) minMs = ts;
    if (ts > maxMs) maxMs = ts;
  }
  if (!Number.isFinite(minMs) || !Number.isFinite(maxMs)) return null;
  if (maxMs <= minMs) return { minMs, maxMs: minMs + 5 * 60000 };
  return { minMs, maxMs };
}

export function buildTopDeltaWindows(input: {
  baselineComments: CommentItem[];
  baselineEvidence: EvidenceItem[];
  compareComments: CommentItem[];
  compareEvidence: EvidenceItem[];
  topK?: number;
  bucketMinutes?: number | "auto";
}): CompareDeltaWindow[] {
  const topK = Math.max(1, Math.min(10, input.topK || 3));
  const baselineComments = input.baselineComments || [];
  const baselineEvidence = input.baselineEvidence || [];
  const compareComments = input.compareComments || [];
  const compareEvidence = input.compareEvidence || [];

  const rangeA = deriveRangeMs(baselineComments, baselineEvidence);
  const rangeB = deriveRangeMs(compareComments, compareEvidence);
  if (!rangeA && !rangeB) return [];

  const fallbackNow = Date.now();
  const t0Ms = Math.min(rangeA?.minMs ?? fallbackNow - 24 * 60 * 60000, rangeB?.minMs ?? fallbackNow - 24 * 60 * 60000);
  const t1Ms = Math.max(rangeA?.maxMs ?? fallbackNow, rangeB?.maxMs ?? fallbackNow);
  const rangeMs = Math.max(60000, t1Ms - t0Ms);
  const bucketMinutes =
    typeof input.bucketMinutes === "number" && input.bucketMinutes > 0 ? input.bucketMinutes : autoBucketMinutes(rangeMs);

  const baseBuckets = buildBuckets(baselineComments, baselineEvidence, t0Ms, t1Ms, bucketMinutes);
  const compareBuckets = buildBuckets(compareComments, compareEvidence, t0Ms, t1Ms, bucketMinutes);
  const bucketCount = Math.min(baseBuckets.length, compareBuckets.length);
  if (!bucketCount) return [];

  const rawRows = Array.from({ length: bucketCount }).map((_, idx) => {
    const base = baseBuckets[idx];
    const cmp = compareBuckets[idx];
    const prevBase = idx > 0 ? baseBuckets[idx - 1].commentCount : 0;
    const prevCmp = idx > 0 ? compareBuckets[idx - 1].commentCount : 0;
    const momentumBase = momentumPct(base.commentCount, prevBase);
    const momentumCmp = momentumPct(cmp.commentCount, prevCmp);
    const momentumDelta = Math.abs((momentumBase ?? 0) - (momentumCmp ?? 0));

    const baseDist = toDistribution(base.evidenceByCluster);
    const cmpDist = toDistribution(cmp.evidenceByCluster);
    const clusterDivergence = jsDistance(baseDist, cmpDist);

    const baseDensity = base.evidenceCount / Math.max(1, bucketMinutes);
    const cmpDensity = cmp.evidenceCount / Math.max(1, bucketMinutes);
    const densityDelta = Math.abs(cmpDensity - baseDensity);

    return {
      t0: new Date(base.startMs).toISOString(),
      t1: new Date(base.endMs).toISOString(),
      momentumDelta,
      clusterDivergence,
      densityDelta,
      support: {
        baseline_comments: base.commentCount,
        compare_comments: cmp.commentCount,
        baseline_evidence: base.evidenceCount,
        compare_evidence: cmp.evidenceCount,
      },
    };
  });

  const maxMomentumDelta = rawRows.reduce((max, row) => Math.max(max, row.momentumDelta), 0);
  const maxClusterDivergence = rawRows.reduce((max, row) => Math.max(max, row.clusterDivergence), 0);
  const maxDensityDelta = rawRows.reduce((max, row) => Math.max(max, row.densityDelta), 0);

  const scoredRows = rawRows.map((row) => {
    const normMomentum = maxMomentumDelta > 0 ? row.momentumDelta / maxMomentumDelta : 0;
    const normCluster = maxClusterDivergence > 0 ? row.clusterDivergence / maxClusterDivergence : 0;
    const normDensity = maxDensityDelta > 0 ? row.densityDelta / maxDensityDelta : 0;
    const score = clamp(0.45 * normMomentum + 0.35 * normCluster + 0.2 * normDensity, 0, 1);
    return {
      ...row,
      score,
      supportTotal:
        row.support.baseline_comments +
        row.support.compare_comments +
        row.support.baseline_evidence +
        row.support.compare_evidence,
    };
  });

  const top = [...scoredRows]
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return b.supportTotal - a.supportTotal;
    })
    .slice(0, topK);

  return top.map((row, idx) => ({
    rank: idx + 1,
    t0: row.t0,
    t1: row.t1,
    bucket_minutes: bucketMinutes,
    score: Number((row.score * 100).toFixed(1)),
    momentum_delta_pct: Number(row.momentumDelta.toFixed(1)),
    cluster_share_divergence: Number(row.clusterDivergence.toFixed(3)),
    evidence_density_delta: Number(row.densityDelta.toFixed(3)),
    support: row.support,
  }));
}
