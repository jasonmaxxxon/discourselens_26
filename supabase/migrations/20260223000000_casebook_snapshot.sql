create table if not exists public.analyst_casebook (
    id uuid primary key default gen_random_uuid(),
    evidence_id text not null,
    comment_id text not null,
    evidence_text text not null,
    post_id text not null,
    captured_at timestamptz not null,
    bucket jsonb not null,
    metrics_snapshot jsonb not null,
    analyst_note text,
    created_at timestamptz not null default now()
);

create index if not exists analyst_casebook_post_created_idx
    on public.analyst_casebook (post_id, created_at desc);

create index if not exists analyst_casebook_comment_idx
    on public.analyst_casebook (comment_id);
