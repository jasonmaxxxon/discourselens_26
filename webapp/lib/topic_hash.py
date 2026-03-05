"""Deterministic Topic Contract V1 hash helpers."""

from __future__ import annotations

import hashlib
import json
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
