create table if not exists public.threads_posts_raw (
  id bigserial primary key,

  run_id text not null,
  post_id text not null,
  post_url text not null,
  crawled_at_utc timestamptz not null,

  fetcher_version text,
  run_dir text,

  raw_html_initial_path text,
  raw_html_final_path text,
  raw_cards_path text,

  created_at timestamptz not null default now()
);

create unique index if not exists ux_threads_posts_raw_run_post
  on public.threads_posts_raw (run_id, post_id);
  create table if not exists public.threads_comment_edges (
  id bigserial primary key,

  run_id text not null,
  post_id bigint not null,

  parent_comment_id text not null,
  child_comment_id text not null,

  edge_type text not null default 'reply',

  created_at timestamptz not null default now()
);

create unique index if not exists ux_threads_comment_edges_unique
  on public.threads_comment_edges (post_id, parent_comment_id, child_comment_id, edge_type);

  do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'ck_no_self_loop'
  ) then
    alter table public.threads_comment_edges
      add constraint ck_no_self_loop
      check (parent_comment_id <> child_comment_id);
  end if;
end $$;

alter table public.threads_comments
  add column if not exists run_id text,
  add column if not exists crawled_at_utc timestamptz,
  add column if not exists source text,
  add column if not exists time_token text,
  add column if not exists approx_created_at_utc timestamptz,
  add column if not exists time_precision text,
  add column if not exists reply_count_ui integer,
  add column if not exists repost_count_ui integer,
  add column if not exists share_count_ui integer,
  add column if not exists metrics_confidence text,
  add column if not exists comment_images jsonb;

  create unique index if not exists ux_threads_posts_url
on public.threads_posts (url);