create table if not exists public.threads_coverage_audits (
    id uuid primary key default gen_random_uuid(),
    post_id bigint not null references public.threads_posts(id) on delete cascade,
    fetch_run_id text not null,
    captured_at timestamptz not null default now(),
    expected_replies_ui int null,
    unique_fetched int not null,
    coverage_ratio double precision null,
    stop_reason text not null,
    budgets_used jsonb not null,
    rounds_json jsonb null,
    rounds_hash text null
);

create index if not exists threads_coverage_audits_post_run_idx
    on public.threads_coverage_audits (post_id, fetch_run_id);

create index if not exists threads_coverage_audits_post_captured_idx
    on public.threads_coverage_audits (post_id, captured_at desc);
