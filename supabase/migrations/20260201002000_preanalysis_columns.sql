-- Add preanalysis columns to threads_posts (Option A)
alter table if exists public.threads_posts
  add column if not exists preanalysis_json jsonb,
  add column if not exists preanalysis_status text not null default 'pending',
  add column if not exists preanalysis_version text,
  add column if not exists preanalysis_updated_at timestamptz;
