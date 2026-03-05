# Backend -> UI Parity Matrix (Week-1)

Last updated: 2026-02-22  
Source of Truth dependency: `/Users/tung/Desktop/DLens_26/docs/cdx-week1/01_CAPABILITY_MAP.md`

## Scoring Rules

- Operational Value:
  - `High`: directly changes analyst judgment or intervention timing.
  - `Medium`: improves investigation quality but not immediate decision.
  - `Low`: peripheral observability or cosmetic utility.
- Gap Type (strict enum):
  - `Visualization Gap`
  - `Depth Gap`
  - `Workflow Gap`
  - `Missing Contract`
- Track:
  - `A`: deterministic, shippable in week-1.
  - `B`: needs contract/calibration or broader backend formalization.

## Matrix

| Capability | API | Data Fields | Update Freq | Current UI Surface | Depth | Operational Value | Gap Type | Proposed Surface | Track | Week | Risk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Job queue telemetry | `GET /api/jobs/?limit=20` | `id,status,processed_count,total_count,updated_at` | 2.5s active, 15s idle/hidden | Topbar + drawer + pipeline queue | High | High | Depth Gap | Add queue trend sparkline + stale-detection alert reason | A | W1 | Low |
| Job run summary + degraded | `GET /api/jobs/{id}/summary` | `status,failed_count,last_heartbeat_at,degraded` | 2.5s/3s active polling | Pipeline + context rail degraded | High | High | Depth Gap | Show heartbeat age and degraded cause tag in rail | A | W1 | Low |
| Run control | `POST /api/jobs/`, `POST /api/jobs/{id}/cancel` | `pipeline_type,mode,input_config,status` | Event-driven | Pipeline run/stop actions | High | High | Workflow Gap | Add action confirmation + cancel reason log entry | A | W1 | Low |
| Analysis package | `GET /api/analysis-json/{post_id}` | `analysis_json,meta,risk,coverage,behavior` | On post switch | Insights + Review | Medium | High | Depth Gap | Promote deterministic meta blocks into decision cards | A | W1 | Medium |
| Cluster intelligence | `GET /api/clusters` | `cluster_key,share,engagement,keywords,tactics,samples,coords` | On post switch | Insights cluster explorer, Library ingest | Medium | High | Depth Gap | Cross-post cluster compare lens (share/engagement delta) | A | W1 | Medium |
| Evidence timeline primitives | `GET /api/evidence` | `created_at,like_count,cluster_key,evidence_id,text` | On post switch | Insights top10 evidence list | Medium | High | Visualization Gap | `Timeline Drift v1` (bucketed evidence momentum + stage markers) | A | W1 | Medium |
| Claims + audit verdict | `GET /api/claims` | `claims[],audit.verdict,kept/dropped` | On post switch | Insights chips + Review snapshot | Medium | High | Depth Gap | Risk chip bound to deterministic audit counters | A | W1 | Low |
| Comments by post | `GET /api/comments/by-post/{post_id}` | `text,author_handle,like_count,reply_count,created_at` | On demand | None | None | High | Workflow Gap | Casebook feed seed + time-sorted comment movement panel | A | W1 | Medium |
| Comments search | `GET /api/comments/search` | `q,author_handle,post_id,like_count` | On demand | None | None | Medium | Workflow Gap | Investigate drawer with saved queries/watchlist | A | W1 | Medium |
| Post catalog | `GET /api/posts` | `snippet,url,metrics,analysis_version,phenomenon_id` | On page load | Post pickers in 3 pages | Medium | Medium | Depth Gap | Unified global post selector + compare pinning | A | W1 | Low |
| Phenomenon registry list | `GET /api/library/phenomena` | `canonical_name,status,total_posts,last_seen_at` | 6s visible, 20s hidden (overview) | Overview cards | Medium | Medium | Depth Gap | Add drill-down CTA to compare related posts | A | W1 | Low |
| Phenomenon detail | `GET /api/library/phenomena/{id}` | `meta,stats,recent_posts` | On demand | No UI use | None | High | Visualization Gap | Phenomenon detail pane with recurrence timeline | A | W1 | Medium |
| Phenomenon promote | `POST /api/library/phenomena/{id}/promote` | `status transition` | On operator action | No UI use | None | Medium | Workflow Gap | Library action: promote with audit note | B | W2 | Medium |
| Ops KPI trends | `GET /api/ops/kpi` | range aggregates/trends | On demand | No UI use | None | Low | Visualization Gap | Small ops card only after decision surfaces land | B | W3 | Low |
| Job items direct | `GET /api/jobs/{id}/items` | item-level `stage,status,error_log` | On demand | Not directly used | Low | Medium | Depth Gap | Expandable per-item trace table in pipeline | B | W2 | Medium |
| Review writeback | `POST /api/reviews` | `label_type,decision,comment_id,notes` | On submit | Review form | Low | Medium | Workflow Gap | Right-click comment -> prefilled review/case action | B | W2 | Medium |
| L2 tactics from clusters/meta | `GET /api/clusters`, `GET /api/analysis-json/{id}` | `tactics,tactic_summary,behavior flags` | On post switch | Partial text only | Low | High | Missing Contract | Formalize tactic taxonomy + confidence contract before UI escalation | B | W3 | High |
| Synthetic probability per comment | (No stable endpoint yet) | `synthetic_prob,detector_version,confidence` | N/A | None | None | High | Missing Contract | Add endpoint + governance before surfacing labels | B | W3 | High |
| Lexical novelty / AI contamination proxy | (present in artifacts, not formal API) | novelty metrics, thresholds | N/A | None | None | Medium | Missing Contract | Contract + calibration report before risk UI use | B | W3 | High |
| Legacy run/status wrappers | `POST /api/run`, `POST /api/run/{pipeline}`, `GET /api/status/{id}` | legacy job result shape | Legacy compatibility | Not used in SPA | None | Low | Workflow Gap | Keep hidden; mark deprecation path in runbook | B | W4 | Low |

## Prioritized Deterministic Upgrades (Top 3)

Ordered by `Operational Value -> Deterministic Readiness -> Implementation Cost`:

1. `Timeline Drift v1` from evidence/comment timestamps  
   Capability base: `/api/evidence` + `/api/comments/by-post/{post_id}`  
   Why now: high value, deterministic, no new model required.

2. `Cross-post Compare v1` from cluster share/engagement and post metrics  
   Capability base: `/api/clusters` + `/api/posts` + `/api/claims`  
   Why now: exposes backend depth with minimal contract change.

3. `Risk Chip v1` from deterministic audit + integrity fields  
   Capability base: `/api/claims` audit + `analysis_json.meta` + job degraded signal  
   Why now: immediate decision utility, low implementation risk.

## Track Guidance

- Track A (this week): deliver visible decision-chain surfaces using deterministic data only.
- Track B (next): formalize contracts for synthetic detection, tactics taxonomy, and novelty calibration before UI claims become stronger.
