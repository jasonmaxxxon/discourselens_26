import hashlib
import os
import logging
import re
from collections import Counter
from typing import List, Dict, Any, Optional, Sequence, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics.pairwise import cosine_similarity

from analysis.v7.utils.text_preprocess import preprocess_for_embedding, PREPROCESS_VERSION
logger = logging.getLogger("QuantEngine")
PERSIST_ASSIGNMENTS = os.getenv("DL_PERSIST_ASSIGNMENTS", "0") == "1"
MIN_CLUSTER_SHARE_FOR_NAMING = float(os.getenv("DL_MIN_CLUSTER_SHARE_FOR_NAMING", "0.05"))
ASSIGNMENT_WRITE_MODE = (os.getenv("DL_ASSIGNMENT_WRITE_MODE") or "fill_nulls").lower()

_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        logger.info("Loading SentenceTransformer: paraphrase-multilingual-MiniLM-L12-v2")
        _embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _embedder


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    if mat.size == 0:
        return mat
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def _get_like_count(comment: Dict[str, Any]) -> int:
    try:
        return int(comment.get("like_count") or comment.get("likes") or 0)
    except Exception:
        return 0


def _normalize_text(val: str) -> str:
    return " ".join((val or "").split()).strip()


def _deterministic_comment_id(post_id: Optional[str | int], comment: Dict[str, Any]) -> str:
    """
    Mirror database.store._fallback_comment_id to keep cluster assignment ids aligned with DB rows.
    """
    for key in ("id", "source_comment_id", "comment_id"):
        val = comment.get(key)
        if val:
            return str(val)
    author = str(comment.get("author_handle") or comment.get("user") or comment.get("author") or "")
    text = _normalize_text(str(comment.get("text") or ""))
    raw = f"{post_id}:{author}:{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _top_keywords(texts: Sequence[str], top_n: int = 6) -> List[str]:
    tokens: List[str] = []
    for t in texts:
        if not t:
            continue
        found = re.findall(r"[A-Za-z0-9#@']{3,}", t.lower())
        tokens.extend(found)
    counter = Counter(tokens)
    return [w for w, _ in counter.most_common(top_n)]


def _centroid(vectors: Sequence[np.ndarray]) -> Optional[List[float]]:
    if not vectors:
        return None
    try:
        stacked = np.vstack(vectors)
        mean_vec = np.mean(stacked, axis=0)
        return [float(x) for x in mean_vec.tolist()]
    except Exception:
        return None


def _cluster_id(post_id: str | int | None, cluster_key: int | str) -> str:
    return f"{post_id}::c{cluster_key}"


def _hash_cluster_fingerprint(centroid_embedding_384: Optional[List[float]], keywords: List[str]) -> str:
    if centroid_embedding_384 and len(centroid_embedding_384) == 384:
        packed = ",".join(f"{float(x):.6f}" for x in centroid_embedding_384)
        return hashlib.sha256(packed.encode("utf-8")).hexdigest()
    key_payload = ",".join([str(k) for k in (keywords or [])])
    return hashlib.sha256(key_payload.encode("utf-8")).hexdigest()


def compute_battlefield_matrix(
    assignments: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
    *,
    edges_total_db: int | None = None,
    edges_db: Optional[List[Dict[str, Any]]] = None,
    post_root_internal_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deterministically compute cross-cluster reply matrix using parent linkage.
    """
    cluster_by_comment_id: Dict[str, int] = {}
    for a in assignments or []:
        cid = a.get("comment_id")
        if cid is None:
            continue
        try:
            cluster_by_comment_id[str(cid)] = int(a.get("cluster_key", -1))
        except Exception:
            cluster_by_comment_id[str(cid)] = -1

    id_map = {str(c.get("comment_id") or c.get("id") or "") for c in comments or [] if c.get("comment_id") or c.get("id")}
    source_map = {
        str(c.get("graph_node_id") or c.get("source_comment_id")): str(c.get("comment_id") or c.get("id"))
        for c in comments or []
        if (c.get("graph_node_id") or c.get("source_comment_id")) and (c.get("comment_id") or c.get("id"))
    }

    matrix: Dict[str, Dict[str, int]] = {}
    total_replies = 0
    orphans = 0
    n_roots = 0
    for c in comments or []:
        parent_source = c.get("graph_parent_id") or c.get("parent_source_comment_id")
        if not parent_source:
            n_roots += 1
            continue
        total_replies += 1
        parent_id = source_map.get(str(parent_source)) or (str(parent_source) if str(parent_source) in id_map else None)
        if not parent_id:
            orphans += 1
            continue
        child_id = str(c.get("comment_id") or c.get("id") or "")
        if not child_id:
            orphans += 1
            continue
        child_cluster = cluster_by_comment_id.get(child_id, -1)
        parent_cluster = cluster_by_comment_id.get(parent_id, -1)
        row = matrix.setdefault(str(child_cluster), {})
        row[str(parent_cluster)] = row.get(str(parent_cluster), 0) + 1

    top_flows: List[Dict[str, Any]] = []
    for child_cluster, targets in matrix.items():
        for parent_cluster, count in targets.items():
            top_flows.append(
                {
                    "from_cluster": int(child_cluster),
                    "to_cluster": int(parent_cluster),
                    "count": count,
                }
            )
    top_flows.sort(key=lambda item: item["count"], reverse=True)
    from analysis.reply_matrix_accounting import account_reply_matrix
    accounting = account_reply_matrix(
        comments=comments or [],
        id_map={k: True for k in id_map},
        source_map=source_map,
        edges_total_db=edges_total_db,
        edges_db=edges_db,
        post_root_internal_id=post_root_internal_id,
    )
    coverage_rate = round((total_replies - orphans) / total_replies, 6) if total_replies else 1.0
    return {
        "matrix": matrix,
        "top_flows": top_flows[:10],
        "health": {
            "total_replies": total_replies,
            "orphans": orphans,
            "coverage_rate": coverage_rate,
            "n_roots": n_roots,
        },
        "accounting": accounting,
    }


def perform_structure_mapping(comments_list: List[Dict[str, Any]], post_id: Optional[str | int] = None):
    """
    L0.5 Quantitative Structure Mapper.
    Enriches comments with quant fields, optionally persists cluster SoT, and returns clustering/similarity stats.
    """
    if os.getenv("DL_ASSERT_NO_QUANT", "0") == "1":
        raise RuntimeError("DL_ASSERT_NO_QUANT=1: quant disabled in narrative path")
    if not comments_list:
        logger.warning("No comments for quant analysis.")
        return None

    MIN_LEN = 5
    valid_indices = []
    valid_texts = []
    for idx, c in enumerate(comments_list):
        if "like_count" not in c:
            try:
                c["like_count"] = int(c.get("likes", 0))
            except Exception:
                c["like_count"] = 0
        text_norm = c.get("text_norm")
        text_val = (c.get("text_raw") or c.get("text") or "").strip()
        text_embed = text_norm if isinstance(text_norm, str) and text_norm.strip() else preprocess_for_embedding(text_val)
        if len(text_embed) >= MIN_LEN:
            valid_indices.append(idx)
            valid_texts.append(text_embed)

    if not valid_texts:
        logger.warning("Valid semantic comments too few after filtering.")
        return None

    try:
        embedder = get_embedder()
        embeddings = embedder.encode(valid_texts)
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return None
    model_id = (
        getattr(embedder, "model_card", None)
        or getattr(embedder, "model_name_or_path", None)
        or "paraphrase-multilingual-MiniLM-L12-v2"
    )
    embedding_meta = {
        "model_id": str(model_id),
        "preprocess_version": PREPROCESS_VERSION,
        "device": str(getattr(embedder, "_target_device", None) or getattr(embedder, "device", "")),
        "normalized": True,
    }
    embedding_lookup: Dict[str, np.ndarray] = {}
    try:
        norm_embeddings = _normalize_rows(np.array(embeddings))
        for idx, orig_idx in enumerate(valid_indices):
            cid = comments_list[orig_idx].get("comment_id") or comments_list[orig_idx].get("id")
            if cid:
                embedding_lookup[str(cid)] = norm_embeddings[idx]
    except Exception:
        embedding_lookup = {}

    # Dimensionality reduction with deterministic fallbacks
    count = len(valid_texts)
    if count == 1:
        coords = np.array([[0.0, 0.0]])
    elif 2 <= count < 5:
        coords = np.array([[float(i), 0.0] for i in range(count)])
    else:
        try:
            pca = PCA(n_components=2)
            coords = pca.fit_transform(embeddings)
        except Exception as e:
            logger.warning(f"PCA failed, using fallback coords: {e}")
            coords = np.array([[float(i), 0.0] for i in range(count)])

    # Clustering with rules
    if count < 3:
        labels = np.zeros(count, dtype=int)
        n_clusters = 1
    else:
        if 3 <= count <= 10:
            n_clusters = 2
        else:
            n_clusters = max(2, min(4, count // 8 or 2))
        try:
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init="auto")
            labels = kmeans.fit_predict(embeddings)
        except Exception as e:
            logger.warning(f"KMeans failed, fallback single cluster: {e}")
            labels = np.zeros(count, dtype=int)
            n_clusters = 1

    # Echo / template-like detection
    echo_indices = set()
    high_sim_pairs_count = 0
    if count >= 2:
        try:
            sim_mat = cosine_similarity(embeddings)
            for i in range(count):
                for j in range(i + 1, count):
                    is_high_sim = sim_mat[i, j] > 0.94
                    is_long_enough = len(valid_texts[i]) >= 8
                    user_i = comments_list[valid_indices[i]].get("user")
                    user_j = comments_list[valid_indices[j]].get("user")
                    is_diff_user = user_i and user_j and user_i != user_j
                    if is_high_sim and is_long_enough and is_diff_user:
                        echo_indices.add(valid_indices[i])
                        echo_indices.add(valid_indices[j])
                        high_sim_pairs_count += 1
        except Exception as e:
            logger.warning(f"Echo similarity computation failed: {e}")

    # Backfill quant fields
    for c in comments_list:
        c.setdefault("quant_cluster_id", -1)
        c.setdefault("quant_x", 0.0)
        c.setdefault("quant_y", 0.0)
        c.setdefault("is_template_like", False)

    for i, orig_idx in enumerate(valid_indices):
        cluster_id = int(labels[i]) if isinstance(labels[i], (int, np.integer)) else 0
        comments_list[orig_idx]["quant_cluster_id"] = cluster_id
        comments_list[orig_idx]["quant_x"] = round(float(coords[i][0]), 4) if coords.shape[1] > 0 else 0.0
        comments_list[orig_idx]["quant_y"] = round(float(coords[i][1]), 4) if coords.shape[1] > 1 else 0.0
        comments_list[orig_idx]["is_template_like"] = orig_idx in echo_indices

    cluster_stats: Dict[Any, int] = {}
    for lab in labels:
        lab_int = int(lab) if isinstance(lab, (int, np.integer)) else -1
        cluster_stats[lab_int] = cluster_stats.get(lab_int, 0) + 1

    # [NEW] Math Homogeneity (dominance ratio)
    total_clustered = sum(cluster_stats.values())
    if total_clustered > 0:
        dominant_count = max(cluster_stats.values())
        math_homogeneity = round(dominant_count / total_clustered, 2)
    else:
        math_homogeneity = 1.0
    clusters_payload: List[Dict[str, Any]] = []
    assignments: List[Dict[str, Any]] = []
    cluster_labels: Dict[int, str] = {}
    cluster_fingerprints: Dict[int, str] = {}
    total_comments = len(comments_list) if isinstance(comments_list, list) else 0

    label_to_members: Dict[int, List[Tuple[int, int]]] = {}
    for idx, lab in enumerate(labels):
        lab_int = int(lab) if isinstance(lab, (int, np.integer)) else 0
        label_to_members.setdefault(lab_int, []).append((idx, valid_indices[idx]))

    for lab_int, members in label_to_members.items():
        member_texts = [comments_list[orig_idx].get("text_raw") or comments_list[orig_idx].get("text") or "" for _, orig_idx in members]
        member_embeddings = [embeddings[i] for i, _ in members if embeddings is not None]
        top_ids_sorted = sorted(
            members,
            key=lambda pair: _get_like_count(comments_list[pair[1]]),
            reverse=True,
        )
        top_comment_ids = [
            _deterministic_comment_id(post_id, comments_list[orig_idx])
            for _, orig_idx in top_ids_sorted
        ]
        keywords = _top_keywords(member_texts)
        # enforce tactics list contract (no strings/nulls)
        tactics_val: List[str] = []
        if tactics_val is None:
            tactics_val = []
        if not isinstance(tactics_val, list):
            raise ValueError(f"tactics must be list for cluster {lab_int}")
        if not all(isinstance(t, str) for t in tactics_val):
            raise ValueError(f"tactics entries must be strings for cluster {lab_int}")
        centroid_embedding_384 = _centroid(member_embeddings)
        if len(members) >= 2:
            if (
                centroid_embedding_384 is None
                or not isinstance(centroid_embedding_384, list)
                or len(centroid_embedding_384) != 384
            ):
                raise RuntimeError(
                    f"Centroid computation failed dim={None if centroid_embedding_384 is None else len(centroid_embedding_384)} "
                    f"cluster={lab_int} post={post_id}"
                )
        share = round(len(members) / total_comments, 4) if total_comments else 0.0
        if share < MIN_CLUSTER_SHARE_FOR_NAMING:
            label = "Other/Noise"
            summary = "low-support cluster"
        else:
            label = f"Cluster {lab_int}"
            summary = None
        cluster_labels[lab_int] = label
        cluster_fingerprints[lab_int] = _hash_cluster_fingerprint(centroid_embedding_384, keywords)
        clusters_payload.append(
            {
                "cluster_key": lab_int,
                "label": label,
                "summary": summary,
                "size": len(members),
                "size_share": share,
                "keywords": keywords,
                "top_comment_ids": top_comment_ids[:5],
                "centroid_embedding_384": centroid_embedding_384,
                "tactics": tactics_val,
                "cluster_fingerprint": cluster_fingerprints[lab_int],
            }
        )

    # Build assignments (idempotent-friendly payload for DB)
    for c in comments_list:
        key_raw = c.get("quant_cluster_id")
        try:
            key_int = int(key_raw)
        except Exception:
            key_int = -1
        if key_int < 0:
            key_int = -1
        comment_id = _deterministic_comment_id(post_id, c)
        assignment_cluster_id = _cluster_id(post_id, key_int) if (post_id is not None and key_int >= 0) else None
        label = cluster_labels.get(key_int) if key_int >= 0 else None
        fingerprint = cluster_fingerprints.get(key_int)
        assignments.append(
            {
                "comment_id": comment_id,
                "cluster_key": key_int,
                "cluster_label": label,
                "cluster_id": assignment_cluster_id,
                "cluster_fingerprint": fingerprint,
            }
        )
        if assignment_cluster_id:
            c["cluster_id"] = assignment_cluster_id
        if label:
            c["cluster_label"] = label

    cluster_run_id = _cluster_run_id(post_id, assignments)

    persistence = {
        "clusters": {"ok": False, "skipped": True},
        "assignments": {"ok": False, "skipped": True},
    }

    if post_id is not None and clusters_payload:
        post_id_for_db = post_id
        try:
            post_id_for_db = int(post_id)
        except Exception:
            post_id_for_db = post_id

        from database.store import (
            apply_comment_cluster_assignments,
            upsert_comment_clusters,
        )

        cluster_res = upsert_comment_clusters(post_id_for_db, clusters_payload)
        persistence["clusters"] = cluster_res

        if PERSIST_ASSIGNMENTS:
            assign_res = apply_comment_cluster_assignments(
                post_id_for_db,
                assignments,
                unassignable_total=0,
                cluster_run_id=cluster_run_id,
                cluster_fingerprints=cluster_fingerprints,
            )
        else:
            assign_res = {"ok": False, "skipped": True, "reason": "DL_PERSIST_ASSIGNMENTS=0"}
        persistence["assignments"] = assign_res

        logger.info(
            f"[QuantEngine] persistence summary post={post_id} "
            f"clusters_attempted={len(clusters_payload)} clusters_ok={cluster_res.get('ok')} "
            f"assignments_attempted={len(assignments)} assignments_ok={assign_res.get('ok')} skipped_assignments={assign_res.get('skipped')}"
        )
        if not cluster_res.get("ok") or (PERSIST_ASSIGNMENTS and not assign_res.get("ok")):
            raise RuntimeError(
                f"[QuantEngine] Cluster persistence degraded post={post_id} "
                f"clusters_ok={cluster_res.get('ok')} assignments_ok={assign_res.get('ok')}"
            )

    return {
        "node_data": comments_list,
        "cluster_stats": cluster_stats,
        "high_sim_pairs": high_sim_pairs_count,
        "math_homogeneity": math_homogeneity,
        "clusters": clusters_payload,
        "assignments": assignments,
        "cluster_fingerprints": cluster_fingerprints,
        "clusters_ref": {"k": len(clusters_payload), "n_clusters": n_clusters},
        "persistence": persistence,
        "cluster_run_id": cluster_run_id,
        "embedding_lookup": embedding_lookup,
        "embedding_meta": embedding_meta,
    }


def _cluster_run_id(post_id: Optional[str | int], assignments: List[Dict[str, Any]]) -> str:
    pairs = sorted((str(a.get("comment_id")), int(a.get("cluster_key", -1))) for a in assignments)
    payload = f"{post_id}|{pairs}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def perform_structure_mapping_bundle(bundle: Dict[str, Any], post_id: Optional[str | int] = None):
    if not isinstance(bundle, dict) or "comments" not in bundle:
        raise ValueError("perform_structure_mapping_bundle expects CanonicalCommentBundleV1 dict")
    return perform_structure_mapping(bundle.get("comments") or [], post_id=post_id)
