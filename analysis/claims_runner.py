import json
import logging
import os
import uuid
import hashlib
from typing import Any, Dict, Optional, Tuple, List

import google.generativeai as genai

from analysis.analyst import (
    build_claims_only_prompt,
    build_cluster_summary_and_samples,
    build_evidence_inputs,
    extract_json_block,
    format_claims_only_metrics,
    _build_llm_stub_json,
    _call_gemini_with_retry,
    _extract_narrative_patch,
    _reverse_map_evidence_ids,
    _evidence_compliance_errors,
    _validate_explainability_gate,
    _build_system_failure_hypothesis,
    _resolve_claim_aliases,
    protect_core_fields,
    validate_analysis_json,
    _record_llm_call_log,
)
from analysis.claims.normalize import normalize_analyst_output_to_claims
from analysis.claims.evidence_audit import audit_claims
from analysis.claims.projector import apply_claims_to_analysis_json
from analysis.build_analysis_json import build_and_validate_analysis_json, safe_dump
from analysis.quant_engine import perform_structure_mapping_bundle
from analysis.quant_calculator import QuantCalculator
from analysis.preanalysis_runner import run_preanalysis
from database.integrity import load_preanalysis_json
from database.store import supabase, save_claim_pack, save_analysis_json

logger = logging.getLogger("ClaimsRunner")


def _cluster_context_from_metrics(per_cluster_metrics: List[Dict[str, Any]]) -> str:
    if not per_cluster_metrics:
        return "(no clusters)"
    lines: List[str] = []
    for item in per_cluster_metrics:
        cid = item.get("cluster_id")
        share = item.get("size_share")
        likes = item.get("like_share")
        try:
            share_pct = f"{round((share or 0) * 100, 1)}%"
        except Exception:
            share_pct = "—"
        try:
            like_pct = f"{round((likes or 0) * 100, 1)}%"
        except Exception:
            like_pct = "—"
        lines.append(f"Cluster {cid}: size_share={share_pct} like_share={like_pct}")
    return "\n".join(lines)


def _build_claims_status(audit_meta: Dict[str, Any], claim_count: int, full_text_present: bool, json_present: bool) -> Tuple[str, str]:
    parse_failed = bool(full_text_present) and not json_present
    if parse_failed:
        return "fail_parse", "fail_parse"
    if claim_count == 0:
        return "fail_no_claims", "fail_no_claims"
    verdict = audit_meta.get("verdict")
    if verdict == "pass":
        return "ok", "ok"
    if verdict == "partial":
        return "partial", "partial"
    return "fail_audit", "fail_audit"


def run_claims_only_for_post(post_id: int, *, use_stub: Optional[bool] = None) -> Dict[str, Any]:
    post_resp = supabase.table("threads_posts").select("*").eq("id", post_id).limit(1).execute()
    post_row = (getattr(post_resp, "data", None) or [None])[0]
    if not post_row:
        raise RuntimeError(f"post_id {post_id} not found")

    analysis_build_id = str(uuid.uuid4())
    analysis_version = "v6.1"

    # Ensure preanalysis exists
    preanalysis_json = load_preanalysis_json(supabase, post_id)
    if not preanalysis_json:
        run_preanalysis(post_id, prefer_sot=True, persist_assignments=True)
        preanalysis_json = load_preanalysis_json(supabase, post_id)

    # Build SoT bundle + quant
    bundle = {
        "comments": [],
    }
    from database.store import get_canonical_comment_bundle

    bundle = get_canonical_comment_bundle(post_id, prefer_sot=True)
    comments = bundle.get("comments") or []
    if not comments:
        return {"post_id": str(post_id), "status": "no_comments"}

    quant_result = perform_structure_mapping_bundle(bundle, post_id=post_id) or {}
    quant_calc_data = QuantCalculator.compute_from_bundle(post_id, bundle)
    cluster_samples = build_cluster_summary_and_samples(comments)

    # Evidence inputs
    evidence_inputs = build_evidence_inputs(
        quant_calc_data.get("sampled_evidence_set") or [],
        comments,
    )
    evidence_catalog = evidence_inputs.get("catalog_for_prompt") or ""
    alias_to_locator = evidence_inputs.get("alias_to_locator") or {}
    alias_to_locator_key = evidence_inputs.get("alias_to_locator_key") or {}
    evidence_map = evidence_inputs.get("evidence_map") or {}
    evidence_rows = evidence_inputs.get("evidence_rows") or {}

    cluster_context = _cluster_context_from_metrics(quant_calc_data.get("per_cluster_metrics") or [])
    claims_metrics_block = format_claims_only_metrics(quant_calc_data.get("hard_metrics") or {})

    system_prompt, user_content = build_claims_only_prompt(
        post_data=post_row,
        cluster_context=cluster_context,
        claims_metrics_block=claims_metrics_block,
        evidence_catalog=evidence_catalog,
    )
    payload_str = system_prompt + "\n\n" + user_content

    model_name = os.getenv("DL_GEMINI_MODEL_CLAIMS_ONLY") or os.getenv("DL_GEMINI_MODEL") or "models/gemini-2.5-flash"
    if use_stub is None:
        use_stub = os.getenv("DL_LLM_STUB", "0").lower() in {"1", "true"}
    if not os.getenv("GEMINI_API_KEY") and not use_stub:
        use_stub = True
        logger.warning("[ClaimsRunner] GEMINI_API_KEY missing; forcing stub mode.")

    full_text = ""
    llm_status = "ok"
    llm_latency_ms: Optional[int] = None
    llm_error: Optional[str] = None
    response = None

    if use_stub:
        alias_list = sorted(list(alias_to_locator.keys()))
        cluster_keys = sorted(
            {
                int(c.get("cluster_key"))
                for c in (quant_result.get("clusters") or [])
                if isinstance(c, dict) and c.get("cluster_key") is not None
            }
        )
        stub_json = _build_llm_stub_json(alias_list, cluster_keys)
        full_text = json.dumps(stub_json, ensure_ascii=False)
        llm_status = "stub"
        _record_llm_call_log(
            post_id=str(post_id),
            run_id=str(((preanalysis_json or {}).get("meta") or {}).get("cluster_run_id") or ""),
            mode="claims_only",
            model_name=model_name,
            status="stub",
            latency_ms=0,
        )
    else:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(model_name)
        try:
            import time
            start = time.perf_counter()
            response = _call_gemini_with_retry(model, payload_str, timeout_seconds=None)
            llm_latency_ms = int((time.perf_counter() - start) * 1000)
            full_text = getattr(response, "text", "") or ""
            llm_status = "ok"
        except Exception as exc:
            llm_error = str(exc)
            llm_status = "error"
            full_text = ""
        _record_llm_call_log(
            post_id=str(post_id),
            run_id=str(((preanalysis_json or {}).get("meta") or {}).get("cluster_run_id") or ""),
            mode="claims_only",
            model_name=model_name,
            status=llm_status,
            latency_ms=llm_latency_ms,
            response=response,
        )

    # Parse + map JSON
    json_data_raw = extract_json_block(full_text) or {}
    json_data_mapped = json_data_raw
    mapping_unknown_aliases: List[str] = []
    evidence_errors: List[str] = []
    if json_data_raw:
        json_data_mapped, mapping_unknown_aliases = _reverse_map_evidence_ids(json_data_raw, alias_to_locator)
        evidence_errors = _evidence_compliance_errors(json_data_mapped)
        explainability_errors = _validate_explainability_gate(
            json_data_mapped,
            bundle=bundle,
            quant_calc_data=quant_calc_data,
            tree_metrics=(bundle.get("tree_metrics") or {}),
        )
        if explainability_errors:
            evidence_errors.extend(explainability_errors)

    # Normalize + audit claims
    run_id = str(((preanalysis_json or {}).get("meta") or {}).get("cluster_run_id") or "")
    claim_pack = normalize_analyst_output_to_claims(
        json_data_raw or {},
        post_id=int(post_id),
        run_id=run_id,
        prompt_hash=hashlib.sha256(payload_str.encode("utf-8")).hexdigest(),
        model_name=model_name,
        build_id=analysis_build_id,
    )
    _resolve_claim_aliases(
        claim_pack,
        alias_to_locator=alias_to_locator,
        alias_to_locator_key=alias_to_locator_key,
        evidence_rows=evidence_rows,
    )
    valid_claims, dropped_claims, audit_meta = audit_claims(
        claim_pack,
        preanalysis_meta=(preanalysis_json or {}).get("meta") or {},
        evidence_map=evidence_map,
    )

    # Build hypotheses for dropped claims
    hypotheses: List[Dict[str, Any]] = []
    if not claim_pack.claims:
        reason = "parse_failed" if full_text and not json_data_raw else "llm_missing_claims"
        failure_key = hashlib.sha256(f"{post_id}:{run_id}:{reason}".encode("utf-8")).hexdigest()
        system_hyp = _build_system_failure_hypothesis("parse_error" if reason == "parse_failed" else "llm_missing_claims")
        system_hyp.update(
            {
                "claim_key": failure_key,
                "text": "LLM did not return any auditable claims.",
                "scope": "post",
                "cluster_keys": [],
                "primary_cluster_key": None,
                "source_agent": "analyst",
                "evidence_aliases": [],
                "run_id": run_id,
            }
        )
        hypotheses.append(system_hyp)
    for claim in dropped_claims:
        hypotheses.append(
            {
                "claim_key": claim.claim_key,
                "text": claim.text,
                "reason": claim.audit_reason,
                "confidence_cap": claim.confidence_cap or 0.4,
                "missing_evidence_type": claim.missing_evidence_type,
                "scope": claim.scope,
                "cluster_keys": claim.cluster_keys or ([] if claim.cluster_key is None else [claim.cluster_key]),
                "primary_cluster_key": claim.primary_cluster_key,
                "source_agent": claim.source_agent,
                "evidence_aliases": claim.evidence_aliases,
                "run_id": claim.run_id,
            }
        )

    # Persist claims + evidence
    if run_id and claim_pack.claims:
        try:
            claim_rows = [c.model_dump() if hasattr(c, "model_dump") else c.dict() for c in (valid_claims + dropped_claims)]
            save_claim_pack(
                post_id=int(post_id),
                run_id=run_id,
                claims=claim_rows,
                audit_meta=audit_meta,
                preanalysis_json=preanalysis_json or {},
                prompt_hash=hashlib.sha256(payload_str.encode("utf-8")).hexdigest(),
                model_name=model_name,
                build_id=analysis_build_id,
                evidence_rows=evidence_rows,
            )
        except Exception:
            logger.exception("[ClaimsRunner] Claim persistence failed (non-fatal)")

    # Build analysis_json patch
    analysis_v4 = build_and_validate_analysis_json(
        post_data=post_row,
        llm_data=json_data_mapped or {},
        cluster_data=cluster_samples or {},
        full_report=full_text,
    )
    if quant_calc_data:
        analysis_v4 = analysis_v4.copy(
            update={
                "hard_metrics": quant_calc_data.get("hard_metrics"),
                "per_cluster_metrics": quant_calc_data.get("per_cluster_metrics") or [],
            }
        )
    analysis_v4 = protect_core_fields(post_row, analysis_v4)
    try:
        claim_projection = apply_claims_to_analysis_json(analysis_v4, valid_claims, audit_meta, hypotheses)
        analysis_v4 = analysis_v4.copy(update=claim_projection)
    except Exception:
        logger.exception("[ClaimsRunner] Claims projection failed")

    # Phenomenon is registry-owned; mark pending to avoid missing id/name.
    try:
        phen_dict = safe_dump(analysis_v4.phenomenon)
        if isinstance(phen_dict, dict) and not phen_dict.get("id") and not phen_dict.get("status"):
            phen_dict["status"] = "pending"
            analysis_v4 = analysis_v4.copy(update={"phenomenon": phen_dict})
    except Exception:
        logger.exception("[ClaimsRunner] Failed to tag phenomenon pending")

    analysis_payload = safe_dump(analysis_v4)
    meta = analysis_payload.get("meta") or {}
    claims_status, narrative_status = _build_claims_status(
        audit_meta,
        len(claim_pack.claims),
        bool(full_text),
        bool(json_data_raw),
    )
    meta.update(
        {
            "stage": "claims_only",
            "narrative_status": narrative_status,
            "narrative_mode": "claims_only",
            "llm_status": llm_status,
            "claims_status": claims_status,
            "llm_model": model_name,
            "llm_payload_chars": len(payload_str),
            "llm_latency_ms": llm_latency_ms,
            "llm_error": llm_error,
            "claims": {
                "total": int(audit_meta.get("total_claims_count") or len(claim_pack.claims) or 0),
                "kept": int(audit_meta.get("kept_claims_count") or len(valid_claims) or 0),
                "dropped": int(audit_meta.get("dropped_claims_count") or len(dropped_claims) or 0),
                "audit_verdict": audit_meta.get("verdict") or "fail",
            },
            "hypotheses": hypotheses if hypotheses else None,
        }
    )
    analysis_payload["meta"] = meta

    is_valid, invalid_reason, missing_keys = validate_analysis_json(analysis_v4)
    if mapping_unknown_aliases:
        is_valid = False
        invalid_reason = "evidence_alias_unknown"
        missing_keys = mapping_unknown_aliases
    if evidence_errors:
        is_valid = False
        invalid_reason = "evidence_requirements_failed"
        missing_keys = (missing_keys or []) + evidence_errors

    narrative_patch = _extract_narrative_patch(analysis_payload)
    save_analysis_json(
        post_id=str(post_id),
        analysis_build_id=analysis_build_id,
        json_obj=narrative_patch,
        mode="merge_narrative",
        analysis_version=analysis_version,
        analysis_is_valid=is_valid,
        analysis_invalid_reason=invalid_reason if not is_valid else None,
        analysis_missing_keys=missing_keys or None,
    )

    # Keep full_report for auditing (claims JSON)
    try:
        supabase.table("threads_posts").update({"full_report": full_text}).eq("id", post_id).execute()
    except Exception:
        logger.warning("[ClaimsRunner] Failed to update full_report post_id=%s", post_id)

    return {
        "post_id": str(post_id),
        "analysis_is_valid": is_valid,
        "analysis_invalid_reason": invalid_reason,
        "analysis_missing_keys": missing_keys,
        "claims_total": audit_meta.get("total_claims_count"),
        "claims_kept": audit_meta.get("kept_claims_count"),
        "claims_dropped": audit_meta.get("dropped_claims_count"),
        "claims_status": claims_status,
    }
