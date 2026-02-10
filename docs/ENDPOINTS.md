# HTTP Endpoints

Base URL: `http://127.0.0.1:8000`
All JSON endpoints return JSON. Errors use standard HTTP status codes and may include a `detail` field.

## JSON API (`/api/*`)

### GET /api/ops/kpi
Purpose: Ops KPI summary and trend metrics.
Request: Query param `range` like `7d`, `30d`, `90d`. Default `7d`.
Response: See `docs/CONTRACTS.md#ops-kpi`.

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
Response:
```json
{
  "analysis_json": { ... },
  "analysis_is_valid": true,
  "analysis_version": "v6.1",
  "analysis_build_id": "...",
  "analysis_invalid_reason": null,
  "analysis_missing_keys": [],
  "phenomenon": { "id": null, "status": "pending", "case_id": null, "canonical_name": null, "source": "default" }
}
```

### GET /api/analysis/{post_id}
Purpose: Legacy full_report markdown.
Response: `{ "post_id": "...", "full_report_markdown": "..." }`

### GET /api/comments/by-post/{post_id}
Purpose: Comment list for a post.
Request params: `limit` (<=500), `offset`, `sort=likes|time`.
Response: `{ "post_id": "...", "total": 123, "items": [ ... ] }`

### GET /api/comments/search
Purpose: Search comments.
Request params: `q`, `author_handle`, `post_id`, `limit` (<=200).
Response: `{ "items": [ ... ] }`

### GET /api/library/phenomena
Purpose: Phenomenon registry lookup (currently disabled in pipeline).
Request params: `status`, `q`, `limit` (<=500).
Response: Array of registry items.

### GET /api/library/phenomena/{phenomenon_id}
Purpose: Phenomenon detail + related posts.
Response: `{ "meta": {...}, "stats": {...}, "recent_posts": [...] }`

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
