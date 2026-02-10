# Data Contracts

This file documents the JSON payloads produced or consumed by the system. It is intended to let new engineers reason about the pipeline without reading code.

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
  "status": "pending|discovering|processing|completed|failed|cancelled",
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
  "status": "pending|processing|completed|failed",
  "stage": "init|fetch|analyst|store|completed|failed",
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
  "analysis_json": { ... },
  "analysis_is_valid": true,
  "analysis_version": "v6.1",
  "analysis_build_id": "...",
  "analysis_invalid_reason": null,
  "analysis_missing_keys": [],
  "phenomenon": { "id": null, "status": "pending", "case_id": null, "canonical_name": null, "source": "default" }
}
```

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
