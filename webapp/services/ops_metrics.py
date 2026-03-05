import asyncio
import os
from datetime import datetime, timezone, timedelta, date
from typing import Any, Dict, List, Optional

from database.store import supabase


DEFAULT_RANGE_DAYS = 7
MAX_RANGE_DAYS = 365


def _parse_range_days(raw: str | None) -> int:
    if not raw:
        return DEFAULT_RANGE_DAYS
    raw = str(raw).strip().lower()
    if raw.endswith("d"):
        raw = raw[:-1]
    try:
        days = int(raw)
    except Exception:
        return DEFAULT_RANGE_DAYS
    if days <= 0:
        return DEFAULT_RANGE_DAYS
    return min(days, MAX_RANGE_DAYS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _date_key(value: Any) -> Optional[str]:
    ts = _parse_ts(value)
    if not ts:
        return None
    return ts.date().isoformat()


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def _safe_rate(numer: int, denom: int) -> Optional[float]:
    if denom <= 0:
        return None
    return numer / denom


async def _fetch_rows(table: str, fields: str, *, since_iso: str, time_field: str, limit: int) -> List[Dict[str, Any]]:
    def _call():
        res = (
            supabase.table(table)
            .select(fields)
            .gte(time_field, since_iso)
            .order(time_field, desc=True)
            .limit(limit)
            .execute()
        )
        if getattr(res, "error", None):
            raise RuntimeError(getattr(res.error, "message", str(res.error)))
        return res

    try:
        res = await asyncio.to_thread(_call)
        return res.data or []
    except Exception:
        return []


def _compute_job_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    success = 0
    failed = 0
    inflight = 0
    daily: Dict[str, Dict[str, int]] = {}
    for row in rows:
        stage = str(row.get("stage") or "").lower()
        status = str(row.get("status") or "").lower()
        is_failed = "failed" in stage or status == "failed"
        is_success = "completed" in stage or status == "completed"
        if is_failed:
            failed += 1
        elif is_success:
            success += 1
        else:
            inflight += 1
        day = _date_key(row.get("updated_at") or row.get("created_at"))
        if not day:
            continue
        bucket = daily.setdefault(day, {"success": 0, "failed": 0, "total": 0})
        if is_failed:
            bucket["failed"] += 1
        elif is_success:
            bucket["success"] += 1
        bucket["total"] += 1
    total = success + failed + inflight
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "inflight": inflight,
        "success_rate": _safe_rate(success, success + failed),
        "daily": daily,
    }


def _compute_coverage_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ratios = [r.get("coverage_ratio") for r in rows if r.get("coverage_ratio") is not None]
    ratios_f = [float(r) for r in ratios if isinstance(r, (int, float))]
    daily: Dict[str, List[float]] = {}
    for row in rows:
        ratio = row.get("coverage_ratio")
        if ratio is None:
            continue
        try:
            ratio_val = float(ratio)
        except Exception:
            continue
        day = _date_key(row.get("captured_at") or row.get("created_at"))
        if not day:
            continue
        daily.setdefault(day, []).append(ratio_val)
    daily_avg = {day: _mean(vals) for day, vals in daily.items()}
    return {
        "rows": len(rows),
        "avg": _mean(ratios_f),
        "p50": _percentile(ratios_f, 0.5),
        "p90": _percentile(ratios_f, 0.9),
        "daily_avg": daily_avg,
    }


def _compute_claim_audit_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_rows = len(rows)
    kept = 0
    dropped = 0
    total_claims = 0
    verdict_fail = 0
    verdict_partial = 0
    daily: Dict[str, Dict[str, int]] = {}
    for row in rows:
        kept += int(row.get("kept_claims_count") or 0)
        dropped += int(row.get("dropped_claims_count") or 0)
        total_claims += int(row.get("total_claims_count") or 0)
        verdict = str(row.get("verdict") or "").lower()
        if verdict == "fail":
            verdict_fail += 1
        elif verdict == "partial":
            verdict_partial += 1
        day = _date_key(row.get("created_at"))
        if day:
            bucket = daily.setdefault(day, {"total": 0, "fail": 0, "partial": 0})
            bucket["total"] += 1
            if verdict == "fail":
                bucket["fail"] += 1
            elif verdict == "partial":
                bucket["partial"] += 1
    kept_rate = _safe_rate(kept, total_claims)
    return {
        "rows": total_rows,
        "kept": kept,
        "dropped": dropped,
        "total_claims": total_claims,
        "kept_rate": kept_rate,
        "audit_fail_rate": _safe_rate(verdict_fail, total_rows),
        "audit_partial_rate": _safe_rate(verdict_partial, total_rows),
        "daily": daily,
    }


def _compute_llm_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [r for r in rows if str(r.get("status") or "").lower() != "stub"]
    total_calls = len(usable)
    timeouts = 0
    errors = 0
    latency_vals: List[int] = []
    total_tokens = 0
    token_rows = 0
    post_ids_with_tokens = set()
    daily: Dict[str, Dict[str, int]] = {}
    for row in usable:
        status = str(row.get("status") or "").lower()
        if status == "timeout":
            timeouts += 1
        elif status == "error":
            errors += 1
        latency = row.get("latency_ms")
        if isinstance(latency, (int, float)):
            latency_vals.append(int(latency))
        tokens = row.get("total_tokens")
        if isinstance(tokens, (int, float)):
            total_tokens += int(tokens)
            token_rows += 1
            pid = row.get("post_id")
            if pid is not None:
                post_ids_with_tokens.add(pid)
        day = _date_key(row.get("created_at"))
        if not day:
            continue
        bucket = daily.setdefault(day, {"calls": 0, "timeouts": 0})
        bucket["calls"] += 1
        if status == "timeout":
            bucket["timeouts"] += 1
    avg_latency = _mean([float(v) for v in latency_vals]) if latency_vals else None
    tokens_per_post = None
    if post_ids_with_tokens:
        tokens_per_post = total_tokens / max(len(post_ids_with_tokens), 1)
    return {
        "rows": total_calls,
        "timeout_rate": _safe_rate(timeouts, total_calls),
        "error_rate": _safe_rate(errors, total_calls),
        "avg_latency_ms": avg_latency,
        "total_tokens": total_tokens if token_rows else None,
        "tokens_per_post": tokens_per_post,
        "token_coverage_rate": _safe_rate(token_rows, total_calls),
        "daily": daily,
    }


def _compute_availability(numer: int, denom: int) -> Optional[float]:
    return _safe_rate(numer, denom)


def _build_trend(days: List[str], coverage: Dict[str, Any], jobs: Dict[str, Any], llm: Dict[str, Any]) -> List[Dict[str, Any]]:
    trend: List[Dict[str, Any]] = []
    cov_daily = coverage.get("daily_avg") or {}
    job_daily = jobs.get("daily") or {}
    llm_daily = llm.get("daily") or {}
    for day in days:
        job_bucket = job_daily.get(day) or {}
        llm_bucket = llm_daily.get(day) or {}
        success = job_bucket.get("success", 0)
        failed = job_bucket.get("failed", 0)
        trend.append(
            {
                "date": day,
                "coverage_avg": cov_daily.get(day),
                "job_success_rate": _safe_rate(success, success + failed),
                "llm_calls": llm_bucket.get("calls", 0),
                "llm_timeout_rate": _safe_rate(llm_bucket.get("timeouts", 0), llm_bucket.get("calls", 0)),
            }
        )
    return trend


async def get_ops_kpi(range_days: int) -> Dict[str, Any]:
    limit = int(os.getenv("DL_OPS_MAX_ROWS", "5000"))
    since = _utcnow() - timedelta(days=range_days)
    since_iso = since.isoformat()

    # Coverage queries sometimes error when captured_at is unavailable in the REST schema.
    # Fall back to created_at to keep KPI endpoint resilient.
    try:
        coverage_rows = await _fetch_rows(
            "threads_coverage_audits",
            "post_id,coverage_ratio,captured_at,created_at",
            since_iso=since_iso,
            time_field="captured_at",
            limit=limit,
        )
    except Exception:
        coverage_rows = await _fetch_rows(
            "threads_coverage_audits",
            "post_id,coverage_ratio,created_at",
            since_iso=since_iso,
            time_field="created_at",
            limit=limit,
        )

    behavior_rows, claim_audit_rows, risk_rows, llm_rows, job_rows = await asyncio.gather(
        _fetch_rows(
            "threads_behavior_audits",
            "post_id,created_at",
            since_iso=since_iso,
            time_field="created_at",
            limit=limit,
        ),
        _fetch_rows(
            "threads_claim_audits",
            "post_id,verdict,dropped_claims_count,kept_claims_count,total_claims_count,created_at",
            since_iso=since_iso,
            time_field="created_at",
            limit=limit,
        ),
        _fetch_rows(
            "threads_risk_briefs",
            "post_id,created_at",
            since_iso=since_iso,
            time_field="created_at",
            limit=limit,
        ),
        _fetch_rows(
            "llm_call_logs",
            "post_id,run_id,mode,model_name,status,latency_ms,total_tokens,created_at",
            since_iso=since_iso,
            time_field="created_at",
            limit=limit,
        ),
        _fetch_rows(
            "job_items",
            "stage,status,updated_at,created_at",
            since_iso=since_iso,
            time_field="updated_at",
            limit=limit,
        ),
    )

    coverage_stats = _compute_coverage_stats(coverage_rows)
    job_stats = _compute_job_stats(job_rows)
    claim_stats = _compute_claim_audit_stats(claim_audit_rows)
    llm_stats = _compute_llm_stats(llm_rows)

    behavior_avail = _compute_availability(len(behavior_rows), len(coverage_rows))
    risk_avail = _compute_availability(len(risk_rows), len(behavior_rows))

    days = [(since.date() + timedelta(days=i)).isoformat() for i in range(range_days + 1)]
    trend = _build_trend(days, coverage_stats, job_stats, llm_stats)

    return {
        "range_days": range_days,
        "generated_at": _utcnow().isoformat(),
        "summary": {
            "jobs_total": job_stats.get("total"),
            "jobs_success_rate": job_stats.get("success_rate"),
            "jobs_failed": job_stats.get("failed"),
            "jobs_inflight": job_stats.get("inflight"),
            "coverage_avg": coverage_stats.get("avg"),
            "coverage_p50": coverage_stats.get("p50"),
            "coverage_p90": coverage_stats.get("p90"),
            "claims_kept_rate": claim_stats.get("kept_rate"),
            "claims_audit_fail_rate": claim_stats.get("audit_fail_rate"),
            "claims_audit_partial_rate": claim_stats.get("audit_partial_rate"),
            "behavior_availability_rate": behavior_avail,
            "risk_brief_availability_rate": risk_avail,
            "llm_timeout_rate": llm_stats.get("timeout_rate"),
            "llm_error_rate": llm_stats.get("error_rate"),
            "llm_avg_latency_ms": llm_stats.get("avg_latency_ms"),
            "llm_total_tokens": llm_stats.get("total_tokens"),
            "llm_tokens_per_post": llm_stats.get("tokens_per_post"),
            "llm_token_coverage_rate": llm_stats.get("token_coverage_rate"),
        },
        "trends": trend,
        "sources": {
            "coverage_rows": coverage_stats.get("rows"),
            "behavior_rows": len(behavior_rows),
            "claim_audit_rows": claim_stats.get("rows"),
            "risk_rows": len(risk_rows),
            "llm_rows": llm_stats.get("rows"),
            "job_rows": job_stats.get("total"),
        },
        "truncated": {
            "coverage": len(coverage_rows) >= limit,
            "behavior": len(behavior_rows) >= limit,
            "claim_audits": len(claim_audit_rows) >= limit,
            "risk": len(risk_rows) >= limit,
            "llm": len(llm_rows) >= limit,
            "jobs": len(job_rows) >= limit,
        },
    }


__all__ = ["get_ops_kpi", "_parse_range_days"]
