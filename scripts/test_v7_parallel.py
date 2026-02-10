"""
DB-connected parallel test to compare legacy vs V7 quant backends on real data.
Read-only: fetches posts/comments and prints comparison; does not write to DB.
"""

import argparse
import copy
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from supabase import Client, create_client

from analysis.v7.quant import run_pre_analyst
from analysis.v7.structs.payloads import PreAnalystPayload
from analysis.v7.naming import apply_cluster_naming, compute_quant_health, determine_naming_mode

load_dotenv()
logger = logging.getLogger("test_v7_parallel")


def get_supabase_client() -> Optional[Client]:
    url = os.environ.get("SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE key in environment.", file=sys.stderr)
        return None
    try:
        return create_client(url, key)
    except Exception as exc:
        print(f"Failed to create Supabase client: {exc}", file=sys.stderr)
        return None


def get_latest_post_ids(client: Client, limit: int = 3) -> List[str]:
    try:
        resp = (
            client.table("threads_posts")
            .select("id")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        print(f"Failed to fetch latest posts: {exc}", file=sys.stderr)
        return []
    rows = getattr(resp, "data", None) or []
    return [str(r.get("id")) for r in rows if r.get("id") is not None]


def fetch_comments(client: Client, post_id: str) -> List[Dict[str, Any]]:
    try:
        resp = (
            client.table("threads_comments")
            .select("id, source_comment_id, text, author_handle, like_count, reply_count, created_at")
            .eq("post_id", post_id)
            .order("like_count", desc=True)
            .execute()
        )
    except Exception as exc:
        print(f"Failed to fetch comments for post {post_id}: {exc}", file=sys.stderr)
        return []
    rows = getattr(resp, "data", None) or []
    comments: List[Dict[str, Any]] = []
    for row in rows:
        comments.append(
            {
                "id": str(row.get("id")),
                "source_comment_id": row.get("source_comment_id"),
                "text": row.get("text") or "",
                "author_handle": row.get("author_handle"),
                "like_count": row.get("like_count"),
                "likes": row.get("like_count"),
                "reply_count": row.get("reply_count"),
                "created_at": row.get("created_at"),
            }
        )
    return comments


def build_comment_lookup(comments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for c in comments:
        cid = str(c.get("id") or c.get("comment_id") or c.get("source_comment_id") or "")
        if cid:
            lookup[cid] = c
    return lookup


def summarize(payload: PreAnalystPayload) -> Dict[str, Any]:
    centroid_missing = sum(
        1
        for c in payload.clusters
        if c.size >= 2 and (not c.centroid_embedding_384 or len(c.centroid_embedding_384) != 384)
    )
    non_noise = sum(1 for a in payload.assignments if a.cluster_key != -1)
    noise = sum(1 for a in payload.assignments if a.cluster_key == -1)
    total = payload.total_comments or len(payload.assignments)
    coverage = payload.coverage if payload.coverage is not None else (round(non_noise / total, 4) if total else 0.0)
    noise_ratio = round(noise / total, 4) if total else 0.0
    cluster_sizes = [c.size for c in payload.clusters if c.size > 0]
    avg_cluster_size = round(sum(cluster_sizes) / len(cluster_sizes), 2) if cluster_sizes else 0.0
    return {
        "cluster_count": len(payload.clusters),
        "noise_count": noise,
        "noise_ratio": noise_ratio,
        "assignment_coverage": coverage,
        "centroid_missing_count": centroid_missing,
        "total_comments": total,
        "avg_cluster_size": avg_cluster_size,
        "backend_params": payload.backend_params or {},
    }


def pct(val: float) -> str:
    return f"{round(val * 100, 2)}%"


def run_dual(post_id: str, comments: List[Dict[str, Any]]) -> Optional[PreAnalystPayload]:
    if not comments:
        print(f"Skipping post {post_id}: no comments found.")
        return None

    total = len(comments)
    print(f"\n=== Testing Post {post_id} (N={total} comments) ===")

    supabase_client = get_supabase_client()
    legacy_payload = run_pre_analyst(post_id=post_id, comments=copy.deepcopy(comments), backend="legacy", supabase_client=None)
    bertopic_payload = run_pre_analyst(post_id=post_id, comments=copy.deepcopy(comments), backend="bertopic", supabase_client=supabase_client)
    comment_lookup = build_comment_lookup(comments)

    legacy = summarize(legacy_payload)
    bertopic = summarize(bertopic_payload)
    quant_health = compute_quant_health(bertopic_payload)
    naming_mode = determine_naming_mode(quant_health)

    def centroid_status(info: Dict[str, Any]) -> str:
        return "OK" if info["centroid_missing_count"] == 0 else f"missing={info['centroid_missing_count']}"

    print(
        f"[Legacy]   Clusters: {legacy['cluster_count']} | AvgSize: {legacy['avg_cluster_size']} | "
        f"Noise: {pct(legacy['noise_ratio'])} | Coverage: {pct(legacy['assignment_coverage'])} | "
        f"Centroid_384: {centroid_status(legacy)}"
    )
    print(
        f"[BERTopic] Clusters: {bertopic['cluster_count']} | AvgSize: {bertopic['avg_cluster_size']} | "
        f"Noise: {pct(bertopic['noise_ratio'])} | Coverage: {pct(bertopic['assignment_coverage'])} | "
        f"Centroid_384: {centroid_status(bertopic)} | params: {bertopic['backend_params']}"
    )
    print(f"[Health] quant_health={quant_health} naming_mode={naming_mode}")
    if bertopic_payload.provenance.get("v7_run_id"):
        print(
            f"[Persist] v7_run_id={bertopic_payload.provenance.get('v7_run_id')} "
            f"input_comment_ids_hash={(bertopic_payload.backend_params or {}).get('input_comment_ids_hash')}"
        )
    print("------------------------------------------------")
    print(f"Key Differences:")
    print(f"- Cluster Count Delta: {bertopic['cluster_count'] - legacy['cluster_count']}")
    print(f"- Noise Filtered: {bertopic['noise_count'] - legacy['noise_count']} comments")
    _print_clusters("Legacy", legacy_payload, comment_lookup)
    _print_clusters("BERTopic", bertopic_payload, comment_lookup)

    params = bertopic_payload.backend_params or {}
    guardrail_before = params.get("guardrail_snapshot_before") if isinstance(params, dict) else None
    if params.get("guardrail_applied") and guardrail_before:
        print("----- BERTopic Guardrail BEFORE ----")
        _print_clusters_snapshot(guardrail_before, comment_lookup)
        print("----- BERTopic Guardrail AFTER -----")
        _print_clusters("BERTopic-After", bertopic_payload, comment_lookup)
        _write_guardrail_snapshot(post_id, guardrail_before, bertopic_payload, comment_lookup)

    if naming_mode != "disabled":
        run_id = bertopic_payload.provenance.get("v7_run_id") or os.getenv("DL_NAMING_RUN_ID") or os.getenv("JOB_ID") or os.getenv("RUN_ID") or os.getenv("HOSTNAME") or "manual-run"
        run_tag = os.getenv("DL_NAMING_RUN_TAG") or "manual-run"
        naming_res = apply_cluster_naming(
            payload=bertopic_payload,
            comments_by_id=comment_lookup,
            run_id=run_id,
            run_tag=run_tag,
            supabase_client=get_supabase_client(),
            backend_name="bertopic",
        )
        _print_naming_results(naming_res)
    print("================================================")
    return bertopic_payload


def _write_guardrail_snapshot(post_id: str, before: Dict[str, Any], after_payload: PreAnalystPayload, comment_lookup: Dict[str, Dict[str, Any]]) -> None:
    snapshot_path = f"guardrail_snapshot_{post_id}.json"
    data = {
        "post_id": post_id,
        "before": before,
        "after": {
            "stats": {
                "cluster_count": len(after_payload.clusters),
                "avg_cluster_size": sum(c.size for c in after_payload.clusters) / len(after_payload.clusters) if after_payload.clusters else 0,
                "noise_ratio": summarize(after_payload)["noise_ratio"],
            },
            "clusters": [
                {
                    "cluster_key": c.cluster_key,
                    "size": c.size,
                    "keywords": c.keywords,
                    "top_comment_ids": c.top_comment_ids,
                }
                for c in after_payload.clusters
            ],
        },
        "comments_preview": {cid: (comment_lookup.get(cid) or {}).get("text") for cid in list(comment_lookup.keys())[:30]},
    }
    try:
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[guardrail] wrote snapshot to {snapshot_path}")
    except Exception as exc:
        print(f"[guardrail] failed to write snapshot: {exc}", file=sys.stderr)


def _print_clusters(label: str, payload: PreAnalystPayload, comment_lookup: Dict[str, Dict[str, Any]]) -> None:
    print(f"--- {label} clusters ---")
    assignments_by_cluster: Dict[str, List[str]] = {}
    for a in payload.assignments:
        key = str(a.cluster_key)
        assignments_by_cluster.setdefault(key, []).append(a.comment_id)
    for c in payload.clusters:
        _print_cluster_line(c.cluster_key, c.size, c.keywords, assignments_by_cluster.get(str(c.cluster_key), []), comment_lookup, c.top_comment_ids)


def _print_clusters_snapshot(snapshot: Dict[str, Any], comment_lookup: Dict[str, Dict[str, Any]]) -> None:
    assignments_by_cluster: Dict[str, List[str]] = {}
    for a in snapshot.get("assignments") or []:
        key = str(a.get("cluster_key"))
        assignments_by_cluster.setdefault(key, []).append(a.get("comment_id"))
    for c in snapshot.get("clusters") or []:
        _print_cluster_line(c.get("cluster_key"), c.get("size"), c.get("keywords"), assignments_by_cluster.get(str(c.get("cluster_key")), []), comment_lookup, c.get("top_comment_ids"))


def _print_cluster_line(cluster_key, size, keywords, assigned_comment_ids: List[str], comment_lookup: Dict[str, Dict[str, Any]], top_comment_ids: Optional[List[str]] = None) -> None:
    label = "Unidentified / Context-External" if str(cluster_key) == "-1" else f"Cluster {cluster_key}"
    kw_display = ", ".join((keywords or [])[:3])
    like_sum = sum((comment_lookup.get(cid, {}).get("like_count") or 0) for cid in assigned_comment_ids)
    deterministic_label = f"{label}"
    if kw_display:
        deterministic_label += f" — {kw_display}"
    print(f"  [{cluster_key}] size={size} like_sum={like_sum} label={deterministic_label}")
    samples = top_comment_ids or []
    for cid in samples[:3]:
        text = (comment_lookup.get(cid) or {}).get("text") or ""
        snippet = (text[:117] + "...") if len(text) > 120 else text
        print(f"     • {cid}: {snippet}")


def _print_naming_results(res: Dict[str, Any]) -> None:
    print("--- Naming results (LLM) ---")
    print(f"quant_health={res.get('quant_health')} naming_mode={res.get('naming_mode')}")
    for item in res.get("results") or []:
        print(
            f"  cluster {item.get('cluster_key')}: llm_label={item.get('llm_label')} "
            f"evidence_count={item.get('evidence_count')} staging={item.get('staging_inserted')} writeback={item.get('writeback')} reason={item.get('reason')}"
        )


def _compute_stability_hashes(payload: PreAnalystPayload) -> tuple[str, str]:
    import json
    import hashlib

    assignments = sorted(
        [(a.comment_id, a.cluster_key) for a in payload.assignments],
        key=lambda x: (str(x[0]), str(x[1])),
    )
    assignment_hash = hashlib.sha256(json.dumps(assignments, sort_keys=True).encode("utf-8")).hexdigest()
    centroids = []
    for c in sorted(payload.clusters, key=lambda x: str(x.cluster_key)):
        centroids.append((c.cluster_key, _hash_list(c.centroid_embedding_384)))
    centroid_hash = hashlib.sha256(json.dumps(centroids, sort_keys=True).encode("utf-8")).hexdigest()
    return assignment_hash, centroid_hash


def _hash_list(vec: List[float]) -> str:
    import struct
    import hashlib

    if not vec:
        return ""
    packed = b"".join(struct.pack("f", float(x)) for x in vec)
    return hashlib.sha256(packed).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Parallel test for V7 quant (legacy vs BERTopic).")
    parser.add_argument("--limit", type=int, default=3, help="Number of latest posts to test when --post_id not set (default: 3).")
    parser.add_argument("--post_id", help="Optional single post_id to test (overrides --limit).")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat runs for the same post to compare stability.")
    parser.add_argument("--assert-stable", action="store_true", default=True, help="Fail if repeat runs drift (default: on).")
    args = parser.parse_args()

    client = get_supabase_client()
    if not client:
        sys.exit(1)

    if args.post_id:
        post_ids = [args.post_id]
    else:
        post_ids = get_latest_post_ids(client, limit=args.limit)
    if not post_ids:
        print("No posts found to test; exiting.", file=sys.stderr)
        sys.exit(1)

    for pid in post_ids:
        comments = fetch_comments(client, pid)
        prev = None
        for i in range(max(1, args.repeat)):
            try:
                bertopic_payload = run_dual(pid, comments)
            except Exception as exc:
                print(f"Error testing post {pid}: {exc}", file=sys.stderr)
                continue
            if bertopic_payload is None:
                continue
            stats = summarize(bertopic_payload)
            ids_hash = (bertopic_payload.backend_params or {}).get("input_comment_ids_hash")
            assignment_hash, centroid_hashes = _compute_stability_hashes(bertopic_payload)
            run_id = bertopic_payload.provenance.get("v7_run_id")
            if prev:
                prev_run_id, prev_stats, prev_hash, prev_assign_hash, prev_centroids = prev
                print(f"[Repeat] prev_run_id={prev_run_id} vs run_id={run_id}")
                print(f"[Repeat] cluster_count delta={stats['cluster_count'] - prev_stats['cluster_count']} noise_ratio delta={stats['noise_ratio'] - prev_stats['noise_ratio']} coverage delta={stats['assignment_coverage'] - prev_stats['assignment_coverage']}")
                print(f"[Repeat] ids_hash match: {prev_hash == ids_hash}")
                drift = []
                if prev_assign_hash != assignment_hash:
                    drift.append("assignment_hash mismatch")
                if prev_centroids != centroid_hashes:
                    drift.append("centroid_hash mismatch")
                if drift:
                    print("❌ DRIFT DETECTED", "; ".join(drift))
                    if args.assert_stable:
                        sys.exit(1)
                else:
                    print("✅ STABILITY VERIFIED (Deterministic Quant Engine)")
            prev = (run_id, stats, ids_hash, assignment_hash, centroid_hashes)


if __name__ == "__main__":
    main()
