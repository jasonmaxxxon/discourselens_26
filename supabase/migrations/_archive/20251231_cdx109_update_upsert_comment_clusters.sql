-- Update RPC to persist centroid embeddings and metadata
create or replace function public.upsert_comment_clusters(p_post_id bigint, p_clusters jsonb)
returns void
language plpgsql
as $$
begin
  insert into threads_comment_clusters (
    post_id,
    cluster_key,
    label,
    summary,
    size,
    keywords,
    top_comment_ids,
    centroid_embedding,
    centroid_embedding_384,
    tactics,
    tactic_summary,
    updated_at
  )
  select
    p_post_id,
    (c->>'cluster_key')::int,
    c->>'label',
    c->>'summary',
    (c->>'size')::int,
    c->'keywords',
    c->'top_comment_ids',
    (c->'centroid_embedding')::vector,
    (c->'centroid_embedding_384')::vector,
    case when c ? 'tactics' then (c->'tactics')::text[] else null end,
    c->>'tactic_summary',
    now()
  from jsonb_array_elements(p_clusters) c
  on conflict (post_id, cluster_key)
  do update set
    label = excluded.label,
    summary = excluded.summary,
    size = excluded.size,
    keywords = excluded.keywords,
    top_comment_ids = excluded.top_comment_ids,
    centroid_embedding = excluded.centroid_embedding,
    centroid_embedding_384 = excluded.centroid_embedding_384,
    tactics = excluded.tactics,
    tactic_summary = excluded.tactic_summary,
    updated_at = now();
end;
$$;
