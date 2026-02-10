create or replace function public.upsert_comment_clusters(p_post_id bigint, p_clusters jsonb)
returns void
language plpgsql
as $$
begin
  if jsonb_typeof(p_clusters) <> 'array' then
    raise exception 'upsert_comment_clusters: p_clusters must be json array' using errcode = '22023';
  end if;

  insert into public.threads_comment_clusters (
    id,
    post_id,
    cluster_key,
    label,
    summary,
    size,
    keywords,
    top_comment_ids,
    tactics,
    tactic_summary,
    centroid_embedding_384,
    updated_at
  )
  select
    p_post_id::text || '::c' || (cl->>'cluster_key'),
    p_post_id,
    (cl->>'cluster_key')::int,
    cl->>'label',
    cl->>'summary',
    nullif(cl->>'size','')::int,
    case
      when jsonb_typeof(coalesce(cl->'keywords','[]'::jsonb)) = 'array'
        then array(select jsonb_array_elements_text(cl->'keywords'))
      else
        raise exception 'upsert_comment_clusters: keywords must be array (cluster_key=%)', (cl->>'cluster_key') using errcode='22023'
    end,
    case
      when jsonb_typeof(coalesce(cl->'top_comment_ids','[]'::jsonb)) = 'array'
        then coalesce(cl->'top_comment_ids','[]'::jsonb)
      else
        raise exception 'upsert_comment_clusters: top_comment_ids must be array (cluster_key=%)', (cl->>'cluster_key') using errcode='22023'
    end,
    case
      when jsonb_typeof(coalesce(cl->'tactics','[]'::jsonb)) = 'array'
        then array(select jsonb_array_elements_text(cl->'tactics'))
      else
        raise exception 'upsert_comment_clusters: tactics must be array (cluster_key=%)', (cl->>'cluster_key') using errcode='22023'
    end,
    cl->>'tactic_summary',
    case
      when jsonb_typeof(cl->'centroid_embedding_384') = 'array'
        then (array(select (jsonb_array_elements_text(cl->'centroid_embedding_384'))::float4))::vector(384)
      else null
    end,
    now()
  from jsonb_array_elements(p_clusters) as cl
  on conflict (id) do update set
    label = excluded.label,
    summary = excluded.summary,
    size = excluded.size,
    keywords = excluded.keywords,
    top_comment_ids = excluded.top_comment_ids,
    tactics = excluded.tactics,
    tactic_summary = excluded.tactic_summary,
    centroid_embedding_384 = excluded.centroid_embedding_384,
    updated_at = now();
end;
$$;
