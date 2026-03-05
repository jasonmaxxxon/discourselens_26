# Timeline Evolve v1 Technical Spec

Last updated: 2026-02-22  
Scope: Week-1 deterministic implementation only  
Upstream SoT:
- `/Users/tung/Desktop/DLens_26/docs/cdx-week1/03_IA_WIREFRAME_SPEC.md`
- `/Users/tung/Desktop/DLens_26/docs/cdx-week1/01_CAPABILITY_MAP.md`
- `/Users/tung/Desktop/DLens_26/docs/cdx-week1/02_PARITY_MATRIX.md`

## A) Problem Statement and Non-Goals

Current UI shows pipeline status and static evidence slices, but does not expose time-evolving discourse dynamics for operator decisions. `Timeline Evolve v1` productizes existing deterministic fields into two actionable surfaces: drift timeline (Detect) and momentum investigation (Investigate), with deep-link continuity and low-sample safety guards.

Non-goals for v1:
- No synthetic probability labels.
- No uncalibrated lexical novelty or L2 tactic scoring in UI.
- No model-driven intervention recommendation.
- No new endpoint creation unless contract is blocked.

## B) Data Model

### `TimelineBucket`
- `bucket_start_iso: string` (ISO8601 UTC)
- `bucket_end_iso: string` (ISO8601 UTC)
- `comment_count: number`
- `evidence_count: number`
- `like_sum: number`
- `reply_sum: number`
- `engagement_sum: number` (`like_sum + reply_sum`)
- `distinct_authors: number`
- `momentum_score: number` (`0..100`)
- `drift_score: number` (`0..100`)
- `anomaly_flag: boolean`

### `MomentumSignal`
- `latest_bucket_momentum: number` (`0..100`)
- `momentum_delta_1: number` (latest - previous)
- `velocity_ratio_1: number` (`latest_engagement_per_min / prev_engagement_per_min`, fallback `1`)
- `trend: "up" | "flat" | "down"`

### `DriftSignal`
- `latest_drift: number` (`0..100`)
- `drift_delta_1: number`
- `author_churn_ratio: number` (`new_authors / max(1, distinct_authors)`)
- `cluster_mix_shift: number` (`0..1`, Jensen-Shannon distance on cluster mix, fallback strategy defined below)

### `SufficiencyFlags`
- `has_min_comments: boolean`
- `has_min_evidence: boolean`
- `has_multi_bucket: boolean`
- `has_cluster_mix: boolean`
- `sufficient_for_compare: boolean`
- `warnings: string[]` (human-readable deterministic warnings)

## C) Bucket Strategy

Input window:
- Default range from Detect:
  - If job active: last `90m`
  - Else: last `24h`
- Investigate deep-link can override via `t0/t1`.

Bucket size `auto` heuristic:
- Let `window_minutes = ceil((t1 - t0)/60s)`.
- If `window_minutes <= 90` -> `bucket = 5m`
- If `90 < window_minutes <= 360` -> `bucket = 15m`
- If `360 < window_minutes <= 1440` -> `bucket = 60m`
- Else -> `bucket = 180m`

Safety:
- Target bucket count range: `8..36`.
- If computed bucket count < 8, halve bucket size once.
- If > 36, double bucket size once.

## D) Deterministic Formulas

### D1) Per-bucket aggregation

From comments and evidence in bucket:
- `engagement_sum = like_sum + reply_sum`
- `engagement_per_min = engagement_sum / max(1, bucket_minutes)`
- `evidence_density = evidence_count / max(1, comment_count)`
- `author_density = distinct_authors / max(1, comment_count)`

### D2) Momentum score (`0..100`)

Normalize each component to `0..1` across current window using min-max:
- `E = norm(engagement_per_min)`
- `C = norm(comment_count)`
- `R = norm(reply_sum / max(1, comment_count))`

Formula:
- `momentum_raw = 0.5*E + 0.3*C + 0.2*R`
- `momentum_score = round(clamp(momentum_raw,0,1)*100)`

### D3) Drift score (`0..100`)

Components:
- `A = author_churn_ratio` compared vs previous bucket (`new authors / current distinct authors`)
- `M = cluster_mix_shift` (Jensen-Shannon distance between cluster distribution current vs previous)
- `V = abs(momentum_delta_1) normalized to window max delta`

Formula:
- `drift_raw = 0.45*M + 0.35*A + 0.20*V`
- `drift_score = round(clamp(drift_raw,0,1)*100)`

Fallbacks:
- If cluster keys unavailable or sparse: set `M` from evidence-only cluster distribution.
- If still unavailable: `M = 0` and add warning `"cluster mix unavailable"`.
- If previous bucket missing: compute provisional drift from `A` only and set warning `"single-bucket drift provisional"`.

### D4) Anomaly thresholding and low-sample gating

Minimum sufficiency thresholds (Week-1 lock):
- `MIN_COMMENTS_TOTAL = 30`
- `MIN_EVIDENCE_TOTAL = 8`
- `MIN_BUCKETS = 4`
- `MIN_COMMENTS_PER_BUCKET_FOR_FLAG = 5`

Rules:
- `sufficient_for_compare = has_min_comments && has_min_evidence && has_multi_bucket`
- A bucket `anomaly_flag = true` only if:
  - `comment_count >= MIN_COMMENTS_PER_BUCKET_FOR_FLAG`
  - and (`momentum_score >= p85(momentum_score)` or `drift_score >= p85(drift_score)`)
- If not sufficient, panel shows warning and suppresses anomaly highlights.

## E) API Payload Requirements

### `GET /api/comments/by-post/{post_id}`
Required fields:
- `id`, `text`, `author_handle`, `like_count`, `reply_count`, `created_at`
- existing `total` also used for sufficiency checks.

### `GET /api/evidence?post_id=...`
Required fields:
- `id`, `evidence_id`, `cluster_key`, `author_handle`, `like_count`, `created_at`, `text`

### `GET /api/jobs/{id}/summary`
Required fields:
- `status`, `last_heartbeat_at`, `degraded`, `processed_count`, `failed_count`

### `GET /api/claims?post_id=...`
Required fields:
- `audit.verdict`, `audit.kept_claims_count`, `audit.dropped_claims_count`, `audit.created_at`

### `GET /api/posts`
Required fields for compare list:
- `id`, `snippet`, `like_count`, `reply_count`, `created_at`, `phenomenon_id`

### `GET /api/clusters?post_id=...`
Required fields for compare:
- `cluster_key`, `share`, `engagement.likes`, `engagement.replies`, `sample_total`

### `GET /api/comments/search`
Required fields for investigate filter refinement:
- `id`, `post_id`, `text`, `author_handle`, `like_count`, `created_at`

## F) UI Contract

### Detect primary visual
- Render timeline buckets with two overlays:
  - momentum bars
  - drift line/markers
- Each bucket click emits deep-link query to Investigate.

### Detect -> Investigate deep-link contract

Route:
- `/library`

Query parameters:
- `post_id: string` (required for drill-down; fallback current selected post)
- `t0: string` ISO8601 UTC (required for drill-down)
- `t1: string` ISO8601 UTC (required for drill-down)
- `cluster_key: number` (optional)
- `author: string` (optional)
- `q: string` (optional text query)

Default behavior:
- Missing `t0/t1`: library opens standard mode without time filter.
- Invalid `t0/t1`: ignore invalid value and show warning chip.
- Missing `post_id`: fallback to currently selected post; if unavailable, prompt user to select post.

### Investigate panel behavior
- Apply time window filter first.
- Then apply optional `cluster_key`, `author`, `q`.
- Show compact filter summary pills and a reset action.

### Empty and degraded states
- No evidence lock: `No evidence lock available · Underspecified`
- No comments in range: `No comments matched current window`
- Degraded telemetry: `Telemetry degraded. Showing latest stable snapshot.`

## G) Performance Budget

Render budget:
- `TimelineDriftPanel p95 render <= 120ms` for `<= 2,000 comments + 500 evidence rows`.
- Interaction (bucket click -> filtered render) `p95 <= 80ms` excluding network.

Payload ceilings (week-1):
- `/api/comments/by-post` request target <= `300KB` response body.
- `/api/evidence` request target <= `250KB` response body.
- If response exceeds target, UI limits window and prompts narrower range.

Caching and refresh:
- Reuse current telemetry cadences:
  - active 2500ms, idle 15000ms, hidden 15000ms.
- Timeline panels do not create new global polling loops.
- On hidden tab, suppress recompute-heavy aggregations until visible.
- Respect degraded header/payload and avoid aggressive refetch on errors.

## H) Validation Checklist (Week-1)

- [ ] Detect panel always mounts frame instantly (skeleton-first).
- [ ] Bucket click generates valid deep-link with `post_id,t0,t1`.
- [ ] Library reads deep-link and applies time filter deterministically.
- [ ] Sufficiency warnings appear when thresholds are not met.
- [ ] Anomaly markers are suppressed under low-sample mode.
- [ ] No dependency on synthetic/novelty/tactic-calibration fields.
- [ ] Route transitions remain non-blocking and no white-edge regression.

## I) Open Questions / Risks

1. `comments/by-post` currently uses limit/offset; large windows may need pagination strategy for stable p95.
2. `evidence.created_at` completeness may vary; missing timestamps reduce drift fidelity.
3. Cross-post compare may require explicit normalization toggle defaults to prevent misread.
4. Author identity normalization (`author_handle`) may need stricter canonicalization for churn accuracy.
5. If cluster keys are sparse in evidence, drift fallback becomes less informative and should be labeled clearly.

## DOCUMENTATION UPDATE (CHANGE-AWARE)

This CDX introduces changes in the following areas:
- [ ] Endpoint behavior / responses
- [ ] Data schema / payload shape
- [x] Ownership or source-of-truth rules
- [x] Dataflow (sync/async, enrichment, background jobs)
- [x] Lifecycle / state transitions

For each checked item, the corresponding integration documents
MUST be updated:

- Endpoint changes -> `/Users/tung/Desktop/DLens_26/docs/ENDPOINTS.md`
- Schema/payload changes -> `/Users/tung/Desktop/DLens_26/docs/CONTRACTS.md`
- Flow or ownership changes -> `/Users/tung/Desktop/DLens_26/docs/FLOWS.md`
- Lifecycle or edge-case changes -> `/Users/tung/Desktop/DLens_26/docs/SYSTEM_OVERVIEW.md`

The CDX is NOT considered complete
until all impacted documents reflect the new reality.
