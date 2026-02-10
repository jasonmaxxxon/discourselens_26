-- V7-compatible columns for threads_comments
alter table public.threads_comments
    add column if not exists source_comment_id text null,
    add column if not exists parent_source_comment_id text null,
    add column if not exists root_source_comment_id text null,
    add column if not exists reply_to_author text null,
    add column if not exists taken_at timestamptz null,
    add column if not exists text_fragments jsonb null,
    add column if not exists depth int null,
    add column if not exists path text null;

-- Indices to support identity + tree lookups
create index if not exists idx_threads_comments_post_source on public.threads_comments (post_id, source_comment_id);
create index if not exists idx_threads_comments_post_parent_source on public.threads_comments (post_id, parent_source_comment_id);
create index if not exists idx_threads_comments_post_taken_at on public.threads_comments (post_id, taken_at);

-- Uniqueness guard for native ids (safe dedupe)
create unique index if not exists idx_threads_comments_post_source_unique on public.threads_comments (post_id, source_comment_id) where source_comment_id is not null;
