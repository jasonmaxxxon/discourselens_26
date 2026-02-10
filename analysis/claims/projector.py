from __future__ import annotations

from typing import Any, Dict, List, Optional

from analysis.build_analysis_json import safe_dump
from analysis.claims.models import Claim
from analysis.schema import BattlefieldMapEntry, NarrativeStack, StructuralInsight, StrategicVerdict, SummaryCompat


def _first_claim_text(claims: List[Claim]) -> Optional[str]:
    for c in claims:
        text = (c.text or "").strip()
        if text:
            return text
    return None


def _claims_by_tag(claims: List[Claim]) -> Dict[str, List[Claim]]:
    mapping: Dict[str, List[Claim]] = {}
    for c in claims:
        tags = c.tags or []
        for tag in tags:
            if not tag:
                continue
            mapping.setdefault(tag, []).append(c)
    return mapping


def apply_claims_to_analysis_json(
    base_analysis_json: Any,
    valid_claims: List[Claim],
    audit_meta: Optional[Dict[str, Any]] = None,
    hypotheses: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    analysis_dict = safe_dump(base_analysis_json)
    update: Dict[str, Any] = {}
    by_tag = _claims_by_tag(valid_claims)

    summary_claim = _first_claim_text(by_tag.get("summary.one_line") or [])
    if summary_claim is not None:
        summary = dict(analysis_dict.get("summary") or {})
        summary["one_line"] = summary_claim
        update["summary"] = SummaryCompat(**summary)
    else:
        update["summary"] = None

    narrative = dict(analysis_dict.get("narrative_stack") or {})
    l1_claim = _first_claim_text(by_tag.get("narrative_stack.l1") or [])
    l2_claim = _first_claim_text(by_tag.get("narrative_stack.l2") or [])
    l3_claim = _first_claim_text(by_tag.get("narrative_stack.l3") or [])
    narrative["l1"] = l1_claim
    narrative["l2"] = l2_claim
    narrative["l3"] = l3_claim
    update["narrative_stack"] = NarrativeStack(**narrative)

    verdict_claims = by_tag.get("strategic_verdict.verdict") or []
    rationale_claims = by_tag.get("strategic_verdict.rationale") or []
    verdict_text = _first_claim_text(verdict_claims)
    rationale_text = _first_claim_text(rationale_claims)
    if verdict_text or rationale_text:
        strategic = dict(analysis_dict.get("strategic_verdict") or {})
        strategic["verdict"] = verdict_text
        strategic["rationale"] = rationale_text
        if verdict_claims:
            strategic["evidence_comment_ids"] = verdict_claims[0].evidence_ids
        update["strategic_verdict"] = StrategicVerdict(**strategic)
    else:
        update["strategic_verdict"] = None

    insight_claims = by_tag.get("structural_insight.counterfactual_analysis") or []
    insight_text = _first_claim_text(insight_claims)
    if insight_text:
        insight = dict(analysis_dict.get("structural_insight") or {})
        insight["counterfactual_analysis"] = insight_text
        if insight_claims:
            insight["evidence_comment_ids"] = insight_claims[0].evidence_ids
        update["structural_insight"] = StructuralInsight(**insight)
    else:
        update["structural_insight"] = None

    battlefield_claims = by_tag.get("battlefield_map.role") or []
    if battlefield_claims:
        existing_map = analysis_dict.get("battlefield_map") or []
        rebuilt: List[BattlefieldMapEntry] = []
        for claim in battlefield_claims:
            if claim.cluster_key is None:
                continue
            for entry in existing_map:
                try:
                    if int(entry.get("cluster_id")) != int(claim.cluster_key):
                        continue
                except Exception:
                    continue
                updated = dict(entry)
                updated["role"] = claim.text
                updated["evidence_comment_ids"] = claim.evidence_ids
                rebuilt.append(BattlefieldMapEntry(**updated))
                break
        update["battlefield_map"] = rebuilt
    else:
        update["battlefield_map"] = []

    meta = dict(analysis_dict.get("meta") or {})
    if audit_meta:
        meta["claims"] = {
            "total": len(valid_claims) + int(audit_meta.get("dropped_claims_count") or 0),
            "kept": len(valid_claims),
            "dropped": int(audit_meta.get("dropped_claims_count") or 0),
            "audit_verdict": audit_meta.get("verdict"),
        }
    if hypotheses is not None:
        meta["hypotheses"] = hypotheses
    update["meta"] = meta

    return update
