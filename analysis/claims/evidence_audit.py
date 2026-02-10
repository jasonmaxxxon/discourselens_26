from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from analysis.claims.models import Claim, ClaimPack
from analysis.utils.evidence_quality import evidence_quality_summary, is_low_information_text


def audit_claims(
    claim_pack: ClaimPack,
    *,
    preanalysis_meta: Dict[str, Any],
    evidence_map: Dict[str, str],
) -> Tuple[List[Claim], List[Claim], Dict[str, Any]]:
    expected_run_id = (preanalysis_meta or {}).get("cluster_run_id")

    valid_claims: List[Claim] = []
    dropped_claims: List[Claim] = []
    fail_reasons: List[str] = []

    if expected_run_id and claim_pack.run_id and claim_pack.run_id != expected_run_id:
        for claim in claim_pack.claims:
            claim.status = "hypothesis"
            claim.audit_reason = "run_id_mismatch"
            claim.missing_evidence_type = "run_id_mismatch"
            claim.confidence_cap = 0.4
            dropped_claims.append(claim)
        audit_meta = {
            "verdict": "fail",
            "dropped_claims_count": len(claim_pack.claims),
            "kept_claims_count": 0,
            "total_claims_count": len(claim_pack.claims),
            "fail_reasons": ["run_id_mismatch"],
        }
        return [], dropped_claims, audit_meta

    for claim in claim_pack.claims:
        reasons: List[str] = []
        evidence_locator_keys = [str(e).strip() for e in (claim.evidence_locator_keys or []) if str(e).strip()]
        if getattr(claim, "evidence_aliases_unknown", None):
            reasons.append("alias_unknown")
        if not evidence_locator_keys:
            reasons.append("missing_evidence_ids")

        if not reasons:
            for locator_key in evidence_locator_keys:
                if not locator_key.startswith("threads:"):
                    reasons.append("unsupported_source")
                    break
                if locator_key not in evidence_map:
                    reasons.append("evidence_not_found")
                    break

        if not reasons:
            evidence_texts = [evidence_map.get(key, "") for key in evidence_locator_keys]
            if not evidence_texts or all(is_low_information_text(t) for t in evidence_texts):
                reasons.append("low_information_evidence")

        if reasons:
            claim.status = "hypothesis"
            claim.audit_reason = reasons[0]
            if "alias_unknown" in reasons:
                claim.missing_evidence_type = "alias_unknown"
            elif "unsupported_source" in reasons:
                claim.missing_evidence_type = "unsupported_source"
            elif "evidence_not_found" in reasons:
                claim.missing_evidence_type = "locator_missing"
            elif "low_information_evidence" in reasons:
                claim.missing_evidence_type = "low_info"
            else:
                claim.missing_evidence_type = "missing_evidence_ids"
            claim.confidence_cap = 0.4
            dropped_claims.append(claim)
            fail_reasons.extend(reasons)
            continue

        try:
            claim.evidence_quality = evidence_quality_summary([evidence_map.get(key, "") for key in evidence_locator_keys])
        except Exception:
            pass
        claim.status = "audited"
        valid_claims.append(claim)

    dropped_count = len(dropped_claims)
    if not claim_pack.claims:
        verdict = "fail"
    elif dropped_count == 0:
        verdict = "pass"
    elif dropped_count < len(claim_pack.claims):
        verdict = "partial"
    else:
        verdict = "fail"

    kept_count = len(valid_claims)
    audit_meta = {
        "verdict": verdict,
        "dropped_claims_count": dropped_count,
        "kept_claims_count": kept_count,
        "total_claims_count": len(claim_pack.claims),
        "fail_reasons": sorted(set(fail_reasons)),
    }
    return valid_claims, dropped_claims, audit_meta
