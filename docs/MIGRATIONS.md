# SQL Migrations (Current)

This is a file-level index of the SQL migrations currently present in the repo.
For full DDL, read the SQL files themselves.

## database/migrations

- `database/migrations/2025-12-22_google_embedding_768.sql`
  - create function: match_phenomena_v768
  - alter table: public.narrative_phenomena

- `database/migrations/2025-axis-reviews-generic-labeling.sql`
  - alter table: analysis_reviews

- `database/migrations/2025-cluster-semantic-writeback.sql`
  - alter table: public.threads_comment_clusters

- `database/migrations/2025-cluster-tactics.sql`
  - alter table: public.threads_comment_clusters

- `database/migrations/2025-comment-identity-hardening.sql`
  - alter table: public.threads_comments

- `database/migrations/2025-enrichment-hardening.sql`
  - alter table: public.threads_posts

- `database/migrations/2025-phenomenon-backfill.sql`

- `database/migrations/2025-phenomenon-registry-increment.sql`
  - create function: increment_occurrence

- `database/migrations/2025-phenomenon-registry.sql`
  - create table: narrative_phenomena, narrative_phenomenon_aliases, narrative_phenomenon_relations
  - create function: match_phenomena
  - alter table: narrative_phenomena, threads_posts

- `database/migrations/2025-threads-comment-clusters.sql`
  - create table: public.threads_comment_clusters
  - alter table: public.threads_comments

- `database/migrations/2025-threads-comments.sql`
  - create table: public.threads_comments
  - alter table: public.threads_comments

- `database/migrations/20250101_cdx_083a_fix_increment_occurrence_rpc.sql`
  - create function: public.increment_occurrence
  - alter table: public.narrative_phenomena

- `database/migrations/2026-threads-cluster-interpretations.sql`
  - create table: public.threads_cluster_interpretations

- `database/migrations/2026-threads-comments-source-locator.sql`
  - alter table: public.threads_comments

## supabase/migrations

- `supabase/migrations/20251223050252_remote_schema.sql`
  - create table: public.analysis_final, public.discourseLens, public.evidence_bank, public.narrative_phenomena, public.raw_ocr, public.threads_comment_clusters, public.threads_comments, public.threads_posts
  - create function: public.increment_occurrence, public.increment_phenomenon_occurrence, public.match_phenomena, public.match_phenomena_v768, public.touch_updated_at
  - alter table: ONLY, public.analysis_final, public.discourseLens, public.evidence_bank, public.narrative_phenomena, public.raw_ocr, public.threads_comment_clusters, public.threads_comments, public.threads_posts

- `supabase/migrations/20251223060752_remote_schema.sql`

- `supabase/migrations/20251223143300_cdx_081_vision_gating.sql`
  - alter table: public.threads_posts

- `supabase/migrations/20251223170000_cdx_083b_fix_increment_occurrence_rpc.sql`
  - create function: public.increment_occurrence
  - alter table: public.narrative_phenomena

- `supabase/migrations/20251224_cdx_314_add_from_top_snapshot.sql`
  - alter table: public.threads_comments

- `supabase/migrations/20251231_cdx114_upsert_comment_clusters_384.sql`
  - create function: public.upsert_comment_clusters

- `supabase/migrations/20260105_v7_quant_audit.sql`
  - create table: public.v7_quant_clusters, public.v7_quant_runs

- `supabase/migrations/20260112_v7_cluster_signals_table.sql`
  - create table: public.v7_cluster_signals

- `supabase/migrations/20260113_axis_unexplained.sql`
  - create table: axis_unexplained

- `supabase/migrations/20260114_threads_comments_v7.sql`
  - alter table: public.threads_comments

- `supabase/migrations/20260122045035_20260122_pipeline_a_sot_fix.sql`
  - create table: public.threads_comment_edges, public.threads_posts_raw
  - alter table: public.threads_comment_edges, public.threads_comments

- `supabase/migrations/20260201000000_canonical_comment_bundle.sql`
  - create table: public.threads_comment_cluster_assignments
  - alter table: public.threads_comment_edges, public.threads_comments

- `supabase/migrations/20260201001000_analysis_reviews.sql`
  - create table: public.analysis_reviews

- `supabase/migrations/20260201002000_preanalysis_columns.sql`
  - alter table: public.threads_posts

- `supabase/migrations/20260206000000_cluster_diagnostics.sql`
  - create table: public.threads_cluster_diagnostics

- `supabase/migrations/20260208000000_cdx_s4_claims_evidence_audit.sql`
  - create table: public.threads_claim_audits, public.threads_claim_evidence, public.threads_claims

- `supabase/migrations/20260208010000_cdx_s4_1v2_library_claims.sql`
  - alter table: public.threads_claim_audits, public.threads_claim_evidence, public.threads_claims

- `supabase/migrations/20260209000000_cdx_s5_behavior_sidechannel.sql`
  - create table: public.threads_behavior_audits

- `supabase/migrations/20260209010000_cdx_s6_risk_composer_min.sql`
  - create table: public.threads_risk_briefs

- `supabase/migrations/20260209020000_cdx_coverage_control_plane.sql`
  - create table: public.threads_coverage_audits

- `supabase/migrations/20260209030000_cdx_102_llm_call_logs.sql`
  - create table: public.llm_call_logs

- `supabase/migrations/20260209040000_cdx_reply_matrix_accounting.sql`
  - create table: public.threads_reply_matrix_audits

- `supabase/migrations/20260223000000_casebook_snapshot.sql`
  - create table: public.analyst_casebook

- `supabase/migrations/20260223010000_casebook_forensic_hardening.sql`
  - alter table: public.analyst_casebook
  - create trigger/function: snapshot immutability enforcement

- `supabase/migrations/20260226150000_topic_engine_phase2_sot.sql`
  - create table: public.topic_runs, public.topic_posts, public.topic_meta_clusters, public.topic_lifecycle_daily
  - create indexes: topic run/meta/lifecycle query paths
  - add FK: topic_lifecycle_daily(topic_run_id, meta_cluster_key) -> topic_meta_clusters

- `supabase/migrations/20260305163000_topic_worker_locking.sql`
  - alter table: public.topic_runs
  - add columns: lock_owner, locked_at, heartbeat_at, lock_lease_seconds, attempt_count
  - add check: topic_runs_lock_lease_seconds_ck
  - add indexes: status+updated, running+heartbeat

## Topic Merge Gates (Phase-3)

- `make topic:migration_smoke`
  - runs `scripts/migration_smoke_topic_phase2.py`
  - validates topic schema presence + CRUD + deterministic hash roundtrip + key constraints

- `make topic:api_contract`
  - runs `scripts/verify_topic_api_contract.py`
  - validates Topic API registry contract (`POST /api/topics/run`, `GET /api/topics/{id}`) and provenance headers

- `make topic:worker_smoke`
  - runs `scripts/verify_topic_worker_smoke.py`
  - validates worker status transitions, lock/lease behavior, deterministic stats overwrite, and never-500 envelope
