#!/usr/bin/env python3
import argparse

from analysis.preanalysis_runner import run_preanalysis


def main() -> int:
    parser = argparse.ArgumentParser(description="Run preanalysis (deterministic, no LLM).")
    parser.add_argument("--post-id", type=int, required=True, help="threads_posts id")
    parser.add_argument("--prefer-sot", action="store_true", help="Prefer SoT threads_comments")
    parser.add_argument("--persist-assignments", action="store_true", help="Write cluster_key to threads_comments")
    args = parser.parse_args()

    payload = run_preanalysis(
        post_id=args.post_id,
        prefer_sot=args.prefer_sot,
        persist_assignments=args.persist_assignments,
    )
    print(f"OK preanalysis post_id={args.post_id} version={payload.get('version')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
