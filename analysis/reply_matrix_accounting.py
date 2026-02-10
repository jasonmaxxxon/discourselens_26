from typing import Any, Dict, List, Optional


def account_reply_matrix(
    *,
    comments: List[Dict[str, Any]],
    id_map: Dict[str, bool],
    source_map: Dict[str, str],
    edges_total_db: Optional[int] = None,
    edges_db: Optional[List[Dict[str, Any]]] = None,
    post_root_internal_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pure accounting pass over reply linkage without changing any behaviors.
    Captures why replies were dropped (missing parent/child) and how many edges
    from DB are not represented in the comment bundle.
    """
    bundle_comment_count = len(comments or [])
    candidate_edges = 0
    used_edges = 0
    orphan_parent = 0
    orphan_child = 0
    invalid_child = 0

    for c in comments or []:
        parent_source = c.get("graph_parent_id") or c.get("parent_source_comment_id")
        if not parent_source:
            continue
        candidate_edges += 1
        parent_id = source_map.get(str(parent_source)) or (str(parent_source) if str(parent_source) in id_map else None)
        if not parent_id:
            orphan_parent += 1
            continue
        child_id = str(c.get("comment_id") or c.get("id") or "")
        if not child_id:
            orphan_child += 1
            invalid_child += 1
            continue
        used_edges += 1

    edges_db_list = edges_db or []
    edges_total_db_val = edges_total_db
    if edges_total_db_val is None and edges_db_list:
        edges_total_db_val = len(edges_db_list)

    root_reply_edges_total = 0
    reply_on_reply_edges_total = 0
    root_children: set[str] = set()
    reply_children: set[str] = set()
    root_id = str(post_root_internal_id) if post_root_internal_id is not None else None
    if edges_db_list:
        for edge in edges_db_list:
            if not isinstance(edge, dict):
                continue
            parent_val = edge.get("parent_comment_id") or edge.get("parent_source_comment_id")
            child_val = edge.get("child_comment_id") or edge.get("child_source_comment_id")
            if not parent_val or not child_val:
                continue
            parent_str = str(parent_val)
            child_str = str(child_val)
            if root_id is not None:
                is_root = parent_str == root_id
            else:
                is_root = parent_str not in id_map
            if is_root:
                root_reply_edges_total += 1
                root_children.add(child_str)
            else:
                reply_on_reply_edges_total += 1
                reply_children.add(child_str)

    edges_db_missing_total = None
    ghost_edge_rate = None
    if edges_total_db_val is not None:
        try:
            edges_db_missing_total = max(int(edges_total_db_val) - int(candidate_edges), 0)
            if int(edges_total_db_val) > 0:
                ghost_edge_rate = round(edges_db_missing_total / int(edges_total_db_val), 6)
            else:
                ghost_edge_rate = 0.0
        except Exception:
            edges_db_missing_total = None
            ghost_edge_rate = None

    return {
        "edges_total_db": int(edges_total_db_val) if edges_total_db_val is not None else None,
        "root_reply_edges_total": root_reply_edges_total,
        "root_reply_unique_children": len(root_children),
        "reply_on_reply_edges_total": reply_on_reply_edges_total,
        "reply_on_reply_unique_children": len(reply_children),
        "edges_candidate": candidate_edges,
        "edges_used": used_edges,
        "bundle_comment_count": bundle_comment_count,
        "edges_dropped_by_reason": {
            "parent_not_found": orphan_parent,
            "child_missing": orphan_child,
            "invalid_child": invalid_child,
        },
        "id_map_size": len(id_map),
        "source_map_size": len(source_map),
        "edges_db_missing_total": edges_db_missing_total,
        "ghost_edge_rate": ghost_edge_rate,
    }
