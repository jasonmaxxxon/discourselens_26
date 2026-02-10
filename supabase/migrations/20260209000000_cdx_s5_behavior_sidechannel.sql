create table if not exists public.threads_behavior_audits (
    id uuid primary key default gen_random_uuid(),
    post_id bigint not null references public.threads_posts(id) on delete cascade,
    cluster_run_id text not null,
    behavior_run_id text not null,
    reply_graph_id_space text not null default 'internal',
    artifact_json jsonb not null,
    quality_flags jsonb null,
    scores jsonb null,
    created_at timestamptz not null default now()
);

create index if not exists threads_behavior_audits_post_cluster_idx
    on public.threads_behavior_audits (post_id, cluster_run_id);

create index if not exists threads_behavior_audits_post_behavior_idx
    on public.threads_behavior_audits (post_id, behavior_run_id);

create index if not exists threads_behavior_audits_artifact_gin
    on public.threads_behavior_audits using gin (artifact_json);
