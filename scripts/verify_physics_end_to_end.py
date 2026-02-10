"""
End-to-end verifier for physics/golden_samples.
Usage:
  python3 scripts/verify_physics_end_to_end.py "<threads_url>"
Optional:
  --api-base http://localhost:8000   # call /api/run/A to run pipeline
  --skip-fetch                       # skip run_fetcher_and_ingest
  --timeout 900                      # seconds to wait for analysis_json
Requires SUPABASE_URL and SUPABASE_* keys in environment.
"""

import argparse
import json
import subprocess
import sys
import time
from typing import Any, Dict

from dotenv import load_dotenv
from supabase import create_client


def get_supabase_client():
    import os

    load_dotenv()
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


def _run_fetch_and_ingest(url: str) -> int:
    cmd = ["python3", "scripts/run_fetcher_and_ingest.py", url]
    return subprocess.call(cmd)


def _call_api_run(api_base: str, url: str) -> Dict[str, Any]:
    import urllib.request

    payload = json.dumps({"url": url, "mode": "analyze"}).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/api/run/A",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            print(f"API run response: {body[:500]}")
            try:
                return json.loads(body)
            except Exception:
                return {}
    except Exception as exc:
        print(f"API run failed: {exc}", file=sys.stderr)
        return {}


def _poll_job(api_base: str, job_id: str, timeout: int) -> Dict[str, Any]:
    import urllib.request

    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{api_base.rstrip('/')}/api/jobs/{job_id}", timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except Exception as exc:
            print(f"Job poll failed: {exc}", file=sys.stderr)
            time.sleep(5)
            continue
        last = payload or {}
        status = (last or {}).get("status")
        if status in {"completed", "failed"}:
            return last
        time.sleep(5)
    return last


def _fetch_latest_by_url(supabase, url: str) -> Dict[str, Any]:
    resp = (
        supabase.table("threads_posts")
        .select("id, url, analysis_json, analysis_build_id, analysis_version, analysis_is_valid, updated_at")
        .eq("url", url)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else {}


def _has_physics_golden(analysis_json: Any) -> bool:
    if not isinstance(analysis_json, dict):
        return False
    return isinstance(analysis_json.get("physics"), dict) and isinstance(analysis_json.get("golden_samples"), dict)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    if not args.skip_fetch:
        code = _run_fetch_and_ingest(args.url)
        if code != 0:
            print("Fetcher/ingest failed.", file=sys.stderr)
            return code

    job_payload: Dict[str, Any] = {}
    if args.api_base:
        job_payload = _call_api_run(args.api_base, args.url)

    supabase = get_supabase_client()
    if supabase is None:
        return 1

    if args.api_base and job_payload.get("job_id"):
        job_status = _poll_job(args.api_base, job_payload["job_id"], args.timeout)
        if job_status:
            print(f"Job status: {job_status.get('status')}")
            items = job_status.get("items") or []
            if items and job_status.get("status") in {"completed", "failed"}:
                last_item = items[0]
                if last_item.get("status") == "failed":
                    print(f"Job failed: {last_item.get('error_log')}")

    deadline = time.time() + args.timeout
    last_row: Dict[str, Any] = {}
    while time.time() < deadline:
        last_row = _fetch_latest_by_url(supabase, args.url)
        analysis_json = last_row.get("analysis_json")
        if _has_physics_golden(analysis_json):
            break
        time.sleep(5)

    if not last_row:
        print("No threads_posts row found for URL.", file=sys.stderr)
        return 2

    analysis_json = last_row.get("analysis_json") or {}
    if not _has_physics_golden(analysis_json):
        print("analysis_json missing physics/golden_samples.", file=sys.stderr)
        return 3

    physics = analysis_json.get("physics") or {}
    golden = analysis_json.get("golden_samples") or {}
    cluster_keys = [k for k in golden.keys() if k != "golden_samples_meta"]
    print("OK: physics + golden_samples present")
    print(f"post_id={last_row.get('id')} build_id={last_row.get('analysis_build_id')}")
    print(f"physics_keys={list(physics.keys())}")
    print(f"golden_clusters={cluster_keys}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
