import os
import sys
import json
import hashlib
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, date
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_removed_root = False
if ROOT_DIR in sys.path:
    sys.path.remove(ROOT_DIR)
    _removed_root = True
try:
    from supabase import create_client, Client
finally:
    if _removed_root:
        sys.path.insert(0, ROOT_DIR)
from dotenv import load_dotenv
from scraper.image_pipeline import process_images_for_post
import requests
from analysis.build_analysis_json import build_and_validate_analysis_json, validate_analysis_json, safe_dump
from database.integrity import AssignmentIntegrityError, guard_semantic_write
from analysis.v7.utils.text_preprocess import preprocess_for_embedding

# Safety net: load .env on import so SUPABASE_* exist even if uvicorn misses it.
load_dotenv()

logger_env = logging.getLogger("dl.env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_KEY")
)

if not SUPABASE_URL or not SUPABASE_URL.startswith("https://"):
    raise RuntimeError(
        f"CRITICAL: SUPABASE_URL missing/invalid: {SUPABASE_URL!r}. "
        "Check .env and runtime env loading."
    )
if not SUPABASE_KEY:
    raise RuntimeError("CRITICAL: SUPABASE_KEY missing. Check .env and runtime env loading.")

logger_env.info("[ENV] SUPABASE_URL loaded (prefix): %s...", SUPABASE_URL[:24])

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logger = logging.getLogger("dl")
mode = "SERVICE_ROLE" if (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")) else "ANON"
if mode == "SERVICE_ROLE":
    logger.info("[DB] Mode: SERVICE_ROLE")
else:
    logger.warning("[DB] Mode: ANON (WARNING: backend running restricted)")

def _env_flag(name: str) -> bool:
    val = os.environ.get(name)
    if val is None:
        return False
    return str(val).lower() in {"1", "true", "yes", "on"}


def _cluster_id(post_id: int | str, cluster_key: int | str) -> str:
    return f"{post_id}::c{cluster_key}"


def _persist_assignment_history(
    post_id: int,
    cluster_run_id: str,
    assignments: List[Dict[str, Any]],
    bundle_id: Optional[str] = None,
    cluster_fingerprints: Optional[Dict[int, str]] = None,
) -> None:
    rows = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for a in assignments:
        key_int = int(a.get("cluster_key", -1))
        fingerprint = None
        if isinstance(cluster_fingerprints, dict):
            fingerprint = cluster_fingerprints.get(key_int)
        rows.append(
            {
                "cluster_run_id": cluster_run_id,
                "post_id": post_id,
                "comment_id": str(a.get("comment_id")),
                "cluster_key": key_int,
                "cluster_id": a.get("cluster_id"),
                "bundle_id": bundle_id,
                "cluster_fingerprint": fingerprint,
                "created_at": now_iso,
            }
        )
    if not rows:
        return
    try:
        supabase.table("threads_comment_cluster_assignments").upsert(rows, on_conflict="cluster_run_id,comment_id").execute()
    except Exception as e:
        logger.warning("[Clusters] assignment history write failed post=%s run=%s err=%s", post_id, cluster_run_id, e)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _deep_merge_keep(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_keep(merged.get(k, {}), v)
        else:
            merged[k] = v
    return merged


def _merge_narrative(existing: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(existing, dict):
        existing = {}
    if not isinstance(patch, dict):
        patch = {}
    merged = dict(existing)
    narrative_keys = {
        "narrative_stack",
        "segments",
        "battlefield_map",
        "strategic_verdict",
        "structural_insight",
        "summary",
        "battlefield",
        "axis_alignment",
        "full_report",
        "emotional_pulse",
        "phenomenon",
        "danger",
    }
    for k, v in patch.items():
        if k in narrative_keys:
            merged[k] = v
    meta_patch = (patch.get("meta") or {}) if isinstance(patch.get("meta"), dict) else {}
    if meta_patch:
        meta = dict(merged.get("meta") or {})
        for key in ("stage", "narrative_status", "llm_model", "llm_latency_ms", "llm_error"):
            if key in meta_patch:
                meta[key] = meta_patch[key]
        merged["meta"] = meta
    return merged


def save_analysis_json(
    post_id: str | int,
    analysis_build_id: str,
    json_obj: Dict[str, Any],
    *,
    mode: str,
    analysis_version: str = "v6.1",
    analysis_is_valid: Optional[bool] = None,
    analysis_invalid_reason: Optional[str] = None,
    analysis_missing_keys: Optional[List[str]] = None,
) -> None:
    if not post_id:
        raise ValueError("post_id is required")
    if mode not in {"skeleton", "merge_narrative", "mark_timeout"}:
        raise ValueError(f"Unsupported save_analysis_json mode: {mode}")

    if mode == "merge_narrative":
        resp = supabase.table("threads_posts").select("analysis_json").eq("id", post_id).limit(1).execute()
        existing = (resp.data or [None])[0] or {}
        existing_json = existing.get("analysis_json") if isinstance(existing, dict) else {}
        merged = _merge_narrative(existing_json or {}, json_obj or {})
        payload = {"analysis_json": _json_safe(merged)}
    elif mode == "mark_timeout":
        resp = supabase.table("threads_posts").select("analysis_json").eq("id", post_id).limit(1).execute()
        existing = (resp.data or [None])[0] or {}
        existing_json = existing.get("analysis_json") if isinstance(existing, dict) else {}
        merged = _deep_merge_keep(existing_json or {}, json_obj or {})
        payload = {"analysis_json": _json_safe(merged)}
    else:
        payload = {"analysis_json": _json_safe(json_obj or {})}

    payload.update(
        {
            "analysis_version": analysis_version,
            "analysis_build_id": analysis_build_id,
        }
    )
    if analysis_is_valid is not None:
        payload["analysis_is_valid"] = analysis_is_valid
    if analysis_invalid_reason is not None:
        payload["analysis_invalid_reason"] = analysis_invalid_reason
    if analysis_missing_keys is not None:
        payload["analysis_missing_keys"] = analysis_missing_keys

    supabase.table("threads_posts").update(payload).eq("id", post_id).execute()


def _normalize_text(val: str) -> str:
    return " ".join((val or "").split()).strip()


def _text_norm(val: str) -> str:
    return preprocess_for_embedding(_normalize_text(val))


def _parse_taken_at(val: Any) -> Optional[str]:
    """
    Best-effort normalize timestamptz to ISO string; returns None on failure.
    """
    if val is None:
        return None
    import datetime

    try:
        if isinstance(val, (int, float)) or (isinstance(val, str) and val.strip().isdigit()):
            return datetime.datetime.fromtimestamp(float(val), datetime.timezone.utc).isoformat()
        if isinstance(val, str):
            try:
                return datetime.datetime.fromisoformat(val.replace("Z", "+00:00")).isoformat()
            except Exception:
                pass
        if isinstance(val, datetime.datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=datetime.timezone.utc)
            return val.isoformat()
    except Exception:
        return None
    return None


def save_analysis_result(post_id: int | str, analysis_payload: dict) -> None:
    invalid_reason: Optional[str] = None
    missing_keys: Optional[list] = None
    validated_payload: Optional[dict] = None
    is_valid = False

    try:
        validated_model = build_and_validate_analysis_json(analysis_payload)
        validated_payload = safe_dump(validated_model)
        is_valid, invalid_reason, missing_keys = validate_analysis_json(validated_model)
    except Exception as exc:
        invalid_reason = f"{type(exc).__name__}: {exc}"
        if hasattr(exc, "errors"):
            try:
                missing_keys = [
                    ".".join(str(part) for part in err.get("loc", []))
                    for err in (exc.errors() or [])
                    if err.get("type") in {"missing", "value_error.missing"}
                ]
            except Exception:
                missing_keys = None

    if is_valid and validated_payload is not None:
        payload = {
            "analysis_json": validated_payload,
            "analysis_is_valid": True,
            "analysis_invalid_reason": None,
            "analysis_missing_keys": None,
        }
    else:
        payload = {
            "analysis_json": analysis_payload,
            "analysis_is_valid": False,
            "analysis_invalid_reason": invalid_reason or "validation_failed",
            "analysis_missing_keys": missing_keys or None,
        }

    supabase.table("threads_posts").update(_json_safe(payload)).eq("id", post_id).execute()


def _legacy_comment_id(post_id: str, comment: Dict[str, Any]) -> str:
    """
    Deterministic fallback when native id is missing.
    """
    author = str(comment.get("author_handle") or comment.get("user") or comment.get("author") or "")
    text = _normalize_text(str(comment.get("text") or ""))
    raw = f"{post_id}:{author}:{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_comments_raw(raw_comments: Any) -> List[Dict[str, Any]]:
    if raw_comments is None:
        return []
    if isinstance(raw_comments, str):
        try:
            parsed = json.loads(raw_comments)
            return _normalize_comments_raw(parsed)
        except Exception:
            return []
    if isinstance(raw_comments, dict):
        for key in ("items", "data", "comments"):
            val = raw_comments.get(key)
            if isinstance(val, list):
                return _normalize_comments_raw(val)
        return []
    if isinstance(raw_comments, list):
        return [c for c in raw_comments if isinstance(c, dict)]
    return []

def _fetch_existing_ids_by_source(post_id: str | int, source_ids: List[str]) -> Dict[str, str]:
    """
    Return mapping source_comment_id -> existing id for a post.
    """
    if not source_ids:
        return {}
    existing: Dict[str, str] = {}
    unique_sources = list({s for s in source_ids if s})
    for chunk in _chunked(unique_sources, 200):
        try:
            resp = supabase.table("threads_comments").select("id, source_comment_id").eq("post_id", post_id).in_("source_comment_id", chunk).execute()
            data = getattr(resp, "data", None) or []
            for row in data:
                src = row.get("source_comment_id")
                cid = row.get("id")
                if src and cid:
                    existing[str(src)] = str(cid)
        except Exception as e:
            logger.warning(f"[CommentsSoT] fetch existing ids by source failed for post {post_id}: {e}")
    return existing


def _map_comments_to_rows(comments: List[Dict[str, Any]], post_id: str | int, now_iso: str, existing_by_source: Dict[str, str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        source_comment_id = c.get("source_comment_id") or c.get("comment_id")
        parent_source_comment_id = c.get("parent_source_comment_id")
        reply_to_author = c.get("reply_to_author")
        text_fragments = c.get("text_fragments") if isinstance(c.get("text_fragments"), list) else None
        text_val = c.get("text") or ""
        if (not str(text_val).strip()) and text_fragments:
            try:
                text_val = "".join(
                    [
                        frag.get("text", "") if isinstance(frag, dict) else str(frag or "")
                        for frag in text_fragments
                    ]
                ).strip()
            except Exception:
                text_val = str(text_val or "").strip()
        text_val_clean = str(text_val or "").strip()
        normalized_text = _normalize_text(text_val_clean)
        author_handle = c.get("author_handle") or c.get("user") or c.get("author") or ""
        # Hybrid identity: primary key stays legacy hash, but reuse existing id when source matches.
        legacy_id = _legacy_comment_id(str(post_id), {"author_handle": author_handle, "text": normalized_text})
        if source_comment_id and source_comment_id in existing_by_source:
            db_comment_id = existing_by_source[source_comment_id]
        else:
            db_comment_id = legacy_id
        c["source_comment_id"] = source_comment_id  # propagate for downstream
        c["id"] = db_comment_id  # keep hash id stable for quant/cluster references
        try:
            like_count = int(c.get("like_count") or c.get("likes") or 0)
        except Exception:
            like_count = 0
        try:
            reply_count = int(c.get("reply_count") or c.get("replies") or 0)
        except Exception:
            reply_count = 0
        taken_at = _parse_taken_at(c.get("taken_at") or c.get("created_at") or c.get("timestamp"))
        ui_created_at_est = c.get("ui_created_at_est") or c.get("approx_created_at_utc")
        time_precision = str(c.get("time_precision") or "").lower()
        if c.get("is_estimated") is None:
            is_estimated = bool(ui_created_at_est) and time_precision not in {"exact", "observed", "native"}
        else:
            is_estimated = bool(c.get("is_estimated"))
        root_source_comment_id = c.get("root_source_comment_id")
        if not root_source_comment_id:
            if parent_source_comment_id:
                root_source_comment_id = None
            else:
                root_source_comment_id = source_comment_id
        raw_json = _json_safe(c.get("raw_json") or c)
        rows.append(
            {
                "id": str(db_comment_id),
                "post_id": int(post_id),
                "text": text_val_clean,
                "text_fragments": text_fragments,
                "author_handle": author_handle,
                "author_id": c.get("author_id"),
                "source_comment_id": source_comment_id,
                "parent_source_comment_id": parent_source_comment_id,
                "root_source_comment_id": root_source_comment_id,
                "reply_to_author": reply_to_author,
                "parent_comment_id": c.get("parent_comment_id"),
                "like_count": like_count,
                "reply_count": reply_count,
                "taken_at": taken_at,
                "created_at": c.get("created_at") or c.get("timestamp"),
                "ui_created_at_est": ui_created_at_est,
                "is_estimated": is_estimated,
                "captured_at": now_iso,
                "raw_json": raw_json,
                "depth": c.get("depth"),
                "path": c.get("path"),
                "updated_at": now_iso,
            }
        )
    return rows


def _chunked(iterable: List[Dict[str, Any]], size: int = 200):
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def _repair_comment_tree(post_id: str | int) -> Dict[str, Any]:
    """
    Fill root_source_comment_id / depth / path using best-effort parent resolution.
    """
    try:
        resp = (
            supabase.table("threads_comments")
            .select(
                "id, source_comment_id, parent_source_comment_id, reply_to_author, author_handle, "
                "root_source_comment_id, depth, path, taken_at, created_at"
            )
            .eq("post_id", post_id)
            .order("inserted_at", desc=False)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
    except Exception as e:
        logger.warning(f"[CommentsSoT] repair fetch failed post={post_id}: {e}")
        return {"ok": False, "error": str(e), "updated": 0, "count": 0}

    if not rows:
        return {"ok": True, "updated": 0, "count": 0}

    parsed_rows = []
    original_by_id = {}
    for idx, row in enumerate(rows):
        taken_at = _parse_taken_at(row.get("taken_at") or row.get("created_at"))
        taken_at_dt = None
        if taken_at:
            try:
                taken_at_dt = datetime.fromisoformat(str(taken_at).replace("Z", "+00:00"))
            except Exception:
                taken_at_dt = None
        parsed = {
            "idx": idx,
            "id": str(row.get("id")),
            "source_comment_id": row.get("source_comment_id"),
            "parent_source_comment_id": row.get("parent_source_comment_id"),
            "reply_to_author": row.get("reply_to_author"),
            "author_handle": row.get("author_handle"),
            "root_source_comment_id": row.get("root_source_comment_id"),
            "depth": row.get("depth"),
            "path": row.get("path"),
            "taken_at": taken_at,
            "taken_at_dt": taken_at_dt,
        }
        parsed_rows.append(parsed)
        original_by_id[parsed["id"]] = parsed.copy()

    def _sort_key(item):
        if item["taken_at_dt"]:
            return (0, item["taken_at_dt"])
        return (1, item["idx"])

    ordered = sorted(parsed_rows, key=_sort_key)
    computed_by_id: Dict[str, Dict[str, Any]] = {}
    computed_by_source: Dict[str, Dict[str, Any]] = {}
    latest_by_author: Dict[str, Dict[str, Any]] = {}

    for _ in range(3):  # few passes to stabilize
        progressed = False
        for c in ordered:
            cid = c["id"]
            base_state = computed_by_id.get(cid) or {
                "id": cid,
                "source_comment_id": c.get("source_comment_id"),
                "root_source_comment_id": c.get("root_source_comment_id"),
                "depth": c.get("depth"),
                "path": c.get("path"),
            }
            parent_info = None
            resolved_parent_source_id = None
            if c.get("parent_source_comment_id") and c["parent_source_comment_id"] in computed_by_source:
                parent_info = computed_by_source[c["parent_source_comment_id"]]
                resolved_parent_source_id = c["parent_source_comment_id"]
            elif not c.get("parent_source_comment_id") and c.get("reply_to_author"):
                candidate = latest_by_author.get(c["reply_to_author"])
                if candidate and candidate.get("source_comment_id"):
                    parent_info = candidate
                    resolved_parent_source_id = candidate.get("source_comment_id")

            root = base_state.get("root_source_comment_id")
            depth = base_state.get("depth")
            path = base_state.get("path")

            if parent_info:
                parent_depth = parent_info.get("depth")
                depth = (parent_depth if parent_depth is not None else 0) + 1
                root = parent_info.get("root_source_comment_id") or parent_info.get("source_comment_id") or root
                parent_path = parent_info.get("path")
                segment = c.get("source_comment_id") or c.get("id")
                if parent_path and segment:
                    path = f"{parent_path}/{segment}"
            if root is None and c.get("source_comment_id"):
                root = c.get("source_comment_id")
            if depth is None:
                depth = 0 if not parent_info else depth
            if path is None and root:
                segment = c.get("source_comment_id") or c.get("id")
                if segment:
                    path = f"{root}/{segment}" if segment != root else root

            new_state = {
                "id": cid,
                "source_comment_id": c.get("source_comment_id"),
                "root_source_comment_id": root,
                "depth": depth,
                "path": path,
                "resolved_parent_source_comment_id": resolved_parent_source_id,
            }

            prev = computed_by_id.get(cid)
            if new_state != prev:
                progressed = True
            computed_by_id[cid] = new_state
            if new_state.get("source_comment_id"):
                computed_by_source[new_state["source_comment_id"]] = new_state
            if c.get("author_handle"):
                latest_by_author[c["author_handle"]] = new_state

        if not progressed:
            break

    updates = []
    for cid, state in computed_by_id.items():
        original = original_by_id.get(cid, {})
        payload: Dict[str, Any] = {}
        if state.get("root_source_comment_id") and state.get("root_source_comment_id") != original.get("root_source_comment_id"):
            payload["root_source_comment_id"] = state.get("root_source_comment_id")
        if state.get("depth") is not None and state.get("depth") != original.get("depth"):
            payload["depth"] = state.get("depth")
        if state.get("path") is not None and state.get("path") != original.get("path"):
            payload["path"] = state.get("path")
        if state.get("resolved_parent_source_comment_id") and not original.get("parent_source_comment_id"):
            payload["parent_source_comment_id"] = state.get("resolved_parent_source_comment_id")
        if payload:
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            updates.append((cid, payload))

    updated = 0
    for cid, payload in updates:
        try:
            supabase.table("threads_comments").update(payload).eq("post_id", post_id).eq("id", cid).execute()
            updated += 1
        except Exception as e:
            logger.warning(f"[CommentsSoT] repair update failed id={cid} post={post_id}: {e}")

    return {"ok": True, "updated": updated, "count": len(rows)}


def sync_comments_to_table(post_id: str | int, raw_comments: Any) -> Dict[str, Any]:
    comments = _normalize_comments_raw(raw_comments)
    now_iso = datetime.now(timezone.utc).isoformat()
    source_ids = [c.get("source_comment_id") or c.get("comment_id") for c in comments if isinstance(c, dict)]
    existing_by_source = _fetch_existing_ids_by_source(post_id, [s for s in source_ids if s])
    rows = _map_comments_to_rows(comments, post_id, now_iso, existing_by_source)
    if not rows:
        return {"ok": True, "count": 0}
    total = 0
    try:
        for chunk in _chunked(rows, 200):
            supabase.table("threads_comments").upsert(chunk).execute()
            total += len(chunk)
        repair = _repair_comment_tree(post_id)
        logger.info(f"✅ [CommentsSoT] upserted {total} comments for post {post_id}; repair_updated={repair.get('updated')}")
        return {"ok": True, "count": total, "repair": repair}
    except Exception as e:
        logger.warning(f"⚠️ [CommentsSoT] upsert failed for post {post_id}: {e}")
        return {"ok": False, "count": total, "error": str(e)}


def _bundle_hash(post_id: str | int, bundle_version: str, ordering_rule: str, comment_ids: List[str]) -> str:
    payload = f"{post_id}|{bundle_version}|{ordering_rule}|{','.join(comment_ids)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ordering_key_hash(ordering_keys: List[str]) -> str:
    payload = "|".join(ordering_keys)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_post_source_id(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    if "/post/" in url:
        tail = url.split("/post/")[-1]
        return tail.split("?")[0].strip() or None
    return None


def _sort_key_for_bundle(comment: Dict[str, Any]) -> tuple:
    taken_at = comment.get("taken_at")
    ui_est = comment.get("ui_created_at_est")
    created_at = comment.get("_created_at")
    captured_at = comment.get("_captured_at")
    ts = taken_at or ui_est or created_at or captured_at or ""
    return (str(ts), str(comment.get("comment_id") or comment.get("id") or ""))


def _calc_tree_quality(comments: List[Dict[str, Any]], post_id: str | int | None = None) -> Dict[str, Any]:
    if not comments:
        return {"partial_tree": True, "edge_coverage": 0.0, "missing_ts_pct": 1.0, "dedupe_count": 0}
    total = len(comments)
    post_id_str = str(post_id) if post_id is not None else None
    with_ts = sum(
        1
        for c in comments
        if c.get("taken_at") or c.get("ui_created_at_est") or c.get("_created_at") or c.get("_captured_at")
    )
    missing_ts_pct = round(1 - (with_ts / total), 6) if total else 1.0
    source_ids = {
        str(c.get("comment_id") or c.get("id") or "")
        for c in comments
        if c.get("comment_id") or c.get("id")
    }
    parent_refs = [
        c.get("parent_source_comment_id")
        for c in comments
        if c.get("parent_source_comment_id") and str(c.get("parent_source_comment_id")) != post_id_str
    ]
    parent_total = len(parent_refs)
    parent_resolved = sum(1 for pid in parent_refs if pid in source_ids)
    edge_coverage = round(parent_resolved / parent_total, 6) if parent_total else 1.0
    # Treat missing depth as non-fatal when internal edges are present.
    partial_tree = edge_coverage < 0.9
    return {
        "partial_tree": partial_tree,
        "edge_coverage": edge_coverage,
        "missing_ts_pct": missing_ts_pct,
    }


def get_canonical_comment_bundle(post_id: str | int, prefer_sot: bool = True, min_sot_comments: int = 1) -> Dict[str, Any]:
    """
    CanonicalCommentBundleV1: single entry for analysis input.
    Falls back to threads_posts.raw_comments when SoT is unavailable or empty.
    """
    bundle_version = "CCBv1"
    ordering_rule = "taken_at asc, ui_created_at_est asc, created_at asc, comment_id asc (fallback: captured_at)"
    source = "sot"
    comments_rows: List[Dict[str, Any]] = []
    edges_rows: List[Dict[str, Any]] = []
    post_root_id = None
    if prefer_sot:
        try:
            post_resp = (
                supabase.table("threads_posts")
                .select("url")
                .eq("id", post_id)
                .limit(1)
                .execute()
            )
            post_row = (getattr(post_resp, "data", None) or [None])[0] or {}
            post_root_id = _extract_post_source_id(post_row.get("url"))
        except Exception as e:
            logger.warning("[CCB] post url fetch failed post=%s: %s", post_id, e)
        try:
            resp = (
                supabase.table("threads_comments")
                .select(
                    "id,post_id,text,author_handle,like_count,reply_count,created_at,taken_at,"
                    "ui_created_at_est,is_estimated,source_comment_id,parent_source_comment_id,parent_comment_id,root_source_comment_id,"
                    "depth,path,captured_at,source_locator,repost_count_ui,share_count_ui,metrics_confidence,raw_json"
                )
                .eq("post_id", post_id)
                .execute()
            )
            comments_rows = getattr(resp, "data", None) or []
        except Exception as e:
            logger.warning("[CCB] SoT fetch failed post=%s: %s (retrying minimal select)", post_id, e)
            try:
                resp = (
                    supabase.table("threads_comments")
                    .select(
                        "id,post_id,text,author_handle,like_count,reply_count,created_at,taken_at,"
                        "source_comment_id,parent_source_comment_id,parent_comment_id,root_source_comment_id,depth,path,"
                        "captured_at,source_locator,repost_count_ui,share_count_ui,metrics_confidence,raw_json"
                    )
                    .eq("post_id", post_id)
                    .execute()
                )
                comments_rows = getattr(resp, "data", None) or []
            except Exception as e2:
                logger.warning("[CCB] SoT minimal fetch failed post=%s: %s", post_id, e2)
                comments_rows = []
        try:
            edge_resp = (
                supabase.table("threads_comment_edges")
                .select("child_comment_id,child_source_comment_id,parent_comment_id,parent_source_comment_id")
                .eq("post_id", post_id)
                .execute()
            )
            edges_rows = getattr(edge_resp, "data", None) or []
        except Exception as e:
            logger.warning("[CCB] edges fetch failed post=%s: %s", post_id, e)
            edges_rows = []
    if not comments_rows or len(comments_rows) < min_sot_comments:
        source = "raw_fallback"
        try:
            resp = supabase.table("threads_posts").select("raw_comments").eq("id", post_id).limit(1).execute()
            raw = (getattr(resp, "data", None) or [{}])[0].get("raw_comments")
            comments_rows = _normalize_comments_raw(raw)
        except Exception as e:
            logger.warning("[CCB] raw fallback failed post=%s: %s", post_id, e)
            comments_rows = []

    canonical: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    dedupe_count = 0
    edge_parent_by_child: Dict[str, str] = {}
    for edge in edges_rows:
        if not isinstance(edge, dict):
            continue
        child_id = edge.get("child_comment_id")
        parent_id = edge.get("parent_comment_id")
        if child_id and parent_id:
            edge_parent_by_child[str(child_id)] = str(parent_id)
    fallback_source_count = 0
    fallback_parent_count = 0
    post_id_str = str(post_root_id or post_id)
    for row in comments_rows:
        if not isinstance(row, dict):
            continue
        comment_id = row.get("id") or row.get("comment_id") or row.get("source_comment_id")
        if not comment_id:
            comment_id = _legacy_comment_id(str(post_id), row)
        comment_id = str(comment_id)
        if comment_id in seen_ids:
            dedupe_count += 1
            continue
        seen_ids.add(comment_id)
        text_raw = str(row.get("text") or row.get("text_raw") or "")
        source_comment_id = row.get("source_comment_id")
        parent_internal_id = row.get("parent_comment_id")
        if not parent_internal_id:
            parent_internal_id = edge_parent_by_child.get(str(comment_id))
            if parent_internal_id:
                fallback_parent_count += 1
        if parent_internal_id and str(parent_internal_id) == post_id_str:
            parent_internal_id = None
        raw_json = row.get("raw_json") if isinstance(row.get("raw_json"), dict) else {}
        metrics_present = raw_json.get("metrics_present") if isinstance(raw_json, dict) else {}
        metrics_hidden_low_value = raw_json.get("metrics_hidden_low_value") if isinstance(raw_json, dict) else {}
        canonical.append(
            {
                "comment_id": comment_id,
                "id": comment_id,
                "source_comment_id": source_comment_id,
                "graph_node_id": str(comment_id),
                "author_handle": row.get("author_handle") or row.get("user") or row.get("author") or "",
                "user": row.get("author_handle") or row.get("user") or row.get("author") or "",
                "text_raw": text_raw,
                "text": text_raw,
                "text_norm": _text_norm(text_raw),
                "like_count": int(row.get("like_count") or row.get("likes") or 0),
                "reply_count": int(row.get("reply_count") or row.get("replies") or 0),
                "repost_count_ui": row.get("repost_count_ui"),
                "share_count_ui": row.get("share_count_ui"),
                "metrics_present": metrics_present if isinstance(metrics_present, dict) else {},
                "metrics_hidden_low_value": metrics_hidden_low_value if isinstance(metrics_hidden_low_value, dict) else {},
                "taken_at": row.get("taken_at"),
                "ui_created_at_est": row.get("ui_created_at_est"),
                "is_estimated": bool(row.get("is_estimated")) if row.get("is_estimated") is not None else None,
                "parent_source_comment_id": parent_internal_id,
                "graph_parent_id": str(parent_internal_id) if parent_internal_id else None,
                "root_source_comment_id": row.get("root_source_comment_id"),
                "depth": row.get("depth"),
                "path": row.get("path"),
                "_created_at": row.get("created_at"),
                "_captured_at": row.get("captured_at"),
                "source_locator": row.get("source_locator"),
            }
        )

    canonical_sorted = sorted(canonical, key=_sort_key_for_bundle)
    ordering_keys = [f"{_sort_key_for_bundle(c)[0]}|{_sort_key_for_bundle(c)[1]}" for c in canonical_sorted]
    quality_flags = _calc_tree_quality(canonical_sorted, post_id=post_id_str)
    quality_flags["dedupe_count"] = dedupe_count
    source_coverage = sum(1 for c in canonical_sorted if c.get("comment_id"))
    parent_coverage = sum(1 for c in canonical_sorted if c.get("parent_source_comment_id"))
    total_comments = len(canonical_sorted)
    source_ratio = round(source_coverage / total_comments, 6) if total_comments else 0.0
    parent_ratio = round(parent_coverage / total_comments, 6) if total_comments else 0.0
    quality_flags["source_comment_id_coverage"] = source_ratio
    quality_flags["parent_source_comment_id_coverage"] = parent_ratio
    quality_flags["source_comment_id_total"] = total_comments
    quality_flags["parent_source_comment_id_total"] = total_comments
    edge_cov = quality_flags.get("edge_coverage") or 0.0
    quality_flags["reply_graph_available"] = bool(total_comments and parent_coverage > 0)
    quality_flags["reply_graph_id_space"] = "internal"
    quality_flags["source_comment_id_fallbacks"] = fallback_source_count
    quality_flags["parent_source_comment_id_fallbacks"] = fallback_parent_count
    def _metric_present(comment: Dict[str, Any], key: str) -> bool:
        mp = comment.get("metrics_present") if isinstance(comment.get("metrics_present"), dict) else {}
        flag = mp.get(key)
        return bool(flag) if isinstance(flag, bool) else False

    def _metric_value_present(comment: Dict[str, Any], key: str) -> bool:
        hidden = comment.get("metrics_hidden_low_value") if isinstance(comment.get("metrics_hidden_low_value"), dict) else {}
        if hidden.get(key):
            return False
        if key == "reposts":
            return comment.get("repost_count_ui") is not None
        if key == "shares":
            return comment.get("share_count_ui") is not None
        return False

    if total_comments:
        rep_presence = sum(1 for c in canonical_sorted if _metric_present(c, "reposts"))
        share_presence = sum(1 for c in canonical_sorted if _metric_present(c, "shares"))
        rep_value = sum(1 for c in canonical_sorted if _metric_value_present(c, "reposts"))
        share_value = sum(1 for c in canonical_sorted if _metric_value_present(c, "shares"))
        quality_flags["metrics_presence_pct"] = {
            "reposts": round(rep_presence / total_comments, 6),
            "shares": round(share_presence / total_comments, 6),
        }
        quality_flags["metrics_value_coverage_pct"] = {
            "reposts": round(rep_value / total_comments, 6),
            "shares": round(share_value / total_comments, 6),
        }
    comment_ids = [str(c.get("comment_id")) for c in canonical_sorted if c.get("comment_id")]
    bundle_id = _bundle_hash(post_id, bundle_version, ordering_rule, comment_ids)
    ordering_key_hash = _ordering_key_hash(ordering_keys)
    tree_metrics = {
        "n_comments": len(canonical_sorted),
        "reply_edges": len([c for c in canonical_sorted if c.get("parent_source_comment_id")]),
        "max_depth": max([int(c.get("depth") or 0) for c in canonical_sorted], default=0),
        "edge_coverage": quality_flags.get("edge_coverage"),
        "missing_ts_pct": quality_flags.get("missing_ts_pct"),
        "partial_tree": quality_flags.get("partial_tree"),
    }
    for c in canonical_sorted:
        c.pop("_created_at", None)
        c.pop("_captured_at", None)
    return {
        "bundle_id": bundle_id,
        "bundle_version": bundle_version,
        "source": source,
        "ordering_rule": ordering_rule,
        "ordering_key_hash": ordering_key_hash,
        "tree_repair_status": quality_flags.get("partial_tree"),
        "quality_flags": quality_flags,
        "tree_metrics": tree_metrics,
        "comments": canonical_sorted,
        "edges_total": len(edges_rows or []),
        "edges_rows": edges_rows or [],
        "post_root_id": post_root_id,
    }


def upsert_comment_clusters(post_id: int, clusters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Upsert post-level clusters into threads_comment_clusters via RPC (set-based).
    """
    if not clusters:
        return {"ok": True, "count": 0, "skipped": True}
    def _as_text_list(val):
        if val is None:
            return []
        if hasattr(val, "tolist"):
            val = val.tolist()
        if isinstance(val, tuple):
            val = list(val)
        if not isinstance(val, list):
            raise ValueError("must be list")
        return [str(x) for x in val]

    def _as_float_list_384(val, key_int: int):
        if val is None:
            return None
        if hasattr(val, "tolist"):
            val = val.tolist()
        if isinstance(val, tuple):
            val = list(val)
        if not isinstance(val, list):
            return None
        if len(val) != 384:
            logger.warning("⚠️ [Clusters] Dropping invalid vec384 dim=%s cluster=%s", len(val), key_int)
            return None
        return [float(x) for x in val]

    sanitized: List[Dict[str, Any]] = []
    logger.info("🚀 [Clusters] Preparing to upsert %s clusters for post=%s", len(clusters), post_id)
    for c in clusters:
        if not isinstance(c, dict):
            raise ValueError("cluster payload must be dict")
        try:
            key_int = int(c.get("cluster_key", 0))
        except Exception:
            raise ValueError(f"Invalid cluster_key: {c.get('cluster_key')}")
        size_val = c.get("size")
        size_int = int(size_val) if size_val is not None else 0

        centroid_384 = _as_float_list_384(c.get("centroid_embedding_384"), key_int)
        if size_int >= 2 and centroid_384 is None:
            raise ValueError(f"🛑 Critical: centroid_384 missing for cluster={key_int} size={size_int} post={post_id}")

        tactics_val = c.get("tactics")
        if tactics_val is None:
            tactics_val = []
        if not isinstance(tactics_val, list):
            raise ValueError(f"tactics must be list for cluster {key_int}")
        if not all(isinstance(t, str) for t in tactics_val):
            raise ValueError(f"tactics entries must be strings for cluster {key_int}")

        sanitized.append(
            {
                "cluster_key": key_int,
                "label": c.get("label"),
                "summary": c.get("summary"),
                "size": size_int,
                "keywords": _as_text_list(c.get("keywords")),
                "top_comment_ids": _as_text_list(c.get("top_comment_ids")),
                "tactics": tactics_val,
                "tactic_summary": c.get("tactic_summary"),
                "centroid_embedding_384": centroid_384,
            }
        )
    logger.info(
        "[Clusters][witness] post=%s clusters=%s detail=%s",
        post_id,
        len(sanitized),
        [
            {
                "cluster_key": c["cluster_key"],
                "size": c["size"],
                "has_centroid_384": c["centroid_embedding_384"] is not None,
                "len_centroid_384": len(c["centroid_embedding_384"] or []),
                "type_centroid_384": type(c["centroid_embedding_384"]).__name__ if c.get("centroid_embedding_384") is not None else None,
            }
            for c in sanitized
        ],
    )
    logger.info(
        "[Clusters][witness2] post=%s detail=%s",
        post_id,
        [
            {
                "cluster_key": c["cluster_key"],
                "size": c["size"],
                "tactics_type": type(c.get("tactics") or []).__name__,
                "tactics_preview": str(c.get("tactics") or [])[:120],
            }
            for c in sanitized
        ],
    )
    try:
        supabase.rpc("upsert_comment_clusters", {"p_post_id": post_id, "p_clusters": sanitized}).execute()
        logger.info("[Clusters] rpc upsert post=%s clusters_attempted=%s", post_id, len(sanitized))
        gate = verify_cluster_centroids(post_id)
        if not gate.get("ok"):
            raise RuntimeError(f"centroid_persistence_failed bad_clusters={gate.get('bad_clusters')}")
        return {"ok": True, "count": len(sanitized), "skipped": False}
    except Exception as e:
        logger.warning("⚠️ [Clusters] rpc upsert failed post=%s err=%s", post_id, e)
        raise


def apply_comment_cluster_assignments(
    post_id: int,
    assignments: List[Dict[str, Any]],
    enforce_coverage: bool = True,
    unassignable_total: int = 0,
    cluster_run_id: Optional[str] = None,
    bundle_id: Optional[str] = None,
    cluster_fingerprints: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """
    Batch update threads_comments with cluster_id/cluster_key via RPC (single call).
    assignments: [{comment_id, cluster_key, cluster_id?}]
    """
    if not assignments:
        return {"ok": True, "count": 0, "skipped": True}
    strict = _env_flag("DL_STRICT_CLUSTER_WRITEBACK")
    force_reassign = _env_flag("DL_FORCE_REASSIGN")
    coverage_min = float(os.environ.get("DL_ASSIGNMENT_COVERAGE_MIN", "0.95") or 0.0)
    mode = (os.environ.get("DL_ASSIGNMENT_WRITE_MODE", "fill_nulls") or "").lower()
    if mode not in {"fill_nulls", "overwrite"}:
        mode = "fill_nulls"
    if strict and mode == "overwrite" and not force_reassign:
        raise RuntimeError("STRICT: overwrite requires DL_FORCE_REASSIGN=1 to proceed")
    assignments_total = len(assignments)
    target_assignments = assignments
    target_rows = len(assignments)
    db_total_comments = 0
    db_null_before = 0
    db_null_after = 0
    coverage_after = 0.0
    try:
        # Witness before
        before = (
            supabase.table("threads_comments")
            .select("id,cluster_key", count="exact")
            .eq("post_id", post_id)
            .execute()
        )
        before_rows = getattr(before, "data", []) or []
        db_total_comments = before.count or len(before_rows)
        db_null_before = len([r for r in before_rows if r.get("cluster_key") is None])
        if mode == "fill_nulls":
            comment_ids = [a.get("comment_id") for a in assignments if a.get("comment_id") is not None]
            if comment_ids:
                existing = (
                    supabase.table("threads_comments")
                    .select("id,cluster_key")
                    .eq("post_id", post_id)
                    .in_("id", comment_ids)
                    .execute()
                )
                rows = getattr(existing, "data", []) or []
                null_ids = {r.get("id") for r in rows if r.get("cluster_key") is None}
                target_assignments = [a for a in assignments if a.get("comment_id") in null_ids]
                target_rows = len(target_assignments)
            else:
                target_assignments = []
                target_rows = 0
        if target_rows == 0:
            coverage = 1.0
            logger.info(
                "[Clusters] assignment writeback skipped (no eligible rows)",
                extra={
                    "post_id": post_id,
                    "assignments_total": assignments_total,
                    "target_rows": target_rows,
                    "updated_rows": 0,
                    "mode": mode,
                    "coverage_pct": round(coverage * 100, 2),
                },
            )
            return {
                "ok": True,
                "count": assignments_total,
                "target_rows": target_rows,
                "updated_rows": 0,
                "coverage": coverage,
                "mode": mode,
                "skipped": True,
            }
        try:
            resp = supabase.rpc("set_comment_cluster_assignments", {"p_post_id": post_id, "p_assignments": target_assignments}).execute()
            data = getattr(resp, "data", None)
            updated_rows = 0
            if isinstance(data, list):
                updated_rows = len(data)
            elif isinstance(data, dict):
                updated_rows = data.get("rows_updated") or data.get("row_count") or 0
            if not updated_rows:
                updated_rows = target_rows  # optimistic fallback when RPC doesn't echo rows
            already_filled = max(assignments_total - target_rows, 0)
            coverage = ((updated_rows + already_filled) / assignments_total) if assignments_total else 1.0
            # Witness after
            after = (
                supabase.table("threads_comments")
                .select("cluster_key", count="exact")
                .eq("post_id", post_id)
                .execute()
            )
            after_rows = getattr(after, "data", []) or []
            db_null_after = len([r for r in after_rows if r.get("cluster_key") is None])
            if db_total_comments:
                coverage_after = (db_total_comments - db_null_after) / db_total_comments
            if _env_flag("DL_PERSIST_ASSIGNMENTS_HISTORY") and cluster_run_id:
                _persist_assignment_history(
                    post_id,
                    cluster_run_id,
                    assignments,
                    bundle_id=bundle_id,
                    cluster_fingerprints=cluster_fingerprints,
                )
            logger.info(
                "[Clusters] rpc assignments",
                extra={
                    "post_id": post_id,
                    "assignments_total": assignments_total,
                    "target_rows": target_rows,
                    "assignments_updated_rows": updated_rows,
                    "mode": mode,
                    "coverage_pct": round(coverage * 100, 2),
                    "db_total_comments": db_total_comments,
                    "db_null_cluster_before": db_null_before,
                    "db_null_cluster_after": db_null_after,
                    "db_coverage_after": round(coverage_after * 100, 2) if db_total_comments else None,
                    "unassignable_total": unassignable_total,
                },
            )
            if strict and (updated_rows == 0 or updated_rows < target_rows):
                raise RuntimeError(
                    f"[Clusters] STRICT assignment writeback failed post={post_id} attempted={assignments_total} target_rows={target_rows} updated_rows={updated_rows}"
                )
            coverage_gate = coverage_after if enforce_coverage else coverage
            if enforce_coverage and coverage_gate < coverage_min:
                msg = f"[Clusters] assignment coverage below min post={post_id} coverage={coverage_gate:.3f} min={coverage_min} mode={mode}"
                if strict:
                    raise RuntimeError(msg)
                logger.error(msg)
                try:
                    supabase.table("threads_posts").update(
                        {"analysis_is_valid": False, "analysis_invalid_reason": "assignment_coverage_below_min"}
                    ).eq("id", post_id).execute()
                except Exception as e:
                    logger.warning("[Clusters] failed to mark post invalid on coverage shortfall post=%s err=%s", post_id, e)
            return {
                "ok": True,
                "count": assignments_total,
                "target_rows": target_rows,
                "updated_rows": updated_rows,
                "coverage": coverage,
                "db_total_comments": db_total_comments,
                "db_null_before": db_null_before,
                "db_null_after": db_null_after,
                "db_coverage_after": coverage_after,
                "mode": mode,
                "skipped": False,
            }
        except Exception as e:
            logger.warning("⚠️ [Clusters] assignment rpc failed post=%s err=%s", post_id, e)
            if strict:
                raise
            return {"ok": False, "count": 0, "error": str(e)}
    except Exception as e:
        if strict:
            raise
        logger.warning("⚠️ [Clusters] assignment writeback failed early post=%s err=%s", post_id, e)
        return {"ok": False, "count": 0, "error": str(e)}


def update_cluster_tactics(post_id: int, updates: List[Dict[str, Any]], *, run_id: str) -> tuple[bool, int]:
    """
    updates: [{"cluster_key": 0, "tactics": ["..."], "tactic_summary": "..."}]
    Returns (ok, updated_count)
    """
    if not updates:
        return True, 0

    raise AssignmentIntegrityError(
        f"tactics writeback is disabled by semantic allowlist (post_id={post_id!r})"
    )

    def _normalize_tactics(val: Any) -> Optional[List[str]]:
        if val is None:
            return None
        if isinstance(val, str):
            return [val]
        if isinstance(val, (list, tuple)):
            return [str(x) for x in val if x is not None]
        return None

    updated = 0
    attempted = 0
    missing = 0
    for item in updates:
        if not isinstance(item, dict):
            continue
        key = item.get("cluster_key")
        if key is None:
            continue
        try:
            key_int = int(key)
        except Exception:
            continue
        tactics_norm = _normalize_tactics(item.get("tactics"))
        payload = {
            "tactics": tactics_norm,
            "tactic_summary": item.get("tactic_summary"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "post_id": post_id,
        }
        attempted += 1
        try:
            resp = supabase.table("threads_comment_clusters").update(payload).eq("post_id", post_id).eq("cluster_key", key_int).execute()
            data = getattr(resp, "data", None) or []
            if data:
                updated += len(data)
            else:
                missing += 1
                logger.warning(f"[Clusters] tactic update missing cluster post={post_id} cluster_key={key_int}")
        except Exception as e:
            logger.warning(f"[Clusters] tactic update failed post={post_id} cluster_key={key_int}: {e}")
    logger.info(
        f"[Clusters] tactics writeback post={post_id} clusters_attempted={attempted} clusters_updated_ok={updated} missing_clusters={missing}"
    )
    return True, updated


def update_cluster_metadata(
    post_id: int,
    updates: List[Dict[str, Any]],
    *,
    run_id: str,
    preanalysis_json: dict,
) -> tuple[bool, int]:
    """
    Idempotently updates label/summary by (post_id, cluster_key).
    updates: [{"cluster_key": int, "label": str?, "summary": str?}]
    Returns (ok, updated_count).
    """
    if not updates:
        return True, 0

    fields: set[str] = set()
    for item in updates:
        if isinstance(item, dict):
            fields.update({str(k) for k in item.keys() if k and k != "cluster_key"})
    guard_semantic_write(
        preanalysis_json,
        run_id,
        "threads_comment_clusters",
        fields,
        context={"post_id": post_id, "caller": "update_cluster_metadata"},
    )
    strict = _env_flag("DL_STRICT_CLUSTER_WRITEBACK")

    sanitized: List[tuple[int, Dict[str, Any]]] = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        key = item.get("cluster_key")
        if key is None:
            continue
        try:
            key_int = int(key)
        except Exception:
            continue

        payload: Dict[str, Any] = {}
        if item.get("label"):
            payload["label"] = item.get("label")
        if item.get("summary"):
            payload["summary"] = item.get("summary")
        if not payload:
            continue

        sanitized.append((key_int, payload))

    if not sanitized:
        return True, 0

    updated = 0
    attempted = 0
    missing = 0
    for key_int, payload in sanitized:
        attempted += 1
        try:
            resp = (
                supabase.table("threads_comment_clusters")
                .update(payload)
                .eq("post_id", post_id)
                .eq("cluster_key", key_int)
                .execute()
            )
            data = getattr(resp, "data", None) or []
            if data:
                updated += len(data)
            else:
                missing += 1
                logger.warning(f"[Clusters] metadata update missing cluster post={post_id} cluster_key={key_int}")
        except Exception as e:
            logger.warning(f"[Clusters] metadata update failed post={post_id} cluster_key={key_int}: {e}")

    logger.info(
        f"[Clusters] metadata writeback post={post_id} clusters_attempted={attempted} clusters_updated_ok={updated} missing_clusters={missing}"
    )
    if strict and (missing > 0 or updated == 0):
        raise RuntimeError(
            f"[Clusters] STRICT writeback failure post={post_id} attempted={attempted} updated={updated} missing={missing}"
        )
    ok = missing == 0 or updated > 0
    return ok, updated


def upsert_cluster_diagnostics(report: Any, *, preanalysis_json: dict) -> None:
    if report is None:
        return
    if hasattr(report, "model_dump"):
        payload = report.model_dump()
    elif hasattr(report, "dict"):
        payload = report.dict()
    elif isinstance(report, dict):
        payload = dict(report)
    else:
        raise ValueError("upsert_cluster_diagnostics requires dict or pydantic model")

    run_id = str(payload.get("run_id") or "").strip()
    post_id = payload.get("post_id")
    cluster_key = payload.get("cluster_key")
    context_mode = payload.get("context_mode") or "card"

    semantic_payload = {
        "run_id": run_id,
        "verdict": payload.get("verdict"),
        "k": payload.get("k"),
        "labels": payload.get("labels") or [],
        "stability_avg": payload.get("stability_avg"),
        "stability_min": payload.get("stability_min"),
        "drift_avg": payload.get("drift_avg"),
        "drift_max": payload.get("drift_max"),
        "context_mode": context_mode,
        "prompt_hash": payload.get("prompt_hash"),
        "model_name": payload.get("model_name"),
    }
    guard_semantic_write(
        preanalysis_json,
        run_id,
        "threads_cluster_diagnostics",
        semantic_payload.keys(),
        context={"post_id": post_id, "cluster_key": cluster_key, "caller": "upsert_cluster_diagnostics"},
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    report_id = payload.get("id") or f"isd:{post_id}:{cluster_key}:{run_id}:{context_mode}"
    system_payload = {
        "id": report_id,
        "post_id": post_id,
        "cluster_key": cluster_key,
        "updated_at": now_iso,
        "created_at": now_iso,
    }
    row = {**system_payload, **semantic_payload}
    supabase.table("threads_cluster_diagnostics").upsert(
        row,
        on_conflict="post_id,cluster_key,run_id,context_mode",
    ).execute()


def save_claim_pack(
    post_id: int,
    run_id: str,
    claims: List[Dict[str, Any]],
    audit_meta: Dict[str, Any],
    *,
    preanalysis_json: dict,
    prompt_hash: str | None = None,
    model_name: str | None = None,
    build_id: str | None = None,
    evidence_rows: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    if not run_id:
        raise AssignmentIntegrityError("save_claim_pack requires run_id")

    claim_fields = [
        "id",
        "claim_key",
        "status",
        "post_id",
        "cluster_key",
        "cluster_keys",
        "primary_cluster_key",
        "run_id",
        "claim_type",
        "scope",
        "text",
        "source_agent",
        "confidence",
        "confidence_cap",
        "tags",
        "prompt_hash",
        "model_name",
        "audit_reason",
        "missing_evidence_type",
    ]
    evidence_fields = [
        "claim_id",
        "evidence_type",
        "evidence_id",
        "span_text",
        "source",
        "locator_type",
        "locator_value",
        "locator_key",
        "cluster_key",
        "author_handle",
        "like_count",
        "capture_hash",
        "evidence_ref",
    ]
    audit_fields = [
        "post_id",
        "run_id",
        "build_id",
        "verdict",
        "dropped_claims_count",
        "kept_claims_count",
        "total_claims_count",
        "reasons",
    ]

    guard_semantic_write(
        preanalysis_json,
        run_id,
        "threads_claims",
        claim_fields,
        context={"post_id": post_id, "caller": "save_claim_pack.claims"},
    )
    guard_semantic_write(
        preanalysis_json,
        run_id,
        "threads_claim_evidence",
        evidence_fields,
        context={"post_id": post_id, "caller": "save_claim_pack.evidence"},
    )
    guard_semantic_write(
        preanalysis_json,
        run_id,
        "threads_claim_audits",
        audit_fields,
        context={"post_id": post_id, "caller": "save_claim_pack.audit"},
    )

    claim_rows: List[Dict[str, Any]] = []
    evidence_inserts: List[Dict[str, Any]] = []
    for item in claims or []:
        if not isinstance(item, dict):
            continue
        claim_id = item.get("claim_id") or item.get("id")
        if not claim_id:
            continue
        row = {
            "id": claim_id,
            "claim_key": item.get("claim_key") or str(claim_id),
            "status": item.get("status") or "audited",
            "post_id": post_id,
            "cluster_key": item.get("cluster_key"),
            "cluster_keys": item.get("cluster_keys"),
            "primary_cluster_key": item.get("primary_cluster_key"),
            "run_id": run_id,
            "claim_type": item.get("claim_type"),
            "scope": item.get("scope"),
            "text": item.get("text"),
            "source_agent": item.get("source_agent") or "analyst",
            "confidence": item.get("confidence"),
            "confidence_cap": item.get("confidence_cap"),
            "tags": item.get("tags"),
            "prompt_hash": prompt_hash,
            "model_name": model_name,
            "audit_reason": item.get("audit_reason"),
            "missing_evidence_type": item.get("missing_evidence_type"),
        }
        claim_rows.append(row)

        ev_refs = item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []
        if ev_refs:
            for ev in ev_refs:
                if not isinstance(ev, dict):
                    continue
                locator = ev.get("locator") if isinstance(ev.get("locator"), dict) else {}
                source = ev.get("source") or "threads"
                locator_type = locator.get("type") or "comment_id"
                locator_value = locator.get("value") or ""
                if not locator_value:
                    continue
                locator_key = f"{source}:{locator_type}:{locator_value}"
                row = (evidence_rows or {}).get(locator_key) or {}
                span = (ev.get("span_text") or row.get("span_text") or "").strip()
                if span:
                    span = span[:280]
                evidence_inserts.append(
                    {
                        "claim_id": claim_id,
                        "evidence_type": locator_type,
                        "evidence_id": str(locator_value),
                        "span_text": span or None,
                        "source": source,
                        "locator_type": locator_type,
                        "locator_value": str(locator_value),
                        "locator_key": locator_key,
                        "cluster_key": row.get("cluster_key"),
                        "author_handle": row.get("author_handle"),
                        "like_count": row.get("like_count"),
                        "capture_hash": row.get("capture_hash"),
                        "evidence_ref": ev,
                    }
                )
        else:
            for ev_id in item.get("evidence_ids") or []:
                if not ev_id:
                    continue
                locator_key = f"threads:comment_id:{ev_id}"
                row = (evidence_rows or {}).get(locator_key) or {}
                span = (row.get("span_text") or "").strip()
                if span:
                    span = span[:280]
                evidence_inserts.append(
                    {
                        "claim_id": claim_id,
                        "evidence_type": "comment_id",
                        "evidence_id": str(ev_id),
                        "span_text": span or None,
                        "source": "threads",
                        "locator_type": "comment_id",
                        "locator_value": str(ev_id),
                        "locator_key": locator_key,
                        "cluster_key": row.get("cluster_key"),
                        "author_handle": row.get("author_handle"),
                        "like_count": row.get("like_count"),
                        "capture_hash": row.get("capture_hash"),
                        "evidence_ref": {
                            "source": "threads",
                            "locator": {"type": "comment_id", "value": str(ev_id)},
                            "capture_hash": row.get("capture_hash"),
                            "span_text": row.get("span_text"),
                        },
                    }
                )

    if claim_rows:
        supabase.table("threads_claims").insert(claim_rows).execute()
    if evidence_inserts:
        supabase.table("threads_claim_evidence").insert(evidence_inserts).execute()

    audit_row = {
        "post_id": post_id,
        "run_id": run_id,
        "build_id": build_id,
        "verdict": audit_meta.get("verdict") or "fail",
        "dropped_claims_count": int(audit_meta.get("dropped_claims_count") or 0),
        "kept_claims_count": int(audit_meta.get("kept_claims_count") or 0),
        "total_claims_count": int(audit_meta.get("total_claims_count") or 0),
        "reasons": audit_meta.get("fail_reasons") or [],
    }
    supabase.table("threads_claim_audits").insert(audit_row).execute()


def save_behavior_audit(
    post_id: int,
    cluster_run_id: str,
    artifact: Dict[str, Any],
    *,
    preanalysis_json: dict,
) -> None:
    if not cluster_run_id:
        raise AssignmentIntegrityError("save_behavior_audit requires cluster_run_id")
    if not isinstance(artifact, dict):
        raise ValueError("behavior artifact must be dict")

    payload = {
        "post_id": post_id,
        "cluster_run_id": cluster_run_id,
        "behavior_run_id": artifact.get("behavior_run_id"),
        "reply_graph_id_space": artifact.get("reply_graph_id_space") or "internal",
        "artifact_json": artifact,
        "quality_flags": artifact.get("quality_flags") or {},
        "scores": artifact.get("scores") or {},
    }
    guard_semantic_write(
        preanalysis_json,
        cluster_run_id,
        "threads_behavior_audits",
        payload.keys(),
        context={"post_id": post_id, "caller": "save_behavior_audit"},
    )
    supabase.table("threads_behavior_audits").insert(_json_safe(payload)).execute()


def save_risk_brief(
    post_id: int,
    cluster_run_id: str,
    risk_brief: Dict[str, Any],
    *,
    preanalysis_json: dict,
) -> None:
    if not cluster_run_id:
        raise AssignmentIntegrityError("save_risk_brief requires cluster_run_id")
    if not isinstance(risk_brief, dict):
        raise ValueError("risk brief must be dict")

    payload = {
        "post_id": post_id,
        "cluster_run_id": cluster_run_id,
        "behavior_run_id": risk_brief.get("behavior_run_id"),
        "risk_run_id": risk_brief.get("risk_run_id"),
        "brief_json": risk_brief,
    }
    guard_semantic_write(
        preanalysis_json,
        cluster_run_id,
        "threads_risk_briefs",
        payload.keys(),
        context={"post_id": post_id, "caller": "save_risk_brief"},
    )
    supabase.table("threads_risk_briefs").insert(_json_safe(payload)).execute()


def save_coverage_audit(
    post_id: int,
    fetch_run_id: str,
    coverage: Dict[str, Any],
    *,
    preanalysis_json: Optional[dict] = None,
    run_id: Optional[str] = None,
) -> None:
    if not fetch_run_id:
        raise ValueError("save_coverage_audit requires fetch_run_id")
    if not isinstance(coverage, dict):
        raise ValueError("coverage payload must be dict")

    payload = {
        "post_id": post_id,
        "fetch_run_id": fetch_run_id,
        "captured_at": coverage.get("captured_at"),
        "expected_replies_ui": coverage.get("expected_replies_ui"),
        "unique_fetched": coverage.get("unique_fetched") or 0,
        "coverage_ratio": coverage.get("coverage_ratio"),
        "stop_reason": coverage.get("stop_reason") or "unknown",
        "budgets_used": coverage.get("budgets_used") or {},
        "rounds_json": coverage.get("rounds_json"),
        "rounds_hash": coverage.get("rounds_hash"),
    }
    if preanalysis_json is not None and run_id:
        guard_semantic_write(
            preanalysis_json,
            run_id,
            "threads_coverage_audits",
            payload.keys(),
            context={"post_id": post_id, "caller": "save_coverage_audit"},
        )
    supabase.table("threads_coverage_audits").insert(_json_safe(payload)).execute()


def save_reply_matrix_audit(
    post_id: int,
    cluster_run_id: str,
    accounting: Dict[str, Any],
    *,
    preanalysis_json: dict,
    reply_graph_id_space: str = "internal",
) -> None:
    if not cluster_run_id:
        raise AssignmentIntegrityError("save_reply_matrix_audit requires cluster_run_id")
    if not isinstance(accounting, dict):
        raise ValueError("reply matrix accounting must be dict")
    payload = {
        "post_id": post_id,
        "cluster_run_id": cluster_run_id,
        "reply_graph_id_space": reply_graph_id_space or "internal",
        "accounting_json": accounting,
    }
    guard_semantic_write(
        preanalysis_json,
        cluster_run_id,
        "threads_reply_matrix_audits",
        payload.keys(),
        context={"post_id": post_id, "caller": "save_reply_matrix_audit"},
    )
    supabase.table("threads_reply_matrix_audits").insert(_json_safe(payload)).execute()


def save_llm_call_log(
    *,
    post_id: Optional[int | str],
    run_id: Optional[str],
    mode: Optional[str],
    model_name: Optional[str],
    status: str,
    latency_ms: Optional[int],
    request_tokens: Optional[int] = None,
    response_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    payload = {
        "post_id": int(post_id) if post_id is not None and str(post_id).isdigit() else post_id,
        "run_id": run_id,
        "mode": mode,
        "model_name": model_name,
        "request_tokens": request_tokens,
        "response_tokens": response_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "status": status or "ok",
    }
    try:
        supabase.table("llm_call_logs").insert(_json_safe(payload)).execute()
    except Exception as e:
        logger.warning("[LLM_LOG] failed to insert llm_call_logs err=%s payload=%s", e, payload)
def verify_cluster_centroids(post_id: int | str) -> Dict[str, Any]:
    """
    Ensure clusters with size>=2 have centroid_embedding_384 persisted.
    """
    try:
        resp = supabase.table("threads_comment_clusters").select("cluster_key,size,centroid_embedding_384").eq("post_id", post_id).execute()
        rows = getattr(resp, "data", []) or []
        bad = []
        for row in rows:
            try:
                size = int(row.get("size") or 0)
            except Exception:
                size = 0
            if size >= 2 and (row.get("centroid_embedding_384") is None):
                bad.append(row.get("cluster_key"))
        return {"ok": len(bad) == 0, "bad_clusters": bad, "total": len([r for r in rows if (r.get('size') or 0) >= 2])}
    except Exception as e:
        logger.warning(f"[Clusters] centroid verification failed post={post_id}: {e}")
        return {"ok": False, "error": str(e), "bad_clusters": []}

def save_thread(data: dict, ingest_source: Optional[str] = None):
    """
    將解析好的 Threads 貼文存入 Supabase 的 threads_posts 表
    目前 image_pipeline 已進入 link-only 模式，不會保存 OCR 結果，
    Supabase 圖片欄位僅包含遠端 URL，OCR 由之後的 Gemini Pipeline 處理。
    """
    comments = data.get("comments", [])
    post_id = (
        data.get("post_id")
        or data.get("Post_ID")
        or data.get("id")
        or "UNKNOWN_POST"
    )

    raw_images = data.get("images") or []
    try:
        enriched_images = process_images_for_post(post_id, raw_images)
    except Exception:
        enriched_images = raw_images
    data["images"] = enriched_images

    url_val = data["url"]
    if isinstance(url_val, str) and url_val.startswith("https://www.threads.com/"):
        url_val = url_val.replace("https://www.threads.com/", "https://www.threads.net/")
        data["url"] = url_val

    reply_count_ui = int(data["metrics"].get("reply_count", 0) or 0)
    reply_count = reply_count_ui if (reply_count_ui > 0 or len(comments) == 0) else len(comments)
    payload = {
        "url": url_val,
        "author": data["author"],
        "post_text": data["post_text"],
        "post_text_raw": data.get("post_text_raw", ""),
        "like_count": data["metrics"].get("likes", 0),
        "view_count": data["metrics"].get("views", 0),
        "reply_count": reply_count,
        "reply_count_ui": reply_count_ui,
        "repost_count": data["metrics"].get("repost_count", 0),
        "share_count": data["metrics"].get("share_count", 0),
        "images": data.get("images", []),
        "raw_comments": comments,
        "ingest_source": ingest_source,
        "is_first_thread": bool(data.get("is_first_thread", False)),
    }

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        print(f"[DB DEBUG] payload keys: {list(payload.keys())}")
        try:
            payload_size = len(json.dumps(payload))
            print(f"[DB DEBUG] payload json size: {payload_size} bytes")
        except Exception:
            pass
        supabase.table("threads_posts").upsert(payload, on_conflict="url").execute()
        res = (
            supabase.table("threads_posts")
            .select("id")
            .eq("url", payload["url"])
            .limit(1)
            .execute()
        )
        if not res.data:
            raise RuntimeError(f"save_thread upsert ok but cannot re-select id for url={payload['url']}")
        post_row_id = res.data[0]["id"]
        data["post_id"] = post_row_id
        data["id"] = post_row_id
        sync_comments_to_table(post_row_id, comments)
    except Exception as e:
        print(f"❌ 寫入 Supabase 失敗：{e}")
        raise
    print("💾 Saved to DB, id =", post_row_id, "comments_upserted=", len(comments))
    return post_row_id


def comment_debug_summary(post_id: str | int) -> Dict[str, Any]:
    """
    Lightweight sanity check for comment dedupe and tree fields.
    """
    try:
        resp = (
            supabase.table("threads_comments")
            .select("id, source_comment_id, parent_source_comment_id, root_source_comment_id", count="exact")
            .eq("post_id", post_id)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        total = resp.count or len(rows)
        with_source = sum(1 for r in rows if r.get("source_comment_id"))
        with_root = sum(1 for r in rows if r.get("root_source_comment_id"))
        with_parent = sum(1 for r in rows if r.get("parent_source_comment_id"))
        return {
            "post_id": post_id,
            "count": total,
            "with_source_comment_id": with_source,
            "with_root_source_comment_id": with_root,
            "with_parent_source_comment_id": with_parent,
        }
    except Exception as e:
        logger.warning(f"[CommentsSoT] debug summary failed post={post_id}: {e}")
        return {"post_id": post_id, "error": str(e)}


def update_post_archive(
    supabase_url: str,
    supabase_anon_key: str,
    post_id: str,
    archive_build_id: str,
    archive_html: str,
    archive_dom_json: dict,
) -> None:
    """
    Best-effort PATCH. Only writes archive_* fields.
    """
    payload = {
        "archive_captured_at": datetime.now(timezone.utc).isoformat(),
        "archive_build_id": archive_build_id,
        "archive_html": archive_html,
        "archive_dom_json": archive_dom_json,
    }

    headers = {
        "apikey": supabase_anon_key,
        "Authorization": f"Bearer {supabase_anon_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    r = requests.patch(
        f"{supabase_url}/rest/v1/threads_posts?id=eq.{post_id}",
        headers=headers,
        json=payload,
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase archive PATCH failed: {r.status_code} {r.text[:300]}")


def update_post_analysis_forensic(
    supabase_url: str,
    supabase_anon_key: str,
    post_id: str,
    analysis_json: dict | None,
    meta: dict,
) -> None:
    """
    Forensic mode: always patch analysis_json if provided (dict), along with meta.
    """
    # DEPRECATED (CDX-106): analysis_json writes must go through save_analysis_result.
    payload = dict(meta or {})
    if analysis_json is not None:
        save_analysis_result(post_id, analysis_json)
        for key in ("analysis_json", "analysis_is_valid", "analysis_invalid_reason", "analysis_missing_keys"):
            payload.pop(key, None)

    if payload:
        supabase.table("threads_posts").update(_json_safe(payload)).eq("id", post_id).execute()


def update_vision_meta(
    supabase_url: str,
    supabase_anon_key: str,
    post_id: str,
    *,
    vision_fields: Dict[str, Any],
    images: Optional[list] = None,
) -> None:
    """
    Unified vision writeback for threads_posts.
    - vision_fields: columns like vision_mode/need_score/reasons/stage_ran/v1/v2/sim/metrics_reliable
    - images: optional enriched images array to write back together
    """
    payload: Dict[str, Any] = dict(vision_fields or {})
    payload["vision_updated_at"] = datetime.now(timezone.utc).isoformat()

    if images is not None:
        payload["images"] = images

    headers = {
        "apikey": supabase_anon_key,
        "Authorization": f"Bearer {supabase_anon_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    r = requests.patch(
        f"{supabase_url}/rest/v1/threads_posts?id=eq.{post_id}",
        headers=headers,
        json=payload,
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase vision PATCH failed: {r.status_code} {r.text[:300]}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Quick sanity checks for threads_comments")
    parser.add_argument("--post-id", required=True, help="threads_posts.id to inspect")
    parser.add_argument("--repair", action="store_true", help="run repair pass before summarizing")
    args = parser.parse_args()

    if args.repair:
        result = _repair_comment_tree(args.post_id)
        print("[debug] repair_result:", result)
    summary = comment_debug_summary(args.post_id)
    print("[debug] summary:", json.dumps(summary, indent=2, ensure_ascii=False))
