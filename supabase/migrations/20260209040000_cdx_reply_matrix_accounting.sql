create table if not exists public.threads_reply_matrix_audits (
    id uuid primary key default gen_random_uuid(),
    post_id bigint not null references public.threads_posts(id) on delete cascade,
    cluster_run_id text not null,
    reply_graph_id_space text not null default 'internal',
    accounting_json jsonb not null,
    created_at timestamptz not null default now()
);

create index if not exists threads_reply_matrix_audits_post_cluster_idx
    on public.threads_reply_matrix_audits (post_id, cluster_run_id);

create index if not exists threads_reply_matrix_audits_post_created_idx
    on public.threads_reply_matrix_audits (post_id, created_at desc);
