create table if not exists public.analysis_reviews (
  id uuid primary key default gen_random_uuid(),
  post_id bigint not null,
  bundle_id text not null,
  analysis_build_id text,
  axis_id text not null,
  axis_version text not null,
  comment_id text not null,
  review_type text not null,
  decision text not null,
  confidence text not null,
  note text,
  created_at timestamptz not null default now()
);

create index if not exists idx_analysis_reviews_axis
  on public.analysis_reviews (axis_id, axis_version);

create index if not exists idx_analysis_reviews_comment
  on public.analysis_reviews (comment_id);

create index if not exists idx_analysis_reviews_post_created
  on public.analysis_reviews (post_id, created_at desc);
