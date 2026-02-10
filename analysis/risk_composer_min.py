from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from analysis.risk_presentation_policy import CONFIDENCE_CAP_LOW_DATA, apply_presentation_policy


RISK_VERSION = "S6_RiskBriefV1"


def _hash_payload(payload: Dict[str, Any]) -> str:
    dumped = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def _score_levels(scores: Dict[str, float]) -> List[Tuple[str, float]]:
    items = []
    for key in ("temporal_score", "coordination_score", "graph_score", "engagement_score", "diversity_score"):
        if key in scores:
            items.append((key, float(scores.get(key) or 0.0)))
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return items


def _raw_risk_level(overall: float) -> str:
    if overall >= 0.75:
        return "high"
    if overall >= 0.45:
        return "med"
    return "low"


def _confidence_for_level(level: str) -> float:
    if level == "high":
        return 0.8
    if level == "med":
        return 0.65
    return 0.5


def _allowed_locator_sets(ui_budget: Dict[str, Any]) -> Dict[str, Set[str]]:
    selections = (ui_budget or {}).get("selections") or {}
    burst = selections.get("burst") or {}
    coord = selections.get("coordination") or {}
    graph = selections.get("graph") or {}
    engage = selections.get("engagement") or {}
    authorship = selections.get("authorship") or {}

    allowed = {
        "comment_id": set(),
        "edge": set(),
        "window": set(),
        "event": set(),
        "author_id": set(),
    }
    for cid in burst.get("comment_ids") or []:
        allowed["comment_id"].add(str(cid))
    for win in burst.get("windows") or []:
        allowed["window"].add(str(win.get("window_key") or ""))
    for ev in coord.get("events") or []:
        allowed["event"].add(str(ev.get("event_key") or ""))
        for cid in ev.get("comment_ids") or []:
            allowed["comment_id"].add(str(cid))
    for cid in coord.get("comment_ids") or []:
        allowed["comment_id"].add(str(cid))
    for node in graph.get("hub_nodes") or []:
        allowed["comment_id"].add(str(node.get("comment_id") or ""))
    for edge in graph.get("anomalous_edges") or []:
        allowed["edge"].add(str(edge.get("edge_key") or ""))
    for cid in engage.get("top_like_comment_ids") or []:
        allowed["comment_id"].add(str(cid))
    for author in engage.get("top_like_authors") or []:
        allowed["author_id"].add(str(author.get("author_id") or ""))
    for author in authorship.get("top_authors_by_comment_count") or []:
        allowed["author_id"].add(str(author.get("author_id") or ""))
    for author in authorship.get("top_authors_by_like_share") or []:
        allowed["author_id"].add(str(author.get("author_id") or ""))
    return allowed


def _validate_evidence_refs(evidence_refs: List[Dict[str, Any]], allowed: Dict[str, Set[str]]) -> None:
    for ref in evidence_refs:
        locator = ref.get("locator") or {}
        kind = locator.get("type") or ""
        value = locator.get("value") or ""
        if kind not in allowed or value not in allowed[kind]:
            raise ValueError(f"risk evidence locator not allowed: {kind}:{value}")


def compose_risk_brief(
    *,
    post_id: int,
    cluster_run_id: str,
    behavior_artifact: Dict[str, Any],
    ui_budget: Dict[str, Any],
    preanalysis_quality_flags: Optional[Dict[str, Any]] = None,
    claims_stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(behavior_artifact, dict):
        raise ValueError("behavior_artifact must be dict")
    if not isinstance(ui_budget, dict):
        raise ValueError("ui_budget must be dict")

    scores = behavior_artifact.get("scores") or {}
    quality_flags = behavior_artifact.get("quality_flags") or {}
    sufficiency = behavior_artifact.get("sufficiency") or {}
    overall = float(scores.get("overall_behavior_risk") or 0.0)
    raw_level = _raw_risk_level(overall)
    confidence = _confidence_for_level(raw_level)
    cap_applied = (quality_flags.get("data_sufficiency") == "RED")
    if cap_applied:
        confidence = min(confidence, CONFIDENCE_CAP_LOW_DATA)

    drivers = _score_levels(scores)
    if sufficiency.get("temporal") == "RED":
        drivers = [d for d in drivers if d[0] != "temporal_score"]
    primary_drivers = [name for name, score in drivers if score >= 0.6]
    if not primary_drivers:
        primary_drivers = [drivers[0][0]] if drivers else []

    alerts = []
    for name, score in drivers:
        if score < 0.7:
            continue
        severity = "high" if score >= 0.85 else "med"
        alerts.append(
            {
                "code": f"S6_{name.upper()}",
                "severity": severity,
                "score": round(score, 4),
                "rationale_codes": [f"{name}_score_high"],
                "evidence_refs": [],
            }
        )

    selections = (ui_budget.get("selections") or {})
    evidence_refs: List[Dict[str, Any]] = []
    if "temporal_score" in primary_drivers:
        burst = selections.get("burst") or {}
        if burst.get("windows"):
            win = burst.get("windows")[0]
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "window",
                    "locator": {"type": "window", "value": win.get("window_key")},
                }
            )
        if burst.get("comment_ids"):
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "comment",
                    "locator": {"type": "comment_id", "value": burst.get("comment_ids")[0]},
                }
            )
    if "coordination_score" in primary_drivers:
        coord = selections.get("coordination") or {}
        if coord.get("events"):
            ev = coord.get("events")[0]
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "event",
                    "locator": {"type": "event", "value": ev.get("event_key")},
                }
            )
        if coord.get("comment_ids"):
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "comment",
                    "locator": {"type": "comment_id", "value": coord.get("comment_ids")[0]},
                }
            )
    if "graph_score" in primary_drivers:
        graph = selections.get("graph") or {}
        if graph.get("hub_nodes"):
            hub = graph.get("hub_nodes")[0]
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "comment",
                    "locator": {"type": "comment_id", "value": hub.get("comment_id")},
                }
            )
        if graph.get("anomalous_edges"):
            edge = graph.get("anomalous_edges")[0]
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "edge",
                    "locator": {"type": "edge", "value": edge.get("edge_key")},
                }
            )
    if "engagement_score" in primary_drivers:
        engage = selections.get("engagement") or {}
        if engage.get("top_like_comment_ids"):
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "comment",
                    "locator": {"type": "comment_id", "value": engage.get("top_like_comment_ids")[0]},
                }
            )
        if engage.get("top_like_authors"):
            author = engage.get("top_like_authors")[0]
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "author",
                    "locator": {"type": "author_id", "value": author.get("author_id")},
                }
            )
    if "diversity_score" in primary_drivers:
        authorship = selections.get("authorship") or {}
        if authorship.get("top_authors_by_comment_count"):
            author = authorship.get("top_authors_by_comment_count")[0]
            evidence_refs.append(
                {
                    "source": "threads",
                    "kind": "author",
                    "locator": {"type": "author_id", "value": author.get("author_id")},
                }
            )

    allowed = _allowed_locator_sets(ui_budget)
    _validate_evidence_refs(evidence_refs, allowed)

    presentation = apply_presentation_policy(raw_level, confidence, cap_applied)
    limitations = []
    if cap_applied:
        limitations.append({"code": "S6_DATA_INSUFFICIENT", "reason": "behavior data_sufficiency RED"})
    if sufficiency.get("temporal") == "RED":
        limitations.append({"code": "S6_TEMPORAL_DISABLED", "reason": "temporal_sufficiency RED"})

    brief = {
        "post_id": post_id,
        "cluster_run_id": cluster_run_id,
        "behavior_run_id": behavior_artifact.get("behavior_run_id"),
        "risk_run_id": "",
        "version": RISK_VERSION,
        "inputs": {
            "behavior_scores": scores,
            "behavior_quality_flags": quality_flags,
            "behavior_sufficiency": sufficiency,
            "preanalysis_quality_flags": preanalysis_quality_flags or {},
            "claims_stats": claims_stats or {},
        },
        "verdict": {
            "raw_risk_level": raw_level,
            "confidence": round(confidence, 4),
            "primary_drivers": primary_drivers,
        },
        "presentation": {
            **presentation,
            "confidence_cap_applied": cap_applied,
            "confidence_cap": CONFIDENCE_CAP_LOW_DATA if cap_applied else None,
        },
        "sections": {
            "alerts": alerts,
            "evidence_refs": evidence_refs,
            "recommended_actions": [],
            "limitations": limitations,
        },
        "ui": {
            "top_evidence_budget_ref": (ui_budget.get("digests") or {}).get("selection_hash"),
            "drilldown_paths": ["behavior_budget", "behavior_artifact"],
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    risk_run_id = _hash_payload(
        {
            "post_id": post_id,
            "cluster_run_id": cluster_run_id,
            "behavior_run_id": behavior_artifact.get("behavior_run_id"),
            "selection_hash": (ui_budget.get("digests") or {}).get("selection_hash"),
            "scores": scores,
            "quality_flags": quality_flags,
            "raw_risk_level": raw_level,
            "confidence": round(confidence, 4),
        }
    )
    brief["risk_run_id"] = risk_run_id
    return brief
