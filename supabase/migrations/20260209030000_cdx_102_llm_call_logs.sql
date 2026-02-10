create table if not exists public.llm_call_logs (
    id uuid primary key default gen_random_uuid(),
    post_id bigint null references public.threads_posts(id) on delete set null,
    run_id text null,
    mode text null,
    model_name text null,
    request_tokens int null,
    response_tokens int null,
    total_tokens int null,
    latency_ms int null,
    status text not null default 'ok',
    created_at timestamptz not null default now()
);

create index if not exists llm_call_logs_post_idx
    on public.llm_call_logs (post_id);

create index if not exists llm_call_logs_run_idx
    on public.llm_call_logs (run_id);

create index if not exists llm_call_logs_created_idx
    on public.llm_call_logs (created_at desc);
