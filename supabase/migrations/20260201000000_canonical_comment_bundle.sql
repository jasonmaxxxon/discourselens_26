-- CanonicalCommentBundleV1 support (additive)
alter table if exists public.threads_comments
  add column if not exists ui_created_at_est timestamptz,
  add column if not exists is_estimated boolean default false;

alter table if exists public.threads_comment_edges
  add column if not exists parent_source_comment_id text,
  add column if not exists child_source_comment_id text,
  add column if not exists edge_source text,
  add column if not exists captured_at timestamptz,
  add column if not exists confidence numeric;

create index if not exists idx_threads_comment_edges_parent_source
  on public.threads_comment_edges (post_id, parent_source_comment_id);

create index if not exists idx_threads_comment_edges_child_source
  on public.threads_comment_edges (post_id, child_source_comment_id);

create table if not exists public.threads_comment_cluster_assignments (
  cluster_run_id text not null,
  post_id bigint not null,
  comment_id text not null,
  cluster_key int not null,
  cluster_id text,
  bundle_id text,
  cluster_fingerprint text,
  created_at timestamptz not null default now(),
  primary key (cluster_run_id, comment_id)
);

create index if not exists idx_comment_cluster_assignments_post
  on public.threads_comment_cluster_assignments (post_id);

create index if not exists idx_comment_cluster_assignments_comment
  on public.threads_comment_cluster_assignments (comment_id);
