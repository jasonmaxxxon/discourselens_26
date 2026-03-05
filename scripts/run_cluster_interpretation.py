#!/usr/bin/env python3
import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis.cluster_interpretation import run_cluster_interpretation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Cluster Interpretation Pack (Sprint 2).")
    parser.add_argument("--post-id", type=int, required=True)
    parser.add_argument("--writeback", action="store_true", help="Write stable labels to threads_comment_clusters")
    parser.add_argument("--run-tag", default=None)
    parser.add_argument("--require-run-match", action="store_true", default=True, help="Require preanalysis run_id match (default true)")
    parser.add_argument("--allow-run-mismatch", action="store_true", help="DEBUG ONLY: allow run_id mismatch")
    args = parser.parse_args()

    require_match = args.require_run_match and not args.allow_run_mismatch
    res = run_cluster_interpretation(
        post_id=args.post_id,
        writeback=args.writeback,
        run_tag=args.run_tag,
        require_run_match=require_match,
    )
    print(f"OK cluster_interpretation post_id={args.post_id} run_id={res.get('run_id')} stats={res.get('stats')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
