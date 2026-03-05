# Week-1 Integration + E2E Gate Report

Last updated: 2026-02-22

## Scope Completed

- Detect integration (`/overview`)
  - `TimelineDriftPanel` + `CommentMomentumPanel` rendered in Detect section.
  - Bucket click deep-links to `/library` with `post_id,t0,t1` (+ optional `cluster_key`).
- Investigate integration (`/library`)
  - Query contract consumed: `post_id,t0,t1,cluster_key,author,q`.
  - Comment momentum panel added.
  - Right-click context menu supports `Save to Casebook` (session storage) + `Mark for Review`.
- Compare integration (`/insights`)
  - Compare toggle and post selector added.
  - Deterministic compare board with sufficiency warnings (`comments<30`, `evidence<8`).
- E2E gate script updated:
  - Added `deepLinkBucketNavigatesToLibraryWithFilters`.

## 10s Interaction List (C/D verification)

1. Open `/overview`, wait for Detect panel frame + skeleton.
2. Confirm timeline bucket row renders.
3. Click one timeline bucket.
4. Verify navigation to `/library` with `post_id,t0,t1` in URL.
5. Confirm investigate panel reflects selected time window.
6. Right-click a comment -> choose `Save to Casebook`.
7. Switch to `/insights`, enable compare mode.
8. Pick second post and check compare board + warning chips.

## Build Evidence

Command:
- `npm --prefix /Users/tung/Desktop/DLens_26/dlcs-ui run build`

Result:
- Passed (TypeScript + Vite build successful).

## Playwright Gate Evidence

Command:
- `npm --prefix /Users/tung/Desktop/DLens_26/dlcs-ui run audit:ui`

Latest report:
- `/Users/tung/Desktop/DLens_26/artifacts/playwright-audit/suite_20260222_191711/suite_report.json`

Key checks:
- `allImmediateRouteFrame = true`
- `bottomWhiteEdgeSuspect = false`
- `iframeCount = 0`
- `deepLinkBucketNavigatesToLibraryWithFilters = true`

Notes:
- Runtime console still shows backend 500s from existing data/API health (`/api/library/phenomena`, `/api/evidence`, `/api/comments/by-post`) in this environment.
- This does not block required Week-1 visual/flow gates, but backend stability should be tracked.

## DOCUMENTATION UPDATE (CHANGE-AWARE)

This CDX introduces changes in the following areas:
- [ ] Endpoint behavior / responses
- [x] Data schema / payload shape
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
