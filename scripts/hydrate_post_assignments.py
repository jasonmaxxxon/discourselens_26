#!/usr/bin/env python3
"""
Hydrate threads_comments.cluster_key for an existing post without re-running the full pipeline.

Strategy:
1) Try to load full assignments from threads_posts.analysis_json (preferred).
2) If missing, try threads_posts.raw_json or cluster_summary samples that carry cluster_key.
3) Fallback to top_comment_ids per cluster (partial hydration) if no per-comment mapping is found.
Logs whether hydration is full or partial.
Respects DL_STRICT_CLUSTER_WRITEBACK for assignment writeback.
"""

import argparse
import logging
import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from supabase import create_client

from database.store import apply_comment_cluster_assignments

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hydrate_assignments")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_KEY")
)

STRICT = str(os.environ.get("DL_STRICT_CLUSTER_WRITEBACK", "0")).lower() in {"1", "true", "yes", "on"}

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY. Check .env.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def _coerce_cluster_key(val: Any) -> int | None:
    try:
        return int(val)
    except Exception:
        return None


def _collect_assignments_from_analysis(analysis_json: Dict[str, Any], cluster_summary: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str]:
    """
    Build per-comment assignments from analysis_json.segments (preferred) or cluster_summary.samples.
    """
    assignments: List[Dict[str, Any]] = []
    segments = analysis_json.get("segments") or []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        cid = _coerce_cluster_key(seg.get("cluster_key") or seg.get("cluster_id") or seg.get("key"))
        if cid is None:
            continue
        comment_id = seg.get("comment_id") or seg.get("id")
        if comment_id is None:
            continue
        assignments.append({"comment_id": comment_id, "cluster_key": cid})

    if assignments:
        return assignments, "full_assignments"

    clusters = (cluster_summary or {}).get("clusters") or {}
    for cid_str, info in clusters.items():
        cid = _coerce_cluster_key(cid_str or info.get("cluster_id") or info.get("cluster_key"))
        if cid is None:
            continue
        samples = info.get("samples") or []
        for s in samples:
            if not isinstance(s, dict):
                continue
            comment_id = s.get("comment_id") or s.get("id")
            if comment_id is None:
                continue
            assignments.append({"comment_id": comment_id, "cluster_key": cid})
    if assignments:
        return assignments, "samples"
    return assignments, "unknown"


def _collect_assignments_from_top_comment_ids(cluster_summary: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str]:
    """
    Partial hydration: use cluster_summary.top_comment_ids to populate some rows.
    """
    assignments: List[Dict[str, Any]] = []
    clusters = (cluster_summary or {}).get("clusters") or {}
    for cid_str, info in clusters.items():
        cid = _coerce_cluster_key(cid_str or info.get("cluster_id") or info.get("cluster_key"))
        if cid is None:
            continue
        top_ids = info.get("top_comment_ids") or []
        for comment_id in top_ids:
            assignments.append({"comment_id": comment_id, "cluster_key": cid})
    return assignments, "top_comment_ids"


def hydrate(post_id: str | int, allow_partial: bool = False) -> None:
    logger.info("[Hydrate] Start post_id=%s strict=%s allow_partial=%s", post_id, STRICT, allow_partial)
    res = supabase.table("threads_posts").select("*").eq("id", post_id).single().execute()
    row = getattr(res, "data", None) or {}
    if not row:
        raise RuntimeError(f"Post {post_id} not found")

    analysis_json = row.get("analysis_json") or {}
    cluster_summary = row.get("cluster_summary") or {}

    assignments, source = _collect_assignments_from_analysis(analysis_json, cluster_summary)
    if not assignments:
        assignments, source = _collect_assignments_from_top_comment_ids(cluster_summary)

    partial = source != "full_assignments"

    logger.info(
        "[Hydrate] assignment candidates collected",
        extra={
            "assignment_candidates_collected": len(assignments),
            "partial_hydration": partial,
            "assignment_source": source,
        },
    )

    if not assignments:
        raise RuntimeError(f"No assignment sources found for post {post_id}")

    enforce_coverage = (source == "full_assignments") or allow_partial
    write_res = apply_comment_cluster_assignments(
        post_id,
        assignments,
        enforce_coverage=enforce_coverage,
        unassignable_total=0,
    )
    logger.info(
        "[Hydrate] assignment writeback result",
        extra={
            "ok": write_res.get("ok"),
            "assignments_total": len(assignments),
            "assignments_updated_rows": write_res.get("updated_rows"),
            "target_rows": write_res.get("target_rows"),
            "coverage_pct": round((write_res.get("coverage") or 0) * 100, 2) if write_res.get("coverage") is not None else None,
            "partial_hydration": partial,
            "assignment_source": source,
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Hydrate threads_comments.cluster_key for an existing post")
    parser.add_argument("post_id", help="Target post id")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow coverage enforcement even when only partial sources are available",
    )
    args = parser.parse_args()
    hydrate(args.post_id, allow_partial=args.allow_partial)


if __name__ == "__main__":
    main()
