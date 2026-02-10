"""
Backfill centroid_embedding_384 for clusters where it is NULL and size >= 2.
Usage:
  python3 scripts/backfill_cluster_centroids.py --limit 50 --after_id 123::c0 --dry_run
Requires SUPABASE_URL + SUPABASE_SERVICE_* env and sentence-transformers installed.
"""
import argparse
import os
from datetime import datetime, timezone
from typing import List, Dict, Any

import numpy as np
from dotenv import load_dotenv
from supabase import create_client

from analysis.quant_engine import get_embedder
from analysis.v7.utils.text_preprocess import preprocess_for_embedding

load_dotenv()


def mean_pool(vectors: List[List[float]]) -> List[float] | None:
    if not vectors:
        return None
    arr = np.array(vectors, dtype=float)
    if arr.size == 0:
        return None
    return [float(x) for x in np.mean(arr, axis=0).tolist()]


def fetch_clusters(client, limit: int, after_id: str | None) -> List[Dict[str, Any]]:
    q = (
        client.table("threads_comment_clusters")
        .select("id,post_id,cluster_key,size,top_comment_ids")
        .is_("centroid_embedding_384", "null")
        .gte("size", 2)
        .order("id")
        .limit(limit)
    )
    if after_id:
        q = q.gt("id", after_id)
    resp = q.execute()
    return getattr(resp, "data", []) or []


def fetch_comments(client, ids: List[str]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    resp = client.table("threads_comments").select("id,text,embedding").in_("id", ids).execute()
    return getattr(resp, "data", []) or []


def main(limit: int, after_id: str | None, dry_run: bool):
    url = os.environ.get("SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    if not url or not key:
        raise SystemExit("SUPABASE_URL/KEY required")
    client = create_client(url, key)
    embedder = get_embedder()

    clusters = fetch_clusters(client, limit, after_id)
    print(f"[INFO] candidates={len(clusters)}")
    repaired = skipped = failed = 0
    for row in clusters:
        cid = row.get("id")
        top_ids = row.get("top_comment_ids") or []
        comments = fetch_comments(client, top_ids)
        texts = [preprocess_for_embedding(c.get("text") or "") for c in comments if c.get("text")]
        if not texts:
            skipped += 1
            print(f"[SKIP] no texts post={row.get('post_id')} cluster={row.get('cluster_key')}")
            continue
        emb_384 = embedder.encode(texts)
        if hasattr(emb_384, "tolist"):
            emb_384 = emb_384.tolist()
        centroid_384 = mean_pool(emb_384)
        if centroid_384 is None:
            failed += 1
            print(f"[FAIL] unable to compute centroid post={row.get('post_id')} cluster={row.get('cluster_key')}")
            continue
        payload = {
            "centroid_embedding_384": centroid_384,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if dry_run:
            print(f"[DRY] would update {cid}")
            repaired += 1
            continue
        client.table("threads_comment_clusters").update(payload).eq("id", cid).execute()
        repaired += 1
        print(f"[OK] updated {cid}")
    print(f"[DONE] repaired={repaired} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--after_id", type=str, default=None)
    parser.add_argument("--dry_run", action="store_true")
    args = parser.parse_args()
    main(args.limit, args.after_id, args.dry_run)
