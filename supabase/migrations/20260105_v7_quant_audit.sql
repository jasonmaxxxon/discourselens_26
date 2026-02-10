create table if not exists public.v7_quant_runs (
  id uuid primary key default gen_random_uuid(),
  post_id bigint references public.threads_posts(id) on delete cascade,
  backend text not null,
  seed int,
  input_comment_count int,
  input_comment_ids_hash text,
  backend_params jsonb,
  cluster_count int,
  avg_cluster_size double precision,
  noise_ratio double precision,
  assignment_coverage double precision,
  centroid_missing_count int,
  quant_health_level text,
  quant_health_reasons text[],
  created_at timestamptz not null default now()
);

create table if not exists public.v7_quant_clusters (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references public.v7_quant_runs(id) on delete cascade,
  post_id bigint,
  cluster_key int,
  size int,
  like_sum int,
  keywords text[],
  top_comment_ids text[],
  centroid_384_hash text,
  created_at timestamptz not null default now()
);

create index if not exists idx_v7_quant_runs_post on public.v7_quant_runs (post_id, created_at desc);
create index if not exists idx_v7_quant_clusters_run on public.v7_quant_clusters (run_id);
