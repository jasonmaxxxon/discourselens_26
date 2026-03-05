# System Overview

## Purpose
DiscourseLens ingests Threads posts, builds a deterministic preanalysis layer, generates audited claims using LLMs, computes behavior and risk signals, and exposes Ops observability. The system is designed for auditability and controlled writeback, not for raw “creative” narrative generation.

## Current Scope
- Fetcher → SoT ingest is active.
- Preanalysis (deterministic) is active.
- Claims-only LLM path is active.
- Behavior side-channel + risk brief is active.
- Ops KPI + job tracking is active.
- Stitch UI primary routes are active (`/overview`, `/pipeline`, `/insights`, `/library`, `/review`) and remain post-centric.
- Topic Contract v1 + Phase-2 Topic SoT tables are provisioned.
- Week-1 deterministic decision UI is active (`Detect`, `Investigate`, `Compare`) using existing APIs and deep-link filters.

## Explicitly Disabled
- Phenomenon enrichment is hard‑disabled by `DL_ENABLE_PHENOMENON_ENRICHMENT=false`.
- Vision/OCR is removed from the runtime path (no image inference).
- Legacy narrative writer is removed from repo.

## Source of Truth
- Posts: `public.threads_posts`
- Comments: `public.threads_comments`
- Reply graph: `public.threads_comment_edges`

These tables are the SoT for all downstream processing. All other tables are derived artifacts.

## Key Invariants
- SoT writes are append/update only; derived artifacts should not mutate SoT semantics.
- Semantic writeback is guarded by run_id + allowlist in `database/integrity.py`.
- Claims without evidence are dropped (not persisted).
- Risk brief is rules-based and capped by data sufficiency.

## Known Gaps
Pipeline B/C depend on missing modules. The following files are referenced but not present in this repo:
- `pipelines/core.py`
- `event_crawler.py`
- `home_crawler.py`

Topic frontend surface is not wired yet:
- Backend has registry skeleton routes: `POST /api/topics/run`, `GET /api/topics/{topic_id}`.
- Backend has worker skeleton route: `POST /api/topics/worker/run-once` (lease lock + deterministic snapshot stats).
- No topic run/detail page in frontend yet.

## Performance Expectations
- Initial value should be visible within 1–5 seconds via deterministic data (counts, cluster stats, coverage).
- LLM claims may take longer; Ops KPI captures latency and failure rates.

## Optimization Targets
- Reduce Time‑to‑First‑Value by surfacing preanalysis results early.
- Keep LLM calls minimal and audited; track token usage in `llm_call_logs`.
- Improve coverage ratio by tuning fetcher budgets and plateau detection.
- Track and expose crawl freshness lag (`freshness_lag_seconds`) as an explicit ops signal for topic/lifecycle trust.
- Keep decision surfaces deterministic-first with explicit low-sample warnings before any model-derived labeling is exposed.
