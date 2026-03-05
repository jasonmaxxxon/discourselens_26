# 01 Endpoint Catalog

Updated: 2026-03-05

## Topic Registry Endpoints (CDX-TOPIC-API-001)

### POST /api/topics/run
Purpose: Create immutable topic snapshot registry row (`topic_runs`) and seed membership rows (`topic_posts`).

Request (minimal):
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

Response:
- `200` + `status="accepted"` (new or idempotent)
- `400` + `reason_code="validation_error"`
- `200` + `status="pending"` when backend/table unavailable
- Never returns `500`

Headers (required):
- `X-Request-ID`
- `X-Build-SHA`
- `X-Env`

## Topic Worker Endpoint (CDX-TOPIC-WORKER-001)

### POST /api/topics/worker/run-once
Purpose: Claim one topic run with lease lock and compute deterministic snapshot stats.

Request:
```json
{
  "lock_owner": "api-topic-worker",
  "lease_seconds": 600,
  "topic_id": "optional uuid",
  "force_recompute": false
}
```

Response:
- `200` + `status="ready"` when one run is processed
- `200` + `status="empty"` when no claimable run exists
- `200` + `status="failed"` when worker processing fails for claimed run
- `404` + `status="not_found"` when `topic_id` not found
- `400` + `reason_code="validation_error"` for invalid input
- Never returns `500`

Headers (required):
- `X-Request-ID`
- `X-Build-SHA`
- `X-Env`

### GET /api/topics/{topic_id}
Purpose: Read topic run registry details and posts preview.

Response:
- `200` + `status="ready|pending|failed"`
- `404` + `status="not_found"`
- `400` + `reason_code="validation_error"`
- Never returns `500`

Headers (required):
- `X-Request-ID`
- `X-Build-SHA`
- `X-Env`
