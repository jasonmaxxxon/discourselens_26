"""Topic run registry store for Phase-3 API skeleton."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from webapp.lib.topic_hash import normalize_seed_query

_DEFAULT_TIME_RANGE_START = "1970-01-01T00:00:00Z"
_DEFAULT_TIME_RANGE_END = "2100-01-01T00:00:00Z"


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso_utc(value: str, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError(f"invalid {field_name}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonicalize_post_ids(post_ids: Iterable[Any], max_items: int = 500) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []
    for raw in post_ids or []:
        text = str(raw).strip()
        if not text:
            continue
        try:
            pid = int(text)
        except Exception as exc:
            raise ValueError(f"invalid post_id: {raw}") from exc
        if pid <= 0:
            raise ValueError(f"invalid post_id: {raw}")
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    out.sort()
    if not out:
        raise ValueError("post_ids must not be empty")
    if len(out) > max_items:
        raise ValueError(f"post_ids exceeds limit {max_items}")
    return out


def normalize_topic_name(topic_name: Optional[str], seed_query: str) -> str:
    candidate = " ".join((topic_name or "").strip().split())
    if candidate:
        return candidate[:160]
    fallback = " ".join((seed_query or "").strip().split())
    if fallback:
        return fallback[:160]
    return "topic-run"


def fetch_posts_created_at(supabase_client: Any, post_ids: List[int]) -> Dict[int, Optional[str]]:
    if not post_ids:
        return {}
    resp = (
        supabase_client.table("threads_posts")
        .select("id,created_at")
        .in_("id", post_ids)
        .execute()
    )
    out: Dict[int, Optional[str]] = {}
    for row in (getattr(resp, "data", None) or []):
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("id"))
        except Exception:
            continue
        created_at = row.get("created_at")
        out[pid] = str(created_at) if created_at else None
    return out


def validate_post_ids_exist(supabase_client: Any, post_ids: List[int]) -> List[int]:
    existing_map = fetch_posts_created_at(supabase_client, post_ids)
    missing = sorted(pid for pid in post_ids if pid not in existing_map)
    return missing


def resolve_time_range(
    supabase_client: Any,
    *,
    post_ids: List[int],
    time_range_start: Optional[str],
    time_range_end: Optional[str],
) -> Tuple[str, str]:
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None

    if time_range_start is not None:
        start_dt = _parse_iso_utc(time_range_start, "time_range_start")
    if time_range_end is not None:
        end_dt = _parse_iso_utc(time_range_end, "time_range_end")

    created_map = fetch_posts_created_at(supabase_client, post_ids)
    post_times: List[datetime] = []
    for value in created_map.values():
        if not value:
            continue
        try:
            post_times.append(_parse_iso_utc(value, "created_at"))
        except Exception:
            continue

    if start_dt is None:
        if post_times:
            start_dt = min(post_times)
        elif end_dt is not None:
            start_dt = end_dt - timedelta(days=7)
        else:
            start_dt = _parse_iso_utc(_DEFAULT_TIME_RANGE_START, "time_range_start")

    if end_dt is None:
        if post_times:
            end_dt = max(post_times) + timedelta(seconds=1)
        else:
            end_dt = _parse_iso_utc(_DEFAULT_TIME_RANGE_END, "time_range_end")

    if end_dt <= start_dt:
        raise ValueError("time_range_end must be greater than time_range_start")

    return _to_utc_iso(start_dt), _to_utc_iso(end_dt)


def build_topic_run_hash_payload(
    *,
    seed_query: str,
    time_range_start: str,
    time_range_end: str,
    post_ids: List[int],
) -> Dict[str, Any]:
    from webapp.lib.topic_hash import compute_topic_run_hash

    return {
        "seed_query": normalize_seed_query(seed_query),
        "time_range_start": time_range_start,
        "time_range_end": time_range_end,
        "post_ids": post_ids,
        "topic_run_hash": compute_topic_run_hash(
            seed_query=seed_query,
            time_range_start=time_range_start,
            time_range_end=time_range_end,
            post_ids=post_ids,
        ),
    }


def get_topic_run_by_hash(supabase_client: Any, topic_run_hash: str) -> Optional[Dict[str, Any]]:
    resp = (
        supabase_client.table("topic_runs")
        .select(
            "id,topic_name,seed_query,seed_post_ids,time_range_start,time_range_end,run_params,topic_run_hash,lifecycle_hash,status,source,freshness_lag_seconds,coverage_gap,stats_json,error_summary,created_by,created_at,updated_at,started_at,finished_at"
        )
        .eq("topic_run_hash", topic_run_hash)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def create_topic_run(
    supabase_client: Any,
    *,
    topic_name: str,
    seed_query: str,
    seed_post_ids: List[int],
    time_range_start: str,
    time_range_end: str,
    run_params: Dict[str, Any],
    source: str,
    created_by: Optional[str],
    topic_run_hash: str,
) -> Tuple[Dict[str, Any], bool]:
    existing = get_topic_run_by_hash(supabase_client, topic_run_hash)
    if existing:
        return existing, True

    row = {
        "topic_name": topic_name,
        "seed_query": seed_query,
        "seed_post_ids": seed_post_ids,
        "time_range_start": time_range_start,
        "time_range_end": time_range_end,
        "run_params": run_params or {},
        "topic_run_hash": topic_run_hash,
        "status": "pending",
        "source": source or "manual",
        "created_by": created_by,
        "stats_json": {},
        "coverage_gap": False,
    }

    try:
        resp = supabase_client.table("topic_runs").insert(row).execute()
        inserted_rows = getattr(resp, "data", None) or []
        if inserted_rows and isinstance(inserted_rows[0], dict):
            return inserted_rows[0], False
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate" in msg or "topic_runs_hash_uq" in msg or "23505" in msg:
            existing = get_topic_run_by_hash(supabase_client, topic_run_hash)
            if existing:
                return existing, True
        raise

    fallback = get_topic_run_by_hash(supabase_client, topic_run_hash)
    if fallback:
        return fallback, True
    raise RuntimeError("topic_run insert failed")


def bulk_insert_topic_posts(
    supabase_client: Any,
    *,
    topic_run_id: str,
    post_ids: List[int],
    inclusion_source: str = "seed",
    inclusion_reason: Optional[str] = "seed_post_ids",
) -> int:
    if not post_ids:
        return 0

    created_at_map = fetch_posts_created_at(supabase_client, post_ids)
    rows = []
    for ordinal, post_id in enumerate(post_ids):
        rows.append(
            {
                "topic_run_id": topic_run_id,
                "post_id": int(post_id),
                "ordinal": ordinal,
                "inclusion_source": inclusion_source,
                "inclusion_reason": inclusion_reason,
                "post_created_at": created_at_map.get(int(post_id)),
            }
        )

    supabase_client.table("topic_posts").upsert(rows, on_conflict="topic_run_id,post_id").execute()
    return len(rows)


def get_topic_run(supabase_client: Any, topic_run_id: str) -> Optional[Dict[str, Any]]:
    resp = (
        supabase_client.table("topic_runs")
        .select(
            "id,topic_name,seed_query,seed_post_ids,time_range_start,time_range_end,run_params,topic_run_hash,lifecycle_hash,status,source,freshness_lag_seconds,coverage_gap,stats_json,error_summary,created_by,created_at,updated_at,started_at,finished_at"
        )
        .eq("id", topic_run_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def list_topic_posts(
    supabase_client: Any,
    topic_run_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    q_limit = max(1, min(int(limit), 200))
    q_offset = max(0, int(offset))
    resp = (
        supabase_client.table("topic_posts")
        .select("post_id,ordinal,inclusion_source,inclusion_reason,post_created_at", count="exact")
        .eq("topic_run_id", topic_run_id)
        .order("ordinal")
        .range(q_offset, q_offset + q_limit - 1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    total = getattr(resp, "count", None)
    if total is None:
        total = len(rows)
    return {"total": int(total), "rows": rows, "limit": q_limit, "offset": q_offset}
