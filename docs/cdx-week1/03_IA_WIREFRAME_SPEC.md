# Decision-First IA + Wireframe Spec (Week-1)

Last updated: 2026-02-22  
Scope: Track A only (`Detect`, `Investigate`, `Compare`)  
Data SoT: `/Users/tung/Desktop/DLens_26/docs/cdx-week1/02_PARITY_MATRIX.md`

## 1) IA Goal

Turn current UI from status/list presentation into a decision surface with deterministic signals.

Hard rule for each flow:
- 1 primary visual
- 1 primary action
- 1 drill-down path

No speculative AI scoring in week-1 surface.

## 2) Navigation Model (Week-1)

Use existing routes and add one sub-surface:
- `Detect`: `/overview` (plus deep-link to timeline card)
- `Investigate`: `/library` (add comment momentum/investigation drawer)
- `Compare`: `/insights` (add compare mode toggle)

Route hotkeys remain unchanged (`left/right`), transitions remain non-blocking.

## 3) Shared Layout Tokens (Low-fi)

Shell:
- `content-max`: `1280px`
- `grid-main`: `12 columns`, `gap 16`
- `card-radius`: `18`
- `card-min-h`: `160`

Frame stability:
- Route frame always mounted (no blank return).
- Skeleton-first rendering for all panels.
- Right rail fixed width token: `rail-w = 300`.

Panel tokens:
- `panel-primary`: main decision panel
- `panel-support`: supporting metrics/action
- `panel-rail`: context and alerts

## 4) Flow: Detect

Objective:
- Detect if discourse is accelerating or destabilizing, and whether operator should inspect immediately.

Primary visual:
- `Timeline Drift + Comment Momentum` strip (time buckets + momentum bars).

Primary action:
- `Open Investigate Window` (jump to filtered investigation state).

Secondary actions:
- `Refresh snapshot`
- `Pin current post`

Right rail:
- `System Context` state card (`idle/tracking/degraded`)
- `Last Run heartbeat`
- `Risk Chip v1` (deterministic: claims audit + integrity + degraded)

Empty state:
- `No evidence lock available · Waiting for first telemetry window`
- Show skeleton timeline (not blank card).

Drill-down path:
- `Detect panel` -> click anomaly bucket -> `/library?post_id=...&t0=...&t1=...`

Data contract binding:
- `/api/evidence` (`created_at`, `like_count`, `cluster_key`)
- `/api/comments/by-post/{post_id}` (`created_at`, `reply_count`, `like_count`)
- `/api/jobs/{id}/summary` (`last_heartbeat_at`, `degraded`)
- `/api/claims` (`audit`)

## 5) Flow: Investigate

Objective:
- Let analyst move from anomaly signal to inspectable comments/evidence quickly.

Primary visual:
- `Comment Momentum Panel` (time-sorted comments + velocity markers).

Primary action:
- `Save to Casebook` (week-1 local/session persistence is acceptable).

Secondary actions:
- `Filter by cluster`
- `Filter by author`
- `Search comments`

Right rail:
- `Selected Post`
- `Phenomenon (if exists)`
- `Selection count` + quick export/copy id list

Empty state:
- If no query/selection: `Start by selecting a time bucket or entering a query`
- If no results: `No comments matched current filters`

Drill-down path:
- `Momentum row` -> evidence detail card -> `Review prefill` (`comment_id`, `post_id`)

Data contract binding:
- `/api/comments/by-post/{post_id}`
- `/api/comments/search`
- `/api/evidence`
- `/api/claims`
- `/api/reviews` (prefill submission path)

## 6) Flow: Compare

Objective:
- Compare narrative dynamics across posts without adding model-dependent interpretation.

Primary visual:
- `Cross-Post Compare Board`:
  - cluster share delta
  - engagement velocity delta
  - claims audit delta

Primary action:
- `Pin 2nd post for compare`

Secondary actions:
- `Swap baseline`
- `Normalize by total comments`
- `Open source post`

Right rail:
- `Compare Summary` (winner/lagger by deterministic metrics)
- `Data sufficiency flags` (low-sample warning)

Empty state:
- `Select a baseline post and one comparison post`

Drill-down path:
- `Delta cell` -> open `Investigate` filtered view for that cluster and post pair

Data contract binding:
- `/api/posts`
- `/api/clusters`
- `/api/claims`
- `/api/evidence` (optional for evidence count per cluster)

## 7) Right Rail Behavior Contract

Modes (shared across three flows):
- `Idle`: single compact card only.
- `Tracking`: show current post + last run + phenomenon (if truthy).
- `Degraded`: prepend telemetry warning card with heartbeat.

Rendering constraints:
- Do not render placeholder sections with only `-`.
- Phenomenon section collapses when name is empty.

## 8) Component Plan (Low-fi Mapping)

New/extended components:
- `TimelineDriftPanel` (new)
- `CommentMomentumPanel` (new)
- `CompareBoard` (new)
- `RiskChipDeterministic` (new)
- `InvestigateDrawer` (new, fed by detect deep-link)

Reuse:
- `ContextRail`
- `SectionCard`
- `PageHeader`
- existing store selectors and telemetry loop

## 9) Interaction Spec (Minimal)

Detect:
- Click momentum bucket => set `t0/t1` filter and navigate to investigate.

Investigate:
- Right-click comment => `Save to Casebook` + `Mark for Review` menu.
- Multi-select comments => show selection summary in rail.

Compare:
- Select baseline post from existing picker.
- Add comparison post from same picker (second slot).
- Toggle normalization updates charts without route change.

## 10) Week-1 Acceptance for IA

1. Each flow has one primary visual + one primary action + one drill-down path implemented or fully spec-locked.
2. No flow depends on non-formalized contracts (`synthetic_prob`, uncalibrated novelty).
3. Detect -> Investigate drill-down works with deterministic filters.
4. Compare uses only existing active endpoints.
5. Right rail complies with `idle/tracking/degraded` contract with no dash spam.

## 11) Explicit Non-Goals (Week-1)

- No synthetic/human classification UI.
- No L2 tactic confidence UI until taxonomy contract is formalized.
- No fancy topology/cosmetic-only panels.
