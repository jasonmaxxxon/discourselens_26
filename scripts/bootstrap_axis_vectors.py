"""
Bootstrap axis vectors (NULL -> fill) without loading full quant pipeline.
Usage:
  python3 scripts/bootstrap_axis_vectors.py
Requires SUPABASE_URL and SUPABASE_* keys in environment.
"""

import sys
from typing import List

from dotenv import load_dotenv
from supabase import create_client

from analysis.v7.behavior.axis_registry import ensure_axis_vectors
from analysis.v7.quant.utils.embedding import get_embedder


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


def main():
    supabase = get_supabase_client()
    if supabase is None:
        sys.exit(1)
    axis_ids: List[str] = ["sarcasm_v1", "aggression_v1", "rationality_v1"]
    encoder = get_embedder()
    axes = ensure_axis_vectors(supabase, axis_ids)
    filled = sum(1 for a in axes if a.get("axis_vector_384") is not None)
    print(f"Axis vectors available: {filled}/{len(axis_ids)}")


if __name__ == "__main__":
    main()
