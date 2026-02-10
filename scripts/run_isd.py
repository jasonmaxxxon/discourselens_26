#!/usr/bin/env python3
import argparse
import os

from analysis.cluster_interpretation import run_cluster_interpretation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Interpretive Stability Diagnostics (Sprint 3).")
    parser.add_argument("--post-id", type=int, required=True)
    parser.add_argument("--k", type=int, default=None, help="Number of label generations per cluster (default DL_ISD_K or 3)")
    parser.add_argument("--context-mode", default="card", help="Context mode (default: card)")
    parser.add_argument("--writeback", action="store_true", help="Write labels/summaries when verdict is stable")
    args = parser.parse_args()

    if args.k is not None:
        os.environ["DL_ISD_K"] = str(args.k)

    res = run_cluster_interpretation(
        post_id=args.post_id,
        writeback=args.writeback,
        isd_k=args.k,
        context_mode=args.context_mode,
        require_run_match=True,
    )

    results = res.get("results") or []
    for item in results:
        ck = item.get("cluster_key")
        isd = item.get("isd") or {}
        verdict = isd.get("verdict")
        stability_avg = isd.get("stability_avg")
        stability_min = isd.get("stability_min")
        drift_avg = isd.get("drift_avg")
        drift_max = isd.get("drift_max")
        action = "WRITEBACK_ALLOWED" if (args.writeback and verdict == "stable") else "WRITEBACK_BLOCKED"
        print(
            "ISD post_id={post_id} run_id={run_id} cluster_key={ck} verdict={verdict} "
            "stability_avg={stability_avg} stability_min={stability_min} drift_avg={drift_avg} drift_max={drift_max} "
            "action={action}".format(
                post_id=res.get("post_id"),
                run_id=res.get("run_id"),
                ck=ck,
                verdict=verdict,
                stability_avg=stability_avg,
                stability_min=stability_min,
                drift_avg=drift_avg,
                drift_max=drift_max,
                action=action,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
