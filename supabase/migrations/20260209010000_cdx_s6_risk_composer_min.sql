create table if not exists public.threads_risk_briefs (
    id uuid primary key default gen_random_uuid(),
    post_id bigint not null references public.threads_posts(id) on delete cascade,
    cluster_run_id text not null,
    behavior_run_id text not null,
    risk_run_id text not null,
    brief_json jsonb not null,
    created_at timestamptz not null default now()
);

create index if not exists threads_risk_briefs_post_cluster_idx
    on public.threads_risk_briefs (post_id, cluster_run_id);

create index if not exists threads_risk_briefs_post_risk_idx
    on public.threads_risk_briefs (post_id, risk_run_id);
