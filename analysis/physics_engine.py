import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from analysis.quant_engine import compute_battlefield_matrix
from analysis.v7.quant.utils.embedding import encode_texts
from analysis.v7.utils.text_preprocess import preprocess_for_embedding


def _comment_id(comment: Dict[str, Any]) -> str:
    return str(comment.get("comment_id") or comment.get("id") or "")


def _comment_text(comment: Dict[str, Any]) -> str:
    raw = comment.get("text_norm") or comment.get("text_raw") or comment.get("text") or ""
    return preprocess_for_embedding(str(raw))


def _comment_likes(comment: Dict[str, Any]) -> int:
    try:
        return int(comment.get("like_count") or comment.get("likes") or 0)
    except Exception:
        return 0


def _comment_replies(comment: Dict[str, Any]) -> int:
    try:
        return int(comment.get("reply_count") or 0)
    except Exception:
        return 0


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    if mat.size == 0:
        return mat
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def _pairwise_mean_cosine(embeddings: np.ndarray) -> Optional[float]:
    if embeddings.shape[0] < 2:
        return None
    sims = embeddings.dot(embeddings.T)
    tri = sims[np.triu_indices(sims.shape[0], k=1)]
    if tri.size == 0:
        return None
    return float(np.mean(tri))


def _pairwise_ratio_above(embeddings: np.ndarray, threshold: float) -> Optional[float]:
    if embeddings.shape[0] < 2:
        return None
    sims = embeddings.dot(embeddings.T)
    tri = sims[np.triu_indices(sims.shape[0], k=1)]
    if tri.size == 0:
        return None
    return float(np.mean(tri > threshold))


def _sample_indices(n: int, k: int, rng: random.Random) -> List[int]:
    if n <= k:
        return list(range(n))
    return rng.sample(list(range(n)), k)


def _cross_cluster_reply_counts(
    comments: List[Dict[str, Any]],
    assignments: List[Dict[str, Any]],
) -> Dict[str, int]:
    cluster_by_comment_id: Dict[str, int] = {}
    for a in assignments or []:
        cid = a.get("comment_id")
        if cid is None:
            continue
        try:
            cluster_by_comment_id[str(cid)] = int(a.get("cluster_key", -1))
        except Exception:
            cluster_by_comment_id[str(cid)] = -1

    source_map = {
        str(c.get("source_comment_id")): str(c.get("comment_id") or c.get("id"))
        for c in comments or []
        if c.get("source_comment_id") and (c.get("comment_id") or c.get("id"))
    }
    cross_counts: Dict[str, int] = {}
    for c in comments or []:
        parent_source = c.get("parent_source_comment_id")
        if not parent_source:
            continue
        parent_id = source_map.get(str(parent_source))
        child_id = _comment_id(c)
        if not parent_id or not child_id:
            continue
        parent_cluster = cluster_by_comment_id.get(parent_id, -1)
        child_cluster = cluster_by_comment_id.get(child_id, -1)
        if parent_cluster == -1 or child_cluster == -1:
            continue
        if parent_cluster != child_cluster:
            cross_counts[child_id] = cross_counts.get(child_id, 0) + 1
            cross_counts[parent_id] = cross_counts.get(parent_id, 0) + 1
    return cross_counts


def _choose_unique(candidate_ids: List[str], taken: set) -> Optional[str]:
    for cid in candidate_ids:
        if cid and cid not in taken:
            return cid
    return None


def compute_physics_and_golden_samples(
    *,
    comments: List[Dict[str, Any]],
    quant_result: Optional[Dict[str, Any]],
    quant_calc_data: Optional[Dict[str, Any]],
    reply_matrix: Optional[Dict[str, Any]],
    embedding_bundle: Optional[Dict[str, Any]] = None,
    seed: int = 42,
    max_samples_per_cluster: int = 200,
    top_k_pairs: int = 20,
    echo_threshold: float = 0.92,
    stability_runs: int = 3,
    stability_ratio: float = 0.7,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    rng = random.Random(seed)
    assignments = (quant_result or {}).get("assignments") or []
    clusters = (quant_result or {}).get("clusters") or []

    embedding_lookup: Dict[str, np.ndarray] = {}
    embedding_model_id = None
    embedding_config_hash = None
    if isinstance(embedding_bundle, dict) and embedding_bundle.get("lookup"):
        embedding_lookup = {
            str(k): v
            for k, v in (embedding_bundle.get("lookup") or {}).items()
            if v is not None
        }
        embedding_model_id = embedding_bundle.get("model_id")
        embedding_config_hash = embedding_bundle.get("config_hash")
    else:
        comment_ids: List[str] = []
        texts: List[str] = []
        for c in comments or []:
            cid = _comment_id(c)
            if not cid:
                continue
            text = _comment_text(c)
            if not text:
                continue
            comment_ids.append(cid)
            texts.append(text)

        embeddings = np.array([])
        if texts:
            embeddings, embedding_model_id, _, embedding_config_hash = encode_texts(texts, normalize_embeddings=True)
        if getattr(embeddings, "size", 0):
            embeddings = _normalize_rows(embeddings)
            for idx, cid in enumerate(comment_ids):
                embedding_lookup[cid] = embeddings[idx]

    cluster_to_comment_ids: Dict[int, List[str]] = {}
    for a in assignments:
        cid = a.get("comment_id")
        if cid is None:
            continue
        try:
            cluster_key = int(a.get("cluster_key", -1))
        except Exception:
            cluster_key = -1
        cluster_to_comment_ids.setdefault(cluster_key, []).append(str(cid))

    reply_matrix = reply_matrix or compute_battlefield_matrix(assignments, comments)
    reply_meta = (reply_matrix.get("meta") or {}) if isinstance(reply_matrix, dict) else {}
    id_space = "source" if any(c.get("source_comment_id") for c in comments or []) else "internal"
    health = reply_matrix.get("health") if isinstance(reply_matrix, dict) else {}
    total_replies = (health or {}).get("total_replies") or 0
    coverage_rate = (health or {}).get("coverage_rate") or 0
    reply_meta["id_space"] = id_space
    if total_replies and coverage_rate > 0:
        reply_meta["status"] = "available"
    else:
        reply_meta["status"] = "unavailable"
        reply_meta["reason"] = "no_reply_edges_or_insufficient_coverage"
    if isinstance(reply_matrix, dict):
        reply_matrix["meta"] = reply_meta

    physics: Dict[str, Any] = {
        "cluster_homogeneity": {},
        "cross_cluster_distance": {"closest": [], "farthest": []},
        "reply_matrix": reply_matrix,
        "dominance": {"clusters": {}, "dominance_ratio": None, "total_likes": 0},
        "echo": {"clusters": {}, "method": "pairwise_cosine", "threshold": echo_threshold},
        "stability": {"runs": stability_runs, "metric": "jaccard_subsample_overlap", "score": None},
        "meta": {
            "schema_version": "v1",
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "params": {
                "max_samples_per_cluster": max_samples_per_cluster,
                "top_k_pairs": top_k_pairs,
                "echo_threshold": echo_threshold,
                "stability_runs": stability_runs,
                "stability_ratio": stability_ratio,
                "embedding_model_id": embedding_model_id,
                "embedding_config_hash": embedding_config_hash,
            },
        },
    }

    for cluster_key, cid_list in cluster_to_comment_ids.items():
        emb_list = [embedding_lookup.get(cid) for cid in cid_list if cid in embedding_lookup]
        if not emb_list:
            continue
        sample_rng = random.Random(seed + int(cluster_key))
        idxs = _sample_indices(len(emb_list), max_samples_per_cluster, sample_rng)
        sample = np.array([emb_list[i] for i in idxs])
        homogeneity = _pairwise_mean_cosine(sample)
        if homogeneity is not None:
            physics["cluster_homogeneity"][str(cluster_key)] = homogeneity
        echo_ratio = _pairwise_ratio_above(sample, echo_threshold)
        if echo_ratio is not None:
            physics["echo"]["clusters"][str(cluster_key)] = echo_ratio

    centroid_lookup: Dict[int, np.ndarray] = {}
    for c in clusters:
        try:
            ck = int(c.get("cluster_key"))
        except Exception:
            continue
        vec = c.get("centroid_embedding_384")
        if isinstance(vec, list) and vec:
            arr = np.array(vec, dtype=float)
            if arr.size:
                centroid_lookup[ck] = arr / (np.linalg.norm(arr) or 1.0)

    pairs: List[Dict[str, Any]] = []
    cluster_keys = sorted(centroid_lookup.keys())
    for i, a in enumerate(cluster_keys):
        for b in cluster_keys[i + 1 :]:
            va = centroid_lookup.get(a)
            vb = centroid_lookup.get(b)
            if va is None or vb is None:
                continue
            sim = float(np.dot(va, vb))
            dist = float(1.0 - sim)
            pairs.append({"a": a, "b": b, "distance": dist})
    if pairs:
        pairs_sorted = sorted(pairs, key=lambda p: p["distance"])
        physics["cross_cluster_distance"]["closest"] = pairs_sorted[:top_k_pairs]
        physics["cross_cluster_distance"]["farthest"] = list(reversed(pairs_sorted[-top_k_pairs:]))

    likes_by_cluster: Dict[int, int] = {}
    replies_by_cluster: Dict[int, int] = {}
    counts_by_cluster: Dict[int, int] = {}
    for c in comments or []:
        try:
            ck = int(c.get("cluster_key", c.get("quant_cluster_id", -1)))
        except Exception:
            ck = -1
        cid = _comment_id(c)
        if not cid:
            continue
        likes_by_cluster[ck] = likes_by_cluster.get(ck, 0) + _comment_likes(c)
        replies_by_cluster[ck] = replies_by_cluster.get(ck, 0) + _comment_replies(c)
        counts_by_cluster[ck] = counts_by_cluster.get(ck, 0) + 1

    total_likes = sum(likes_by_cluster.values())
    dominance_ratio = None
    if total_likes:
        dominance_ratio = max(likes_by_cluster.values()) / total_likes
    physics["dominance"]["total_likes"] = total_likes
    physics["dominance"]["dominance_ratio"] = dominance_ratio
    for ck, count in counts_by_cluster.items():
        likes = likes_by_cluster.get(ck, 0)
        replies = replies_by_cluster.get(ck, 0)
        likes_per_comment = (likes / count) if count else 0
        physics["dominance"]["clusters"][str(ck)] = {
            "likes_sum": likes,
            "replies_sum": replies,
            "likes_per_comment": likes_per_comment,
        }

    if stability_runs >= 2 and cluster_to_comment_ids:
        run_sets: List[Dict[int, set]] = []
        all_comment_ids = sorted({cid for ids in cluster_to_comment_ids.values() for cid in ids})
        for run in range(stability_runs):
            run_rng = random.Random(seed + 100 + run)
            sample_size = max(1, int(len(all_comment_ids) * stability_ratio))
            sampled = set(run_rng.sample(all_comment_ids, min(sample_size, len(all_comment_ids))))
            cluster_sets: Dict[int, set] = {}
            for ck, ids in cluster_to_comment_ids.items():
                cluster_sets[ck] = set(ids) & sampled
            run_sets.append(cluster_sets)
        scores: List[float] = []
        for i in range(len(run_sets)):
            for j in range(i + 1, len(run_sets)):
                for ck in cluster_to_comment_ids.keys():
                    a = run_sets[i].get(ck, set())
                    b = run_sets[j].get(ck, set())
                    union = a | b
                    if not union:
                        continue
                    scores.append(len(a & b) / len(union))
        if scores:
            physics["stability"]["score"] = float(np.mean(scores))

    golden_samples: Dict[str, Any] = {}
    golden_samples_detail: Dict[str, Any] = {}
    golden_meta: Dict[str, Any] = {
        "seed": seed,
        "rules": {
            "central": "min_distance_to_centroid",
            "leader": "max_like_count_then_reply_count",
            "radical": "max_distance_to_centroid",
            "random": "seeded_choice",
            "counter": "lowest_similarity_not_radical_or_fallback",
            "bridge": "max_cross_cluster_replies_or_fallback",
        },
        "fallbacks": {},
    }

    cross_reply_counts = _cross_cluster_reply_counts(comments, assignments)
    if not cross_reply_counts:
        golden_meta["fallbacks"]["bridge"] = "no_reply_edges_or_insufficient_coverage"
    comment_lookup = {_comment_id(c): c for c in comments or [] if _comment_id(c)}
    for ck, ids in cluster_to_comment_ids.items():
        taken: set = set()
        fallbacks: List[str] = []
        cluster_centroid = centroid_lookup.get(ck)
        with_embeddings = [(cid, embedding_lookup.get(cid)) for cid in ids if cid in embedding_lookup]

        distances: List[Tuple[str, float]] = []
        if cluster_centroid is not None:
            for cid, emb in with_embeddings:
                if emb is None:
                    continue
                dist = float(1.0 - np.dot(cluster_centroid, emb))
                distances.append((cid, dist))
            distances.sort(key=lambda t: t[1])

        central = distances[0][0] if distances else None
        radical = distances[-1][0] if distances else None
        if central:
            taken.add(central)
        if radical:
            taken.add(radical)

        leader = None
        if ids:
            sorted_by_like = sorted(ids, key=lambda cid: _comment_likes(comment_lookup.get(cid, {})), reverse=True)
            leader = _choose_unique(sorted_by_like, taken)
            if not leader:
                sorted_by_reply = sorted(ids, key=lambda cid: _comment_replies(comment_lookup.get(cid, {})), reverse=True)
                leader = _choose_unique(sorted_by_reply, taken)
            if not leader and central:
                leader = central
                fallbacks.append("leader_fallback_to_central")
        if leader:
            taken.add(leader)

        counter = None
        if distances:
            reverse = list(reversed(distances))
            counter = _choose_unique([cid for cid, _ in reverse], taken)
        if not counter and ids:
            counter = _choose_unique(ids, taken)
            if counter:
                fallbacks.append("counter_fallback_to_random")
        if counter:
            taken.add(counter)

        rand_choice = None
        if ids:
            rng_local = random.Random(seed + int(ck) + 999)
            rand_choice = rng_local.choice(ids)
            if rand_choice in taken and len(ids) > 1:
                rand_choice = _choose_unique(ids, taken) or rand_choice
        if rand_choice:
            taken.add(rand_choice)

        bridge = None
        if ids:
            ids_by_cross = sorted(ids, key=lambda cid: cross_reply_counts.get(cid, 0), reverse=True)
            bridge = _choose_unique(ids_by_cross, taken)
            if not bridge and ids:
                bridge = _choose_unique(ids, taken)
                if bridge:
                    fallbacks.append("bridge_fallback_to_random")

        samples = {
            "central": central,
            "leader": leader,
            "radical": radical,
            "random": rand_choice,
            "counter": counter,
            "bridge": bridge,
        }
        golden_samples[str(ck)] = samples
        detail: Dict[str, Any] = {}
        for key, cid in samples.items():
            if not cid:
                detail[key] = None
                continue
            row = comment_lookup.get(str(cid)) or {}
            detail[key] = {
                "comment_id": str(cid),
                "text_raw": row.get("text_raw") or row.get("text") or "",
                "like_count": _comment_likes(row),
                "reply_count": _comment_replies(row),
                "source_locator": row.get("source_locator"),
            }
        golden_samples_detail[str(ck)] = detail
        if fallbacks:
            golden_meta["fallbacks"][str(ck)] = fallbacks

    golden_samples["golden_samples_meta"] = golden_meta
    golden_samples_detail["golden_samples_meta"] = golden_meta

    return physics, golden_samples, golden_samples_detail
