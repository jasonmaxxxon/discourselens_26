from __future__ import annotations

from typing import Any, Dict, List, Optional

from analysis.claims.models import Claim, ClaimPack, ClaimPackMeta


def _normalize_evidence_ids(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _claim_from_entry(
    entry: Dict[str, Any],
    *,
    post_id: int,
    run_id: str,
    source_agent: str,
    default_scope: str = "post",
    default_type: str = "interpret",
    tags: Optional[List[str]] = None,
) -> Optional[Claim]:
    text = (entry.get("text") or entry.get("claim") or entry.get("verdict") or entry.get("summary") or "").strip()
    if not text:
        return None
    evidence_ids = _normalize_evidence_ids(entry.get("evidence_ids") or entry.get("evidence_comment_ids"))
    scope = entry.get("scope") or default_scope
    claim_type = entry.get("claim_type") or entry.get("type") or default_type
    cluster_key = entry.get("cluster_key")
    if cluster_key is None:
        cluster_key = entry.get("cluster_id")
    if cluster_key is not None:
        try:
            cluster_key = int(cluster_key)
        except Exception:
            cluster_key = None
    cluster_keys: List[int] = []
    raw_cluster_keys = entry.get("cluster_keys")
    if isinstance(raw_cluster_keys, list):
        for ck in raw_cluster_keys:
            try:
                cluster_keys.append(int(ck))
            except Exception:
                continue
    if cluster_key is not None and cluster_key not in cluster_keys:
        cluster_keys.append(cluster_key)
    primary_cluster_key = cluster_key if cluster_key is not None else (min(cluster_keys) if cluster_keys else None)
    return Claim(
        post_id=int(post_id),
        run_id=str(run_id or ""),
        claim_type=claim_type,
        scope=scope,
        text=text,
        source_agent=source_agent,
        evidence_ids=evidence_ids,
        evidence_aliases=list(evidence_ids),
        confidence=entry.get("confidence"),
        tags=tags or entry.get("tags"),
        cluster_key=cluster_key,
        cluster_keys=cluster_keys,
        primary_cluster_key=primary_cluster_key,
    )


def normalize_analyst_output_to_claims(
    raw_llm_json: Dict[str, Any],
    *,
    post_id: int,
    run_id: str,
    prompt_hash: Optional[str],
    model_name: Optional[str],
    build_id: Optional[str],
    source_agent: str = "analyst",
) -> ClaimPack:
    claims: List[Claim] = []

    if isinstance(raw_llm_json, dict):
        raw_claims = raw_llm_json.get("claims")
        if isinstance(raw_claims, list):
            for entry in raw_claims:
                if not isinstance(entry, dict):
                    continue
                claim = _claim_from_entry(
                    entry,
                    post_id=post_id,
                    run_id=run_id,
                    source_agent=source_agent,
                )
                if claim:
                    claims.append(claim)

    # Fallback extraction from existing blocks with evidence ids
    if not claims and isinstance(raw_llm_json, dict):
        battlefield_map = raw_llm_json.get("battlefield_map") or []
        if isinstance(battlefield_map, list):
            for entry in battlefield_map:
                if not isinstance(entry, dict):
                    continue
                text = (
                    entry.get("role")
                    or entry.get("label")
                    or entry.get("tactic")
                    or entry.get("summary")
                    or entry.get("rationale")
                    or ""
                )
                entry_copy = {
                    "text": str(text).strip(),
                    "evidence_comment_ids": entry.get("evidence_comment_ids"),
                    "cluster_key": entry.get("cluster_id") or entry.get("cluster"),
                    "scope": "cluster",
                    "claim_type": "summarize",
                }
                claim = _claim_from_entry(
                    entry_copy,
                    post_id=post_id,
                    run_id=run_id,
                    source_agent=source_agent,
                    tags=["battlefield_map.role"],
                )
                if claim:
                    claims.append(claim)

        strategic_verdict = raw_llm_json.get("strategic_verdict") or {}
        if isinstance(strategic_verdict, dict):
            verdict_text = strategic_verdict.get("verdict") or strategic_verdict.get("summary")
            entry_copy = {
                "text": verdict_text or "",
                "evidence_comment_ids": strategic_verdict.get("evidence_comment_ids"),
                "scope": "post",
                "claim_type": "interpret",
            }
            claim = _claim_from_entry(
                entry_copy,
                post_id=post_id,
                run_id=run_id,
                source_agent=source_agent,
                tags=["strategic_verdict.verdict"],
            )
            if claim:
                claims.append(claim)

        structural_insight = raw_llm_json.get("structural_insight") or {}
        if isinstance(structural_insight, dict):
            insight_text = (
                structural_insight.get("counterfactual_analysis")
                or structural_insight.get("rationale")
                or structural_insight.get("summary")
                or ""
            )
            entry_copy = {
                "text": insight_text,
                "evidence_comment_ids": structural_insight.get("evidence_comment_ids"),
                "scope": "post",
                "claim_type": "interpret",
            }
            claim = _claim_from_entry(
                entry_copy,
                post_id=post_id,
                run_id=run_id,
                source_agent=source_agent,
                tags=["structural_insight.counterfactual_analysis"],
            )
            if claim:
                claims.append(claim)

        summary = raw_llm_json.get("summary") or {}
        if isinstance(summary, dict):
            entry_copy = {
                "text": summary.get("one_line") or "",
                "evidence_comment_ids": summary.get("evidence_comment_ids"),
                "scope": "post",
                "claim_type": "summarize",
            }
            claim = _claim_from_entry(
                entry_copy,
                post_id=post_id,
                run_id=run_id,
                source_agent=source_agent,
                tags=["summary.one_line"],
            )
            if claim:
                claims.append(claim)

    meta = ClaimPackMeta(
        prompt_hash=prompt_hash,
        model_name=model_name,
        build_id=build_id,
        audit_verdict="fail",
        dropped_claims_count=0,
        fail_reasons=[],
    )
    return ClaimPack(post_id=int(post_id), run_id=str(run_id or ""), claims=claims, meta=meta)
