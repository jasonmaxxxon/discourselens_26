create extension if not exists pgcrypto;
create extension if not exists vector;

create table if not exists public.topic_runs (
    id uuid primary key default gen_random_uuid(),
    topic_name text not null,
    seed_query text,
    seed_post_ids jsonb not null default '[]'::jsonb,
    time_range_start timestamptz not null,
    time_range_end timestamptz not null,
    run_params jsonb not null default '{}'::jsonb,
    topic_run_hash text not null,
    lifecycle_hash text,
    status text not null default 'pending',
    source text not null default 'manual',
    freshness_lag_seconds int,
    coverage_gap boolean not null default false,
    stats_json jsonb not null default '{}'::jsonb,
    error_summary text,
    created_by text,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz not null default now(),
    constraint topic_runs_hash_uq unique (topic_run_hash),
    constraint topic_runs_time_range_ck check (time_range_end > time_range_start),
    constraint topic_runs_status_ck check (status in ('pending', 'running', 'completed', 'failed', 'canceled'))
);

create index if not exists topic_runs_status_created_idx
    on public.topic_runs (status, created_at desc);

create index if not exists topic_runs_time_range_idx
    on public.topic_runs (time_range_start, time_range_end);

create table if not exists public.topic_posts (
    id bigserial primary key,
    topic_run_id uuid not null references public.topic_runs(id) on delete cascade,
    post_id bigint not null references public.threads_posts(id) on delete cascade,
    ordinal int not null default 0,
    inclusion_source text not null default 'seed',
    inclusion_reason text,
    post_created_at timestamptz,
    cluster_run_id text,
    post_cluster_count int not null default 0,
    comments_total int not null default 0,
    evidence_total int not null default 0,
    coverage_ratio double precision,
    weights_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint topic_posts_topic_post_uq unique (topic_run_id, post_id),
    constraint topic_posts_ordinal_ck check (ordinal >= 0),
    constraint topic_posts_post_cluster_count_ck check (post_cluster_count >= 0),
    constraint topic_posts_comments_total_ck check (comments_total >= 0),
    constraint topic_posts_evidence_total_ck check (evidence_total >= 0),
    constraint topic_posts_coverage_ratio_ck check (coverage_ratio is null or (coverage_ratio >= 0 and coverage_ratio <= 1))
);

create index if not exists topic_posts_run_ordinal_idx
    on public.topic_posts (topic_run_id, ordinal);

create index if not exists topic_posts_post_idx
    on public.topic_posts (post_id);

create table if not exists public.topic_meta_clusters (
    id uuid primary key default gen_random_uuid(),
    topic_run_id uuid not null references public.topic_runs(id) on delete cascade,
    meta_cluster_key int not null,
    meta_cluster_hash text not null,
    centroid_embedding_384 vector(384),
    member_clusters jsonb not null default '[]'::jsonb,
    member_posts jsonb not null default '[]'::jsonb,
    dominance_share double precision not null default 0,
    comment_count int not null default 0,
    evidence_count int not null default 0,
    metrics_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint topic_meta_clusters_run_key_uq unique (topic_run_id, meta_cluster_key),
    constraint topic_meta_clusters_run_hash_uq unique (topic_run_id, meta_cluster_hash),
    constraint topic_meta_clusters_dominance_ck check (dominance_share >= 0 and dominance_share <= 1),
    constraint topic_meta_clusters_comment_count_ck check (comment_count >= 0),
    constraint topic_meta_clusters_evidence_count_ck check (evidence_count >= 0)
);

create index if not exists topic_meta_clusters_run_dominance_idx
    on public.topic_meta_clusters (topic_run_id, dominance_share desc);

create index if not exists topic_meta_clusters_centroid_ivfflat_idx
    on public.topic_meta_clusters
    using ivfflat (centroid_embedding_384 vector_cosine_ops) with (lists='100');

create table if not exists public.topic_lifecycle_daily (
    id bigserial primary key,
    topic_run_id uuid not null references public.topic_runs(id) on delete cascade,
    meta_cluster_key int not null,
    day_utc date not null,
    dominance_share double precision not null default 0,
    comment_count int not null default 0,
    evidence_count int not null default 0,
    managed_score double precision not null default 0,
    organic_score double precision not null default 0,
    drift_score double precision not null default 0,
    lifecycle_stage text not null default 'birth',
    supporting_post_ids jsonb not null default '[]'::jsonb,
    metrics_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint topic_lifecycle_daily_run_day_uq unique (topic_run_id, meta_cluster_key, day_utc),
    constraint topic_lifecycle_daily_dominance_ck check (dominance_share >= 0 and dominance_share <= 1),
    constraint topic_lifecycle_daily_managed_ck check (managed_score >= 0 and managed_score <= 1),
    constraint topic_lifecycle_daily_organic_ck check (organic_score >= 0 and organic_score <= 1),
    constraint topic_lifecycle_daily_drift_ck check (drift_score >= 0 and drift_score <= 1),
    constraint topic_lifecycle_daily_comment_count_ck check (comment_count >= 0),
    constraint topic_lifecycle_daily_evidence_count_ck check (evidence_count >= 0),
    constraint topic_lifecycle_daily_stage_ck check (lifecycle_stage in ('birth', 'growth', 'peak', 'decline', 'dormant'))
);

create index if not exists topic_lifecycle_daily_run_day_idx
    on public.topic_lifecycle_daily (topic_run_id, day_utc);

create index if not exists topic_lifecycle_daily_run_cluster_day_idx
    on public.topic_lifecycle_daily (topic_run_id, meta_cluster_key, day_utc desc);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'topic_lifecycle_daily_run_cluster_fk'
    ) then
        alter table public.topic_lifecycle_daily
            add constraint topic_lifecycle_daily_run_cluster_fk
            foreign key (topic_run_id, meta_cluster_key)
            references public.topic_meta_clusters (topic_run_id, meta_cluster_key)
            on delete cascade;
    end if;
end $$;

