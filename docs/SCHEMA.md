# Database Schema (Current)

This doc is derived from the SQL migrations and schema export currently present in the repo.
Columns shown below come from CREATE TABLE + ALTER TABLE ADD COLUMN statements.

## public.threads_posts
SoT for posts; analysis_json & preanalysis_json live here.

_Sources: database/migrations/2025-enrichment-hardening.sql, supabase/exports/ops_public_schema.sql, supabase/migrations/20251223050252_remote_schema.sql, supabase/migrations/20251223143300_cdx_081_vision_gating.sql, supabase/migrations/20260201002000_preanalysis_columns.sql_

Columns:
- `enrichment_status`: "text" DEFAULT 'idle'::"text"
- `enrichment_last_error`: "text"
- `enrichment_retry_count`: integer DEFAULT 0
- `enrichment_queued_at`: timestamp with time zone
- `enrichment_started_at`: timestamp with time zone
- `enrichment_completed_at`: timestamp with time zone
- `id`: bigint NOT NULL
- `url`: "text"
- `author`: "text"
- `post_text`: "text"
- `post_text_raw`: "text"
- `like_count`: integer
- `view_count`: integer
- `reply_count`: integer
- `repost_count`: integer
- `share_count`: integer
- `raw_comments`: "jsonb"
- `captured_at`: timestamp with time zone DEFAULT "now"()
- `images`: "jsonb"
- `ingest_source`: "text"
- `is_first_thread`: boolean DEFAULT false NOT NULL
- `image_types`: "jsonb"
- `analysis`: "jsonb"
- `reply_count_ui`: integer
- `created_at`: timestamp with time zone DEFAULT "now"()
- `ai_tags`: "jsonb"
- `full_report`: "text"
- `quant_summary`: "jsonb"
- `cluster_summary`: "jsonb"
- `analysis_json`: "jsonb"
- `raw_json`: "jsonb"
- `analysis_is_valid`: boolean DEFAULT false NOT NULL
- `analysis_version`: "text"
- `analysis_build_id`: "text"
- `analysis_invalid_reason`: "text"
- `missing_keys`: "jsonb"
- `has_analysis`: boolean DEFAULT false
- `archive_captured_at`: timestamp with time zone
- `archive_build_id`: "text"
- `archive_dom_json`: "jsonb"
- `archive_html`: "text"
- `analysis_missing_keys`: "jsonb"
- `phenomenon_id`: "uuid"
- `phenomenon_status`: "text"
- `phenomenon_case_id`: "text"
- `updated_at`: timestamp with time zone DEFAULT "now"() NOT NULL
- `vision_mode`: "text" DEFAULT 'auto'::"text"
- `vision_need_score`: numeric
- `vision_reasons`: "jsonb"
- `vision_stage_ran`: "text" DEFAULT 'none'::"text"
- `vision_v1`: "jsonb"
- `vision_v2`: "jsonb"
- `vision_sim_post_comments`: numeric
- `vision_metrics_reliable`: boolean DEFAULT false
- `vision_updated_at`: timestamp with time zone
- `preanalysis_json`: jsonb
- `preanalysis_status`: text not null default 'pending'
- `preanalysis_version`: text
- `preanalysis_updated_at`: timestamptz

## public.threads_comments
SoT for comments; cluster_key writeback target.

_Sources: database/migrations/2025-comment-identity-hardening.sql, database/migrations/2025-threads-comment-clusters.sql, database/migrations/2025-threads-comments.sql, database/migrations/2026-threads-comments-source-locator.sql, supabase/exports/ops_public_schema.sql, supabase/migrations/20251223050252_remote_schema.sql, supabase/migrations/20251224_cdx_314_add_from_top_snapshot.sql, supabase/migrations/20260114_threads_comments_v7.sql, supabase/migrations/20260122045035_20260122_pipeline_a_sot_fix.sql, supabase/migrations/20260201000000_canonical_comment_bundle.sql_

Columns:
- `source_comment_id`: "text"
- `parent_source_comment_id`: "text"
- `cluster_id`: "text"
- `cluster_key`: integer
- `id`: "text" NOT NULL
- `post_id`: bigint NOT NULL
- `text`: "text"
- `author_handle`: "text"
- `like_count`: integer
- `reply_count`: integer
- `created_at`: timestamp with time zone
- `captured_at`: timestamp with time zone DEFAULT "now"()
- `parent_comment_id`: "text"
- `author_id`: "text"
- `raw_json`: "jsonb"
- `cluster_label`: "text"
- `tactic_tag`: "text"
- `embedding`: "public"."vector"(1536)
- `inserted_at`: timestamp with time zone DEFAULT "now"()
- `updated_at`: timestamp with time zone DEFAULT "now"()
- `source_locator`: text
- `from_top_snapshot`: boolean DEFAULT false NOT NULL
- `likes`: integer DEFAULT 0
- `raw`: "jsonb"
- `user`: "jsonb"
- `root_source_comment_id`: text null
- `reply_to_author`: text null
- `taken_at`: timestamptz null
- `text_fragments`: jsonb null
- `depth`: int null
- `path`: text null
- `run_id`: text
- `crawled_at_utc`: timestamptz
- `source`: text
- `time_token`: text
- `approx_created_at_utc`: timestamptz
- `time_precision`: text
- `reply_count_ui`: integer
- `repost_count_ui`: integer
- `share_count_ui`: integer
- `metrics_confidence`: text
- `comment_images`: jsonb
- `ui_created_at_est`: timestamptz
- `is_estimated`: boolean default false

## public.threads_comment_edges
Reply graph edges (parent -> child).

_Sources: supabase/migrations/20260122045035_20260122_pipeline_a_sot_fix.sql, supabase/migrations/20260201000000_canonical_comment_bundle.sql_

Columns:
- `id`: bigserial primary key
- `run_id`: text not null
- `post_id`: bigint not null
- `parent_comment_id`: text not null
- `child_comment_id`: text not null
- `edge_type`: text not null default 'reply'
- `created_at`: timestamptz not null default now()
- `parent_source_comment_id`: text
- `child_source_comment_id`: text
- `edge_source`: text
- `captured_at`: timestamptz
- `confidence`: numeric

## public.threads_posts_raw
Raw fetcher run metadata (one row per run_id/post).

_Sources: supabase/migrations/20260122045035_20260122_pipeline_a_sot_fix.sql_

Columns:
- `id`: bigserial primary key
- `run_id`: text not null
- `post_id`: text not null
- `post_url`: text not null
- `crawled_at_utc`: timestamptz not null
- `fetcher_version`: text
- `run_dir`: text
- `raw_html_initial_path`: text
- `raw_html_final_path`: text
- `raw_cards_path`: text
- `created_at`: timestamptz not null default now()

## public.threads_coverage_audits
Coverage control plane stats per fetch run.

_Sources: supabase/migrations/20260209020000_cdx_coverage_control_plane.sql_

Columns:
- `id`: uuid primary key default gen_random_uuid()
- `post_id`: bigint not null references public.threads_posts(id) on delete cascade
- `fetch_run_id`: text not null
- `captured_at`: timestamptz not null default now()
- `expected_replies_ui`: int null
- `coverage_ratio`: double precision null
- `stop_reason`: text not null
- `budgets_used`: jsonb not null
- `rounds_json`: jsonb null
- `rounds_hash`: text null

## public.threads_comment_clusters
Per-post cluster labels/summary writeback.

_Sources: database/migrations/2025-cluster-semantic-writeback.sql, database/migrations/2025-cluster-tactics.sql, database/migrations/2025-threads-comment-clusters.sql, supabase/exports/ops_public_schema.sql, supabase/migrations/20251223050252_remote_schema.sql_

Columns:
- `label`: "text"
- `summary`: "text"
- `tactics`: "text"[]
- `tactic_summary`: "text"
- `id`: "text" NOT NULL
- `post_id`: bigint NOT NULL
- `cluster_key`: integer NOT NULL
- `size`: integer DEFAULT 0
- `top_comment_ids`: "jsonb"
- `centroid_embedding`: "public"."vector"(1536)
- `created_at`: timestamp with time zone DEFAULT "now"()
- `updated_at`: timestamp with time zone DEFAULT "now"()
- `keywords`: "text"[]
- `centroid_embedding_384`: "public"."vector"(384)

## public.threads_comment_cluster_assignments
History of comment->cluster assignments per run.

_Sources: supabase/migrations/20260201000000_canonical_comment_bundle.sql_

Columns:
- `cluster_run_id`: text not null
- `post_id`: bigint not null
- `comment_id`: text not null
- `cluster_key`: int not null
- `cluster_id`: text
- `bundle_id`: text
- `cluster_fingerprint`: text
- `created_at`: timestamptz not null default now()

## public.threads_cluster_diagnostics
ISD outputs and stability/drift metrics.

_Sources: supabase/migrations/20260206000000_cluster_diagnostics.sql_

Columns:
- `id`: text primary key
- `post_id`: bigint not null
- `cluster_key`: int not null
- `run_id`: text not null
- `verdict`: text not null
- `k`: int not null
- `labels`: jsonb not null
- `stability_avg`: numeric
- `stability_min`: numeric
- `drift_avg`: numeric
- `drift_max`: numeric
- `context_mode`: text not null
- `prompt_hash`: text
- `model_name`: text
- `created_at`: timestamptz default now()
- `updated_at`: timestamptz default now()

## public.threads_cluster_interpretations
CIP label/summary artifacts with evidence ids.

_Sources: database/migrations/2026-threads-cluster-interpretations.sql_

Columns:
- `id`: text primary key
- `post_id`: bigint not null references public.threads_posts(id) on delete cascade
- `cluster_key`: int not null
- `run_id`: text not null
- `label`: text
- `one_liner`: text
- `label_style`: text
- `label_confidence`: numeric
- `label_unstable`: boolean default false
- `evidence_ids`: jsonb
- `context_cards`: jsonb
- `cluster_signature`: text
- `drift_score_avg`: numeric
- `drift_score_min`: numeric
- `labels_raw`: jsonb
- `prompt_hash`: text
- `model_name`: text
- `drift_model_name`: text
- `drift_model_hash`: text
- `created_at`: timestamptz default now()
- `updated_at`: timestamptz default now()

## public.threads_claims
Audited claims (library-first).

_Sources: supabase/migrations/20260208000000_cdx_s4_claims_evidence_audit.sql, supabase/migrations/20260208010000_cdx_s4_1v2_library_claims.sql_

Columns:
- `id`: uuid primary key default gen_random_uuid()
- `post_id`: bigint not null references public.threads_posts(id) on delete cascade
- `cluster_key`: int null
- `run_id`: text not null
- `claim_type`: text not null
- `scope`: text not null
- `text`: text not null
- `source_agent`: text not null default 'analyst'
- `confidence`: double precision null
- `tags`: jsonb null
- `prompt_hash`: text null
- `model_name`: text null
- `created_at`: timestamptz not null default now()
- `claim_key`: text not null default ''
- `status`: text not null default 'audited'
- `cluster_keys`: jsonb
- `primary_cluster_key`: int
- `audit_reason`: text
- `missing_evidence_type`: text
- `confidence_cap`: double precision

## public.threads_claim_evidence
Evidence rows for claims.

_Sources: supabase/migrations/20260208000000_cdx_s4_claims_evidence_audit.sql, supabase/migrations/20260208010000_cdx_s4_1v2_library_claims.sql_

Columns:
- `id`: bigserial primary key
- `claim_id`: uuid not null references public.threads_claims(id) on delete cascade
- `evidence_type`: text not null
- `evidence_id`: text not null
- `span_text`: text null
- `created_at`: timestamptz not null default now()
- `source`: text not null default 'threads'
- `locator_type`: text not null default 'comment_id'
- `locator_value`: text not null default ''
- `locator_key`: text not null default ''
- `cluster_key`: int
- `author_handle`: text
- `like_count`: int
- `capture_hash`: text
- `evidence_ref`: jsonb

## public.threads_claim_audits
Claims audit verdicts + counts.

_Sources: supabase/migrations/20260208000000_cdx_s4_claims_evidence_audit.sql, supabase/migrations/20260208010000_cdx_s4_1v2_library_claims.sql_

Columns:
- `id`: bigserial primary key
- `post_id`: bigint not null
- `run_id`: text not null
- `build_id`: text null
- `verdict`: text not null
- `dropped_claims_count`: int not null default 0
- `reasons`: jsonb null
- `created_at`: timestamptz not null default now()
- `kept_claims_count`: int not null default 0
- `total_claims_count`: int not null default 0

## public.threads_behavior_audits
Behavior side-channel artifact + ui_budget.

_Sources: supabase/migrations/20260209000000_cdx_s5_behavior_sidechannel.sql_

Columns:
- `id`: uuid primary key default gen_random_uuid()
- `post_id`: bigint not null references public.threads_posts(id) on delete cascade
- `cluster_run_id`: text not null
- `behavior_run_id`: text not null
- `reply_graph_id_space`: text not null default 'internal'
- `artifact_json`: jsonb not null
- `quality_flags`: jsonb null
- `scores`: jsonb null
- `created_at`: timestamptz not null default now()

## public.threads_risk_briefs
Risk brief derived from behavior artifacts.

_Sources: supabase/migrations/20260209010000_cdx_s6_risk_composer_min.sql_

Columns:
- `id`: uuid primary key default gen_random_uuid()
- `post_id`: bigint not null references public.threads_posts(id) on delete cascade
- `cluster_run_id`: text not null
- `behavior_run_id`: text not null
- `risk_run_id`: text not null
- `brief_json`: jsonb not null
- `created_at`: timestamptz not null default now()

## public.threads_reply_matrix_audits
Reply-matrix accounting (coverage/edges).

_Sources: supabase/migrations/20260209040000_cdx_reply_matrix_accounting.sql_

Columns:
- `id`: uuid primary key default gen_random_uuid()
- `post_id`: bigint not null references public.threads_posts(id) on delete cascade
- `cluster_run_id`: text not null
- `reply_graph_id_space`: text not null default 'internal'
- `accounting_json`: jsonb not null
- `created_at`: timestamptz not null default now()

## public.llm_call_logs
LLM call telemetry for Ops KPI.

_Sources: supabase/migrations/20260209030000_cdx_102_llm_call_logs.sql_

Columns:
- `id`: uuid primary key default gen_random_uuid()
- `post_id`: bigint null references public.threads_posts(id) on delete set null
- `run_id`: text null
- `mode`: text null
- `model_name`: text null
- `request_tokens`: int null
- `response_tokens`: int null
- `total_tokens`: int null
- `latency_ms`: int null
- `status`: text not null default 'ok'
- `created_at`: timestamptz not null default now()

## public.job_batches
Ops job batches (pipeline runs).

_Sources: supabase/exports/ops_public_schema.sql_

Columns:
- `id`: "uuid" DEFAULT "gen_random_uuid"() NOT NULL
- `pipeline_type`: "text" NOT NULL
- `mode`: "text" NOT NULL
- `input_config`: "jsonb" NOT NULL
- `status`: "text" DEFAULT 'pending'::"text" NOT NULL
- `error_summary`: "text"
- `total_count`: integer DEFAULT 0
- `processed_count`: integer DEFAULT 0
- `success_count`: integer DEFAULT 0
- `failed_count`: integer DEFAULT 0
- `created_at`: timestamp with time zone DEFAULT "now"()
- `updated_at`: timestamp with time zone DEFAULT "now"()
- `finished_at`: timestamp with time zone
- `last_heartbeat_at`: timestamp with time zone DEFAULT "now"()

## public.job_items
Ops job items (per-target progress).

_Sources: supabase/exports/ops_public_schema.sql_

Columns:
- `id`: "uuid" DEFAULT "gen_random_uuid"() NOT NULL
- `job_id`: "uuid" NOT NULL
- `target_id`: "text" NOT NULL
- `status`: "text" DEFAULT 'pending'::"text" NOT NULL
- `stage`: "text" DEFAULT 'init'::"text" NOT NULL
- `error_log`: "text"
- `retry_count`: integer DEFAULT 0
- `result_post_id`: "text"
- `locked_at`: timestamp with time zone
- `lock_expires_at`: timestamp with time zone
- `locked_by`: "text"
- `created_at`: timestamp with time zone DEFAULT "now"()
- `updated_at`: timestamp with time zone DEFAULT "now"()

## public.analysis_reviews
Human review labels (repurposed axis reviews).

_Sources: supabase/migrations/20260201001000_analysis_reviews.sql_

Columns:
- `id`: uuid primary key default gen_random_uuid()
- `post_id`: bigint not null
- `bundle_id`: text not null
- `analysis_build_id`: text
- `axis_id`: text not null
- `axis_version`: text not null
- `comment_id`: text not null
- `review_type`: text not null
- `decision`: text not null
- `confidence`: text not null
- `note`: text
- `created_at`: timestamptz not null default now()

## public.narrative_phenomena
Phenomenon registry (currently disabled in pipeline).

_Sources: database/migrations/2025-12-22_google_embedding_768.sql, database/migrations/20250101_cdx_083a_fix_increment_occurrence_rpc.sql, supabase/exports/ops_public_schema.sql, supabase/migrations/20251223050252_remote_schema.sql, supabase/migrations/20251223170000_cdx_083b_fix_increment_occurrence_rpc.sql_

Columns:
- `embedding_v768`: "public"."vector"(768)
- `occurrence_count`: integer DEFAULT 1
- `id`: "uuid" NOT NULL
- `canonical_name`: "text"
- `description`: "text"
- `embedding`: "public"."vector"(1536)
- `status`: "text" DEFAULT 'provisional'::"text"
- `minted_by_case_id`: "text"
- `created_at`: timestamp with time zone DEFAULT "now"()
- `updated_at`: timestamp with time zone DEFAULT "now"()

