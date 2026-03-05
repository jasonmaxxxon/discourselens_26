"""Topic API contract smoke checks.

Run:
  PYTHONPATH=. python3 scripts/verify_topic_api_contract.py

Env:
  TOPIC_API_BASE_URL (default: http://127.0.0.1:8000)
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Dict, List, Tuple

import requests


BASE_URL = (os.environ.get("TOPIC_API_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
TIMEOUT_SEC = float(os.environ.get("TOPIC_API_TIMEOUT_SEC") or "20")


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

    if out:
        return out

    latest_resp = _request("GET", "/api/debug/latest-post")
    if latest_resp.status_code != 200:
        raise AssertionError("No posts available for topic contract test")
    latest_body = _json(latest_resp)
    try:
        return [int(latest_body.get("id"))]
    except Exception as exc:
        raise AssertionError("No numeric post id available for topic contract test") from exc


def main() -> None:
    seed_post_ids = _pick_seed_post_ids()
    payload_1 = {
        "topic_name": "Contract Smoke Topic",
        "seed_query": "contract smoke topic",
        "post_ids": seed_post_ids,
        "time_range": {
            "start": "2026-02-01T00:00:00Z",
            "end": "2026-02-07T00:00:00Z",
        },
        "run_params": {
            "bucket_granularity": "day",
            "meta_cluster_algo": "kmeans",
            "meta_cluster_k": 4,
        },
        "source": "manual",
        "created_by": "verify_topic_api_contract.py",
    }

    create_1 = _request("POST", "/api/topics/run", json=payload_1)
    if create_1.status_code != 200:
        raise AssertionError(f"POST /api/topics/run unexpected status={create_1.status_code}")
    body_1 = _json(create_1)
    if body_1.get("status") == "pending" and body_1.get("reason_code") == "topic_registry_pending":
        raise AssertionError(
            "topic registry pending (likely missing topic schema). "
            "Apply supabase/migrations/20260226150000_topic_engine_phase2_sot.sql first."
        )
    if body_1.get("status") != "accepted":
        raise AssertionError(f"create-1 status mismatch: {body_1}")

    topic_id_1 = str(body_1.get("topic_id") or body_1.get("topic_run_id") or "").strip()
    topic_hash_1 = str(body_1.get("topic_run_hash") or "").strip()
    if not topic_id_1 or not topic_hash_1:
        raise AssertionError(f"create-1 missing topic id/hash: {body_1}")

    reordered = list(reversed(seed_post_ids)) + [seed_post_ids[0]]
    payload_2 = dict(payload_1)
    payload_2["post_ids"] = reordered
    create_2 = _request("POST", "/api/topics/run", json=payload_2)
    if create_2.status_code != 200:
        raise AssertionError(f"POST /api/topics/run (idempotent) status={create_2.status_code}")
    body_2 = _json(create_2)

    topic_id_2 = str(body_2.get("topic_id") or body_2.get("topic_run_id") or "").strip()
    topic_hash_2 = str(body_2.get("topic_run_hash") or "").strip()
    if topic_id_2 != topic_id_1:
        raise AssertionError(f"idempotent topic_id mismatch: {topic_id_1} vs {topic_id_2}")
    if topic_hash_2 != topic_hash_1:
        raise AssertionError(f"idempotent topic_run_hash mismatch: {topic_hash_1} vs {topic_hash_2}")

    get_resp = _request("GET", f"/api/topics/{topic_id_1}")
    if get_resp.status_code != 200:
        raise AssertionError(f"GET /api/topics/{{id}} unexpected status={get_resp.status_code}")
    get_body = _json(get_resp)
    if str((get_body.get("topic_run") or {}).get("topic_run_hash") or "") != topic_hash_1:
        raise AssertionError("GET topic hash mismatch")
    if not isinstance(get_body.get("topic_posts"), dict):
        raise AssertionError("GET topic_posts missing")

    bad_id_resp = _request("GET", "/api/topics/not-a-uuid")
    if bad_id_resp.status_code != 400:
        raise AssertionError(f"invalid topic_id should be 400, got {bad_id_resp.status_code}")

    missing_id = str(uuid.uuid4())
    missing_resp = _request("GET", f"/api/topics/{missing_id}")
    if missing_resp.status_code != 404:
        raise AssertionError(f"missing topic_id should be 404, got {missing_resp.status_code}")

    print("[OK] POST /api/topics/run create")
    print("[OK] POST /api/topics/run idempotent canonicalization")
    print("[OK] GET /api/topics/{id} contract shape")
    print("[OK] GET /api/topics/not-a-uuid -> 400")
    print("[OK] GET /api/topics/{missing} -> 404")
    print("All topic API contract checks passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
