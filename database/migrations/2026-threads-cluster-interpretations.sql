-- Sprint 2: Cluster Interpretation Pack (CIP) storage
create table if not exists public.threads_cluster_interpretations (
  id text primary key,
  post_id bigint not null references public.threads_posts(id) on delete cascade,
  cluster_key int not null,
  run_id text not null,
  label text,
  one_liner text,
  label_style text,
  label_confidence numeric,
  label_unstable boolean default false,
  evidence_ids jsonb,
  context_cards jsonb,
  cluster_signature text,
  drift_score_avg numeric,
  drift_score_min numeric,
  labels_raw jsonb,
  prompt_hash text,
  model_name text,
  drift_model_name text,
  drift_model_hash text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create unique index if not exists idx_tci_post_cluster_run
  on public.threads_cluster_interpretations(post_id, cluster_key, run_id);

create index if not exists idx_tci_post
  on public.threads_cluster_interpretations(post_id);
