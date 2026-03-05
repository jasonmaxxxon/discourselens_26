# Data Contracts

This file documents the JSON payloads produced or consumed by the system. It is intended to let new engineers reason about the pipeline without reading code.

## Topic Contract V1
Topic/Case deterministic run contracts (input shape, canonical hashes, lifecycle/managed score rules) are defined in:
- `docs/TOPIC_CONTRACT_V1.md`

Current status:
- Topic schema tables are provisioned.
- Public `/api/topics/run` and `/api/topics/{topic_id}` registry routes are wired (Phase-3 skeleton).
- Topic worker skeleton route is wired: `/api/topics/worker/run-once` (Phase-3.5).
- Meta-cluster/lifecycle compute is still pending worker materialization.

## Runtime Provenance Headers
Every `/api/*` response includes:
- `X-Request-ID`: request trace id (preserved from inbound if provided).
- `X-Build-SHA`: backend build SHA.
- `X-Env`: runtime environment (`dev|staging|prod`).

## ApiEnvelope (Common Status Semantics)
```json
{
  "status": "ready|pending|empty|not_found|error",
  "reason": "optional_reason_code_or_null",
  "reason_code": "optional_reason_code_or_null",
  "trace_id": "<x-request-id>",
  "detail": "optional human-safe message"
}
```
Rules:
- `202 pending`: transient transport or asset-not-ready states; includes `retry_after_ms`.
- `200 empty`: valid empty data; never fake/demo fallback.
- `404 not_found`: requested resource id absent.
- `500 error`: internal bug only; must include `trace_id`.

## BuildMetaResponse
Returned by `/api/_meta/build`.
```json
{
  "status": "ok",
  "build_sha": "6f13301",
  "build_time": "2026-02-26T02:17:04.741052Z",
  "env": "dev",
  "version": "0.0.0"
}
```

## TopicRunCreateRequest (Phase-3 skeleton)
Returned by `POST /api/topics/run` request body.
```json
{
  "topic_name": "optional display name",
  "seed_query": "public health subsidy rumor",
  "post_ids": [109, 202, 301],
  "time_range": {
    "start": "2026-02-01T00:00:00Z",
    "end": "2026-02-07T00:00:00Z"
  },
  "run_params": {},
  "source": "manual",
  "created_by": "operator@team"
}
```
Notes:
- `seed_post_ids` is accepted as alias for `post_ids`.
- `time_range_start`/`time_range_end` are accepted aliases for `time_range`.
- `post_ids` canonicalization: unique + numeric sort.

## TopicRunCreateResponse (Phase-3 skeleton)
Returned by `POST /api/topics/run`.
```json
{
  "status": "accepted",
  "reason": null,
  "reason_code": "accepted_new|idempotent_hit",
  "trace_id": "<x-request-id>",
  "topic_id": "<uuid>",
  "topic_run_id": "<uuid>",
  "topic_run_hash": "<sha256>"
}
```
Status contract:
- `200 accepted` for both fresh insert and idempotent reuse.
- `400 validation_error` for malformed payload.
- `200 pending` for backend unavailable states.
- never `500`.

## TopicRunDetailResponse (Phase-3 skeleton)
Returned by `GET /api/topics/{topic_id}`.
```json
{
  "status": "ready|pending|failed|not_found|error",
  "reason": "topic_pending|topic_failed|not_found|validation_error|null",
  "reason_code": "topic_pending|topic_failed|not_found|validation_error|null",
  "trace_id": "<x-request-id>",
  "topic_id": "<uuid>",
  "topic_run": {
    "id": "<uuid>",
    "topic_name": "string",
    "seed_query": "string",
    "seed_post_ids": [109, 202, 301],
    "time_range_start": "iso8601",
    "time_range_end": "iso8601",
    "run_params": {},
    "topic_run_hash": "<sha256>",
    "lifecycle_hash": null,
    "status": "pending|running|completed|failed|canceled"
  },
  "topic_posts": {
    "post_count": 3,
    "posts_preview": [
      { "post_id": 109, "ordinal": 0 }
    ]
  },
  "meta_clusters": { "status": "pending", "items": [] },
  "lifecycle": { "status": "pending", "daily": [] },
  "managed_lane": { "status": "pending", "summary": null }
}
```
Status contract:
- malformed uuid -> `400 validation_error`
- missing id -> `404 not_found`
- backend unavailable -> `200 pending`
- never `500`

## TopicWorkerRunOnceRequest (Phase-3.5 skeleton)
Returned by `POST /api/topics/worker/run-once` request body.
```json
{
  "lock_owner": "api-topic-worker",
  "lease_seconds": 600,
  "topic_id": "optional uuid",
  "force_recompute": false
}
```

## TopicWorkerRunOnceResponse (Phase-3.5 skeleton)
Returned by `POST /api/topics/worker/run-once`.
```json
{
  "status": "ready|empty|failed|not_found|error|pending",
  "reason": "reason_code_or_null",
  "reason_code": "reason_code_or_null",
  "trace_id": "<x-request-id>",
  "topic_id": "uuid",
  "topic_run_hash": "sha256",
  "stats_json": {
    "worker_version": "topic_worker_v1",
    "post_count": 3,
    "first_post_time": "iso8601|null",
    "last_post_time": "iso8601|null",
    "comment_count_total": 42,
    "engagement_sum": 1337
  },
  "worker": {
    "lock_owner": "api-topic-worker",
    "lease_seconds": 600,
    "force_recompute": false
  }
}
```
Rules:
- Worker never mutates stats via incremental `+=`; always deterministic overwrite.
- `force_recompute=true` allows re-entrant recompute for idempotence checks.
- `running` lease is reclaimable when heartbeat timeout is exceeded.
- endpoint never returns `500`.

## Pipeline B Batch Request
```json
{
  "keyword": "optional search keyword",
  "urls": ["https://www.threads.net/@.../post/..."],
  "max_posts": 20,
  "exclude_existing": true,
  "reprocess_policy": "skip_if_exists",
  "ingest_source": "B",
  "mode": "run",
  "preview": false,
  "pipeline_mode": "full",
  "concurrency": 2,
  "vision_mode": "auto",
  "vision_stage_cap": "auto"
}
```
Notes: Pipeline B requires missing modules in this repo, so it is currently non-functional.

## JobCreate
```json
{
  "pipeline_type": "A|B|C",
  "mode": "ingest|analyze|full",
  "input_config": { "url": "..." }
}
```

## JobStatusResponse
```json
{
  "id": "<uuid>",
  "status": "pending|discovering|processing|completed|failed|canceled|stale",
  "pipeline_type": "A|B|C",
  "mode": "ingest|analyze|full",
  "total_count": 0,
  "processed_count": 0,
  "success_count": 0,
  "failed_count": 0,
  "created_at": "2026-02-10T00:00:00Z",
  "updated_at": "2026-02-10T00:00:00Z",
  "finished_at": null,
  "input_config": { ... },
  "error_summary": null,
  "items": [ ... ]
}
```

## JobItemPreview
```json
{
  "id": "<uuid>",
  "target_id": "https://www.threads.net/@.../post/...",
  "status": "pending|processing|completed|failed|canceled",
  "stage": "init|fetch|analyst|store|completed|failed|canceled",
  "result_post_id": "12345",
  "error_log": null,
  "updated_at": "2026-02-10T00:00:00Z"
}
```

## JobResult (Legacy)
```json
{
  "status": "pending|processing|completed|failed",
  "pipeline": "A|B|C",
  "job_id": "<uuid>",
  "mode": "ingest|analyze",
  "post_id": "123",
  "posts": null,
  "summary": "...",
  "logs": []
}
```

## PostListItem
```json
{
  "id": "123",
  "snippet": "short text...",
  "created_at": "2026-02-10T00:00:00Z",
  "author": "...",
  "like_count": 0,
  "reply_count": 0,
  "view_count": 0,
  "has_analysis": true,
  "analysis_is_valid": true,
  "analysis_version": "v6.1",
  "analysis_build_id": "...",
  "archive_captured_at": null,
  "archive_build_id": null,
  "has_archive": false,
  "ai_tags": ["..."]
}
```

## Analysis JSON Response
```json
{
  "status": "ready|pending|not_found|error",
  "reason": null,
  "reason_code": null,
  "trace_id": "<x-request-id>",
  "post_id": "440",
  "analysis_json": { ... },
  "analysis_is_valid": true,
  "analysis_version": "v6.1",
  "analysis_build_id": "...",
  "analysis_invalid_reason": null,
  "analysis_missing_keys": [],
  "phenomenon": { "id": null, "status": "pending", "case_id": null, "canonical_name": null, "source": "default" }
}
```

## OverviewTelemetryResponse
Returned by `/api/overview/telemetry`.
```json
{
  "window": "24h",
  "drift_buckets": [
    {
      "ts_hour": "2026-02-24T02:00:00Z",
      "drift_score": 12.5,
      "baseline": 8.2,
      "sample_n": 14
    }
  ],
  "momentum_events": [
    {
      "ts": "2026-02-24T02:12:00Z",
      "level": "info",
      "actor": "job-worker",
      "action": "ingest · processing",
      "ref_type": "job_item",
      "ref_id": "..."
    }
  ],
  "active_context": {
    "job_id": "...",
    "post_id": "...",
    "phenomenon_id": "..."
  },
  "meta": {
    "generated_at": "2026-02-24T02:13:00Z",
    "degraded": false,
    "source": ["job_batches", "job_items", "threads_posts"]
  }
}
```
Notes:
- `drift_score` is backend deterministic proxy (currently based on behavior quality missing timestamp ratio).
- Frontend must not infer drift semantics independently.

## ClaimsResponse
Returned by `/api/claims`.
```json
{
  "status": "ready|empty|pending|not_found|error",
  "reason": null,
  "reason_code": null,
  "trace_id": "<x-request-id>",
  "post_id": "440",
  "claims": [],
  "audit": null
}
```

## EvidenceResponse
Returned by `/api/evidence`.
```json
{
  "status": "ready|empty|pending|not_found|error",
  "reason": null,
  "reason_code": null,
  "trace_id": "<x-request-id>",
  "post_id": "440",
  "items": [],
  "claims": []
}
```

## ClusterListResponse
Returned by `/api/clusters`.
```json
{
  "status": "ready|empty|pending|not_found|error",
  "reason": null,
  "reason_code": null,
  "trace_id": "<x-request-id>",
  "post_id": "440",
  "clusters": [],
  "total_comments": 0,
  "engagement_truncated": false,
  "degraded": false
}
```

## ClusterGraphResponse
Returned by `/api/clusters/{post_id}/graph`.
```json
{
  "status": "ready|empty|pending|not_found|error",
  "reason": null,
  "reason_code": null,
  "trace_id": "<x-request-id>",
  "post_id": "440",
  "nodes": [
    {
      "id": "c-1",
      "cluster_key": 1,
      "weight": 47,
      "label": "關於外傭工作表現不佳的幽默討論",
      "share": 0.57,
      "coords": { "x": 0.22, "y": 0.78 },
      "metrics": { "likes": 19019, "replies": 155 },
      "cip": { "run_id": "...", "label_confidence": 0.85, "label_unstable": true }
    }
  ],
  "links": [
    { "source": "c-1", "target": "c-3", "weight": 4, "type": "claim_coupling" }
  ],
  "coords": [
    { "id": "c-1", "x": 0.22, "y": 0.78 }
  ],
  "meta": {
    "run_id": "...",
    "generated_at": "2026-02-24T02:13:00Z",
    "layout_version": "v1",
    "degraded": false,
    "source": ["threads_comment_clusters", "threads_claims"]
  }
}
```
Notes:
- `nodes/links/coords` form the explorer SoT; UI should render directly and emit only interaction events.

## PhenomenonDetailResponse
Returned by `/api/library/phenomena/{phenomenon_id}`.
```json
{
  "status": "ready|empty|pending|not_found|error",
  "reason": null,
  "reason_code": null,
  "trace_id": "<x-request-id>",
  "phenomenon_id": "34aa6d22-df89-4beb-9414-c5b21d2f11aa",
  "meta": {
    "id": "34aa6d22-df89-4beb-9414-c5b21d2f11aa",
    "canonical_name": "Screenshot_Justice",
    "description": "optional",
    "status": "active"
  },
  "stats": {
    "total_posts": 3,
    "total_likes": 188,
    "last_seen_at": "2026-02-24T02:13:00Z"
  },
  "recent_posts": []
}
```

## PhenomenonSignalsResponse
Returned by `/api/library/phenomena/{phenomenon_id}/signals`.
```json
{
  "status": "ready|empty|pending|not_found|error",
  "reason": null,
  "reason_code": null,
  "trace_id": "<x-request-id>",
  "phenomenon_id": "34aa6d22-df89-4beb-9414-c5b21d2f11aa",
  "window": "24h",
  "occurrence_timeline": [
    {
      "ts_hour": "2026-02-24T02:00:00Z",
      "post_count": 1,
      "comment_count": 13,
      "risk_max": 0.44
    }
  ],
  "related_signals": [
    {
      "signal_id": "sig-a1b2c3d4",
      "title": "Signal title",
      "strength_pct": 72,
      "source_type": "claim",
      "source_ref": "a1b2c3d4...",
      "evidence_count": 3,
      "last_seen": "2026-02-24T02:12:00Z"
    }
  ],
  "supporting_refs": {
    "latest_post_id": "440",
    "latest_run_id": "..."
  },
  "meta": {
    "computed_at": "2026-02-24T02:13:00Z",
    "version": "v0",
    "degraded": false
  }
}
```
Notes:
- Related signals are backend semantic aggregation (claims + evidence) with deterministic order:
  `strength_pct DESC`, then `evidence_count DESC`, then `last_seen DESC`.
- Frontend should not re-assemble signals from raw claims/evidence APIs.

## Analysis JSON (Shape)
`analysis_json` follows the AnalysisV4 schema with compatibility fields:
```json
{
  "post": { "post_id": "...", "author": "...", "text": "...", "link": "...", "images": [], "timestamp": "...", "metrics": { "likes": 0, "views": 0, "replies": 0 } },
  "phenomenon": { "id": null, "status": null, "name": null, "description": null, "ai_image": null },
  "emotional_pulse": { "primary": null, "cynicism": 0.2, "hope": 0.1, "outrage": 0.3, "notes": null },
  "segments": [ { "label": "Cluster 0", "share": 0.4, "samples": [ ... ] } ],
  "narrative_stack": { "l1": "...", "l2": "...", "l3": "..." },
  "hard_metrics": { ... },
  "per_cluster_metrics": [ ... ],
  "battlefield_map": [ ... ],
  "structural_insight": { ... },
  "strategic_verdict": { ... },
  "summary": { "one_line": "...", "narrative_type": "..." },
  "battlefield": { "factions": [ ... ] },
  "meta": { "bundle_id": "...", "cluster_run_id": "...", "coverage": { ... }, "behavior": { ... }, "sufficiency": { ... }, "risk": { ... } }
}
```

## Fetcher Artifacts (run_dir)
`run_fetcher_test` writes a run directory with the following files.

### manifest.json
Contains run metadata and coverage telemetry.
Key fields:
- `run_id`, `run_dir`, `crawled_at_utc`
- `harvest_stats.main` with `coverage_goal`, `stop_reason`, `rounds`, `expected_comment_count`
- `merged_total`, `coverage` block, `coverage_estimate`

### threads_posts_raw.json
```json
{
  "run_id": "run_...",
  "crawled_at_utc": "2026-02-10T00:00:00Z",
  "post_url": "https://www.threads.net/@.../post/...",
  "post_id": "...",
  "fetcher_version": "...",
  "run_dir": "...",
  "raw_html_initial_path": "...",
  "raw_html_final_path": "...",
  "raw_cards_path": "..."
}
```

### post_payload.json
```json
{
  "post_id": "...",
  "url": "...",
  "author": "...",
  "post_text": "...",
  "post_text_raw": "...",
  "metrics": { "likes": 0, "views": 0, "reply_count": 0, "repost_count": 0, "share_count": 0 },
  "images": [ { "src": "...", "original_src": "..." } ]
}
```

### threads_comments.json
Each element is normalized for DB ingest:
```json
{
  "comment_id": "...",
  "post_url": "...",
  "run_id": "run_...",
  "text": "...",
  "author_handle": "...",
  "like_count": 0,
  "reply_count_ui": 0,
  "parent_comment_id": "...",
  "crawled_at_utc": "...",
  "approx_created_at_utc": "...",
  "time_token": "2h",
  "metrics_confidence": "exact|partial|missing",
  "comment_images": [],
  "source": "main|drill",
  "source_locator": { ... }
}
```

### threads_comment_edges.json
```json
{ "parent_comment_id": "...", "child_comment_id": "...", "edge_type": "reply" }
```

## Preanalysis JSON
Produced by `analysis/preanalysis_runner.py` and written to `threads_posts.preanalysis_json`.
```json
{
  "version": "preanalysis_v1",
  "hard_metrics": { ... },
  "per_cluster_metrics": [ ... ],
  "reply_matrix": { ... },
  "physics": { ... },
  "golden_samples": { ... },
  "golden_samples_detail": { ... },
  "quality_flags": { ... },
  "meta": {
    "post_id": 123,
    "bundle_id": "...",
    "bundle_version": "...",
    "cluster_run_id": "...",
    "computed_at": "...",
    "reply_graph_id_space": "internal|source",
    "seed": 42,
    "harvest_coverage_summary": { ... },
    "reply_matrix_accounting": { ... },
    "coverage": { ... },
    "behavior": { ... },
    "sufficiency": { ... },
    "risk": { ... }
  }
}
```

## Behavior Artifact
Stored in `threads_behavior_audits.artifact_json`.
Key fields:
- `behavior_run_id`, `cluster_run_id`, `reply_graph_id_space`
- `quality_flags` with `missing_ts_pct`, `partial_tree`, `edge_coverage`, `data_sufficiency`
- `metrics` (temporal, coordination, graph, engagement, diversity)
- `scores` (temporal_score, coordination_score, graph_score, engagement_score, diversity_score, overall_behavior_risk)
- `sufficiency` (temporal, structural)
- `evidence` (burst windows, coordination events, hub nodes, anomalous edges)
- `ui_budget` (added later by `behavior_budget.py`)

## Risk Brief
Stored in `threads_risk_briefs.brief_json`.
Key fields:
- `risk_run_id`, `behavior_run_id`, `cluster_run_id`
- `verdict.raw_risk_level`, `verdict.confidence`, `verdict.primary_drivers`
- `presentation.effective_level`, `presentation.ui_color`, `presentation.confidence_cap_applied`
- `sections.alerts`, `sections.evidence_refs`, `sections.limitations`

## Claims
Claims are library-first and audited before persistence.

### Claim
```json
{
  "claim_id": "uuid",
  "claim_key": "...",
  "post_id": 123,
  "cluster_key": 0,
  "cluster_keys": [0],
  "run_id": "...",
  "claim_type": "assert|interpret|infer|summarize",
  "scope": "post|cluster|cross_cluster",
  "text": "...",
  "source_agent": "analyst",
  "evidence_ids": ["e1"],
  "evidence_locator_keys": ["threads:comment_id:..."]
}
```

### ClaimPack
```json
{
  "post_id": 123,
  "run_id": "...",
  "claims": [ ... ],
  "meta": { "prompt_hash": "...", "model_name": "...", "audit_verdict": "pass|fail|partial" }
}
```

## Comments By Post Response
Returned by `/api/comments/by-post/{post_id}`.
```json
{
  "post_id": "123",
  "total": 482,
  "items": [
    {
      "id": "cmt_1",
      "text": "...",
      "author_handle": "@user",
      "like_count": 12,
      "reply_count": 3,
      "cluster_key": 4,
      "created_at": "2026-02-10T00:00:00Z"
    }
  ]
}
```

## Comments Search Response
Returned by `/api/comments/search`.
```json
{
  "items": [
    {
      "id": "cmt_1",
      "post_id": "123",
      "text": "...",
      "author_handle": "@user",
      "like_count": 12,
      "reply_count": 3,
      "cluster_key": 4,
      "created_at": "2026-02-10T00:00:00Z"
    }
  ]
}
```

## Casebook Snapshot (Immutable)
Written by `/api/casebook` and read by `/api/casebook`.
```json
{
  "id": "uuid",
  "evidence_id": "cmt_1",
  "comment_id": "cmt_1",
  "evidence_text": "comment text",
  "post_id": "123",
  "captured_at": "2026-02-23T02:00:00Z",
  "bucket": {
    "t0": "2026-02-23T01:20:00Z",
    "t1": "2026-02-23T01:35:00Z"
  },
  "metrics_snapshot": {
    "bucket_comment_count": 42,
    "prev_bucket_comment_count": 15,
    "momentum_pct": 180.0,
    "dominant_cluster_id": 4,
    "dominant_cluster_share": 61.9
  },
  "coverage": {
    "comments_loaded": 300,
    "comments_total": 5234,
    "is_truncated": true
  },
  "summary_version": "casebook_summary_v1",
  "filters": {
    "author": "@analyst",
    "cluster_key": 4,
    "query": "shipping delay",
    "sort": "time_desc"
  },
  "analyst_note": null,
  "created_at": "2026-02-23T02:00:01Z"
}
```
Rules:
- `summary_version` is required (`casebook_summary_v1`) and immutable.
- `coverage.is_truncated` must equal `(comments_total > comments_loaded)` when `comments_total` is known.
- Snapshot fields (`bucket`, `metrics_snapshot`, `coverage`, `summary_version`, `filters`) are immutable once written.
- Only `analyst_note` is mutable after insertion.
- Export uses stored snapshot fields directly (no recomputation).

## Ops KPI
Returned by `/api/ops/kpi`.
```json
{
  "range_days": 7,
  "generated_at": "...",
  "summary": {
    "jobs_total": 10,
    "jobs_success_rate": 0.8,
    "jobs_failed": 2,
    "jobs_inflight": 1,
    "coverage_avg": 0.7,
    "coverage_p50": 0.72,
    "coverage_p90": 0.9,
    "claims_kept_rate": 0.6,
    "claims_audit_fail_rate": 0.2,
    "claims_audit_partial_rate": 0.2,
    "behavior_availability_rate": 0.8,
    "risk_brief_availability_rate": 0.6,
    "llm_timeout_rate": 0.05,
    "llm_error_rate": 0.02,
    "llm_avg_latency_ms": 4200,
    "llm_total_tokens": 123456,
    "llm_tokens_per_post": 890,
    "llm_token_coverage_rate": 0.7
  },
  "trends": [
    { "date": "2026-02-01", "coverage_avg": 0.7, "job_success_rate": 0.8, "llm_calls": 12, "llm_timeout_rate": 0.1 }
  ],
  "sources": { "coverage_rows": 10, "behavior_rows": 8, "claim_audit_rows": 7, "risk_rows": 6, "llm_rows": 12, "job_rows": 10 },
  "truncated": { "coverage": false, "behavior": false, "claim_audits": false, "risk": false, "llm": false, "jobs": false }
}
```

## Analysis Review
Written by `/api/reviews` into `analysis_reviews`.
```json
{
  "post_id": "123",
  "bundle_id": "...",
  "analysis_build_id": "...",
  "label_type": "golden_sample|stance|speech_act|mft|other",
  "schema_version": "v1",
  "decision": { ... },
  "comment_id": "...",
  "cluster_key": 0,
  "notes": "..."
}
```
