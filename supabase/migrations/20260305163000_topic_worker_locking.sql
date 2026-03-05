alter table if exists public.topic_runs
    add column if not exists lock_owner text,
    add column if not exists locked_at timestamptz,
    add column if not exists heartbeat_at timestamptz,
    add column if not exists lock_lease_seconds int not null default 600,
    add column if not exists attempt_count int not null default 0;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'topic_runs_lock_lease_seconds_ck'
    ) then
        alter table public.topic_runs
            add constraint topic_runs_lock_lease_seconds_ck
            check (lock_lease_seconds > 0 and lock_lease_seconds <= 86400);
    end if;
end $$;

create index if not exists topic_runs_status_updated_idx
    on public.topic_runs (status, updated_at asc);

create index if not exists topic_runs_running_heartbeat_idx
    on public.topic_runs (status, heartbeat_at asc)
    where status = 'running';
