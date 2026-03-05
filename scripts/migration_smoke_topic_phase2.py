"""Topic Phase-2 migration smoke gate.

Run:
  PYTHONPATH=. python3 scripts/migration_smoke_topic_phase2.py

Checks:
- topic schema tables exist
- insert/read roundtrip for topic_runs + topic_posts
- topic_run_hash recompute matches persisted hash
- topic_posts unique(topic_run_id,post_id) constraint rejects duplicates
- topic_lifecycle_daily score check rejects invalid range
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from database.store import supabase
from webapp.lib.topic_hash import compute_meta_cluster_hash, compute_topic_run_hash
from webapp.services import topic_store


def _table_exists(table_name: str) -> bool:
    try:
        supabase.table(table_name).select("id").limit(1).execute()
        return True
    except Exception as exc:
        text = str(exc).lower()
        if (
            "does not exist" in text
            or "42p01" in text
            or "relation" in text
            or "pgrst205" in text
            or "could not find the table" in text
        ):
            return False
        raise


def _require_tables() -> None:
    required = ["topic_runs", "topic_posts", "topic_meta_clusters", "topic_lifecycle_daily"]
    missing = [t for t in required if not _table_exists(t)]
    if missing:
        raise RuntimeError(
            "Topic schema missing. Apply migration first: "
            "supabase/migrations/20260226150000_topic_engine_phase2_sot.sql "
            f"(missing: {', '.join(missing)})"
        )


def _pick_seed_post_ids() -> List[int]:
    resp = supabase.table("threads_posts").select("id").order("created_at", desc=True).limit(3).execute()
    ids: List[int] = []
    for row in (getattr(resp, "data", None) or []):
        if not isinstance(row, dict):
            continue
        try:
            ids.append(int(row.get("id")))
        except Exception:
            continue
    ids = sorted(set(ids))
    if not ids:
        raise RuntimeError("No rows found in threads_posts; cannot run topic migration smoke")
    return ids


def _expect_insert_fail(fn, label: str) -> None:
    try:
        fn()
    except Exception:
        return
    raise AssertionError(f"expected constraint failure: {label}")


def _canonical_iso_utc(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    _require_tables()
    post_ids = _pick_seed_post_ids()
    canonical_post_ids = topic_store.canonicalize_post_ids(post_ids, max_items=500)
    time_range_start = "2026-02-01T00:00:00Z"
    time_range_end = "2026-02-07T00:00:00Z"
    seed_query = f"migration smoke {uuid.uuid4().hex[:12]}"
    topic_run_hash = compute_topic_run_hash(
        seed_query=seed_query,
        time_range_start=time_range_start,
        time_range_end=time_range_end,
        post_ids=canonical_post_ids,
    )

    topic_run_id: str | None = None
    try:
        run_resp = (
            supabase.table("topic_runs")
            .insert(
                {
                    "topic_name": "migration-smoke",
                    "seed_query": seed_query,
                    "seed_post_ids": canonical_post_ids,
                    "time_range_start": time_range_start,
                    "time_range_end": time_range_end,
                    "run_params": {"gate": "migration_smoke_topic_phase2"},
                    "topic_run_hash": topic_run_hash,
                    "status": "pending",
                    "source": "manual",
                    "created_by": "migration_smoke_topic_phase2.py",
                    "stats_json": {},
                }
            )
            .execute()
        )
        run_row = (getattr(run_resp, "data", None) or [None])[0]
        if not isinstance(run_row, dict) or not run_row.get("id"):
            raise RuntimeError("topic_runs insert returned no id")
        topic_run_id = str(run_row.get("id"))

        post_rows = []
        for idx, pid in enumerate(canonical_post_ids):
            post_rows.append(
                {
                    "topic_run_id": topic_run_id,
                    "post_id": int(pid),
                    "ordinal": idx,
                    "inclusion_source": "seed",
                    "inclusion_reason": "migration_smoke",
                }
            )
        supabase.table("topic_posts").insert(post_rows).execute()

        _expect_insert_fail(
            lambda: supabase.table("topic_posts").insert(post_rows[:1]).execute(),
            "topic_posts unique(topic_run_id,post_id)",
        )

        meta_cluster_hash = compute_meta_cluster_hash([f"{canonical_post_ids[0]}::c0"])
        supabase.table("topic_meta_clusters").insert(
            {
                "topic_run_id": topic_run_id,
                "meta_cluster_key": 0,
                "meta_cluster_hash": meta_cluster_hash,
                "member_clusters": [f"{canonical_post_ids[0]}::c0"],
                "member_posts": canonical_post_ids,
                "dominance_share": 0.5,
            }
        ).execute()

        _expect_insert_fail(
            lambda: supabase.table("topic_lifecycle_daily")
            .insert(
                {
                    "topic_run_id": topic_run_id,
                    "meta_cluster_key": 0,
                    "day_utc": "2026-02-02",
                    "dominance_share": 0.4,
                    "managed_score": 1.5,
                    "organic_score": 0.2,
                    "drift_score": 0.1,
                    "lifecycle_stage": "growth",
                }
            )
            .execute(),
            "topic_lifecycle_daily managed_score range check",
        )

        read_run_resp = supabase.table("topic_runs").select(
            "id,seed_query,time_range_start,time_range_end,topic_run_hash"
        ).eq("id", topic_run_id).limit(1).execute()
        read_run = (getattr(read_run_resp, "data", None) or [None])[0]
        if not isinstance(read_run, dict):
            raise RuntimeError("failed to read back topic_run")

        read_posts_resp = (
            supabase.table("topic_posts")
            .select("post_id")
            .eq("topic_run_id", topic_run_id)
            .order("post_id")
            .execute()
        )
        read_post_ids = [int(r["post_id"]) for r in (getattr(read_posts_resp, "data", None) or []) if isinstance(r, dict)]
        recomputed = compute_topic_run_hash(
            seed_query=str(read_run.get("seed_query") or ""),
            time_range_start=_canonical_iso_utc(read_run.get("time_range_start")),
            time_range_end=_canonical_iso_utc(read_run.get("time_range_end")),
            post_ids=read_post_ids,
        )
        if recomputed != str(read_run.get("topic_run_hash") or ""):
            raise AssertionError(f"hash mismatch: recomputed={recomputed} stored={read_run.get('topic_run_hash')}")

        print("[OK] schema tables exist")
        print("[OK] topic_runs + topic_posts CRUD")
        print("[OK] topic_run_hash roundtrip")
        print("[OK] unique/check constraints enforce")
        print("Topic phase-2 migration smoke passed.")
    finally:
        if topic_run_id:
            try:
                supabase.table("topic_runs").delete().eq("id", topic_run_id).execute()
            except Exception as exc:
                print(f"[WARN] cleanup failed topic_run_id={topic_run_id} err={exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
