import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from analysis.axis_sanitize import sanitize_analysis_json
from analysis.behavior_budget import build_behavior_evidence_budget
from analysis.behavior_sidechannel import compute_behavior_sidechannel
from analysis.risk_composer_min import compose_risk_brief
from analysis.build_analysis_json import build_and_validate_analysis_json, protect_core_fields, safe_dump
from analysis.physics_engine import compute_physics_and_golden_samples
from analysis.quant_calculator import QuantCalculator
from analysis.quant_engine import compute_battlefield_matrix, perform_structure_mapping_bundle
from database.store import (
    apply_comment_cluster_assignments,
    get_canonical_comment_bundle,
    save_behavior_audit,
    save_risk_brief,
    save_reply_matrix_audit,
    supabase,
)


PREANALYSIS_VERSION = "preanalysis_v1"
DEFAULT_SEED = 42
logger = logging.getLogger("PreanalysisRunner")


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    return value


def _reply_matrix_with_meta(
    assignments: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
    *,
    edges_total_db: int | None = None,
    edges_db: Optional[List[Dict[str, Any]]] = None,
    post_root_internal_id: Optional[str] = None,
) -> Dict[str, Any]:
    reply_matrix = compute_battlefield_matrix(
        assignments=assignments,
        comments=comments,
        edges_total_db=edges_total_db,
        edges_db=edges_db,
        post_root_internal_id=post_root_internal_id,
    ) or {}
    health = reply_matrix.get("health") if isinstance(reply_matrix, dict) else {}
    total_replies = (health or {}).get("total_replies") or 0
    coverage_rate = (health or {}).get("coverage_rate") or 0
    id_space = "internal"
    meta = {
        "id_space": id_space,
        "status": "available" if total_replies and coverage_rate > 0 else "unavailable",
    }
    if meta["status"] == "unavailable":
        meta["reason"] = "no_reply_edges_or_insufficient_coverage"
    if isinstance(reply_matrix, dict):
        reply_matrix["meta"] = meta
    return reply_matrix


def _build_analysis_skeleton(
    *,
    post_data: Dict[str, Any],
    bundle: Dict[str, Any],
    quant_result: Dict[str, Any],
    quant_calc_data: Dict[str, Any],
    reply_matrix: Dict[str, Any],
) -> Dict[str, Any]:
    analysis_v4 = build_and_validate_analysis_json(
        post_data=post_data,
        llm_data={},
        cluster_data={},
        full_report=None,
    )
    analysis_v4 = protect_core_fields(post_data, analysis_v4)
    analysis_payload = safe_dump(analysis_v4)
    hard_metrics = dict((quant_calc_data or {}).get("hard_metrics") or {})
    hard_metrics["tree_metrics"] = bundle.get("tree_metrics") or {}
    analysis_payload["hard_metrics"] = hard_metrics
    analysis_payload["per_cluster_metrics"] = (quant_calc_data or {}).get("per_cluster_metrics") or []
    analysis_payload["reply_matrix"] = reply_matrix
    meta = analysis_payload.get("meta") or {}
    meta.update(
        {
            "bundle_version": bundle.get("bundle_version"),
            "bundle_id": bundle.get("bundle_id"),
            "ordering_rule": bundle.get("ordering_rule"),
            "ordering_key_hash": bundle.get("ordering_key_hash"),
            "tree_repair_status": bundle.get("tree_repair_status"),
            "cluster_run_id": (quant_result or {}).get("cluster_run_id"),
            "cluster_fingerprint": (quant_result or {}).get("cluster_fingerprints") or {},
            "stage": "skeleton",
            "narrative_status": "pending",
        }
    )
    analysis_payload["meta"] = meta
    return sanitize_analysis_json(analysis_payload)


def _update_preanalysis_status(post_id: int, payload: Dict[str, Any], status: str, version: str) -> None:
    supabase.table("threads_posts").update(
        {
            "preanalysis_json": _to_json_safe(payload),
            "preanalysis_status": status,
            "preanalysis_version": version,
            "preanalysis_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", post_id).execute()


def run_preanalysis(post_id: int, prefer_sot: bool = True, persist_assignments: bool = True) -> Dict[str, Any]:
    post_resp = (
        supabase.table("threads_posts")
        .select("id, url, post_text, author, images, like_count, reply_count, view_count, created_at, captured_at, analysis_json")
        .eq("id", post_id)
        .limit(1)
        .execute()
    )
    post_row = (getattr(post_resp, "data", None) or [None])[0]
    if not post_row:
        raise RuntimeError(f"post_id {post_id} not found in threads_posts")

    bundle = get_canonical_comment_bundle(post_id, prefer_sot=prefer_sot)
    comments = bundle.get("comments") or []
    if not comments:
        payload = {
            "version": PREANALYSIS_VERSION,
            "meta": {
                "post_id": post_id,
                "bundle_id": bundle.get("bundle_id"),
                "bundle_version": bundle.get("bundle_version"),
                "computed_at": datetime.now(timezone.utc).isoformat(),
                "seed": DEFAULT_SEED,
                "harvest_coverage_summary": {
                    "comments_total": 0,
                    "source_comment_id_coverage": 0,
                    "parent_source_comment_id_coverage": 0,
                    "reply_graph_available": False,
                    "reply_graph_id_space": (bundle.get("quality_flags") or {}).get("reply_graph_id_space"),
                },
                "preanalysis_version": PREANALYSIS_VERSION,
                "notes": {"no_llm": True, "reason": "no_comments"},
            },
            "hard_metrics": {},
            "per_cluster_metrics": [],
            "reply_matrix": {},
            "physics": {},
            "golden_samples": {},
            "golden_samples_detail": {},
            "quality_flags": bundle.get("quality_flags") or {},
        }
        payload = sanitize_analysis_json(payload)
        _update_preanalysis_status(post_id, payload, status="failed", version=PREANALYSIS_VERSION)
        return payload

    quant_result = perform_structure_mapping_bundle(bundle, post_id=post_id) or {}
    quant_calc_data = QuantCalculator.compute_from_bundle(post_id, bundle)
    reply_matrix = _reply_matrix_with_meta(
        quant_result.get("assignments") or [],
        comments,
        edges_total_db=bundle.get("edges_total"),
        edges_db=bundle.get("edges_rows"),
        post_root_internal_id=bundle.get("post_root_id"),
    )
    embedding_bundle = None
    if isinstance(quant_result, dict) and quant_result.get("embedding_lookup"):
        meta = quant_result.get("embedding_meta") or {}
        embedding_bundle = {
            "lookup": quant_result.get("embedding_lookup"),
            "model_id": meta.get("model_id"),
            "config_hash": meta.get("config_hash"),
        }
    physics_payload, golden_samples_payload, golden_samples_detail = compute_physics_and_golden_samples(
        comments=comments,
        quant_result=quant_result,
        quant_calc_data=quant_calc_data,
        reply_matrix=reply_matrix,
        embedding_bundle=embedding_bundle,
    )

    quality_flags = bundle.get("quality_flags") or {}
    coverage_meta = None
    try:
        coverage_resp = (
            supabase.table("threads_coverage_audits")
            .select("expected_replies_ui, unique_fetched, coverage_ratio, stop_reason, budgets_used, rounds_hash")
            .eq("post_id", post_id)
            .order("captured_at", desc=True)
            .limit(1)
            .execute()
        )
        coverage_row = (getattr(coverage_resp, "data", None) or [None])[0] or {}
        if coverage_row:
            budgets_used = coverage_row.get("budgets_used") if isinstance(coverage_row.get("budgets_used"), dict) else {}
            coverage_meta = {
                "expected_replies_ui": coverage_row.get("expected_replies_ui"),
                "unique_fetched": coverage_row.get("unique_fetched"),
                "coverage_ratio": coverage_row.get("coverage_ratio"),
                "stop_reason": coverage_row.get("stop_reason"),
                "budgets_used": budgets_used,
                "plateau_summary": budgets_used.get("plateau_summary") if isinstance(budgets_used, dict) else None,
                "rounds_digest": coverage_row.get("rounds_hash"),
            }
    except Exception:
        coverage_meta = None
    harvest_coverage_summary = {
        "comments_total": (bundle.get("tree_metrics") or {}).get("n_comments"),
        "source_comment_id_coverage": quality_flags.get("source_comment_id_coverage"),
        "parent_source_comment_id_coverage": quality_flags.get("parent_source_comment_id_coverage"),
        "reply_graph_available": quality_flags.get("reply_graph_available"),
        "reply_graph_id_space": quality_flags.get("reply_graph_id_space"),
    }
    reply_matrix_accounting = {}
    if isinstance(reply_matrix, dict):
        reply_matrix_accounting = reply_matrix.get("accounting") or {}

    behavior_artifact = None
    behavior_meta = None
    risk_meta = None
    sufficiency_meta = None
    try:
        behavior_artifact = compute_behavior_sidechannel(
            post_id=post_id,
            cluster_run_id=(quant_result or {}).get("cluster_run_id"),
            comments=comments,
            assignments=(quant_result or {}).get("assignments") or [],
            reply_graph_id_space=quality_flags.get("reply_graph_id_space") or "internal",
            ordering_key_hash=bundle.get("ordering_key_hash"),
            quality_flags=quality_flags,
            reply_matrix_health=(reply_matrix or {}).get("health") or {},
            coverage_ratio=(coverage_meta or {}).get("coverage_ratio"),
            comments_total=len(comments),
        )
        ui_budget = build_behavior_evidence_budget(behavior_artifact, comments)
        behavior_artifact["ui_budget"] = ui_budget
        behavior_meta = {
            "behavior_run_id": behavior_artifact.get("behavior_run_id"),
            "overall_behavior_risk": (behavior_artifact.get("scores") or {}).get("overall_behavior_risk"),
            "top_flags": behavior_artifact.get("top_flags") or [],
            "quality_flags": behavior_artifact.get("quality_flags") or {},
            "ui_budget": {
                "selection_hash": (ui_budget.get("digests") or {}).get("selection_hash"),
                "caps": ui_budget.get("caps") or {},
                "top_flags": behavior_artifact.get("top_flags") or [],
            },
        }
        if behavior_artifact.get("sufficiency"):
            sufficiency_meta = behavior_artifact.get("sufficiency")
    except Exception as e:
        logger.warning("[Behavior] compute failed post_id=%s err=%s", post_id, e)

    preanalysis_payload = {
        "version": PREANALYSIS_VERSION,
        "hard_metrics": (quant_calc_data or {}).get("hard_metrics") or {},
        "per_cluster_metrics": (quant_calc_data or {}).get("per_cluster_metrics") or [],
        "reply_matrix": reply_matrix,
        "physics": physics_payload,
        "golden_samples": golden_samples_payload,
        "golden_samples_detail": golden_samples_detail,
        "quality_flags": quality_flags,
        "meta": {
            "post_id": post_id,
            "bundle_id": bundle.get("bundle_id"),
            "bundle_version": bundle.get("bundle_version"),
            "cluster_run_id": (quant_result or {}).get("cluster_run_id"),
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "reply_graph_id_space": quality_flags.get("reply_graph_id_space"),
            "seed": DEFAULT_SEED,
            "harvest_coverage_summary": harvest_coverage_summary,
            "reply_matrix_accounting": reply_matrix_accounting,
            "preanalysis_version": PREANALYSIS_VERSION,
            "notes": {"no_llm": True},
        },
    }
    if coverage_meta:
        preanalysis_payload["meta"]["coverage"] = coverage_meta
    if behavior_meta:
        preanalysis_payload["meta"]["behavior"] = behavior_meta
    if sufficiency_meta:
        preanalysis_payload["meta"]["sufficiency"] = sufficiency_meta
    if risk_meta:
        preanalysis_payload["meta"]["risk"] = risk_meta
    preanalysis_payload = sanitize_analysis_json(preanalysis_payload)
    _update_preanalysis_status(post_id, preanalysis_payload, status="done", version=PREANALYSIS_VERSION)

    if reply_matrix_accounting and (quant_result or {}).get("cluster_run_id"):
        try:
            save_reply_matrix_audit(
                post_id,
                (quant_result or {}).get("cluster_run_id"),
                reply_matrix_accounting,
                preanalysis_json=preanalysis_payload,
                reply_graph_id_space=quality_flags.get("reply_graph_id_space") or "internal",
            )
        except Exception as e:
            logger.warning("[ReplyMatrix] persist audit failed post_id=%s err=%s", post_id, e)

    if behavior_artifact:
        try:
            save_behavior_audit(
                post_id,
                (quant_result or {}).get("cluster_run_id"),
                behavior_artifact,
                preanalysis_json=preanalysis_payload,
            )
        except Exception as e:
            logger.warning("[Behavior] persist failed post_id=%s err=%s", post_id, e)

    enable_risk = str(os.getenv("DL_ENABLE_RISK_COMPOSER_MIN", "") or "").lower() in {"1", "true", "yes", "on"}
    if enable_risk and behavior_artifact and behavior_meta:
        try:
            risk_brief = compose_risk_brief(
                post_id=post_id,
                cluster_run_id=(quant_result or {}).get("cluster_run_id"),
                behavior_artifact=behavior_artifact,
                ui_budget=behavior_artifact.get("ui_budget") or {},
                preanalysis_quality_flags=quality_flags,
            )
            save_risk_brief(
                post_id,
                (quant_result or {}).get("cluster_run_id"),
                risk_brief,
                preanalysis_json=preanalysis_payload,
            )
            risk_meta = {
                "risk_run_id": risk_brief.get("risk_run_id"),
                "raw_risk_level": (risk_brief.get("verdict") or {}).get("raw_risk_level"),
                "effective_level": (risk_brief.get("presentation") or {}).get("effective_level"),
                "ui_color": (risk_brief.get("presentation") or {}).get("ui_color"),
                "confidence": (risk_brief.get("verdict") or {}).get("confidence"),
                "confidence_cap_applied": (risk_brief.get("presentation") or {}).get("confidence_cap_applied"),
                "confidence_cap": (risk_brief.get("presentation") or {}).get("confidence_cap"),
                "primary_drivers": (risk_brief.get("verdict") or {}).get("primary_drivers") or [],
                "alerts_count": len(((risk_brief.get("sections") or {}).get("alerts") or [])),
            }
        except Exception as e:
            logger.warning("[Risk] compose/persist failed post_id=%s err=%s", post_id, e)

    coverage_after = None
    if persist_assignments:
        assignments = (quant_result or {}).get("assignments") or []
        if assignments:
            assign_res = apply_comment_cluster_assignments(
                post_id=post_id,
                assignments=assignments,
                enforce_coverage=True,
                unassignable_total=0,
                cluster_run_id=(quant_result or {}).get("cluster_run_id"),
                cluster_fingerprints=(quant_result or {}).get("cluster_fingerprints") or {},
            )
            coverage_after = assign_res.get("db_coverage_after")

    coverage_min = float(os.getenv("DL_ASSIGNMENT_COVERAGE_MIN", "0.95") or 0.95)
    if coverage_after is not None and coverage_after < coverage_min:
        _update_preanalysis_status(
            post_id,
            {
                **preanalysis_payload,
                "meta": {
                    **(preanalysis_payload.get("meta") or {}),
                    "coverage_after": coverage_after,
                    "status_reason": "coverage_below_min",
                },
            },
            status="failed",
            version=PREANALYSIS_VERSION,
        )
        logger.warning(
            "[Preanalysis] coverage below min post_id=%s coverage=%s min=%s",
            post_id,
            coverage_after,
            coverage_min,
        )
    else:
        logger.info(
            "[Preanalysis] done post_id=%s comments=%s coverage=%s",
            post_id,
            len(comments),
            coverage_after,
        )

    analysis_json = post_row.get("analysis_json")
    if not isinstance(analysis_json, dict) or not analysis_json:
        skeleton = _build_analysis_skeleton(
            post_data=post_row,
            bundle=bundle,
            quant_result=quant_result,
            quant_calc_data=quant_calc_data,
            reply_matrix=reply_matrix,
        )
        skeleton["physics"] = physics_payload
        skeleton["golden_samples"] = golden_samples_payload
        skeleton["golden_samples_detail"] = golden_samples_detail
        if coverage_meta or behavior_meta or sufficiency_meta or risk_meta:
            meta = skeleton.get("meta") or {}
            if coverage_meta:
                meta["coverage"] = coverage_meta
            if behavior_meta:
                meta["behavior"] = behavior_meta
            if sufficiency_meta:
                meta["sufficiency"] = sufficiency_meta
            if risk_meta:
                meta["risk"] = risk_meta
            skeleton["meta"] = meta
        skeleton = sanitize_analysis_json(skeleton)
        supabase.table("threads_posts").update({"analysis_json": _to_json_safe(skeleton)}).eq("id", post_id).execute()
    elif coverage_meta or behavior_meta or sufficiency_meta or risk_meta:
        meta = analysis_json.get("meta") if isinstance(analysis_json, dict) else {}
        meta = dict(meta or {})
        if coverage_meta:
            meta["coverage"] = coverage_meta
        if behavior_meta:
            meta["behavior"] = behavior_meta
        if sufficiency_meta:
            meta["sufficiency"] = sufficiency_meta
        if risk_meta:
            meta["risk"] = risk_meta
        analysis_json["meta"] = meta
        supabase.table("threads_posts").update({"analysis_json": _to_json_safe(analysis_json)}).eq("id", post_id).execute()

    return preanalysis_payload
