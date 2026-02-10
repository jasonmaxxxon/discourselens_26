import os
from typing import Any, Dict

from database.store import supabase


MATH_KEYS = {
    "hard_metrics",
    "per_cluster_metrics",
    "reply_matrix",
    "physics",
    "golden_samples",
    "clusters",
    "cluster_summary",
    "quant_summary",
}


def assert_no_quant_calls() -> None:
    """
    Set guard flag so any quant_engine call errors during narrative execution.
    """
    os.environ["DL_ASSERT_NO_QUANT"] = "1"


def assert_preanalysis_present(post_id: int) -> None:
    resp = (
        supabase.table("threads_posts")
        .select("id, preanalysis_json, preanalysis_status")
        .eq("id", post_id)
        .limit(1)
        .execute()
    )
    row = (resp.data or [None])[0]
    if not row:
        raise RuntimeError(f"post_id {post_id} not found")
    if row.get("preanalysis_status") != "done" or not isinstance(row.get("preanalysis_json"), dict):
        raise RuntimeError("preanalysis_json missing or not done")


def assert_analysis_json_narrative_only(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError("analysis_json must be a dict")
    forbidden = [k for k in payload.keys() if k in MATH_KEYS]
    if forbidden:
        raise RuntimeError(f"analysis_json contains math keys: {forbidden}")
