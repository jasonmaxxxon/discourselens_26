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

## Flow 6: Stitch MVP UI (Overview / Pipeline / Insights / Library / Review)
Trigger: SPA routes `/overview`, `/pipeline`, `/insights`, `/library`, `/review`

Steps:
1. Global top bar is persistent; page containers are kept mounted for seamless route transitions.
2. Overview (`/overview`) consumes backend-owned telemetry:
   - `GET /api/overview/telemetry?window=24h`
   - `GET /api/jobs/?limit=20`
   - `GET /api/library/phenomena?limit=60`
   - `GET /api/posts`
   and renders drift/momentum/context directly from backend payload.
3. Pipeline (`/pipeline`) consumes real job state only:
   - `GET /api/jobs/?limit=20`
   - `GET /api/jobs/{job_id}`
   - `POST /api/jobs/`
   - `POST /api/jobs/{job_id}/cancel`
   with queued-run click selecting the active center card and real live log rows.
4. Insights (`/insights`) consumes cluster semantics from backend:
   - `GET /api/analysis-json/{post_id}`
   - `GET /api/clusters?post_id=...`
   - `GET /api/clusters/{post_id}/graph`
   - `GET /api/claims?post_id=...`
   - `GET /api/comments/by-post/{post_id}`
   and uses graph node selection to update right-side stack/diagnostics.
5. Library (`/library`) consumes backend semantic aggregation for signals:
   - `GET /api/library/phenomena`
   - `GET /api/library/phenomena/{id}`
   - `GET /api/library/phenomena/{id}/signals?window=24h`
   - `POST /api/library/phenomena/{id}/promote`
   replacing frontend claims/evidence signal assembly.
6. Review (`/review`) uses evidence/comments/claims streams for card + inspector rendering and review submission.

Writes:
- `job_batches`, `job_items` through Jobs API.
- `analyst_casebook` through `/api/casebook` (where used in review/investigate path).
- `analysis_reviews` through `/api/reviews`.

Ownership / SoT:
- Overview drift/momentum semantics: backend.
- Insights graph layout (`nodes/links/coords`): backend.
- Library related signals/timeline scoring and ordering: backend.

Result:
- UI becomes a view-model renderer over backend contracts, reducing silent semantic drift from frontend recomputation.

## Flow 7: UI Audit Gate (Boot + Wait + Gate)
Trigger: `npm run audit:ui:gate` from repo root.

Steps:
1. Start backend with `uvicorn webapp.main:app --host <api-host> --port <api-port>`.
2. Start frontend in deterministic mode:
   - default: `npm run build` then `npm run preview -- --host localhost --strictPort --port 5173`
   - optional: `AUDIT_UI_FRONTEND_MODE=dev npm run audit:ui:gate`
3. Wait for readiness checks:
   - Backend: `GET /api/health` must return HTTP 200.
   - Frontend: `GET /` at `http://localhost:5173` (fallback probe `http://127.0.0.1:5173`) must return HTTP 200.
4. Run Playwright gate suite (`dlcs-ui/scripts/playwright_suite.mjs`) using reachable `UI_BASE_URL`.
5. Always cleanup spawned backend/frontend processes after pass/fail.

Artifacts:
- Backend gate log: `logs/audit_gate_backend.log`
- Frontend gate log: `dlcs-ui/logs/audit_gate_frontend.log`
- Playwright report directory: `artifacts/playwright-audit/suite_<timestamp>/`

Result:
- Single-command deterministic gate with pass/fail exit code and no orphaned dev servers.

## Flow 8: Runtime Provenance + Never-500 Envelope
Trigger: Any `/api/*` request from UI, curl, or Playwright.

Steps:
1. Request middleware sets or preserves `X-Request-ID` and stores it as request trace.
2. Response middleware appends:
   - `X-Request-ID`
   - `X-Build-SHA`
   - `X-Env`
3. Endpoint handlers classify failures:
   - `UPSTREAM_TRANSPORT` -> `202 pending` with `Retry-After`.
   - `ASSET_NOT_READY` -> `202 pending`.
   - valid empty data -> `200 empty`.
   - missing resource -> `404 not_found`.
   - internal bug -> `500 error` (still JSON with `trace_id`).
4. Frontend renders provenance state explicitly:
   - `pending` -> `PENDING BACKEND`.
   - `empty` -> `NO_DATA_YET`.
   - `error` -> `ERROR` with trace id.
   No silent demo fallback for failed real-data calls.
5. Playwright contract gate verifies:
   - `/api/_meta/build` availability.
   - build headers on `/api/*`.
   - target endpoints return only `200|202|404` for known transient/not-ready conditions.

Result:
- Runtime drift becomes observable immediately.
- UI trust is tied to backend contract states, not hidden fallback data.

## Flow 9: Topic SoT Materialization (Phase-2 schema)
Trigger: Supabase migration `20260226150000_topic_engine_phase2_sot.sql`

Steps:
1. Provision immutable topic snapshot tables:
   - `topic_runs`
   - `topic_posts`
   - `topic_meta_clusters`
   - `topic_lifecycle_daily`
2. Enforce deterministic and integrity constraints:
   - unique `topic_run_hash`
   - per-run unique `meta_cluster_key` and `meta_cluster_hash`
   - lifecycle score range checks (`managed/organic/drift` in `[0,1]`)
   - FK chain from lifecycle rows to meta clusters
3. Add query-path indexes for run status, run timeline, meta-cluster dominance, and lifecycle day scans.

Writes:
- DDL only (no runtime data ingestion in this phase).

Result:
- Topic-level SoT is physically provisioned for `/api/topics/*` registry routes and upcoming cross-post compute workers.
- Current phase still does not materialize meta-clusters/lifecycle compute in runtime workers.

## Flow 10: Topic Registry API Skeleton (Phase-3)
Trigger:
- `POST /api/topics/run`
- `GET /api/topics/{topic_id}`

Steps:
1. Validate deterministic inputs (`seed_query`, `post_ids`, optional time range aliases).
2. Canonicalize:
   - `post_ids`: unique + sorted numeric list.
   - time range: canonical UTC ISO8601.
3. Compute `topic_run_hash` using Topic Contract V1 canonical hash discipline.
4. Insert immutable registry row (`topic_runs`) and seed membership rows (`topic_posts`).
   - On hash collision, return existing run (`idempotent_hit`).
5. Return envelope payload with trace id and hash.
6. For `GET`, return run metadata + posts preview; meta/lifecycle lanes remain `pending` placeholders.

Writes:
- `topic_runs`
- `topic_posts`

Status semantics:
- `200 accepted` for create.
- `200 pending` for transient/unavailable backend states.
- `400 validation_error` for bad input.
- `404 not_found` for missing topic id.
- Never `500` for these endpoints.

## Flow 11: Topic Merge Gates (Migration + API Contract)
Trigger:
- `make topic:migration_smoke`
- `make topic:api_contract`

Steps:
1. `scripts/migration_smoke_topic_phase2.py`
   - verify topic tables exist
   - CRUD insert/read for `topic_runs` + `topic_posts`
   - recompute `topic_run_hash` roundtrip
   - verify unique/check constraints
2. `scripts/verify_topic_api_contract.py`
   - verify topic POST create + idempotent canonical hit
   - verify topic GET shape
   - verify bad id -> 400 and missing id -> 404
   - verify provenance headers (`X-Request-ID`, `X-Build-SHA`, `X-Env`)

Result:
- Topic schema and API contracts become executable merge gates instead of document-only assumptions.
