import assert from "node:assert/strict";
import { buildCasebookSnapshot } from "../src/lib/casebook";
import type { CommentItem } from "../src/lib/types";

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function makeWindowComments(params: {
  prefix: string;
  count: number;
  startMs: number;
  endMs: number;
  clusterKey: number;
}): CommentItem[] {
  const span = Math.max(1, params.endMs - params.startMs);
  return Array.from({ length: params.count }).map((_, idx) => {
    const ts = params.startMs + Math.floor(((idx + 1) / (params.count + 1)) * span);
    return {
      id: `${params.prefix}-${idx + 1}`,
      text: `${params.prefix}-${idx + 1}`,
      cluster_key: params.clusterKey,
      created_at: iso(ts),
    };
  });
}

function baseSnapshotInput(comments: CommentItem[], selected: CommentItem, t0: string, t1: string) {
  return {
    postId: "post-42",
    comment: selected,
    comments,
    commentsTotal: comments.length + 20,
    windowT0: t0,
    windowT1: t1,
    filters: {
      author: "@auditor",
      cluster_key: 7,
      query: "supply chain",
      sort: "time_desc",
    },
  };
}

function runPrevZeroCase(): void {
  const t0 = "2026-02-01T10:00:00.000Z";
  const t1 = "2026-02-01T10:15:00.000Z";
  const startMs = Date.parse(t0);
  const endMs = Date.parse(t1);
  const current = makeWindowComments({
    prefix: "curr-zero",
    count: 5,
    startMs,
    endMs,
    clusterKey: 7,
  });
  const payload = buildCasebookSnapshot(baseSnapshotInput(current, current[0], t0, t1));

  assert.equal(payload.metrics_snapshot.prev_bucket_comment_count, 0);
  assert.equal(payload.metrics_snapshot.momentum_pct, null);
  assert.equal(payload.summary_version, "casebook_summary_v1");
  assert.equal(payload.coverage.is_truncated, true);
}

function runRoundingCase(): void {
  const prevStart = Date.parse("2026-02-01T10:00:00.000Z");
  const prevEnd = Date.parse("2026-02-01T10:15:00.000Z");
  const currStart = Date.parse("2026-02-01T10:15:00.000Z");
  const currEnd = Date.parse("2026-02-01T10:30:00.000Z");

  const prev = makeWindowComments({
    prefix: "prev-round",
    count: 120,
    startMs: prevStart,
    endMs: prevEnd,
    clusterKey: 4,
  });
  const currDominant = makeWindowComments({
    prefix: "curr-round-dom",
    count: 108,
    startMs: currStart,
    endMs: currEnd,
    clusterKey: 7,
  });
  const currOther = makeWindowComments({
    prefix: "curr-round-other",
    count: 54,
    startMs: currStart,
    endMs: currEnd,
    clusterKey: 9,
  });
  const comments = [...prev, ...currDominant, ...currOther];
  const payload = buildCasebookSnapshot(
    baseSnapshotInput(comments, currDominant[0], iso(currStart), iso(currEnd))
  );

  assert.equal(payload.metrics_snapshot.bucket_comment_count, 162);
  assert.equal(payload.metrics_snapshot.prev_bucket_comment_count, 120);
  assert.equal(payload.metrics_snapshot.momentum_pct, 35.0);
  assert.equal(payload.metrics_snapshot.dominant_cluster_id, 7);
  assert.equal(payload.metrics_snapshot.dominant_cluster_share, 66.67);
}

function runNegativeMomentumCase(): void {
  const prevStart = Date.parse("2026-02-01T11:00:00.000Z");
  const prevEnd = Date.parse("2026-02-01T11:15:00.000Z");
  const currStart = Date.parse("2026-02-01T11:15:00.000Z");
  const currEnd = Date.parse("2026-02-01T11:30:00.000Z");

  const prev = makeWindowComments({
    prefix: "prev-neg",
    count: 120,
    startMs: prevStart,
    endMs: prevEnd,
    clusterKey: 3,
  });
  const curr = makeWindowComments({
    prefix: "curr-neg",
    count: 80,
    startMs: currStart,
    endMs: currEnd,
    clusterKey: 3,
  });
  const comments = [...prev, ...curr];
  const payload = buildCasebookSnapshot(baseSnapshotInput(comments, curr[0], iso(currStart), iso(currEnd)));

  assert.equal(payload.metrics_snapshot.bucket_comment_count, 80);
  assert.equal(payload.metrics_snapshot.prev_bucket_comment_count, 120);
  assert.equal(payload.metrics_snapshot.momentum_pct, -33.3);
}

runPrevZeroCase();
runRoundingCase();
runNegativeMomentumCase();

console.log("casebook_formula_golden: all fixtures passed");
