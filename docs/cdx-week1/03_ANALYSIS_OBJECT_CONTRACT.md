# 03 Analysis Object Contract

Updated: 2026-03-05

## Topic Run Create Contract (Registry Skeleton)

### Request
```json
{
  "topic_name": "optional",
  "seed_query": "string <= 256",
  "post_ids": [109, 202, 301],
  "seed_post_ids": [109, 202, 301],
  "time_range": {
    "start": "iso8601",
    "end": "iso8601"
  },
  "time_range_start": "iso8601",
  "time_range_end": "iso8601",
  "run_params": {},
  "source": "manual",
  "created_by": "optional"
}
```

Normalization:
- `post_ids` -> unique + numeric sort
- `seed_query` -> trim + collapse spaces (hash lowercases per Topic Contract V1)
- `topic_run_hash` uses `seed_query + canonical time_range + canonical post_ids`

### Response
```json
{
  "status": "accepted",
  "reason": null,
  "reason_code": "accepted_new|idempotent_hit",
  "trace_id": "request-id",
  "topic_id": "uuid",
  "topic_run_id": "uuid",
  "topic_run_hash": "sha256"
}
```

## Topic Run Detail Contract

```json
{
  "status": "ready|pending|failed|not_found|error",
  "reason": "topic_pending|topic_failed|not_found|validation_error|null",
  "reason_code": "topic_pending|topic_failed|not_found|validation_error|null",
  "trace_id": "request-id",
  "topic_id": "uuid",
  "topic_run": {
    "id": "uuid",
    "topic_name": "string",
    "seed_query": "string",
    "seed_post_ids": [109, 202, 301],
    "time_range_start": "iso8601",
    "time_range_end": "iso8601",
    "run_params": {},
    "topic_run_hash": "sha256",
    "lifecycle_hash": null,
    "status": "pending|running|completed|failed|canceled"
  },
  "topic_posts": {
    "post_count": 3,
    "posts_preview": [{"post_id": 109, "ordinal": 0}],
    "limit": 20,
    "offset": 0
  },
  "meta_clusters": {"status": "pending", "items": []},
  "lifecycle": {"status": "pending", "daily": []},
  "managed_lane": {"status": "pending", "summary": null}
}
```

## Topic Worker Run-Once Contract

### Request
```json
{
  "lock_owner": "api-topic-worker",
  "lease_seconds": 600,
  "topic_id": "optional uuid",
  "force_recompute": false
}
```

### Response
```json
{
  "status": "ready|empty|failed|not_found|error|pending",
  "reason": "reason_code_or_null",
  "reason_code": "reason_code_or_null",
  "trace_id": "request-id",
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
- Worker is re-entrant: same `topic_run` recompute must produce identical `stats_json`.
- `stats_json` writeback is overwrite-based, never additive.
- Running locks are lease-based and reclaimable on timeout.
