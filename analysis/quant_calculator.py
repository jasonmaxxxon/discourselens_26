import math
from typing import Any, Dict, List, Tuple

def _deterministic_comment_id(post_id: Any, comment: Dict[str, Any]) -> str:
    """
    Deterministic fallback when comment id is missing.
    Mirrors quant_engine fallback to keep IDs aligned.
    """
    import hashlib

    for key in ("id", "comment_id", "source_comment_id"):
        val = comment.get(key)
        if val is not None:
            return str(val)
    author = str(comment.get("author_handle") or comment.get("user") or comment.get("author") or "")
    text = " ".join(str(comment.get("text") or "").split()).strip()
    raw = f"{post_id}:{author}:{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_like(comment: Dict[str, Any]) -> int:
    try:
        return int(comment.get("like_count") or comment.get("likes") or 0)
    except Exception:
        return 0


def _safe_repost(comment: Dict[str, Any]) -> int:
    val = comment.get("repost_count_ui")
    if val is None:
        val = comment.get("reposts")
    try:
        return int(val or 0)
    except Exception:
        return 0


def _safe_share(comment: Dict[str, Any]) -> int:
    val = comment.get("share_count_ui")
    if val is None:
        val = comment.get("shares")
    try:
        return int(val or 0)
    except Exception:
        return 0


def _gini(values: List[float]) -> float:
    """
    Deterministic Gini over a list of non-negative values.
    """
    if not values:
        return 0.0
    n = len(values)
    sorted_vals = sorted(values)
    cum = 0.0
    cum_sum = 0.0
    for i, v in enumerate(sorted_vals, start=1):
        cum += v
        cum_sum += cum
    mean = sum(sorted_vals) / n
    if mean == 0:
        return 0.0
    # Standard discrete Gini formula
    gini = (n + 1 - 2 * (cum_sum / cum)) / n
    return round(gini, 6)


def _entropy(probabilities: List[float]) -> float:
    ent = 0.0
    for p in probabilities:
        if p <= 0:
            continue
        ent -= p * math.log(p)
    return round(ent, 6)


def _distance_to_centroid(comment: Dict[str, Any], centroid: Tuple[float, float]) -> float:
    try:
        x = float(comment.get("quant_x", 0.0))
        y = float(comment.get("quant_y", 0.0))
        return (x - centroid[0]) ** 2 + (y - centroid[1]) ** 2
    except Exception:
        return float("inf")


class QuantCalculator:
    """
    Deterministic quantitative metrics for clusters.
    Runs after clustering and before LLM.
    """

    @staticmethod
    def compute(
        post_id: Any,
        comments: List[Dict[str, Any]],
        top_liked_n: int = 3,
        representative_n: int = 2,
    ) -> Dict[str, Any]:
        # normalize ids + likes
        normalized: List[Dict[str, Any]] = []
        for c in comments or []:
            c = dict(c)
            c["comment_id"] = c.get("comment_id") or _deterministic_comment_id(post_id, c)
            c["like_count"] = _safe_like(c)
            c["repost_count_ui"] = _safe_repost(c)
            c["share_count_ui"] = _safe_share(c)
            normalized.append(c)

        # group by cluster
        clusters: Dict[int, List[Dict[str, Any]]] = {}
        for c in normalized:
            try:
                cid = int(c.get("cluster_key") if c.get("cluster_key") is not None else c.get("quant_cluster_id", -1))
            except Exception:
                cid = -1
            if cid < 0:
                continue
            clusters.setdefault(cid, []).append(c)

        total_comments = sum(len(v) for v in clusters.values())
        total_likes = sum(_safe_like(c) for clist in clusters.values() for c in clist)
        total_reposts = sum(_safe_repost(c) for clist in clusters.values() for c in clist)
        total_shares = sum(_safe_share(c) for clist in clusters.values() for c in clist)

        per_cluster_metrics: List[Dict[str, Any]] = []
        size_shares: List[Tuple[int, float]] = []
        like_shares: List[Tuple[int, float]] = []
        per_capita_likes: List[Tuple[int, float]] = []
        per_capita_reposts: List[Tuple[int, float]] = []
        per_capita_shares: List[Tuple[int, float]] = []
        repost_shares: List[Tuple[int, float]] = []
        share_shares: List[Tuple[int, float]] = []

        for cid, clist in sorted(clusters.items(), key=lambda kv: kv[0]):
            size = len(clist)
            like_sum = sum(_safe_like(c) for c in clist)
            repost_sum = sum(_safe_repost(c) for c in clist)
            share_sum = sum(_safe_share(c) for c in clist)
            size_share = (size / total_comments) if total_comments else 0.0
            like_share = (like_sum / total_likes) if total_likes else 0.0
            lpc = (like_sum / size) if size else 0.0
            rpc = (repost_sum / size) if size else 0.0
            spc = (share_sum / size) if size else 0.0
            repost_share = (repost_sum / total_reposts) if total_reposts else 0.0
            share_share = (share_sum / total_shares) if total_shares else 0.0
            per_cluster_metrics.append(
                {
                    "cluster_id": cid,
                    "size": size,
                    "size_share": round(size_share, 6),
                    "like_sum": like_sum,
                    "like_share": round(like_share, 6),
                    "likes_per_comment": round(lpc, 6),
                    "repost_sum": repost_sum,
                    "share_sum": share_sum,
                    "reposts_per_comment": round(rpc, 6),
                    "shares_per_comment": round(spc, 6),
                }
            )
            size_shares.append((cid, size_share))
            like_shares.append((cid, like_share))
            per_capita_likes.append((cid, lpc))
            per_capita_reposts.append((cid, rpc))
            per_capita_shares.append((cid, spc))
            repost_shares.append((cid, repost_share))
            share_shares.append((cid, share_share))

        like_share_values = [ls for _, ls in like_shares]
        gini_like_share = _gini(like_share_values)
        entropy_like_share = _entropy(like_share_values)
        dominance_ratio_top1 = round(max(like_share_values) if like_share_values else 0.0, 6)

        # minority dominance index (default threshold 0.5)
        sorted_by_like = sorted(per_cluster_metrics, key=lambda m: m["like_share"], reverse=True)
        cumulative = 0.0
        top_k = 0
        like_share_cum = 0.0
        size_share_cum = 0.0
        for item in sorted_by_like:
            cumulative += item["like_share"]
            like_share_cum += item["like_share"]
            size_share_cum += item["size_share"]
            top_k += 1
            if cumulative >= 0.5:
                break

        hard_metrics = {
            "n_comments": total_comments,
            "n_clusters": len(per_cluster_metrics),
            "cluster_size_share": [{"cluster_id": cid, "share": round(share, 6)} for cid, share in size_shares],
            "cluster_like_share": [{"cluster_id": cid, "share": round(share, 6)} for cid, share in like_shares],
            "per_capita_like": [{"cluster_id": cid, "value": round(val, 6)} for cid, val in per_capita_likes],
            "cluster_repost_share": [{"cluster_id": cid, "share": round(share, 6)} for cid, share in repost_shares],
            "cluster_share_share": [{"cluster_id": cid, "share": round(share, 6)} for cid, share in share_shares],
            "per_capita_repost": [{"cluster_id": cid, "value": round(val, 6)} for cid, val in per_capita_reposts],
            "per_capita_share": [{"cluster_id": cid, "value": round(val, 6)} for cid, val in per_capita_shares],
            "repost_total": total_reposts,
            "share_total": total_shares,
            "gini_like_share": gini_like_share,
            "entropy_like_share": entropy_like_share,
            "dominance_ratio_top1": dominance_ratio_top1,
            "minority_dominance_index": {
                "top_k_clusters": top_k,
                "like_share": round(like_share_cum, 6),
                "size_share": round(size_share_cum, 6),
            },
        }

        # evidence set: top liked + representative (nearest centroid by quant coords)
        evidence_set: List[Dict[str, Any]] = []
        for cid, clist in sorted(clusters.items(), key=lambda kv: kv[0]):
            # top liked
            top_liked = sorted(clist, key=lambda c: c["like_count"], reverse=True)[:top_liked_n]

            centroid = None
            try:
                xs = [float(c.get("quant_x", 0.0)) for c in clist]
                ys = [float(c.get("quant_y", 0.0)) for c in clist]
                if xs and ys:
                    centroid = (sum(xs) / len(xs), sum(ys) / len(ys))
            except Exception:
                centroid = None

            representative = []
            if centroid is not None:
                representative = sorted(clist, key=lambda c: _distance_to_centroid(c, centroid))[:representative_n]
            else:
                representative = clist[:representative_n]

            seen = set()
            combined: List[Dict[str, Any]] = []
            for candidate in top_liked + representative:
                cid_real = str(candidate.get("comment_id"))
                if cid_real in seen:
                    continue
                seen.add(cid_real)
                combined.append(
                    {
                        "comment_id": cid_real,
                        "cluster_id": cid,
                        "text": candidate.get("text") or "",
                        "like_count": candidate.get("like_count", 0),
                    }
                )
            evidence_set.append({"cluster_id": cid, "evidence": combined})

        return {
            "hard_metrics": hard_metrics,
            "per_cluster_metrics": per_cluster_metrics,
            "sampled_evidence_set": evidence_set,
        }

    @staticmethod
    def compute_from_bundle(
        post_id: Any,
        bundle: Dict[str, Any],
        top_liked_n: int = 3,
        representative_n: int = 2,
    ) -> Dict[str, Any]:
        if not isinstance(bundle, dict) or "comments" not in bundle:
            raise ValueError("QuantCalculator.compute_from_bundle expects CanonicalCommentBundleV1 dict")
        return QuantCalculator.compute(
            post_id,
            bundle.get("comments") or [],
            top_liked_n=top_liked_n,
            representative_n=representative_n,
        )
