# HTTP Endpoints

Base URL: `http://127.0.0.1:8000`
All JSON endpoints return JSON.

Global runtime provenance for every `/api/*` response:
- `X-Request-ID`: trace id (preserved from inbound header when present, otherwise generated)
- `X-Build-SHA`: backend build commit hash
- `X-Env`: runtime environment name

Pending responses include `Retry-After` and payload `{status:"pending", reason, trace_id, ...}`.
Empty-but-valid responses return HTTP 200 with `{status:"empty", reason:"no_data", trace_id, ...}`.
Only internal bugs return HTTP 500 and include `{status:"error", trace_id, ...}`.

## JSON API (`/api/*`)

### GET /api/ops/kpi
Purpose: Ops KPI summary and trend metrics.
Request: Query param `range` like `7d`, `30d`, `90d`. Default `7d`.
Response: See `docs/CONTRACTS.md#ops-kpi`.

### GET /api/health
Purpose: Lightweight readiness probe for backend boot gating.
Response: `{ "status": "ok" }`

### GET /api/_meta/build
Purpose: Runtime build identity for drift detection and smoke gates.
Response:
```json
{
  "status": "ok",
  "build_sha": "6f13301",
  "build_time": "2026-02-26T02:17:04.741052Z",
  "env": "dev",
  "version": "0.0.0"
}
```
Must never return 500.

### POST /api/topics/run
Purpose: Register immutable Topic Run snapshot (Phase-3 skeleton, registry only).
Request body (minimal):
```json
{
  "seed_query": "public health subsidy rumor",
  "post_ids": [109, 202, 301],
  "time_range": {
    "start": "2026-02-01T00:00:00Z",
    "end": "2026-02-07T00:00:00Z"
  }
}
```
Supported aliases:
- `seed_post_ids` can be used instead of `post_ids`.
- `time_range_start` / `time_range_end` can be used instead of nested `time_range`.

Response:
- `200 accepted` on new insert or idempotent hash hit.
- `400 validation_error` for bad payload (empty post_ids, invalid topic_id, invalid time range, missing posts).
- `200 pending` when backend/topic tables are temporarily unavailable.
Never returns `500` (topic-specific never-500 envelope).

### GET /api/topics/{topic_id}
Purpose: Read topic snapshot registry row + posts preview (no meta-cluster/lifecycle compute yet).
Response:
- `200 ready|pending|failed` with `topic_run` and `topic_posts`.
- `404 not_found` when topic id does not exist.
- `400 validation_error` when topic_id is malformed UUID.
Never returns `500` (topic-specific never-500 envelope).

Notes:
- Contract SoT: `docs/TOPIC_CONTRACT_V1.md`
- Schema SoT: `supabase/migrations/20260226150000_topic_engine_phase2_sot.sql`
- Current phase only covers registry; no cross-post compute/materialization in these endpoints.

### GET /api/overview/telemetry
Purpose: Backend-owned Overview telemetry model for Timeline Drift / Comment Momentum cards.
Request params: `window` in hours text (`24h` default, clamped to `1h..168h`).
Response: See `docs/CONTRACTS.md#overviewtelemetryresponse`.
Headers: `x-ops-degraded: 1` on partial data.
Ownership: Drift and momentum semantics are defined in backend; frontend must render payload directly (no semantic recompute).

### POST /api/run/batch
Purpose: Pipeline B backend API (batch). Currently blocked by missing modules.
Request body (JSON): See `PipelineBBatchRequest` in `docs/CONTRACTS.md#pipeline-b-batch-request`.
Response: Summary object with counts and logs.
Notes: Requires `pipelines/core.py`, `event_crawler.py` to exist.

### POST /api/run
Purpose: Legacy Pipeline A trigger (uses Supabase JobManager).
Request body:
```json
{ "url": "https://www.threads.net/@.../post/..." }
```
Response:
```json
{ "job_id": "<uuid>", "status": "pending", "pipeline": "A" }
```

### POST /api/run/{pipeline}
Purpose: Legacy wrapper for Pipeline A/B/C (uses Supabase JobManager).
Request body:
- Pipeline A: `{ "url": "...", "mode": "analyze" }`
- Pipeline B: `{ "keyword": "...", "urls": ["..."], "max_posts": 50 }`
- Pipeline C: `{ "max_posts": 50, "threshold": 0 }`
Response: `{ "job_id": "<uuid>", "status": "pending", "pipeline": "A|B|C" }`

### GET /api/status/{job_id}
Purpose: Legacy compatibility endpoint for job status (Supabase-backed).
Response: JobResult shape (see `docs/CONTRACTS.md#jobresult-legacy`).

### GET /api/posts
Purpose: Latest posts with analysis_json (up to 20 rows).
Response: Array of PostListItem (see `docs/CONTRACTS.md#postlistitem`).
Notes: Does not filter on `analysis_is_valid`.

### GET /api/analysis-json/{post_id}
Purpose: Primary analysis_json endpoint.
Status contract:
- `200 ready` with `status="ready"`, `trace_id`, and analysis payload.
- `202 pending` with `status="pending"`, `reason="asset_not_ready"|"upstream_transport"`, `trace_id`, `retry_after_ms`.
- `404 not_found` with `status="not_found"`, `reason="post_not_found"`, `trace_id`.
- `500 error` only for internal bugs and includes `status="error"`, `trace_id`.

### GET /api/analysis/{post_id}
Purpose: Legacy full_report markdown.
Response: `{ "post_id": "...", "full_report_markdown": "..." }`

### GET /api/claims
Purpose: Claims list + latest audit summary for a post.
Request params: `post_id` (required), `limit` (<=500), `cluster_key`, `status`.
Status contract:
- `200 ready|empty`
- `202 pending` (`reason="asset_not_ready"|"upstream_transport"`)
- `404 not_found`
- `500 error` (internal only)
Response: `{ "status": "...", "trace_id": "...", "claims": [ ... ], "audit": { ... } }`

### GET /api/evidence
Purpose: Evidence rows for claims (by post or claim).
Request params: `post_id` or `claim_id` (one required), `cluster_key`, `limit` (<=500).
Status contract:
- `200 ready|empty`
- `202 pending` (`reason="asset_not_ready"|"upstream_transport"`)
- `404 not_found`
- `500 error` (internal only)
Response: `{ "status": "...", "trace_id": "...", "post_id": "...", "items": [ ... ], "claims": [ ... ] }`

### GET /api/clusters
Purpose: Cluster metadata + samples for a post.
Request params: `post_id` (required), `limit` (<=60), `sample_limit` (<=12).
Status contract:
- `200 ready|empty`
- `202 pending` (`reason="asset_not_ready"|"upstream_transport"`)
- `404 not_found`
- `500 error` (internal only)
Response: `{ "status": "...", "trace_id": "...", "clusters": [ ... ], "total_comments": 123 }`

### GET /api/clusters/{post_id}/graph
Purpose: Cluster explorer graph view-model for Insights center canvas.
Request params: `limit` (<=60).
Status contract: `200 ready|empty`, `202 pending`, `404 not_found`, `500 error`.
Response: See `docs/CONTRACTS.md#clustergraphresponse`.
`status="empty"` with `reason="no_relation_edges_yet"` means nodes exist but no semantic links yet.
Headers: `x-ops-degraded: 1` when link derivation is partial.
Ownership: Node/link/coords are backend SoT for graph layout; frontend should not synthesize graph semantics.

### GET /api/comments/by-post/{post_id}
Purpose: Comment list for a post.
Request params: `limit` (<=500), `offset`, `sort=likes|time`.
Response: `{ "post_id": "...", "total": 123, "items": [ ... ] }`

### GET /api/comments/search
Purpose: Search comments.
Request params: `q`, `author_handle`, `post_id`, `limit` (<=200).
Response: `{ "items": [ ... ] }`

### POST /api/casebook
Purpose: Persist immutable deterministic casebook snapshot (Supabase SoT).
Request body: `{ evidence_id, comment_id, evidence_text, post_id, captured_at, bucket, metrics_snapshot, coverage, summary_version, filters, analyst_note? }`.
Response: `{ "status": "recorded", "id": "...", "created_at": "..." }`

### GET /api/casebook
Purpose: List casebook snapshots for export/review.
Request params: `post_id` (optional), `limit` (<=500).
Response: `{ "items": [ ... ] }`
Headers: `x-ops-degraded: 1` when DB snapshot list is temporarily unavailable (response falls back to empty list).

### GET /api/library/phenomena
Purpose: Phenomenon registry lookup (currently disabled in pipeline).
Request params: `status`, `q`, `limit` (<=500).
Response: Array of registry items.

### GET /api/library/phenomena/{phenomenon_id}
Purpose: Phenomenon detail + related posts.
Status contract: `200 ready|empty`, `202 pending`, `404 not_found`, `500 error`.
Response: `{ "status": "...", "trace_id": "...", "meta": {...}, "stats": {...}, "recent_posts": [...] }`

### GET /api/library/phenomena/{phenomenon_id}/signals
Purpose: Backend semantic aggregation for library occurrence timeline + related signals.
Request params: `window` in hours text (`24h` default, clamped to `1h..168h`).
Response: See `docs/CONTRACTS.md#phenomenonsignalsresponse`.
Headers: `x-ops-degraded: 1` on partial aggregation.
Ownership: Replaces frontend `/api/claims + /api/evidence` signal assembly. Sorting/strength semantics are backend-owned.

### POST /api/library/phenomena/{phenomenon_id}/promote
Purpose: Promote a phenomenon from `provisional` to `active`.
Response: `{ "ok": true, "id": "...", "status": "active" }`

### POST /api/reviews
Purpose: Write analysis review labels (repurposed from axis reviews).
Request body: See `docs/CONTRACTS.md#analysis-review`.
Response: `{ "status": "recorded" }`

### GET /api/debug/phenomenon/match/{post_id}
Purpose: Debug matching against phenomenon registry.
Response: Debug payload with candidates.
Notes: Requires phenomenon/embedding modules (currently removed).

### POST /api/debug/phenomenon/backfill_from_json
Purpose: Backfill phenomenon fields from analysis_json.
Request params: `limit`.
Response: `{ "ok": true, "rows_scanned": N, "rows_updated": M }`

### GET /api/debug/latest-post
Purpose: Debug view of latest threads_posts row.
Response: `{ "id": ..., "url": ..., "analysis_json": ... }`

### GET /api/run/batch
Purpose: Deprecated endpoint. Always returns 404.

## Jobs API (`/api/jobs/*`)

### GET /api/jobs/?limit=20
Purpose: List recent jobs.
Response: Array of JobStatusResponse (see `docs/CONTRACTS.md#jobstatusresponse`).

### POST /api/jobs/
Purpose: Create a job and start worker.
Request body: JobCreate (see `docs/CONTRACTS.md#jobcreate`).
Response: JobStatusResponse with `items` populated.

### GET /api/jobs/{job_id}
Purpose: Job detail.
Response: JobStatusResponse.

### GET /api/jobs/{job_id}/items?limit=200
Purpose: Job items list.
Response: Array of JobItemPreview.
Headers: `x-ops-degraded` may be set to `1` if data is stale.

### GET /api/jobs/{job_id}/summary
Purpose: Aggregated counters and heartbeat.
Response: `{ job_id, status, total_count, processed_count, success_count, failed_count, last_item_updated_at, last_heartbeat_at, degraded }`.

### POST /api/jobs/{job_id}/cancel
Purpose: Operator cancel for an in-flight/queued run.
Response: JobStatusResponse (status will become `canceled` when accepted).
Notes:
- Best-effort cancel. Current worker stops claiming new items once batch status is `canceled`.
- Pending/processing `job_items` are marked `canceled`.

## HTML Console (Jinja)

### GET /
Purpose: Console landing page with Pipeline forms.

### GET /run/a
Purpose: Redirect to `/` to avoid 405.

### POST /run/a
Purpose: Trigger Pipeline A and redirect to `/ops/jobs?job_id=...`.

### POST /run/b
Purpose: Legacy Pipeline B using in-memory job_store.

### POST /run/c
Purpose: Legacy Pipeline C using in-memory job_store.

### GET /status/{job_id}
Purpose: Legacy status HTML for pipeline runs.

### GET /proxy_image?url=...
Purpose: Proxy image with browser-like headers (best-effort).
