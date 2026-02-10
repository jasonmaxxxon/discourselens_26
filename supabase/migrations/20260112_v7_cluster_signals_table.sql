-- Create v7_cluster_signals if missing
create table if not exists public.v7_cluster_signals (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null,
  post_id bigint not null,
  cluster_key int not null,
  signals_json jsonb,
  signals_hash text,
  created_at timestamptz not null default now()
);

-- Idempotent indexes and dedup key
create unique index if not exists idx_v7_cluster_signals_run_cluster on public.v7_cluster_signals (run_id, post_id, cluster_key);
create index if not exists idx_v7_cluster_signals_post on public.v7_cluster_signals (post_id);
create index if not exists idx_v7_cluster_signals_run on public.v7_cluster_signals (run_id);
create index if not exists idx_v7_cluster_signals_cluster on public.v7_cluster_signals (cluster_key);

-- Supabase/PostgREST schema cache note:
-- If schema cache errors occur (PGRST205), run `NOTIFY pgrst, 'reload schema'` after applying.
