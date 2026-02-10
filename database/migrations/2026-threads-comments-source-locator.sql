-- CDX-ReplyGraph Lean Fix v1.1: source locator decoration (minimal)
alter table if exists public.threads_comments
  add column if not exists source_locator text;

create index if not exists idx_threads_comments_source_locator
  on public.threads_comments(source_locator);
