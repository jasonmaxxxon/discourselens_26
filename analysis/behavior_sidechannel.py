from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "S6_BehaviorSidechannelV1"

BIN_SECONDS = 60
BURST_Z_MIN = 2.5
BURST_MIN_COUNT = 6
BURST_WINDOW_CAP = 5
BURST_COMMENT_CAP = 200

COORD_WINDOW_SECONDS = 180
COORD_MIN_COMMENTS = 6
COORD_MIN_AUTHORS = 4
COORD_ENTROPY_MAX = 1.2
COORD_TOP_AUTHOR_SHARE_MIN = 0.55
COORD_EVENT_CAP = 10
COORD_COMMENT_CAP = 200

HUB_NODE_CAP = 6
ANOMALOUS_EDGE_CAP = 80

TOP_LIKE_COMMENT_CAP = 10
TOP_LIKE_AUTHOR_CAP = 6


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


def _json_hash(payload: Dict[str, Any]) -> str:
    dumped = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    mid = len(vals) // 2
    if len(vals) % 2 == 1:
        return float(vals[mid])
    return (vals[mid - 1] + vals[mid]) / 2.0


def _mad(values: List[float], median: Optional[float] = None) -> float:
    if not values:
        return 0.0
    med = _median(values) if median is None else median
    return _median([abs(v - med) for v in values])


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except Exception:
        return 0.0


def _entropy_from_counts(counts: Iterable[int]) -> float:
    vals = [c for c in counts if c > 0]
    total = sum(vals)
    if total <= 0:
        return 0.0
    ent = 0.0
    for c in vals:
        p = c / total
        ent -= p * math.log(p)
    return ent


def _gini(values: Iterable[float]) -> float:
    vals = [max(0.0, float(v)) for v in values if v is not None]
    if not vals:
        return 0.0
    vals.sort()
    total = sum(vals)
    if total <= 0:
        return 0.0
    n = len(vals)
    cum = 0.0
    for i, v in enumerate(vals, start=1):
        cum += i * v
    return (2.0 * cum) / (n * total) - (n + 1) / n


def _stable_unique(seq: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _cap_sorted(values: Iterable[str], cap: int) -> List[str]:
    uniq = _stable_unique(sorted([str(v) for v in values if v is not None]))
    return uniq[:cap]


def _build_edges(comments: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    comment_ids = {str(c.get("comment_id") or c.get("id")) for c in comments if c.get("comment_id") or c.get("id")}
    edges: List[Tuple[str, str]] = []
    for c in comments:
        child = c.get("comment_id") or c.get("id")
        if not child:
            continue
        parent = c.get("parent_source_comment_id") or c.get("graph_parent_id") or c.get("parent_comment_id")
        if not parent:
            continue
        parent = str(parent)
        child = str(child)
        if parent in comment_ids and child in comment_ids:
            edges.append((parent, child))
    return sorted(set(edges))


def compute_behavior_sidechannel(
    *,
    post_id: int,
    cluster_run_id: str,
    comments: List[Dict[str, Any]],
    assignments: Optional[List[Dict[str, Any]]] = None,
    reply_graph_id_space: str = "internal",
    ordering_key_hash: Optional[str] = None,
    quality_flags: Optional[Dict[str, Any]] = None,
    reply_matrix_health: Optional[Dict[str, Any]] = None,
    coverage_ratio: Optional[float] = None,
    comments_total: Optional[int] = None,
) -> Dict[str, Any]:
    assignments = assignments or []
    quality_flags = quality_flags or {}
    reply_matrix_health = reply_matrix_health or {}
    cluster_by_comment: Dict[str, int] = {}
    for a in assignments:
        cid = a.get("comment_id")
        if cid is None:
            continue
        try:
            cluster_by_comment[str(cid)] = int(a.get("cluster_key", -1))
        except Exception:
            cluster_by_comment[str(cid)] = -1

    normalized_comments: List[Dict[str, Any]] = []
    missing_ts = 0
    for c in comments:
        cid = c.get("comment_id") or c.get("id")
        if not cid:
            continue
        cid = str(cid)
        ts = _parse_ts(c.get("taken_at") or c.get("ui_created_at_est") or c.get("created_at") or c.get("captured_at"))
        if ts is None:
            missing_ts += 1
        cluster_key = c.get("cluster_key")
        if cluster_key is None:
            cluster_key = cluster_by_comment.get(cid, -1)
        try:
            cluster_key = int(cluster_key)
        except Exception:
            cluster_key = -1
        normalized_comments.append(
            {
                "comment_id": cid,
                "author": c.get("author_handle") or c.get("user") or "",
                "like_count": int(c.get("like_count") or 0),
                "reply_count": int(c.get("reply_count") or 0),
                "cluster_key": cluster_key,
                "ts": ts,
            }
        )

    total_comments = len(normalized_comments)
    if comments_total is None:
        comments_total = total_comments
    missing_ts_pct = round(missing_ts / total_comments, 6) if total_comments else 0.0
    edges = _build_edges(comments)
    edges_count = len(edges)
    comment_ids_sorted = sorted([c["comment_id"] for c in normalized_comments])

    run_id_payload = {
        "cluster_run_id": cluster_run_id,
        "comment_ids": comment_ids_sorted,
        "edges": [f"{p}->{c}" for p, c in edges],
        "ordering_key_hash": ordering_key_hash or "",
        "version": VERSION,
    }
    behavior_run_id = _json_hash(run_id_payload)

    edge_coverage = reply_matrix_health.get("coverage_rate") or quality_flags.get("edge_coverage") or 0.0
    partial_tree = bool(reply_matrix_health.get("partial_tree") or quality_flags.get("partial_tree"))
    data_sufficiency = "GREEN"
    if missing_ts_pct >= 0.6 or edge_coverage < 0.4:
        data_sufficiency = "RED"
    elif missing_ts_pct >= 0.2 or partial_tree or edge_coverage < 0.7:
        data_sufficiency = "YELLOW"

    temporal_sufficiency = "GREEN"
    structural_sufficiency = "GREEN"
    missing_ts_pct_val = missing_ts_pct
    edge_cov_val = edge_coverage
    coverage_val = coverage_ratio if coverage_ratio is not None else 1.0
    min_comments_temporal = int(os.getenv("DL_MIN_COMMENTS_FOR_TEMPORAL", "80") or 80)
    min_coverage_temporal = float(os.getenv("DL_MIN_COVERAGE_FOR_TEMPORAL", "0.60") or 0.60)
    min_comments_structural = int(os.getenv("DL_MIN_COMMENTS_FOR_STRUCTURAL", "30") or 30)
    min_replies_for_graph = int(os.getenv("DL_MIN_REPLIES_FOR_GRAPH", "10") or 10)

    if missing_ts_pct_val >= 0.6 or (comments_total or 0) < min_comments_temporal or coverage_val < min_coverage_temporal:
        temporal_sufficiency = "RED"
    elif missing_ts_pct_val >= 0.2 or partial_tree or coverage_val < 0.75 or (comments_total or 0) < 150:
        temporal_sufficiency = "YELLOW"

    total_replies_val = reply_matrix_health.get("total_replies") or 0
    if (edge_cov_val < 0.4 and total_replies_val >= min_replies_for_graph) or (comments_total or 0) < min_comments_structural:
        structural_sufficiency = "RED"
    elif edge_cov_val < 0.7 or partial_tree:
        structural_sufficiency = "YELLOW"

    # Reconcile overall data_sufficiency with axis split.
    if temporal_sufficiency == "RED" or structural_sufficiency == "RED":
        data_sufficiency = "RED"
    elif temporal_sufficiency == "YELLOW" or structural_sufficiency == "YELLOW":
        data_sufficiency = "YELLOW"

    disabled_axes: List[str] = []
    limitations: List[str] = []
    if temporal_sufficiency == "RED":
        disabled_axes.append("temporal")
        limitations.append("LOW_TEMPORAL_CONFIDENCE")
    if structural_sufficiency == "RED":
        disabled_axes.extend(["graph", "engagement", "diversity"])
        limitations.append("LOW_STRUCTURAL_CONFIDENCE")

    # Temporal burst metrics
    time_items = [(c["ts"], c["comment_id"]) for c in normalized_comments if c.get("ts")]
    time_items.sort(key=lambda x: (x[0], x[1]))
    temporal = {
        "bin_seconds": BIN_SECONDS,
        "rate_spike_zscore_max": 0.0,
        "bins": 0,
        "burst_windows": [],
    }
    burst_comment_ids: List[str] = []
    if time_items and temporal_sufficiency != "RED":
        start_ts = time_items[0][0]
        bins: Dict[int, List[str]] = {}
        for ts, cid in time_items:
            idx = int((ts - start_ts).total_seconds() // BIN_SECONDS)
            bins.setdefault(idx, []).append(cid)
        bin_counts = [len(bins[idx]) for idx in sorted(bins)]
        median = _median(bin_counts)
        mad = _mad(bin_counts, median)
        denom = 1.4826 * mad if mad > 0 else 1.0
        zscores = [(idx, (len(bins[idx]) - median) / denom if denom > 0 else 0.0) for idx in sorted(bins)]
        temporal["bins"] = len(bins)
        temporal["rate_spike_zscore_max"] = max((z for _, z in zscores), default=0.0)
        windows = []
        for idx, z in zscores:
            count = len(bins[idx])
            if count < BURST_MIN_COUNT or z < BURST_Z_MIN:
                continue
            win_start = start_ts + timedelta(seconds=idx * BIN_SECONDS)
            win_end = win_start + timedelta(seconds=BIN_SECONDS)
            comment_ids = sorted(bins[idx])
            windows.append(
                {
                    "start_ts": win_start.isoformat(),
                    "end_ts": win_end.isoformat(),
                    "count": count,
                    "zscore": round(z, 4),
                    "comment_ids": comment_ids[:BURST_COMMENT_CAP],
                }
            )
        windows.sort(key=lambda w: (-w["zscore"], w["start_ts"]))
        temporal["burst_windows"] = windows[:BURST_WINDOW_CAP]
        for w in temporal["burst_windows"]:
            burst_comment_ids.extend(w.get("comment_ids") or [])
    burst_comment_ids = _cap_sorted(burst_comment_ids, BURST_COMMENT_CAP) if temporal_sufficiency != "RED" else []

    # Coordination proxy
    coord_events = []
    coord_comment_ids: List[str] = []
    coord_event_ids: List[str] = []
    if temporal_sufficiency != "RED":
        by_cluster: Dict[int, List[Dict[str, Any]]] = {}
        for c in normalized_comments:
            if not c.get("ts"):
                continue
            by_cluster.setdefault(int(c.get("cluster_key") or -1), []).append(c)
        for cluster_key, items in sorted(by_cluster.items(), key=lambda x: x[0]):
            items.sort(key=lambda x: (x["ts"], x["comment_id"]))
            start = 0
            while start < len(items):
                start_ts = items[start]["ts"]
                end = start
                while end < len(items) and items[end]["ts"] < start_ts + timedelta(seconds=COORD_WINDOW_SECONDS):
                    end += 1
                window_items = items[start:end]
                count = len(window_items)
                if count >= COORD_MIN_COMMENTS:
                    author_counts: Dict[str, int] = {}
                    for w in window_items:
                        author = w.get("author") or ""
                        author_counts[author] = author_counts.get(author, 0) + 1
                    unique_authors = len(author_counts)
                    entropy = _entropy_from_counts(author_counts.values())
                    top_author_share = max(author_counts.values()) / count if count else 0.0
                    if unique_authors >= COORD_MIN_AUTHORS and (
                        entropy <= COORD_ENTROPY_MAX or top_author_share >= COORD_TOP_AUTHOR_SHARE_MIN
                    ):
                        comment_ids = [w["comment_id"] for w in window_items]
                        coord_events.append(
                            {
                                "cluster_key": cluster_key,
                                "window_start": start_ts.isoformat(),
                                "window_end": (start_ts + timedelta(seconds=COORD_WINDOW_SECONDS)).isoformat(),
                                "comments_count": count,
                                "unique_authors": unique_authors,
                                "entropy": round(entropy, 4),
                                "top_author_share": round(top_author_share, 4),
                                "comment_ids": comment_ids,
                            }
                        )
                        coord_comment_ids.extend(comment_ids)
                        start = end
                        continue
                start += 1
        coord_events.sort(key=lambda e: (-e["comments_count"], e["window_start"], e["cluster_key"]))
        coord_events = coord_events[:COORD_EVENT_CAP]
        coord_comment_ids = _cap_sorted(coord_comment_ids, COORD_COMMENT_CAP)
        coord_event_ids = [
            _json_hash({"cluster_key": e["cluster_key"], "window_start": e["window_start"], "window_end": e["window_end"]})
            for e in coord_events
        ]

    # Graph anomalies
    in_deg: Dict[str, int] = {}
    out_deg: Dict[str, int] = {}
    node_ids = sorted({cid for cid in comment_ids_sorted})
    max_out = 0
    max_in = 0
    reciprocity = 0.0
    hub_dominance = 0.0
    scc_count = 0
    hub_nodes: List[Dict[str, Any]] = []
    anomalous_edges: List[Tuple[str, str]] = []
    graph_metrics = {"disabled": structural_sufficiency == "RED"}

    if structural_sufficiency != "RED":
        for parent, child in edges:
            out_deg[parent] = out_deg.get(parent, 0) + 1
            in_deg[child] = in_deg.get(child, 0) + 1
        max_out = max(out_deg.values(), default=0)
        max_in = max(in_deg.values(), default=0)
        out_vals = [v for v in out_deg.values() if v > 0]
        median_out = _median(out_vals) if out_vals else 0.0
        hub_dominance = max_out / max(1.0, median_out)
        edge_set = set(edges)
        reciprocity = (sum(1 for (u, v) in edge_set if (v, u) in edge_set) / edges_count) if edges_count else 0.0

        graph = {nid: [] for nid in node_ids}
        for parent, child in edges:
            graph.setdefault(parent, []).append(child)

        index = 0
        stack: List[str] = []
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on_stack: set[str] = set()

        def _strongconnect(node: str) -> None:
            nonlocal index, scc_count
            indices[node] = index
            lowlink[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)
            for neigh in graph.get(node, []):
                if neigh not in indices:
                    _strongconnect(neigh)
                    lowlink[node] = min(lowlink[node], lowlink[neigh])
                elif neigh in on_stack:
                    lowlink[node] = min(lowlink[node], indices[neigh])
            if lowlink[node] == indices[node]:
                component = []
                while stack:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == node:
                        break
                if len(component) > 1:
                    scc_count += 1

        for node in node_ids:
            if node not in indices:
                _strongconnect(node)

        hubs = sorted(
            [
                {
                    "comment_id": nid,
                    "out_degree": out_deg.get(nid, 0),
                    "in_degree": in_deg.get(nid, 0),
                }
                for nid in node_ids
            ],
            key=lambda h: (-h["out_degree"], -h["in_degree"], h["comment_id"]),
        )
        hub_nodes = hubs[:HUB_NODE_CAP]
        anomalous_edges = [(p, c) for p, c in edges if p in {h["comment_id"] for h in hub_nodes}]
        anomalous_edges = [(p, c) for p, c in anomalous_edges][:ANOMALOUS_EDGE_CAP]

        graph_metrics = {
            "node_count": len(node_ids),
            "edge_count": edges_count,
            "avg_in_degree": round((sum(in_deg.values()) / len(node_ids)) if node_ids else 0.0, 4),
            "avg_out_degree": round((sum(out_deg.values()) / len(node_ids)) if node_ids else 0.0, 4),
            "max_out_degree": max_out,
            "max_in_degree": max_in,
            "reciprocity": round(reciprocity, 4),
            "cycle_count_small": scc_count,
            "hub_dominance": round(hub_dominance, 4),
        }

    # Engagement metrics
    total_likes = sum(c["like_count"] for c in normalized_comments)
    total_replies = sum(c["reply_count"] for c in normalized_comments)
    like_to_reply_ratio_global = total_likes / max(1, total_replies)
    likes_by_author: Dict[str, int] = {}
    like_by_comment: List[Tuple[str, int]] = []
    cluster_like_ratio: Dict[int, float] = {}
    clusters_with_extreme_ratios: List[int] = []
    top_like_comment_ids: List[str] = []
    top_like_authors: List[Tuple[str, int]] = []
    like_concentration_gini = 0.0
    top_k_like_share = 0.0
    engagement_metrics = {"disabled": structural_sufficiency == "RED"}

    if structural_sufficiency != "RED":
        for c in normalized_comments:
            likes_by_author[c["author"]] = likes_by_author.get(c["author"], 0) + c["like_count"]
            like_by_comment.append((c["comment_id"], c["like_count"]))
        like_concentration_gini = _gini(likes_by_author.values())
        like_by_comment.sort(key=lambda x: (-x[1], x[0]))
        top_like_comment_ids = [cid for cid, _ in like_by_comment[:TOP_LIKE_COMMENT_CAP]]
        if total_likes > 0:
            top_k_like_share = sum(l for _, l in like_by_comment[:TOP_LIKE_COMMENT_CAP]) / total_likes
        top_like_authors = sorted(likes_by_author.items(), key=lambda x: (-x[1], x[0]))[:TOP_LIKE_AUTHOR_CAP]
        cluster_like_totals: Dict[int, int] = {}
        cluster_reply_totals: Dict[int, int] = {}
        for c in normalized_comments:
            ck = int(c.get("cluster_key") or -1)
            cluster_like_totals[ck] = cluster_like_totals.get(ck, 0) + c["like_count"]
            cluster_reply_totals[ck] = cluster_reply_totals.get(ck, 0) + c["reply_count"]
        for ck in sorted(cluster_like_totals.keys()):
            cluster_like_ratio[ck] = cluster_like_totals[ck] / max(1, cluster_reply_totals.get(ck, 0))
        clusters_with_extreme_ratios = [ck for ck, ratio in cluster_like_ratio.items() if ratio >= 5.0 and cluster_like_totals.get(ck, 0) >= 5]

        engagement_metrics = {
            "like_to_reply_ratio_global": round(like_to_reply_ratio_global, 4),
            "per_cluster_like_to_reply_ratio": {str(k): round(v, 4) for k, v in cluster_like_ratio.items()},
            "like_concentration_gini": round(like_concentration_gini, 4),
            "top_k_like_share": round(top_k_like_share, 4),
        }

    # Diversity metrics
    diversity_by_cluster: Dict[int, Dict[str, float]] = {}
    concentrated_clusters: List[int] = []
    diversity_metrics = {"disabled": structural_sufficiency == "RED"}
    if structural_sufficiency != "RED":
        for ck in sorted({c.get("cluster_key") for c in normalized_comments}):
            ck_int = int(ck or -1)
            authors = [c["author"] for c in normalized_comments if c["cluster_key"] == ck_int]
            counts: Dict[str, int] = {}
            for a in authors:
                counts[a] = counts.get(a, 0) + 1
            gini = _gini(counts.values())
            entropy = _entropy_from_counts(counts.values())
            effective = math.exp(entropy) if entropy > 0 else 0.0
            diversity_by_cluster[ck_int] = {
                "author_comment_gini": round(gini, 4),
                "author_entropy": round(entropy, 4),
                "effective_author_count": round(effective, 4),
            }
            if gini >= 0.6 and len(authors) >= 5:
                concentrated_clusters.append(ck_int)

        diversity_metrics = {
            "per_cluster": {str(k): v for k, v in diversity_by_cluster.items()},
            "concentrated_influence_clusters": concentrated_clusters,
        }

    # Scores
    temporal_score = _sigmoid(temporal.get("rate_spike_zscore_max", 0.0) - 2.0) if temporal_sufficiency != "RED" else 0.0
    coordination_score = min(1.0, len(coord_events) / 3.0) if temporal_sufficiency != "RED" else 0.0
    loopiness = graph_metrics.get("cycle_count_small", 0) / max(1, edges_count) if structural_sufficiency != "RED" else 0.0
    graph_score = (
        min(1.0, 0.6 * min(1.0, hub_dominance / 5.0) + 0.4 * min(1.0, loopiness * 5.0))
        if structural_sufficiency != "RED"
        else 0.0
    )
    engagement_score = (
        min(1.0, max(min(1.0, like_to_reply_ratio_global / 10.0), like_concentration_gini))
        if structural_sufficiency != "RED"
        else 0.0
    )
    diversity_score = (
        min(1.0, max((v.get("author_comment_gini") or 0.0) for v in diversity_by_cluster.values()) if diversity_by_cluster else 0.0)
        if structural_sufficiency != "RED"
        else 0.0
    )
    overall_behavior_risk = max(temporal_score, coordination_score, graph_score, engagement_score, diversity_score)

    scores = {
        "temporal_score": round(temporal_score, 4),
        "coordination_score": round(coordination_score, 4),
        "graph_score": round(graph_score, 4),
        "engagement_score": round(engagement_score, 4),
        "diversity_score": round(diversity_score, 4),
        "overall_behavior_risk": round(overall_behavior_risk, 4),
    }

    top_flags = [
        name
        for name, score in [
            ("temporal", temporal_score),
            ("coordination", coordination_score),
            ("graph", graph_score),
            ("engagement", engagement_score),
            ("diversity", diversity_score),
        ]
        if score >= 0.7
    ]

    evidence = {
        "burst_comment_ids": burst_comment_ids,
        "burst_windows": temporal.get("burst_windows") or [],
        "coordination_event_ids": coord_event_ids,
        "coordination_events": coord_events,
        "coordination_comment_ids": coord_comment_ids,
        "hub_nodes": hub_nodes,
        "anomalous_edges": [{"parent": p, "child": c} for p, c in anomalous_edges],
        "top_like_comment_ids": top_like_comment_ids if structural_sufficiency != "RED" else [],
        "top_like_authors": [{"author": a, "likes": v} for a, v in top_like_authors] if structural_sufficiency != "RED" else [],
        "clusters_with_extreme_ratios": clusters_with_extreme_ratios if structural_sufficiency != "RED" else [],
        "concentrated_influence_clusters": concentrated_clusters if structural_sufficiency != "RED" else [],
    }

    artifact = {
        "version": VERSION,
        "post_id": post_id,
        "cluster_run_id": cluster_run_id,
        "behavior_run_id": behavior_run_id,
        "reply_graph_id_space": reply_graph_id_space,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "inputs_fingerprint": {
            "comments_count": total_comments,
            "edges_count": edges_count,
            "missing_ts_pct": missing_ts_pct,
            "reply_graph_id_space": reply_graph_id_space,
            "ordering_key_hash": ordering_key_hash,
        },
        "quality_flags": {
            "missing_ts_pct": missing_ts_pct,
            "partial_tree": partial_tree,
            "edge_coverage": edge_coverage,
            "data_sufficiency": data_sufficiency,
        },
        "metrics": {
            "temporal": temporal if temporal_sufficiency != "RED" else {"disabled": True},
            "coordination": {
                "events_total": len(coord_events),
                "events": coord_events,
                "disabled": temporal_sufficiency == "RED",
            },
            "graph": graph_metrics,
            "engagement": engagement_metrics,
            "diversity": diversity_metrics,
        },
        "evidence": evidence,
        "scores": scores,
        "top_flags": top_flags,
        "sufficiency": {
            "temporal": temporal_sufficiency,
            "structural": structural_sufficiency,
        },
        "disabled_axes": disabled_axes,
        "limitations": limitations,
    }
    return artifact
