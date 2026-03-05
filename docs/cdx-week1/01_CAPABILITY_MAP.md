# Backend Capability Map (Week-1 SoT)

Last updated: 2026-02-22  
Scope: API capabilities currently present in repository and their frontend usage parity.

## Source Evidence

Primary backend sources:
- `/Users/tung/Desktop/DLens_26/webapp/routers/api.py`
- `/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py`
- `/Users/tung/Desktop/DLens_26/webapp/services/job_manager.py`
- `/Users/tung/Desktop/DLens_26/docs/ENDPOINTS.md`
- `/Users/tung/Desktop/DLens_26/docs/CONTRACTS.md`

Primary frontend sources:
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/hooks/useTelemetryLoop.ts`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/components/MainLayout.tsx`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/components/ContextRail.tsx`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/OverviewPage.tsx`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/PipelinePage.tsx`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/InsightsPage.tsx`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/LibraryPage.tsx`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/ReviewPage.tsx`

## Runtime Cadence and State Semantics

Telemetry/polling cadences:
- Global telemetry loop: active `2500ms`, idle `15000ms`, hidden `15000ms`  
  Evidence: `/Users/tung/Desktop/DLens_26/dlcs-ui/src/hooks/useTelemetryLoop.ts:5`
- Pipeline page poll: visible `3000ms`, hidden `15000ms`  
  Evidence: `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/PipelinePage.tsx:150`
- Overview phenomenon poll: visible `6000ms`, hidden `20000ms`  
  Evidence: `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/OverviewPage.tsx:90`

Degraded signaling:
- Backend jobs APIs emit header `x-ops-degraded: 1` and `Cache-Control: max-age=2`  
  Evidence: `/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:26`, `/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:33`, `/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:93`
- Frontend parses header in `requestWithMeta`  
  Evidence: `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts:56`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts:100`

Job status normalization:
- Canonicalized states include `queued`, `discovering`, `processing`, `stale`, `completed`, `failed`, `canceled`  
  Evidence: `/Users/tung/Desktop/DLens_26/webapp/services/job_manager.py:166`

## Capability Inventory

Legend:
- `UI Surface`: where capability is currently surfaced.
- `Depth`: `High` = actionable with drill-down; `Med` = visible but shallow; `Low` = wrapper exists or basic read only.
- `Availability`: `Active`, `Legacy`, `Debug`, or `Deprecated`.

### A) Core Capabilities Used by UI

| Capability | API | Key Fields | Owner | Update/Freshness | Availability | UI Surface | Depth | Notes |
|---|---|---|---|---|---|---|---|---|
| Job queue snapshot | `GET /api/jobs/?limit=20` | `id,status,pipeline_type,total_count,processed_count,updated_at,items[]` | `jobs router + JobManager` (`/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:19`) | Telemetry 2.5s active, 15s idle/hidden | Active | Topbar, drawer, pipeline list, store snapshot (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/hooks/useTelemetryLoop.ts:47`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/PipelinePage.tsx:175`) | High | Header-aware degraded supported. |
| Job detail | `GET /api/jobs/{job_id}` | full `JobStatusResponse` + items preview | `jobs router + JobManager` (`/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:51`) | Pulled for active/preferred job during polling | Active | Pipeline current run and topbar selection (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/PipelinePage.tsx:206`) | High | Drives stage and progress visuals. |
| Job summary with degraded flag | `GET /api/jobs/{job_id}/summary` | `status,total_count,processed_count,failed_count,last_heartbeat_at,degraded` | `jobs router + JobManager` (`/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:80`) | 2.5s/3s active loops; cache max-age=2 | Active | Store `jobSummary`, degraded banner/rail (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/hooks/useTelemetryLoop.ts:106`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/PipelinePage.tsx:225`) | High | Degraded detected by header and payload. |
| Cancel run | `POST /api/jobs/{job_id}/cancel` | updated `JobStatusResponse` | `jobs router + JobManager` (`/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:101`) | On operator action | Active | Pipeline stop button + drawer cancel (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/PipelinePage.tsx:400`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/components/MainLayout.tsx:172`) | High | Operator control exists in two surfaces. |
| Create run | `POST /api/jobs/` | `pipeline_type,mode,input_config` | `jobs router + JobManager` (`/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:37`) | On submit | Active | Pipeline run form + insights rerun (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/PipelinePage.tsx:372`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/InsightsPage.tsx:320`) | High | Supports optimistic insert in Pipeline page. |
| Post list for analysis-ready content | `GET /api/posts` | `id,snippet,url,metrics,analysis metadata,phenomenon fields` | `api router + runner.supabase` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:650`) | Loaded on page entry; cached in localStorage by pages | Active | Insights, Library, Review pickers (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/InsightsPage.tsx:143`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/LibraryPage.tsx:165`) | Med | No global shared post cache/store yet. |
| Structured analysis payload | `GET /api/analysis-json/{post_id}` | `analysis_json`, `analysis_is_valid`, `analysis_version`, `phenomenon` | `api router + runner.supabase` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:768`) | Loaded on post select | Active | Insights and Review (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/InsightsPage.tsx:196`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/ReviewPage.tsx:74`) | Med | Major backend intelligence exists, UI still partial consumption. |
| Cluster intelligence | `GET /api/clusters?post_id=&limit=&sample_limit=` | `clusters[]` with `label,summary,share,keywords,tactics,samples,engagement,coords` | `api router + clustering/engagement assembly` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1010`) | On post select | Active | Insights explorer + Library ingest (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/InsightsPage.tsx:197`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/LibraryPage.tsx:213`) | Med | Rich payload available; comparison/time evolution not surfaced yet. |
| Claim inventory | `GET /api/claims?post_id=` | `claims[]`, `audit` verdict block | `api router + claims/audits tables` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:849`) | On post select | Active | Insights claim chips, Library grouping, Review claim panel (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/ReviewPage.tsx:74`) | Med | Audit verdict surfaced but no intervention logic. |
| Evidence retrieval | `GET /api/evidence?post_id|claim_id` | `items[]` with `evidence_id,text,author,like_count,cluster_key,claim fields` | `api router + claim evidence join` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:889`) | On post select | Active | Insights timeline list + Library graph (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/InsightsPage.tsx:199`, `/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/LibraryPage.tsx:213`) | Med | Deterministic top10 logic is implemented in FE helper. |
| Phenomenon registry list | `GET /api/library/phenomena` | `id,canonical_name,status,total_posts,last_seen_at` | `api router + narrative_phenomena` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:813`) | Overview poll every 6s/20s | Active | Overview `Recent Intelligence` + `Registry Pulse` (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/OverviewPage.tsx:98`) | Med | Not linked to deeper compare workflow yet. |
| Review writeback | `POST /api/reviews` | `post_id,bundle_id,label_type,decision,comment_id,notes` | `api router + analysis_reviews` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1430`) | On operator submit | Active | Review form submit (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/pages/ReviewPage.tsx:117`) | Low | Basic write path; no casebook or right-click capture yet. |

### B) Capabilities Available but Not Surfaced (or not wired)

| Capability | API | Key Fields | Owner | Availability | Current UI Usage | Gap |
|---|---|---|---|---|---|---|
| Ops KPI trends | `GET /api/ops/kpi` | ops counters/trends by range | `api router + ops_metrics` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:495`) | Active | Wrapper exists only (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts:171`) | No trend surface on Overview despite endpoint existing. |
| Phenomenon detail | `GET /api/library/phenomena/{id}` | `meta,stats,recent_posts` | `api router` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1338`) | Active | Wrapper exists only (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts:192`) | Missing drill-down from overview/library into phenomenon case page. |
| Promote phenomenon status | `POST /api/library/phenomena/{id}/promote` | status transition to `active` | `api router` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1401`) | Active | Not used | No analyst action layer for registry governance. |
| Job items direct list | `GET /api/jobs/{job_id}/items` | item-level statuses/stages | `jobs router + JobManager` (`/Users/tung/Desktop/DLens_26/webapp/routers/jobs.py:62`) | Active | Not used | UI relies on embedded items in job detail only. |
| Plain listJobs wrapper | `GET /api/jobs/?limit=20` via non-meta request | jobs only without degraded header parse | FE API wrapper | Active | Not used (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts:176`) | Keep or remove to avoid duplicate paths. |
| Plain getJobSummary wrapper | `GET /api/jobs/{id}/summary` via non-meta request | summary without header parse | FE API wrapper | Active | Not used (`/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts:179`) | Meta version is used; plain wrapper likely dead code. |
| Comment list by post | `GET /api/comments/by-post/{post_id}` | paginated comments with sorting | `api router` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1520`) | Active | Not used | Can power timeline evolve and comparative comment movement. |
| Comment search | `GET /api/comments/search` | text/author search results | `api router` (`/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1548`) | Active | Not used | Useful for investigative drill-down and saved watchlists. |

### C) Legacy/Debug/Deprecated Capabilities

| Capability | API | Availability | Evidence | Notes |
|---|---|---|---|---|
| Legacy run trigger (A) | `POST /api/run` | Legacy | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:538` | Kept for compatibility; jobs API preferred. |
| Legacy run trigger (A/B/C) | `POST /api/run/{pipeline}` | Legacy | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:558` | Compatibility wrapper only. |
| Legacy status compatibility | `GET /api/status/{job_id}` | Legacy | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:619` | Returns JobResult-shaped payload. |
| Legacy markdown analysis | `GET /api/analysis/{post_id}` | Legacy | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1643` | Structured `analysis-json` is preferred. |
| Debug latest post | `GET /api/debug/latest-post` | Debug | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1566` | Dev debugging only. |
| Debug phenomenon match | `GET /api/debug/phenomenon/match/{post_id}` | Debug/conditional | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1472` | Depends on optional modules. |
| Debug backfill phenomenon | `POST /api/debug/phenomenon/backfill_from_json` | Debug | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1597` | Backfill utility endpoint. |
| Deprecated GET run/batch | `GET /api/run/batch` | Deprecated | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1668` | Always 404. |
| Pipeline B batch API | `POST /api/run/batch` | Conditional/fragile | `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:513`, `/Users/tung/Desktop/DLens_26/docs/ENDPOINTS.md:14` | Marked blocked by missing modules in docs. |

## Frontend Surface Map (Current)

Routes:
- `/overview`, `/pipeline`, `/insights`, `/library`, `/review`  
  Evidence: `/Users/tung/Desktop/DLens_26/dlcs-ui/src/App.tsx:16`

Shared shell:
- Global telemetry loop is enabled on non-pipeline routes  
  Evidence: `/Users/tung/Desktop/DLens_26/dlcs-ui/src/components/MainLayout.tsx:53`
- Context rail has explicit modes `idle`, `tracking`, `degraded`  
  Evidence: `/Users/tung/Desktop/DLens_26/dlcs-ui/src/components/ContextRail.tsx:25`

Current data-to-surface mapping:
- Overview: jobs snapshot + phenomenon registry pulse.
- Pipeline: run orchestration and near-real-time status.
- Insights: post-centric narrative package + clusters/claims/evidence.
- Library: evidence grouping, claim linkage, quality tiers.
- Review: claim audit snapshot and manual review submission.

## Capability Parity Summary (Current)

High parity:
- Run orchestration and job telemetry (`create`, `list`, `detail`, `summary`, `cancel`).

Medium parity:
- Narrative analysis read paths (`analysis-json`, `clusters`, `claims`, `evidence`, `phenomena list`) are present but mostly single-post and static.

Low parity:
- Comparative and temporal capabilities are not productized despite available primitives (`comments/by-post`, `comments/search`, phenomenon detail, ops KPI).
- Action layer is minimal (review submit only).

## Immediate Leverage Points (for Week-1 build)

1. Timeline Evolve v1
- Data source candidates already available: `evidence.created_at`, `comments/by-post.created_at`, `job summary timestamps`.

2. Cross-post compare v1
- Data source candidates: `clusters.share`, `clusters.engagement`, `claims.audit`, per-post metrics from `/api/posts`.

3. Investigate panel
- Use `/api/comments/search` + saved filters for operator drill-down.

4. Context-to-action parity
- Extend current context rail from passive telemetry to explicit actions (`open timeline`, `open compare`, `open review case`).

## Open Risks

- The richest backend metadata in `analysis_json.meta` is not fully normalized in UI, making behavior/risk interpretation shallow.
- Legacy and active endpoints coexist; without explicit deprecation policy, frontend drift risk remains.
- No formal casebook object yet, so analyst actions are not accumulating into reusable evidence sets.
