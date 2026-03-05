import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from database.store import save_coverage_audit, supabase


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _chunk(items: List[Dict[str, Any]], size: int = 200) -> Iterable[List[Dict[str, Any]]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def _execute_with_retry(call, *, label: str, retries: int = 3, base_sleep: float = 0.4):
    for attempt in range(retries):
        try:
            return call()
        except Exception:
            if attempt >= retries - 1:
                raise
            time.sleep(base_sleep * (2 ** attempt))


def ingest_run(run_dir: str) -> Dict[str, Any]:
    if not supabase:
        raise RuntimeError("Supabase client is not configured")

    posts_raw_path = os.path.join(run_dir, "threads_posts_raw.json")
    comments_path = os.path.join(run_dir, "threads_comments.json")
    edges_path = os.path.join(run_dir, "threads_comment_edges.json")
    post_payload_path = os.path.join(run_dir, "post_payload.json")

    posts_raw = _read_json(posts_raw_path)
    manifest_path = os.path.join(run_dir, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        try:
            manifest = _read_json(manifest_path) or {}
        except Exception:
            manifest = {}
    run_id = posts_raw.get("run_id")
    crawled_at_utc = posts_raw.get("crawled_at_utc")
    post_url = posts_raw.get("post_url")
    post_id_external = posts_raw.get("post_id")

    if not run_id or not post_url:
        raise RuntimeError("threads_posts_raw.json missing run_id/post_url")

    raw_payload = {
        "run_id": run_id,
        "crawled_at_utc": crawled_at_utc,
        "post_url": post_url,
        "post_id": post_id_external,
        "fetcher_version": posts_raw.get("fetcher_version"),
        "run_dir": posts_raw.get("run_dir") or run_dir,
        "raw_html_initial_path": posts_raw.get("raw_html_initial_path"),
        "raw_html_final_path": posts_raw.get("raw_html_final_path"),
        "raw_cards_path": posts_raw.get("raw_cards_path"),
    }
    _execute_with_retry(
        lambda: supabase.table("threads_posts_raw").upsert(raw_payload, on_conflict="run_id,post_id").execute(),
        label="threads_posts_raw",
    )

    captured_at = crawled_at_utc or datetime.now(timezone.utc).isoformat()
    _execute_with_retry(
        lambda: supabase.table("threads_posts")
        .upsert({"url": post_url, "captured_at": captured_at}, on_conflict="url")
        .execute(),
        label="threads_posts",
    )
    res = _execute_with_retry(
        lambda: supabase.table("threads_posts").select("id").eq("url", post_url).limit(1).execute(),
        label="threads_posts_select",
    )
    if not res.data:
        raise RuntimeError(f"threads_posts upsert ok but cannot re-select id for url={post_url}")
    post_row_id = res.data[0]["id"]

    if manifest:
        coverage = (manifest.get("coverage") or {}).copy()
        if not coverage:
            main_stats = (manifest.get("harvest_stats") or {}).get("main") or {}
            expected_ui = main_stats.get("expected_comment_count")
            unique_fetched = manifest.get("merged_total") or 0
            coverage_ratio = unique_fetched / max(1, int(expected_ui)) if expected_ui else None
            coverage = {
                "expected_replies_ui": expected_ui,
                "unique_fetched": unique_fetched,
                "coverage_ratio": coverage_ratio,
                "stop_reason": main_stats.get("stop_reason"),
                "budgets_used": (main_stats.get("coverage") or {}).get("budgets_used") or {},
                "rounds_json": (main_stats.get("rounds") or [])[-20:],
            }
        rounds = coverage.get("rounds_json") or (manifest.get("harvest_stats") or {}).get("main", {}).get("rounds") or []
        rounds_hash = coverage.get("rounds_hash")
        if rounds and not rounds_hash:
            rounds_hash = hashlib.sha256(
                json.dumps(rounds, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
        plateau_summary = coverage.get("plateau_summary")
        budgets_used = coverage.get("budgets_used") or {}
        if plateau_summary:
            budgets_used["plateau_summary"] = plateau_summary
        coverage_payload = {
            "captured_at": posts_raw.get("crawled_at_utc"),
            "expected_replies_ui": coverage.get("expected_replies_ui"),
            "unique_fetched": coverage.get("unique_fetched") or 0,
            "coverage_ratio": coverage.get("coverage_ratio"),
            "stop_reason": coverage.get("stop_reason") or "unknown",
            "budgets_used": budgets_used,
            "rounds_json": rounds[-20:] if rounds else None,
            "rounds_hash": rounds_hash,
        }
        try:
            save_coverage_audit(
                post_row_id,
                run_id,
                coverage_payload,
            )
        except Exception:
            pass

    comments = _read_json(comments_path) or []

    post_payload = {}
    if os.path.exists(post_payload_path):
        try:
            post_payload = _read_json(post_payload_path) or {}
        except Exception:
            post_payload = {}

    post_update: Dict[str, Any] = {}
    if post_payload:
        author = post_payload.get("author")
        post_text = post_payload.get("post_text")
        post_text_raw = post_payload.get("post_text_raw")
        images = post_payload.get("images") or post_payload.get("post_images") or []
        metrics = post_payload.get("metrics") or {}
        if author:
            post_update["author"] = author
        if post_text:
            post_update["post_text"] = post_text
        if post_text_raw:
            post_update["post_text_raw"] = post_text_raw
        if images:
            post_update["images"] = images
        if metrics:
            post_update["like_count"] = int(metrics.get("likes") or 0)
            post_update["view_count"] = int(metrics.get("views") or 0)
            post_update["reply_count_ui"] = int(metrics.get("reply_count") or 0)
            post_update["repost_count"] = int(metrics.get("repost_count") or 0)
            post_update["share_count"] = int(metrics.get("share_count") or 0)
    reply_count_ui = int((post_payload.get("metrics") or {}).get("reply_count") or 0) if post_payload else 0
    # Prefer UI metric when available; fallback to fetched count if UI is missing.
    if reply_count_ui > 0 or len(comments) == 0:
        post_update["reply_count"] = reply_count_ui
    else:
        post_update["reply_count"] = len(comments)
    if post_update:
        post_update["updated_at"] = datetime.now(timezone.utc).isoformat()
        _execute_with_retry(
            lambda: supabase.table("threads_posts").update(post_update).eq("id", post_row_id).execute(),
            label="threads_posts_update",
        )

    comment_rows: List[Dict[str, Any]] = []
    metrics_quality = {"exact": 0, "partial": 0, "missing": 0}
    for row in comments:
        quality = row.get("metrics_confidence") or "missing"
        if quality not in metrics_quality:
            quality = "missing"
        metrics_quality[quality] += 1
        raw_json = None
        metrics_present = row.get("metrics_present")
        metrics_hidden = row.get("metrics_hidden_low_value")
        if isinstance(metrics_present, dict) or isinstance(metrics_hidden, dict):
            raw_json = {
                "metrics_present": metrics_present if isinstance(metrics_present, dict) else {},
                "metrics_hidden_low_value": metrics_hidden if isinstance(metrics_hidden, dict) else {},
            }
        comment_rows.append(
            {
                "id": row.get("comment_id"),
                "post_id": post_row_id,
                "text": row.get("text"),
                "author_handle": row.get("author_handle"),
                "like_count": row.get("like_count") or 0,
                "reply_count": row.get("reply_count_ui") or 0,
                "created_at": row.get("approx_created_at_utc"),
                "captured_at": row.get("crawled_at_utc") or crawled_at_utc,
                "parent_comment_id": row.get("parent_comment_id"),
                "run_id": row.get("run_id") or run_id,
                "crawled_at_utc": row.get("crawled_at_utc") or crawled_at_utc,
                "post_url": row.get("post_url") or post_url,
                "time_token": row.get("time_token"),
                "approx_created_at_utc": row.get("approx_created_at_utc"),
                "reply_count_ui": row.get("reply_count_ui") or 0,
                "repost_count_ui": row.get("repost_count_ui"),
                "share_count_ui": row.get("share_count_ui"),
                "metrics_confidence": row.get("metrics_confidence"),
                "raw_json": raw_json,
                "source": row.get("source"),
                "comment_images": row.get("comment_images") or [],
                "source_locator": row.get("source_locator"),
            }
        )

    for chunk in _chunk(comment_rows, 120):
        _execute_with_retry(
            lambda chunk=chunk: supabase.table("threads_comments").upsert(chunk, on_conflict="id").execute(),
            label="threads_comments_upsert",
        )

    edges = _read_json(edges_path) or []
    edge_rows: List[Dict[str, Any]] = []
    for edge in edges:
        parent_id = edge.get("parent_comment_id")
        child_id = edge.get("child_comment_id")
        if not parent_id or not child_id or parent_id == child_id:
            continue
        edge_rows.append(
            {
                "run_id": run_id,
                "post_id": post_row_id,
                "parent_comment_id": parent_id,
                "child_comment_id": child_id,
                "edge_type": edge.get("edge_type") or "reply",
            }
        )

    for chunk in _chunk(edge_rows, 200):
        _execute_with_retry(
            lambda chunk=chunk: supabase.table("threads_comment_edges")
            .upsert(chunk, on_conflict="post_id,parent_comment_id,child_comment_id,edge_type")
            .execute(),
            label="threads_comment_edges_upsert",
        )

    return {
        "run_id": run_id,
        "post_id": post_row_id,
        "crawled_at_utc": crawled_at_utc,
        "comment_count": len(comment_rows),
        "edge_count": len(edge_rows),
        "metrics_quality": metrics_quality,
    }
