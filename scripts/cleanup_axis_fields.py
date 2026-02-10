"""
One-time cleanup: remove deprecated axis fields from threads_posts.analysis_json.
Usage:
  python3 scripts/cleanup_axis_fields.py
Requires SUPABASE_URL and SUPABASE_* keys in environment.
"""

import sys
from typing import Any, Dict

from dotenv import load_dotenv
from supabase import create_client

from analysis.axis_sanitize import sanitize_analysis_json


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


def _needs_sanitize(analysis_json: Any) -> bool:
    if not isinstance(analysis_json, dict):
        return False
    if "axis_signals" in analysis_json:
        return True
    meta = analysis_json.get("meta")
    if isinstance(meta, dict) and any(k in meta for k in ("axis_registry_version", "missing_axes")):
        return True
    return False


def main() -> int:
    supabase = get_supabase_client()
    if supabase is None:
        return 1

    page_size = 500
    offset = 0
    scanned = 0
    updated = 0
    while True:
        resp = supabase.table("threads_posts").select("id, analysis_json").range(offset, offset + page_size - 1).execute()
        rows = resp.data or []
        if not rows:
            break
        for row in rows:
            scanned += 1
            analysis_json = row.get("analysis_json")
            if not _needs_sanitize(analysis_json):
                continue
            sanitized = sanitize_analysis_json(analysis_json)
            if sanitized == analysis_json:
                continue
            supabase.table("threads_posts").update({"analysis_json": sanitized}).eq("id", row.get("id")).execute()
            updated += 1
        offset += page_size

    print(f"Scan complete. Rows scanned={scanned} updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
