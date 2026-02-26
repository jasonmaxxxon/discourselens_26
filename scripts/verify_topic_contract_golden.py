"""
Golden contract checks for Topic Engine V1 hash discipline.

Run:
  PYTHONPATH=. python3 scripts/verify_topic_contract_golden.py
"""

import hashlib
import json
import sys
from typing import Any, Dict, Iterable, List


def _canonical_json(value: Dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def normalize_seed_query(raw: str) -> str:
    return " ".join((raw or "").strip().split()).lower()


def compute_topic_run_hash(*, seed_query: str, time_range_start: str, time_range_end: str, post_ids: Iterable[int]) -> str:
    payload = {
        "seed_query": normalize_seed_query(seed_query),
        "time_range_start": str(time_range_start),
        "time_range_end": str(time_range_end),
        "post_ids": sorted({int(v) for v in post_ids}),
    }
    return _sha256_hex(payload)


def compute_meta_cluster_hash(cluster_ids: Iterable[str]) -> str:
    payload = {
        "cluster_ids": sorted({str(v).strip() for v in cluster_ids if str(v).strip()}),
    }
    return _sha256_hex(payload)


def compute_lifecycle_hash(meta_cluster_hashes: Iterable[str], daily_dominance_matrix: List[Dict[str, Any]]) -> str:
    normalized_rows: List[Dict[str, Any]] = []
    for row in daily_dominance_matrix:
        normalized_rows.append(
            {
                "day_utc": str(row["day_utc"]),
                "meta_cluster_key": int(row["meta_cluster_key"]),
                "dominance_share": round(float(row["dominance_share"]), 6),
            }
        )
    normalized_rows.sort(key=lambda r: (r["day_utc"], r["meta_cluster_key"]))
    payload = {
        "meta_cluster_hashes": sorted({str(v).strip() for v in meta_cluster_hashes if str(v).strip()}),
        "daily_dominance_matrix": normalized_rows,
    }
    return _sha256_hex(payload)


FIXTURE = {
    "seed_query": "  Public  Health  subsidy   rumor  ",
    "time_range_start": "2026-02-01T00:00:00Z",
    "time_range_end": "2026-02-07T00:00:00Z",
    "post_ids": [301, 109, 202, 202],
    "meta_clusters": [
        {"meta_cluster_key": 0, "cluster_ids": ["301::c0", "109::c2", "202::c1"]},
        {"meta_cluster_key": 1, "cluster_ids": ["301::c1", "202::c0"]},
    ],
    "daily_dominance_matrix": [
        {"day_utc": "2026-02-01", "meta_cluster_key": 0, "dominance_share": 0.61},
        {"day_utc": "2026-02-01", "meta_cluster_key": 1, "dominance_share": 0.39},
        {"day_utc": "2026-02-02", "meta_cluster_key": 0, "dominance_share": 0.55},
        {"day_utc": "2026-02-02", "meta_cluster_key": 1, "dominance_share": 0.45},
        {"day_utc": "2026-02-03", "meta_cluster_key": 0, "dominance_share": 0.47},
        {"day_utc": "2026-02-03", "meta_cluster_key": 1, "dominance_share": 0.53},
    ],
}

EXPECTED = {
    "topic_run_hash": "8dda00551d23b08f1e78ed8db5a4150c7f6934e0ad1fbd2c3b766dda29538c5d",
    "meta_cluster_hashes": {
        0: "a049d0aa10e1a440a84aebf2822f9fcb87b24b56f0b21d3542fd7a631b9886d8",
        1: "674a775ca9571e5bd3a109eaf047f52b1cfa5484f9cacce60e8401bdff5638ae",
    },
    "lifecycle_hash": "9a3b158310339c0d6f4c7ec4879927cc5d8aa0bc9116c3f61397c1afc0ecc49e",
}


def assert_topic_run_hash() -> None:
    got = compute_topic_run_hash(
        seed_query=FIXTURE["seed_query"],
        time_range_start=FIXTURE["time_range_start"],
        time_range_end=FIXTURE["time_range_end"],
        post_ids=FIXTURE["post_ids"],
    )
    assert got == EXPECTED["topic_run_hash"], f"topic_run_hash mismatch got={got}"

    # Order/duplication invariance check.
    got_2 = compute_topic_run_hash(
        seed_query="public health subsidy rumor",
        time_range_start=FIXTURE["time_range_start"],
        time_range_end=FIXTURE["time_range_end"],
        post_ids=[202, 301, 109],
    )
    assert got_2 == EXPECTED["topic_run_hash"], "topic_run_hash should be invariant to id order/duplication and spacing"


def assert_meta_cluster_hashes() -> Dict[int, str]:
    got: Dict[int, str] = {}
    for row in FIXTURE["meta_clusters"]:
        key = int(row["meta_cluster_key"])
        got[key] = compute_meta_cluster_hash(row["cluster_ids"])
        assert got[key] == EXPECTED["meta_cluster_hashes"][key], f"meta_cluster_hash mismatch key={key} got={got[key]}"
    return got


def assert_lifecycle_hash(meta_cluster_hashes: Dict[int, str]) -> None:
    got = compute_lifecycle_hash(
        meta_cluster_hashes=meta_cluster_hashes.values(),
        daily_dominance_matrix=FIXTURE["daily_dominance_matrix"],
    )
    assert got == EXPECTED["lifecycle_hash"], f"lifecycle_hash mismatch got={got}"

    # Ordering invariance for lifecycle rows.
    shuffled = list(reversed(FIXTURE["daily_dominance_matrix"]))
    got_2 = compute_lifecycle_hash(
        meta_cluster_hashes=list(reversed(list(meta_cluster_hashes.values()))),
        daily_dominance_matrix=shuffled,
    )
    assert got_2 == EXPECTED["lifecycle_hash"], "lifecycle_hash should be invariant to row/hash ordering"


def main() -> None:
    try:
        assert_topic_run_hash()
        print("[OK] topic_run_hash")
        meta_hashes = assert_meta_cluster_hashes()
        print("[OK] meta_cluster_hashes")
        assert_lifecycle_hash(meta_hashes)
        print("[OK] lifecycle_hash")
    except AssertionError as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print("All topic contract golden checks passed.")


if __name__ == "__main__":
    main()
