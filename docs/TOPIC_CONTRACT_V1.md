# Topic Contract V1

Last updated: 2026-02-26  
Status: Draft for implementation (Phase 0 + Phase 2 baseline)

## 1. Purpose

Define deterministic, auditable contracts for Topic/Case execution so cross-post outputs are:

- traceable
- evidence-backed
- reproducible
- safe for decision surfaces

This contract is for **Topic Engine V1 (immutable snapshot runs)**.

## 2. Scope and Non-Goals

In scope:
- deterministic topic run inputs and outputs
- canonical hash strategy
- Topic SoT physical tables
- lifecycle and managed/organic score contracts
- evidence-link integrity requirements

Out of scope (V1):
- automatic AI keyword expansion
- incremental mutable topic runs
- black-box classifier labels (for example "bot" or "state actor")

## 3. Core Run Model

`topic_run` is an immutable snapshot:
- fixed input window
- fixed seed set
- fixed deterministic parameters
- fixed hash outputs

New ingestion data must create a new topic run.  
V1 does not rewrite previous run outputs.

## 4. API Contract (V1)

## 4.1 `POST /api/topics/run` (proposed)

Request shape:

```json
{
  "topic_name": "Public health subsidy rumor",
  "seed_query": "public health subsidy rumor",
  "seed_post_ids": [109, 202, 301],
  "time_range_start": "2026-02-01T00:00:00Z",
  "time_range_end": "2026-02-07T00:00:00Z",
  "run_params": {
    "max_posts": 200,
    "bucket_granularity": "day",
    "meta_cluster_algo": "kmeans",
    "meta_cluster_k": 6,
    "embedding_model_version": "text-embedding-3-large@2026-02-01"
  },
  "source": "manual"
}
```

Response shape:

```json
{
  "status": "accepted",
  "topic_run_id": "uuid",
  "topic_run_hash": "sha256",
  "trace_id": "request-id"
}
```

## 4.2 `GET /api/topics/{id}` (proposed)

Response shape:

```json
{
  "status": "ready|pending|failed|not_found",
  "trace_id": "request-id",
  "topic_run": {
    "id": "uuid",
    "topic_name": "string",
    "topic_run_hash": "sha256",
    "lifecycle_hash": "sha256|null",
    "time_range_start": "iso8601",
    "time_range_end": "iso8601",
    "freshness_lag_seconds": 420,
    "coverage_gap": false,
    "stats_json": {}
  }
}
```

## 5. Canonical Hash Contract

All hashes must use:
- UTF-8
- JSON with `sort_keys=true`
- JSON separators `(",", ":")`
- SHA-256 hex digest

## 5.1 `topic_run_hash`

Canonical payload:

```json
{
  "seed_query": "<normalized_query>",
  "time_range_start": "iso8601",
  "time_range_end": "iso8601",
  "post_ids": [sorted unique int]
}
```

Normalization:
- query: trim -> collapse whitespace -> lowercase
- post ids: unique + numeric sort

## 5.2 `meta_cluster_hash`

Canonical payload:

```json
{
  "cluster_ids": ["sorted unique post_id::c<cluster_key>"]
}
```

## 5.3 `lifecycle_hash`

Canonical payload:

```json
{
  "meta_cluster_hashes": ["sorted unique sha256"],
  "daily_dominance_matrix": [
    {
      "day_utc": "YYYY-MM-DD",
      "meta_cluster_key": 0,
      "dominance_share": 0.61
    }
  ]
}
```

Normalization:
- rows sorted by `(day_utc, meta_cluster_key)`
- `dominance_share` rounded to 6 decimals before hashing

## 6. Deterministic Gate Contract

V1 gates are deterministic only:
- lexical anchor gate
- embedding similarity gate
- evidence-linked relevance gate

LLM can propose candidates but cannot directly mutate Topic SoT.

## 7. Managed/Organic Score Contract (V1)

V1 stores deterministic continuous scores in `[0,1]`:
- `managed_score`
- `organic_score`
- `drift_score`

Suggested deterministic managed score:

`managed_score = clamp(0.35*burst + 0.25*duplication + 0.25*author_concentration + 0.15*hub_dominance, 0, 1)`

Where:
- burst: synchronized time concentration
- duplication: near-template text density
- author_concentration: Gini/top-k share style concentration
- hub_dominance: reply graph out-degree dominance

`organic_score` can be set as deterministic complement (`1 - managed_score`) in V1, unless explicitly calibrated otherwise.

## 8. Lifecycle Contract

Per day, per meta-cluster:
- `dominance_share`
- `comment_count`
- `evidence_count`
- `drift_score`
- `lifecycle_stage` in `{birth,growth,peak,decline,dormant}`

V1 supports daily granularity only (`day_utc`).

## 9. Evidence-Link Integrity (Non-negotiable)

Any topic-level claim or risk attribute must be traceable through:

`topic_meta_cluster -> post_cluster -> comment -> evidence`

Minimum persisted evidence link payload:
- `topic_run_id`
- `meta_cluster_key`
- `post_id`
- `comment_id`
- `evidence_id`
- `capture_hash` (if available)

No trace link, no persistence.

## 10. Operational SLO/SLA (V1)

- Freshness SLA target: p95 lag <= 30 minutes (can tighten to 15 minutes after crawl stability)
- Determinism gate: same input rerun 10x => hash match 100%
- Trace integrity gate: broken-link rate = 0%

## 11. Golden Hash Fixtures

Reference fixture hashes used by `scripts/verify_topic_contract_golden.py`:

- `topic_run_hash`: `8dda00551d23b08f1e78ed8db5a4150c7f6934e0ad1fbd2c3b766dda29538c5d`
- `meta_cluster_hash[0]`: `a049d0aa10e1a440a84aebf2822f9fcb87b24b56f0b21d3542fd7a631b9886d8`
- `meta_cluster_hash[1]`: `674a775ca9571e5bd3a109eaf047f52b1cfa5484f9cacce60e8401bdff5638ae`
- `lifecycle_hash`: `9a3b158310339c0d6f4c7ec4879927cc5d8aa0bc9116c3f61397c1afc0ecc49e`

## 12. Change Policy

Any change to canonical hash payload or normalization requires:
- contract version bump
- golden fixture update
- explicit migration note

