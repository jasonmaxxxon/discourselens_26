"""Topic worker skeleton (Phase 3.5): claim + lease + deterministic snapshot stats."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

RUN_SELECT = (
    "id,topic_name,seed_query,seed_post_ids,time_range_start,time_range_end,run_params,"
    "topic_run_hash,lifecycle_hash,status,source,freshness_lag_seconds,coverage_gap,stats_json,"
    "error_summary,created_by,created_at,updated_at,started_at,finished_at,"
    "lock_owner,locked_at,heartbeat_at,lock_lease_seconds,attempt_count"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _coerce_nonneg_int(value: Any) -> int:
    try:
        v = int(value)
    except Exception:
        return 0
    return max(0, v)


def _load_topic_run(supabase_client: Any, topic_run_id: str) -> Optional[Dict[str, Any]]:
    resp = supabase_client.table("topic_runs").select(RUN_SELECT).eq("id", topic_run_id).limit(1).execute()
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def _fetch_topic_posts_all(supabase_client: Any, topic_run_id: str, chunk_size: int = 500) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    offset = 0
    while True:
        resp = (
            supabase_client.table("topic_posts")
            .select("post_id,ordinal,post_created_at", count="exact")
            .eq("topic_run_id", topic_run_id)
            .order("ordinal")
            .range(offset, offset + chunk_size - 1)
            .execute()
        )
        rows = [r for r in (getattr(resp, "data", None) or []) if isinstance(r, dict)]
        out.extend(rows)
        if len(rows) < chunk_size:
            break
        offset += chunk_size
    return out


def _load_threads_posts_metrics(supabase_client: Any, post_ids: List[int], chunk_size: int = 200) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    if not post_ids:
        return out
    ordered_ids = sorted(set(int(pid) for pid in post_ids))
    for idx in range(0, len(ordered_ids), chunk_size):
        chunk = ordered_ids[idx : idx + chunk_size]
        resp = (
            supabase_client.table("threads_posts")
            .select("id,created_at,like_count,reply_count,repost_count,share_count")
            .in_("id", chunk)
            .execute()
        )
        for row in (getattr(resp, "data", None) or []):
            if not isinstance(row, dict):
                continue
            try:
                pid = int(row.get("id"))
            except Exception:
                continue
            out[pid] = row
    return out


def compute_topic_snapshot_stats(supabase_client: Any, topic_run_id: str) -> Dict[str, Any]:
    topic_rows = _fetch_topic_posts_all(supabase_client, topic_run_id)
    post_ids: List[int] = []
    topic_times: Dict[int, Optional[datetime]] = {}
    for row in topic_rows:
        try:
            pid = int(row.get("post_id"))
        except Exception:
            continue
        post_ids.append(pid)
        topic_times[pid] = _parse_iso(row.get("post_created_at"))

    post_ids = sorted(set(post_ids))
    metrics_map = _load_threads_posts_metrics(supabase_client, post_ids)

    times: List[datetime] = []
    comment_count_total = 0
    engagement_sum = 0
    for pid in post_ids:
        ts = topic_times.get(pid)
        if ts is None:
            ts = _parse_iso((metrics_map.get(pid) or {}).get("created_at"))
        if ts is not None:
            times.append(ts)

        row = metrics_map.get(pid) or {}
        likes = _coerce_nonneg_int(row.get("like_count"))
        replies = _coerce_nonneg_int(row.get("reply_count"))
        reposts = _coerce_nonneg_int(row.get("repost_count"))
        shares = _coerce_nonneg_int(row.get("share_count"))

        comment_count_total += replies
        engagement_sum += likes + replies + reposts + shares

    first_post_time = _to_iso(min(times)) if times else None
    last_post_time = _to_iso(max(times)) if times else None

    # Deterministic overwrite payload: no volatile timestamps.
    return {
        "worker_version": "topic_worker_v1",
        "post_count": len(post_ids),
        "first_post_time": first_post_time,
        "last_post_time": last_post_time,
        "comment_count_total": int(comment_count_total),
        "engagement_sum": int(engagement_sum),
    }


def _lease_seconds_for_row(row: Dict[str, Any], fallback: int) -> int:
    raw = row.get("lock_lease_seconds")
    try:
        val = int(raw)
    except Exception:
        val = int(fallback)
    return max(1, min(val, 86400))


def _is_lease_expired(row: Dict[str, Any], now_dt: datetime, fallback_lease_seconds: int) -> bool:
    lease_seconds = _lease_seconds_for_row(row, fallback_lease_seconds)
    hb = _parse_iso(row.get("heartbeat_at")) or _parse_iso(row.get("updated_at")) or _parse_iso(row.get("locked_at"))
    if hb is None:
        return True
    return (now_dt - hb).total_seconds() > lease_seconds


def _try_claim(
    supabase_client: Any,
    row: Dict[str, Any],
    *,
    lock_owner: str,
    lease_seconds: int,
    force: bool,
) -> Optional[Dict[str, Any]]:
    now_iso = _to_iso(_utc_now())
    expected_status = str(row.get("status") or "").strip().lower()
    expected_updated = row.get("updated_at")
    started_at = row.get("started_at") or now_iso
    attempt_count = _coerce_nonneg_int(row.get("attempt_count")) + 1

    patch = {
        "status": "running",
        "lock_owner": lock_owner,
        "locked_at": now_iso,
        "heartbeat_at": now_iso,
        "lock_lease_seconds": int(max(1, min(lease_seconds, 86400))),
        "attempt_count": attempt_count,
        "started_at": started_at,
        "finished_at": None,
        "updated_at": now_iso,
    }

    query = supabase_client.table("topic_runs").update(patch).eq("id", row.get("id"))
    if not force:
        query = query.eq("status", expected_status)
    if expected_updated:
        query = query.eq("updated_at", expected_updated)

    resp = query.select(RUN_SELECT).execute()
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None
    claimed = rows[0]
    return claimed if isinstance(claimed, dict) else None


def claim_topic_run(
    supabase_client: Any,
    *,
    lock_owner: str,
    lease_seconds: int,
    topic_run_id: Optional[str] = None,
    force_recompute: bool = False,
) -> Dict[str, Any]:
    now_dt = _utc_now()

    if topic_run_id:
        row = _load_topic_run(supabase_client, topic_run_id)
        if not row:
            return {"status": "not_found", "reason_code": "topic_not_found"}

        status = str(row.get("status") or "").strip().lower()
        if status == "running" and not force_recompute and not _is_lease_expired(row, now_dt, lease_seconds):
            return {"status": "empty", "reason_code": "topic_locked"}
        if status == "completed" and not force_recompute:
            return {"status": "empty", "reason_code": "topic_already_ready"}

        claimed = _try_claim(
            supabase_client,
            row,
            lock_owner=lock_owner,
            lease_seconds=lease_seconds,
            force=bool(force_recompute),
        )
        if not claimed:
            return {"status": "empty", "reason_code": "topic_claim_conflict"}
        return {
            "status": "claimed",
            "row": claimed,
            "reclaimed": bool(status == "running" and _is_lease_expired(row, now_dt, lease_seconds)),
        }

    pending_resp = (
        supabase_client.table("topic_runs")
        .select(RUN_SELECT)
        .eq("status", "pending")
        .order("created_at")
        .limit(30)
        .execute()
    )
    pending_rows = [r for r in (getattr(pending_resp, "data", None) or []) if isinstance(r, dict)]
    for row in pending_rows:
        claimed = _try_claim(
            supabase_client,
            row,
            lock_owner=lock_owner,
            lease_seconds=lease_seconds,
            force=False,
        )
        if claimed:
            return {"status": "claimed", "row": claimed, "reclaimed": False}

    running_resp = (
        supabase_client.table("topic_runs")
        .select(RUN_SELECT)
        .eq("status", "running")
        .order("updated_at")
        .limit(30)
        .execute()
    )
    running_rows = [r for r in (getattr(running_resp, "data", None) or []) if isinstance(r, dict)]
    for row in running_rows:
        if not _is_lease_expired(row, now_dt, lease_seconds):
            continue
        claimed = _try_claim(
            supabase_client,
            row,
            lock_owner=lock_owner,
            lease_seconds=lease_seconds,
            force=False,
        )
        if claimed:
            return {"status": "claimed", "row": claimed, "reclaimed": True, "reason_code": "lease_expired"}

    return {"status": "empty", "reason_code": "no_accepted_topic_runs"}


def _complete_topic_run(
    supabase_client: Any,
    *,
    topic_run_id: str,
    lock_owner: str,
    stats_json: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    now_iso = _to_iso(_utc_now())
    patch = {
        "status": "completed",
        "stats_json": stats_json,
        "error_summary": None,
        "finished_at": now_iso,
        "heartbeat_at": now_iso,
        "updated_at": now_iso,
        "lock_owner": None,
        "locked_at": None,
    }
    resp = (
        supabase_client.table("topic_runs")
        .update(patch)
        .eq("id", topic_run_id)
        .eq("status", "running")
        .eq("lock_owner", lock_owner)
        .select(RUN_SELECT)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def _fail_topic_run(
    supabase_client: Any,
    *,
    topic_run_id: str,
    lock_owner: str,
    reason: str,
) -> Optional[Dict[str, Any]]:
    now_iso = _to_iso(_utc_now())
    patch = {
        "status": "failed",
        "error_summary": reason[:300],
        "heartbeat_at": now_iso,
        "updated_at": now_iso,
        "lock_owner": None,
        "locked_at": None,
    }
    resp = (
        supabase_client.table("topic_runs")
        .update(patch)
        .eq("id", topic_run_id)
        .eq("status", "running")
        .eq("lock_owner", lock_owner)
        .select(RUN_SELECT)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def run_topic_worker_once(
    supabase_client: Any,
    *,
    lock_owner: str,
    lease_seconds: int,
    topic_run_id: Optional[str] = None,
    force_recompute: bool = False,
) -> Dict[str, Any]:
    claim = claim_topic_run(
        supabase_client,
        lock_owner=lock_owner,
        lease_seconds=lease_seconds,
        topic_run_id=topic_run_id,
        force_recompute=force_recompute,
    )

    claim_status = claim.get("status")
    if claim_status != "claimed":
        return claim

    row = claim.get("row") or {}
    topic_id = str(row.get("id") or "")
    topic_run_hash = str(row.get("topic_run_hash") or "")

    try:
        stats_json = compute_topic_snapshot_stats(supabase_client, topic_id)
        done = _complete_topic_run(
            supabase_client,
            topic_run_id=topic_id,
            lock_owner=lock_owner,
            stats_json=stats_json,
        )
        if not done:
            return {"status": "empty", "reason_code": "topic_complete_conflict", "topic_id": topic_id}
        return {
            "status": "ready",
            "reason_code": "topic_run_ready",
            "topic_id": topic_id,
            "topic_run_hash": topic_run_hash,
            "stats_json": stats_json,
            "reclaimed": bool(claim.get("reclaimed")),
            "force_recompute": bool(force_recompute),
        }
    except Exception as exc:
        _fail_topic_run(
            supabase_client,
            topic_run_id=topic_id,
            lock_owner=lock_owner,
            reason=f"worker_failed: {type(exc).__name__}: {exc}",
        )
        return {
            "status": "failed",
            "reason_code": "topic_run_failed",
            "topic_id": topic_id,
            "topic_run_hash": topic_run_hash,
            "detail": f"worker failed: {type(exc).__name__}",
        }
