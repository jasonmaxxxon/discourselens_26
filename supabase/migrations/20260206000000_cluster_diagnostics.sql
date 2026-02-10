-- ISD: Interpretive Stability Diagnostics
create table if not exists public.threads_cluster_diagnostics (
    id text primary key,
    post_id bigint not null,
    cluster_key int not null,
    run_id text not null,
    verdict text not null,
    k int not null,
    labels jsonb not null,
    stability_avg numeric,
    stability_min numeric,
    drift_avg numeric,
    drift_max numeric,
    context_mode text not null,
    prompt_hash text,
    model_name text,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists threads_cluster_diagnostics_post_cluster_idx
    on public.threads_cluster_diagnostics (post_id, cluster_key);

create index if not exists threads_cluster_diagnostics_post_run_idx
    on public.threads_cluster_diagnostics (post_id, run_id);

create unique index if not exists threads_cluster_diagnostics_post_cluster_run_mode_idx
    on public.threads_cluster_diagnostics (post_id, cluster_key, run_id, context_mode);
