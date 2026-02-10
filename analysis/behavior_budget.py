from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


BUDGET_VERSION = "S6_EvidenceBudgetV1"


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        val = value.strip()
        if not val:
            return None
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(val)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _hash_payload(payload: Dict[str, Any]) -> str:
    dumped = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except Exception:
        return default


def _stable_unique(values: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _comment_meta_map(comments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    for c in comments or []:
        cid = c.get("comment_id") or c.get("id")
        if not cid:
            continue
        cid = str(cid)
        meta[cid] = {
            "created_at": _parse_ts(c.get("created_at") or c.get("taken_at") or c.get("ui_created_at_est") or c.get("captured_at")),
            "like_count": int(c.get("like_count") or 0),
            "reply_count": int(c.get("reply_count") or 0),
            "author_id": (c.get("author_handle") or c.get("user") or ""),
        }
    return meta


def _author_stats(comments: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    counts: Dict[str, int] = {}
    likes: Dict[str, int] = {}
    for c in comments or []:
        author = c.get("author_handle") or c.get("user") or ""
        counts[author] = counts.get(author, 0) + 1
        likes[author] = likes.get(author, 0) + int(c.get("like_count") or 0)
    return counts, likes


def build_behavior_evidence_budget(
    artifact: Dict[str, Any],
    comments: List[Dict[str, Any]],
    *,
    caps: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError("behavior artifact must be dict")
    if not isinstance(comments, list):
        comments = []

    caps = caps or {
        "max_burst_windows": _env_int("DL_S6_BUDGET_MAX_BURST_WINDOWS", 5),
        "max_burst_comment_ids": _env_int("DL_S6_BUDGET_MAX_BURST_COMMENT_IDS", 50),
        "max_coord_events": _env_int("DL_S6_BUDGET_MAX_COORD_EVENTS", 5),
        "max_coord_comment_ids": _env_int("DL_S6_BUDGET_MAX_COORD_COMMENT_IDS", 50),
        "max_hub_nodes": _env_int("DL_S6_BUDGET_MAX_HUB_NODES", 10),
        "max_anom_edges": _env_int("DL_S6_BUDGET_MAX_ANOM_EDGES", 50),
        "max_top_like_comments": _env_int("DL_S6_BUDGET_MAX_TOP_LIKE_COMMENTS", 20),
        "max_top_authors": _env_int("DL_S6_BUDGET_MAX_TOP_AUTHORS", 20),
    }

    meta_map = _comment_meta_map(comments)
    author_counts, author_likes = _author_stats(comments)
    total_likes = sum(author_likes.values()) or 0
    total_comments = sum(author_counts.values()) or 0

    evidence = artifact.get("evidence") or {}
    metrics = artifact.get("metrics") or {}
    temporal = (metrics.get("temporal") or {}) if isinstance(metrics, dict) else {}

    # Burst windows
    burst_windows = []
    for win in (temporal.get("burst_windows") or []):
        start = win.get("start_ts") or ""
        end = win.get("end_ts") or ""
        window_key = f"{start}|{end}"
        burst_windows.append(
            {
                "start_ts": start,
                "end_ts": end,
                "count": int(win.get("count") or 0),
                "zscore": float(win.get("zscore") or 0.0),
                "window_key": window_key,
                "comment_ids": [str(c) for c in (win.get("comment_ids") or []) if c],
            }
        )
    burst_windows.sort(
        key=lambda w: (
            -w.get("zscore", 0.0),
            -w.get("count", 0),
            w.get("start_ts") or "",
            w.get("window_key") or "",
        )
    )
    burst_windows = burst_windows[: caps["max_burst_windows"]]

    burst_comment_ids: List[str] = []
    for win in burst_windows:
        burst_comment_ids.extend(win.get("comment_ids") or [])
    if not burst_comment_ids:
        burst_comment_ids = [str(c) for c in (evidence.get("burst_comment_ids") or []) if c]
    burst_comment_ids = _stable_unique(burst_comment_ids)
    burst_comment_ids.sort(
        key=lambda cid: (
            meta_map.get(cid, {}).get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
            cid,
        )
    )
    burst_comment_ids = burst_comment_ids[: caps["max_burst_comment_ids"]]

    # Coordination events
    coord_events = []
    for ev in (evidence.get("coordination_events") or []):
        start = ev.get("window_start") or ""
        end = ev.get("window_end") or ""
        event_key = f"{ev.get('cluster_key')}|{start}|{end}"
        coord_events.append(
            {
                "cluster_key": ev.get("cluster_key"),
                "window_start": start,
                "window_end": end,
                "comments_count": int(ev.get("comments_count") or 0),
                "unique_authors": int(ev.get("unique_authors") or 0),
                "entropy": float(ev.get("entropy") or 0.0),
                "top_author_share": float(ev.get("top_author_share") or 0.0),
                "event_key": event_key,
                "comment_ids": [str(c) for c in (ev.get("comment_ids") or []) if c],
            }
        )
    coord_events.sort(
        key=lambda e: (
            -e.get("comments_count", 0),
            -e.get("unique_authors", 0),
            e.get("entropy", 0.0),
            e.get("window_start") or "",
            e.get("event_key") or "",
        )
    )
    coord_events = coord_events[: caps["max_coord_events"]]
    coord_comment_ids: List[str] = []
    for ev in coord_events:
        coord_comment_ids.extend(ev.get("comment_ids") or [])
    coord_comment_ids = _stable_unique(coord_comment_ids)
    coord_comment_ids.sort(
        key=lambda cid: (
            meta_map.get(cid, {}).get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
            cid,
        )
    )
    coord_comment_ids = coord_comment_ids[: caps["max_coord_comment_ids"]]

    # Graph
    hub_nodes = []
    for node in (evidence.get("hub_nodes") or []):
        out_deg = int(node.get("out_degree") or 0)
        in_deg = int(node.get("in_degree") or 0)
        hub_score = out_deg + in_deg
        cid = str(node.get("comment_id") or "")
        hub_nodes.append(
            {
                "comment_id": cid,
                "out_degree": out_deg,
                "in_degree": in_deg,
                "hub_score": hub_score,
            }
        )
    hub_nodes.sort(
        key=lambda h: (-h.get("hub_score", 0), -h.get("out_degree", 0), -h.get("in_degree", 0), h.get("comment_id") or "")
    )
    hub_nodes = hub_nodes[: caps["max_hub_nodes"]]

    anom_edges = []
    for edge in (evidence.get("anomalous_edges") or []):
        parent = str(edge.get("parent") or "")
        child = str(edge.get("child") or "")
        edge_key = f"{parent}->{child}"
        anom_edges.append({"parent": parent, "child": child, "edge_key": edge_key})
    anom_edges.sort(key=lambda e: (e.get("parent") or "", e.get("child") or ""))
    anom_edges = anom_edges[: caps["max_anom_edges"]]

    # Engagement
    top_like_comment_ids = [str(cid) for cid in (evidence.get("top_like_comment_ids") or []) if cid]
    top_like_comment_ids = _stable_unique(top_like_comment_ids)
    top_like_comment_ids.sort(
        key=lambda cid: (
            -(meta_map.get(cid, {}).get("like_count") or 0),
            meta_map.get(cid, {}).get("reply_count") or 0,
            cid,
        )
    )
    top_like_comment_ids = top_like_comment_ids[: caps["max_top_like_comments"]]

    top_like_authors = []
    for item in (evidence.get("top_like_authors") or []):
        author_id = str(item.get("author") or "")
        likes_val = int(item.get("likes") or 0)
        share = (likes_val / total_likes) if total_likes else 0.0
        top_like_authors.append({"author_id": author_id, "likes": likes_val, "share": round(share, 4)})
    top_like_authors.sort(
        key=lambda a: (-a.get("likes", 0), -a.get("share", 0.0), a.get("author_id") or "")
    )
    top_like_authors = top_like_authors[: caps["max_top_authors"]]

    # Authorship
    top_authors_by_comment = []
    for author_id, count in author_counts.items():
        share = (count / total_comments) if total_comments else 0.0
        top_authors_by_comment.append({"author_id": str(author_id), "count": int(count), "share": round(share, 4)})
    top_authors_by_comment.sort(
        key=lambda a: (-a.get("count", 0), -a.get("share", 0.0), a.get("author_id") or "")
    )
    top_authors_by_comment = top_authors_by_comment[: caps["max_top_authors"]]

    top_authors_by_like = []
    for author_id, likes_val in author_likes.items():
        share = (likes_val / total_likes) if total_likes else 0.0
        top_authors_by_like.append({"author_id": str(author_id), "likes": int(likes_val), "share": round(share, 4)})
    top_authors_by_like.sort(
        key=lambda a: (-a.get("likes", 0), -a.get("share", 0.0), a.get("author_id") or "")
    )
    top_authors_by_like = top_authors_by_like[: caps["max_top_authors"]]

    selections = {
        "burst": {"windows": burst_windows, "comment_ids": burst_comment_ids},
        "coordination": {"events": coord_events, "comment_ids": coord_comment_ids},
        "graph": {"hub_nodes": hub_nodes, "anomalous_edges": anom_edges},
        "engagement": {"top_like_comment_ids": top_like_comment_ids, "top_like_authors": top_like_authors},
        "authorship": {
            "top_authors_by_comment_count": top_authors_by_comment,
            "top_authors_by_like_share": top_authors_by_like,
        },
    }

    artifact_for_hash = dict(artifact or {})
    artifact_for_hash.pop("ui_budget", None)
    source_artifact_hash = _hash_payload(artifact_for_hash)
    selection_hash = _hash_payload(
        {
            "behavior_run_id": artifact.get("behavior_run_id"),
            "caps": caps,
            "selections": selections,
        }
    )

    return {
        "post_id": artifact.get("post_id"),
        "cluster_run_id": artifact.get("cluster_run_id"),
        "behavior_run_id": artifact.get("behavior_run_id"),
        "budget_version": BUDGET_VERSION,
        "caps": caps,
        "selections": selections,
        "digests": {
            "selection_hash": selection_hash,
            "source_artifact_hash": source_artifact_hash,
        },
    }

