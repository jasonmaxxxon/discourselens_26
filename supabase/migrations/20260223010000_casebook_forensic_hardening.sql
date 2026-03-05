create extension if not exists pgcrypto;

create table if not exists public.analyst_casebook (
    id uuid primary key default gen_random_uuid(),
    evidence_id text not null,
    comment_id text not null,
    evidence_text text not null,
    post_id text not null,
    captured_at timestamptz not null,
    bucket jsonb not null,
    metrics_snapshot jsonb not null,
    coverage jsonb not null default '{"comments_loaded": 0, "comments_total": null, "is_truncated": false}'::jsonb,
    summary_version text not null default 'casebook_summary_v1',
    filters jsonb not null default '{"author": null, "cluster_key": null, "query": null, "sort": null}'::jsonb,
    analyst_note text,
    created_at timestamptz not null default now()
);

create index if not exists analyst_casebook_post_created_idx
    on public.analyst_casebook (post_id, created_at desc);

create index if not exists analyst_casebook_comment_idx
    on public.analyst_casebook (comment_id);

alter table public.analyst_casebook
    add column if not exists coverage jsonb,
    add column if not exists summary_version text,
    add column if not exists filters jsonb;

update public.analyst_casebook
set coverage = coalesce(
    coverage,
    '{"comments_loaded": 0, "comments_total": null, "is_truncated": false}'::jsonb
);

update public.analyst_casebook
set summary_version = coalesce(summary_version, 'casebook_summary_v1');

update public.analyst_casebook
set filters = coalesce(
    filters,
    '{"author": null, "cluster_key": null, "query": null, "sort": null}'::jsonb
);

alter table public.analyst_casebook
    alter column coverage set default '{"comments_loaded": 0, "comments_total": null, "is_truncated": false}'::jsonb,
    alter column coverage set not null,
    alter column summary_version set default 'casebook_summary_v1',
    alter column summary_version set not null,
    alter column filters set default '{"author": null, "cluster_key": null, "query": null, "sort": null}'::jsonb,
    alter column filters set not null;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'analyst_casebook_summary_version_check'
    ) then
        alter table public.analyst_casebook
            add constraint analyst_casebook_summary_version_check
            check (summary_version = 'casebook_summary_v1');
    end if;
end $$;

create or replace function public.enforce_casebook_snapshot_immutability()
returns trigger
language plpgsql
as $$
begin
    if new.bucket is distinct from old.bucket then
        raise exception 'bucket is immutable for analyst_casebook';
    end if;
    if new.metrics_snapshot is distinct from old.metrics_snapshot then
        raise exception 'metrics_snapshot is immutable for analyst_casebook';
    end if;
    if new.coverage is distinct from old.coverage then
        raise exception 'coverage is immutable for analyst_casebook';
    end if;
    if new.summary_version is distinct from old.summary_version then
        raise exception 'summary_version is immutable for analyst_casebook';
    end if;
    if new.filters is distinct from old.filters then
        raise exception 'filters is immutable for analyst_casebook';
    end if;
    return new;
end;
$$;

drop trigger if exists analyst_casebook_snapshot_immutable on public.analyst_casebook;
create trigger analyst_casebook_snapshot_immutable
before update on public.analyst_casebook
for each row
execute function public.enforce_casebook_snapshot_immutability();
