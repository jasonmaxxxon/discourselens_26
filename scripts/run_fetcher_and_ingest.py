#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scraper.fetcher import run_fetcher_test
from webapp.services.ingest_sql import ingest_run


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Threads post and ingest into Supabase (v15 SoT)")
    parser.add_argument("url", help="Threads post URL")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    args = parser.parse_args()

    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        _fail("SUPABASE_URL or SUPABASE_KEY is missing in env")

    t0 = time.time()
    fetch_summary = run_fetcher_test(args.url, headless=args.headless)
    t_fetch_done = time.time()
    run_dir = (fetch_summary.get("summary") or {}).get("output_dir")
    if not run_dir:
        _fail("Fetcher did not return output_dir")

    ingest_info = ingest_run(run_dir)
    t_db_done = time.time()
    try:
        manifest_path = os.path.join(run_dir, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        expected = ((manifest.get("harvest_stats") or {}).get("main") or {}).get("expected_comment_count")
        merged_total = manifest.get("merged_total") or 0
        runtime_spans = {
            "fetch_seconds": round(t_fetch_done - t0, 2),
            "parse_seconds": round(t_fetch_done - t0, 2),
            "db_write_seconds": round(t_db_done - t_fetch_done, 2),
            "total_seconds": round(t_db_done - t0, 2),
        }
        manifest["runtime_spans"] = runtime_spans
        if manifest.get("coverage"):
            manifest["coverage_estimate"] = (manifest.get("coverage") or {}).get("coverage_ratio")
        elif expected:
            manifest["coverage_estimate"] = round((merged_total or 0) / expected, 6) if expected else None
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    print(
        "OK: fetch+ingest "
        f"post_id={ingest_info.get('post_id')} run_id={ingest_info.get('run_id')} "
        f"comments={ingest_info.get('comment_count')} edges={ingest_info.get('edge_count')} "
        f"run_dir={run_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
