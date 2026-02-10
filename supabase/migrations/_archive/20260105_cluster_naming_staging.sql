create table if not exists public.cluster_naming_staging (
  id uuid primary key default gen_random_uuid(),
  post_id bigint references public.threads_posts(id) on delete cascade,
  cluster_key int not null,
  run_id text not null,
  quant_health text not null,
  assignment_coverage double precision,
  noise_ratio double precision,
  backend_name text,
  backend_params jsonb,
  model_provider text,
  model_name text,
  prompt_hash text,
  label text,
  summary text,
  evidence_comment_ids jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists idx_cluster_naming_staging_uniq on public.cluster_naming_staging (post_id, cluster_key, run_id);
create index if not exists idx_cluster_naming_staging_post_created on public.cluster_naming_staging (post_id, created_at desc);
