# CDX Week-1 Scope Lock

Date range: 2026-02-22 to 2026-02-28  
Workspace: `/Users/tung/Desktop/DLens_26`  
Purpose: lock one-week upgrade scope so frontend value matches backend capability, with explicit evidence and acceptance gates.

## Problem Statement

Current UI works as a status board, but it does not expose enough backend intelligence depth for decision workflows.  
Primary gap is parity, not styling.

## Week-1 Outcome (Definition of Done)

By 2026-02-28, we deliver a reviewable upgrade package with:

1. Capability Source of Truth
- `01_CAPABILITY_MAP.md` complete and evidence-backed.

2. Backend-to-UI parity matrix
- Every active backend capability is marked as `Used`, `Partially Used`, or `Unused` in current UI.
- Each `Partially/Unused` row has one explicit proposed surface.

3. Decision-first IA spec for 4 flows
- Flows: `Detect`, `Compare`, `Investigate`, `Act`.
- For each flow: primary action, right-rail behavior, empty-state behavior, drill-down path.

4. One visible product slice implemented in UI
- Candidate slice: `Timeline Evolve v1` using existing deterministic fields only.
- Must run on current data contracts without adding LLM dependencies.

5. Regression safety
- No white bottom edge, no route blocking, no context rail dash spam regression.
- Playwright route sanity + overflow checks passing.

## Scope Boundaries

In scope:
- Inventory and map existing backend capabilities.
- Recompose UI information architecture around decision tasks.
- Productize deterministic intelligence from existing fields.
- Land one minimal but real decision surface.

Out of scope (this week):
- New ML model training.
- New external crawler integrations.
- Full synthetic-vs-human classifier productionization.
- Multi-tenant RBAC and billing.
- Major schema migrations beyond minimal additive fields.

## SoT Ordering (Non-negotiable)

Artifacts must be produced in this order. Each next artifact uses the previous as sole source:

1. `01_CAPABILITY_MAP.md` (data SoT)
2. `02_PARITY_MATRIX.md` (mapping SoT)
3. `03_IA_WIREFRAME_SPEC.md` (experience SoT)
4. `04_WEEKLY_BACKLOG.md` (execution SoT)
5. `05_TIMELINE_EVOLVE_V1_SPEC.md` (module SoT)

## Acceptance Gates

Gate A: Capability evidence gate
- Every mapped capability has file evidence (`path:line`).
- Poll cadence and degraded behavior are documented where applicable.

Gate B: Product gate
- At least one new decision-first UI slice is demonstrably useful on real data.
- The slice has deterministic logic and explicit empty/degraded states.

Gate C: UX stability gate
- Route transitions remain non-blocking.
- No white-edge exposure on viewport bottom during navigation/scroll.
- Context rail remains stateful (`idle`, `tracking`, `degraded`) without placeholder noise.

Gate D: Operability gate
- Manual smoke run across `/overview`, `/pipeline`, `/insights`, `/library`, `/review`.
- Playwright run captures screenshots and no critical console error.

## Metrics (Week-1)

Product parity metrics:
- `Coverage`: `% of active backend capabilities represented in UI` (target >= 80% mapped, >= 40% surfaced).
- `Depth`: `% of surfaced capabilities with drill-down path` (target >= 60%).

UX quality metrics:
- `Route blocking`: no blank frame before skeleton appears.
- `White edge`: 0 occurrences in route-switch screenshot set.
- `Context noise`: 0 placeholder cards with only `-` in idle state.

Engineering metrics:
- `Deterministic slice`: at least 1 implemented with tests or deterministic assertions.
- `Regression`: no new console errors in navigation smoke.

## Risks and Controls

Risk: over-scoping into six-week roadmap in one sprint  
Control: enforce single visible slice this week; keep other outputs as spec/backlog.

Risk: capability mapping drift as code changes  
Control: all rows require `path:line`; treat map as living SoT.

Risk: UI polish consumes delivery bandwidth  
Control: prioritize decision utility over cosmetic expansion.

## Daily Plan (Compressed)

Day 1:
- Finalize capability map and parity matrix baseline.

Day 2:
- Freeze IA for `Detect/Compare/Investigate/Act`.

Day 3:
- Technical spec for `Timeline Evolve v1`; define data model and deterministic calculations.

Day 4:
- Implement timeline slice and wire into existing insights workflow.

Day 5:
- QA, Playwright visual/assertion pass, docs update, release candidate.

## Ownership (Current)

- Backend mapping owner: `webapp/routers/*` + `webapp/services/*` maintainers.
- Frontend parity owner: `dlcs-ui/src/pages/*`, `dlcs-ui/src/components/*`, `dlcs-ui/src/hooks/*`.
- QA owner: Playwright MCP + manual smoke checklist.

## Exit Criteria

Week-1 closes only when:

1. All five SoT docs exist and are internally consistent.
2. One implemented decision surface is merged and demo-ready.
3. Regression checks are attached as artifacts (screenshots/log summary).
