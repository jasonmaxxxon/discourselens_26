# System Flows

This document describes the operational flows, inputs, outputs, and DB writes. It is aligned to the current code after pruning.

## Flow 1: CLI Fetch + Ingest (S1)
Trigger: `python3 scripts/run_fetcher_and_ingest.py <threads_url>`

Steps:
1. Fetcher loads a Threads URL via Playwright and scrolls until coverage target or plateau.
2. Artifacts are written to a run_dir with `manifest.json`, `threads_posts_raw.json`, `post_payload.json`, `threads_comments.json`, `threads_comment_edges.json`.
3. Ingest writes to SoT tables.

Writes:
- `threads_posts_raw` (one row per run_id/post)
- `threads_posts` (upsert by url)
- `threads_comments` (upsert by comment id)
- `threads_comment_edges` (upsert by edge key)
- `threads_coverage_audits` (coverage telemetry)

Result: A SoT post id is created or updated.

## Flow 2: Pipeline A via API (S1 + S2)
Trigger: POST `/api/run` or HTML form `/run/a`

Steps:
1. JobManager creates a `job_batches` row and `job_items` rows.
2. Worker claims an item using `claim_job_item` RPC.
3. `run_pipeline_a_job` executes:
   - Fetch + ingest (Flow 1)
   - Preanalysis (Flow 3)
   - Optional CIP (if `DL_ENABLE_CIP`)
4. Job item is marked complete; counters updated.

Writes:
- `job_batches`, `job_items`
- All S1 and S2 writes

Result: `analysis_json` exists (at least the deterministic skeleton), job status updates in Ops UI.

## Flow 3: Preanalysis (S2)
Trigger: `run_preanalysis(post_id, prefer_sot=True, persist_assignments=True)`

Steps:
1. Load `threads_posts` and canonical comment bundle (SoT comments + edges).
2. Run quant clustering (SentenceTransformer + PCA + KMeans).
3. Compute hard_metrics, per_cluster_metrics, reply_matrix, physics, golden_samples.
4. Compute behavior side-channel and UI budget.
5. Optionally compute risk brief (requires `DL_ENABLE_RISK_COMPOSER_MIN=1`).
6. Write preanalysis_json and optionally analysis_json skeleton.

Writes:
- `threads_posts.preanalysis_json`
- `threads_comment_clusters` and `threads_comments.cluster_key` when assignments persist
- `threads_reply_matrix_audits`
- `threads_behavior_audits`
- `threads_risk_briefs` (optional)

## Flow 4: Claims-only LLM (S4)
Trigger: `analysis.analyst.generate_commercial_report` with `DL_NARRATIVE_MODE=claims_only`

Steps:
1. Build evidence catalog from preanalysis bundle.
2. LLM generates claims only.
3. Normalize to ClaimPack.
4. Evidence audit drops any claim lacking evidence.
5. Persist claims and claim evidence.

Writes:
- `threads_claims`
- `threads_claim_evidence`
- `threads_claim_audits`
- `threads_posts.analysis_json.meta.claims` and `meta.hypotheses` (if present)
- `llm_call_logs` (telemetry)

Notes:
- This is implemented but not wired into Pipeline A by default.

## Flow 5: Ops KPI
Trigger: GET `/api/ops/kpi?range=7d`

Steps:
1. Aggregate rows from `threads_coverage_audits`, `threads_behavior_audits`, `threads_claim_audits`, `threads_risk_briefs`, `llm_call_logs`, `job_items`.
2. Return summary and daily trends.

Result: Ops dashboard live metrics and trends.
