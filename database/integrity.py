import logging
from typing import Iterable, Optional


logger = logging.getLogger("AssignmentIntegrity")


class AssignmentIntegrityError(RuntimeError):
    pass


# Semantic write allowlist: only these tables/columns may receive semantic artifacts.
SEMANTIC_WRITE_ALLOWLIST = {
    "threads_cluster_interpretations": {
        "label",
        "label_confidence",
        "label_unstable",
        "evidence_ids",
        "context_cards",
        "run_id",
        "prompt_hash",
        "model_name",
    },
    "threads_comment_clusters": {"label", "summary", "run_id"},
    "threads_cluster_diagnostics": {
        "run_id",
        "verdict",
        "k",
        "labels",
        "stability_avg",
        "stability_min",
        "drift_avg",
        "drift_max",
        "context_mode",
        "prompt_hash",
        "model_name",
    },
    "threads_claims": {
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
    },
    "threads_claim_evidence": {
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
    },
    "threads_claim_audits": {
        "post_id",
        "run_id",
        "build_id",
        "verdict",
        "dropped_claims_count",
        "kept_claims_count",
        "total_claims_count",
        "reasons",
    },
    "threads_behavior_audits": {
        "post_id",
        "cluster_run_id",
        "behavior_run_id",
        "reply_graph_id_space",
        "artifact_json",
        "quality_flags",
        "scores",
        "created_at",
    },
    "threads_risk_briefs": {
        "post_id",
        "cluster_run_id",
        "behavior_run_id",
        "risk_run_id",
        "brief_json",
        "created_at",
    },
    "threads_coverage_audits": {
        "post_id",
        "fetch_run_id",
        "captured_at",
        "expected_replies_ui",
        "unique_fetched",
        "coverage_ratio",
        "stop_reason",
        "budgets_used",
        "rounds_json",
        "rounds_hash",
    },
    "threads_reply_matrix_audits": {
        "post_id",
        "cluster_run_id",
        "reply_graph_id_space",
        "accounting_json",
        "created_at",
    },
}


def _norm_run_id(run_id: Optional[str]) -> str:
    return str(run_id or "").strip()


def _norm_table(table: str) -> str:
    return str(table or "").strip().lower()


def _format_context(context: Optional[dict]) -> str:
    if not context:
        return ""
    parts = []
    for key in ("post_id", "cluster_key", "caller", "table", "fields"):
        if key in context and context.get(key) is not None:
            parts.append(f"{key}={context.get(key)!r}")
    return (" " + " ".join(parts)) if parts else ""


def load_preanalysis_json(client, post_id: int | str) -> dict:
    try:
        resp = (
            client.table("threads_posts")
            .select("id, preanalysis_json")
            .eq("id", post_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise AssignmentIntegrityError(
            f"failed to fetch preanalysis_json for post_id={post_id}: {exc}"
        ) from exc
    rows = getattr(resp, "data", None) or []
    if not rows:
        return {}
    return rows[0].get("preanalysis_json") or {}


def assert_cluster_run_integrity(preanalysis_json: dict, run_id: Optional[str], context: Optional[dict] = None) -> None:
    got_run_id = _norm_run_id(run_id)
    meta = (preanalysis_json or {}).get("meta") or {}
    expected_run_id = _norm_run_id(meta.get("cluster_run_id"))
    ctx = _format_context(context)
    if not expected_run_id:
        raise AssignmentIntegrityError(
            f"cluster_run_id missing in preanalysis_json; got_run_id={got_run_id!r}{ctx}"
        )
    if not got_run_id or got_run_id != expected_run_id:
        raise AssignmentIntegrityError(
            f"run_id mismatch: expected_run_id={expected_run_id!r} got_run_id={got_run_id!r}{ctx}"
        )


def is_semantic_target_allowed(table: str, columns: Optional[Iterable[str]] = None) -> bool:
    table_key = _norm_table(table)
    if table_key not in SEMANTIC_WRITE_ALLOWLIST:
        return False
    if columns is None:
        return True
    allowed_cols = set(SEMANTIC_WRITE_ALLOWLIST.get(table_key) or set())
    cols = {str(c).strip() for c in columns if c}
    return cols.issubset(allowed_cols)


def assert_semantic_write_allowed(table: str, columns: Optional[Iterable[str]] = None, context: Optional[dict] = None) -> None:
    table_key = _norm_table(table)
    ctx = _format_context(context)
    if table_key not in SEMANTIC_WRITE_ALLOWLIST:
        raise AssignmentIntegrityError(
            f"semantic writes are forbidden for table={table!r}{ctx}"
        )
    if columns is None:
        return
    allowed_cols = set(SEMANTIC_WRITE_ALLOWLIST.get(table_key) or set())
    cols = {str(c).strip() for c in columns if c}
    forbidden = cols - allowed_cols
    if forbidden:
        raise AssignmentIntegrityError(
            f"semantic writes to table={table!r} include forbidden columns={sorted(forbidden)} "
            f"allowed={sorted(allowed_cols)}{ctx}"
        )


def guard_semantic_write(
    preanalysis_json: dict,
    run_id: Optional[str],
    table: str,
    fields: Iterable[str],
    context: Optional[dict] = None,
) -> None:
    # Order is fixed: run integrity -> allowlist.
    fields_list = sorted({str(f).strip() for f in fields if f})
    ctx = dict(context or {})
    ctx.setdefault("table", table)
    ctx.setdefault("fields", fields_list)
    assert_cluster_run_integrity(preanalysis_json, run_id, context=ctx)
    assert_semantic_write_allowed(table, fields_list, context=ctx)
