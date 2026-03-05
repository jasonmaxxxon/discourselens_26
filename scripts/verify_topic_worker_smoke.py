"""Topic worker smoke gate (Phase 3.5).

Run:
  PYTHONPATH=. python3 scripts/verify_topic_worker_smoke.py

Env:
  TOPIC_API_BASE_URL (default: http://127.0.0.1:8000)
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Dict, List

import requests


BASE_URL = (os.environ.get("TOPIC_API_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
TIMEOUT_SEC = float(os.environ.get("TOPIC_API_TIMEOUT_SEC") or "25")


EXPECTED_STATS_KEYS = {
    "worker_version",
    "post_count",
    "first_post_time",
    "last_post_time",
    "comment_count_total",
    "engagement_sum",
}


def _assert_headers(resp: requests.Response) -> None:
    for key in ("X-Request-ID", "X-Build-SHA", "X-Env"):
        if not resp.headers.get(key):
            raise AssertionError(f"missing header: {key}")


def _request(method: str, path: str, **kwargs: Any) -> requests.Response:
    url = f"{BASE_URL}{path}"
    resp = requests.request(method, url, timeout=TIMEOUT_SEC, **kwargs)
    _assert_headers(resp)
    if resp.status_code == 500:
        raise AssertionError(f"never-500 violation at {path}")
    return resp


def _json(resp: requests.Response) -> Dict[str, Any]:
    try:
        return resp.json()
    except Exception as exc:
        raise AssertionError(f"non-json response ({resp.status_code}): {resp.text[:300]}") from exc


def _pick_seed_post_ids() -> List[int]:
    posts_resp = _request("GET", "/api/posts")
    if posts_resp.status_code != 200:
        raise AssertionError(f"GET /api/posts failed: {posts_resp.status_code}")
    rows = _json(posts_resp)
    if not isinstance(rows, list):
        raise AssertionError("GET /api/posts shape mismatch")

    out: List[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(int(row.get("id")))
        except Exception:
            continue
        if len(out) >= 3:
            break
    if not out:
        raise AssertionError("No posts available for worker smoke")
    return out


def _create_topic(post_ids: List[int]) -> Dict[str, Any]:
    payload = {
        "topic_name": "Worker Smoke Topic",
        "seed_query": f"worker smoke {uuid.uuid4().hex[:8]}",
        "post_ids": post_ids,
        "time_range": {
            "start": "2026-02-01T00:00:00Z",
            "end": "2026-02-07T00:00:00Z",
        },
        "source": "manual",
        "created_by": "verify_topic_worker_smoke.py",
    }
    resp = _request("POST", "/api/topics/run", json=payload)
    if resp.status_code != 200:
        raise AssertionError(f"POST /api/topics/run unexpected status={resp.status_code}")
    body = _json(resp)
    if body.get("status") == "pending":
        raise AssertionError(
            "topic registry pending (apply topic migrations and ensure backend points to updated schema)"
        )
    if body.get("status") != "accepted":
        raise AssertionError(f"topic create failed: {body}")
    return body


def _run_worker(topic_id: str, force: bool) -> Dict[str, Any]:
    payload = {
        "topic_id": topic_id,
        "lock_owner": "verify-topic-worker-smoke",
        "lease_seconds": 120,
        "force_recompute": force,
    }
    resp = _request("POST", "/api/topics/worker/run-once", json=payload)
    if resp.status_code != 200:
        raise AssertionError(f"POST /api/topics/worker/run-once unexpected status={resp.status_code}")
    body = _json(resp)
    if body.get("status") == "pending":
        raise AssertionError(
            "topic worker pending (apply 20260305163000_topic_worker_locking.sql and retry)"
        )
    if body.get("status") != "ready":
        raise AssertionError(f"worker run failed: {body}")
    return body


def _get_topic(topic_id: str) -> Dict[str, Any]:
    resp = _request("GET", f"/api/topics/{topic_id}")
    if resp.status_code != 200:
        raise AssertionError(f"GET /api/topics/{{id}} unexpected status={resp.status_code}")
    body = _json(resp)
    return body


def _extract_stats(topic_body: Dict[str, Any]) -> Dict[str, Any]:
    run = topic_body.get("topic_run") or {}
    if str(run.get("status") or "").lower() != "completed":
        raise AssertionError(f"topic_run.status should be completed after worker run, got={run.get('status')}")
    stats = run.get("stats_json") or {}
    if not isinstance(stats, dict):
        raise AssertionError("stats_json missing")
    missing = sorted(EXPECTED_STATS_KEYS - set(stats.keys()))
    if missing:
        raise AssertionError(f"stats_json missing keys: {missing}")
    return stats


def main() -> None:
    meta_resp = _request("GET", "/api/_meta/build")
    if meta_resp.status_code != 200:
        raise AssertionError(f"GET /api/_meta/build failed status={meta_resp.status_code}")
    meta = _json(meta_resp)
    print(f"[INFO] build={meta.get('build_sha')} env={meta.get('env')}")

    post_ids = _pick_seed_post_ids()
    topic = _create_topic(post_ids)
    topic_id = str(topic.get("topic_id") or topic.get("topic_run_id") or "").strip()
    if not topic_id:
        raise AssertionError(f"topic_id missing from create response: {topic}")

    run_1 = _run_worker(topic_id, force=False)
    body_after_1 = _get_topic(topic_id)
    stats_1 = _extract_stats(body_after_1)

    run_2 = _run_worker(topic_id, force=True)
    body_after_2 = _get_topic(topic_id)
    stats_2 = _extract_stats(body_after_2)

    if stats_1 != stats_2:
        raise AssertionError(f"worker idempotence failed stats mismatch:\nfirst={stats_1}\nsecond={stats_2}")

    print("[OK] worker run once -> ready")
    print("[OK] worker force recompute -> ready")
    print("[OK] stats_json deterministic overwrite (reentrant idempotence)")
    print("[OK] never-500 + trace/build/env headers")
    print(f"[INFO] run1_reason={run_1.get('reason_code')} run2_reason={run_2.get('reason_code')}")
    print("Topic worker smoke passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
