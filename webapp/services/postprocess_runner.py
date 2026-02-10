import logging
from typing import Any, Dict, Optional

from database.store import supabase

# Optional preanalysis module; safe import
try:
    from analysis.preanalysis_runner import run_preanalysis
except Exception:
    run_preanalysis = None

logger = logging.getLogger(__name__)


def run_postprocess_for_post(post_id: str) -> Dict[str, Any]:
    """
    Idempotent deterministic preanalysis (quant/physics/writeback) for a single post_id.
    - Fetches threads_posts row
    - Runs preanalysis and writes to preanalysis_json
    """
    if not post_id:
        raise ValueError("post_id is required for postprocess")

    resp = supabase.table("threads_posts").select("*").eq("id", post_id).limit(1).execute()
    row = (resp.data or [None])[0]
    if not row:
        raise RuntimeError(f"post_id {post_id} not found in threads_posts")

    if not run_preanalysis:
        logger.info("[Postprocess] Preanalysis module not available; skipping for post_id=%s", post_id)
        return {"post_id": post_id, "status": "skipped"}

    logger.info("[Postprocess] Preanalysis start post_id=%s", post_id)
    payload = run_preanalysis(int(post_id), prefer_sot=True, persist_assignments=True)
    logger.info("[Postprocess] Preanalysis done post_id=%s", post_id)
    return {"post_id": post_id, "status": "done", "version": payload.get("version")}
