"""
DiscourseLens Commercial Analyst (v3.0)
Features: Full Theory Injection, Dynamic Taxonomy (Sector X), Dashboard-Ready JSON
Runtime modes: disabled | claims_only (Flash) | legacy_writer (Pro)
"""

import os
import json
import logging
import re
import hashlib
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, Any, List, Optional
import heapq
import textwrap
import time
import random
from time import perf_counter

import google.generativeai as genai
from supabase import create_client, Client
from dotenv import load_dotenv
from pydantic import ValidationError
from analysis.axis_manager import AxisManager
from analysis.axis_models import AxisAlignmentBlock
from analysis.quant_engine import perform_structure_mapping_bundle, compute_battlefield_matrix
from analysis.build_analysis_json import (
    build_and_validate_analysis_json,
    protect_core_fields,
    validate_analysis_json,
)
try:
    from analysis.phenomenon_enricher import PhenomenonEnricher
except Exception:
    PhenomenonEnricher = None
from analysis.quant_calculator import QuantCalculator
from analysis.physics_engine import compute_physics_and_golden_samples
from analysis.claims.normalize import normalize_analyst_output_to_claims
from analysis.claims.evidence_audit import audit_claims
from analysis.claims.projector import apply_claims_to_analysis_json
from analysis.runtime.status_policy import (
    AttemptContext,
    circuit_state,
    decide_action,
    record_llm_failure,
    record_llm_success,
)
import uuid
from database.store import (
    update_cluster_metadata,
    save_analysis_result,
    save_analysis_json,
    apply_comment_cluster_assignments,
    get_canonical_comment_bundle,
    save_claim_pack,
    save_llm_call_log,
)
from database.integrity import load_preanalysis_json
from analysis.schema import Phenomenon
from analysis.axis_sanitize import sanitize_analysis_json

# --- Configuration ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CommercialAnalyst")
PERSIST_ASSIGNMENTS = os.getenv("DL_PERSIST_ASSIGNMENTS", "0") == "1"
STRICT_CLUSTER_WRITEBACK = os.getenv("DL_STRICT_CLUSTER_WRITEBACK", "0") == "1"
MIN_CLUSTER_SHARE_FOR_NAMING = float(os.getenv("DL_MIN_CLUSTER_SHARE_FOR_NAMING", "0.05"))
ASSIGNMENT_COVERAGE_MIN = float(os.getenv("DL_ASSIGNMENT_COVERAGE_MIN", "0.95"))
PHENOMENON_ENRICHMENT_ENABLED = str(
    os.getenv("DL_ENABLE_PHENOMENON_ENRICHMENT") or os.getenv("ENABLE_PHENOMENON_ENRICHMENT") or ""
).lower() in {"1", "true", "yes", "on"}

def _safe_dump(x):
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    md = getattr(x, "model_dump", None)
    if callable(md):
        try:
            return md(exclude_none=True)
        except Exception:
            pass
    return dict(getattr(x, "__dict__", {}) or {})

def _get_post_id(x: Any):
    if x is None:
        return None
    if isinstance(x, dict):
        return x.get("post_id") or x.get("id")
    return getattr(x, "post_id", None) or getattr(x, "id", None)

def _to_json_safe(value: Any) -> Any:
    """
    Recursively convert values into JSON-serializable types:
    - datetime/date -> ISO 8601 strings
    - Decimal -> float
    - dict/list/tuple -> walk recursively
    Leaves other primitive types unchanged.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v) for v in value]
    return value


def _normalize_text_for_hash(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _extract_usage_tokens(response: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    if response is None:
        return None, None, None
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = None
    response_tokens = None
    total_tokens = None
    try:
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_token_count") or usage.get("prompt_tokens")
            response_tokens = usage.get("candidates_token_count") or usage.get("response_tokens")
            total_tokens = usage.get("total_token_count") or usage.get("total_tokens")
        elif usage is not None:
            prompt_tokens = getattr(usage, "prompt_token_count", None) or getattr(usage, "prompt_tokens", None)
            response_tokens = getattr(usage, "candidates_token_count", None) or getattr(usage, "response_tokens", None)
            total_tokens = getattr(usage, "total_token_count", None) or getattr(usage, "total_tokens", None)
    except Exception:
        prompt_tokens = None
        response_tokens = None
        total_tokens = None
    try:
        prompt_tokens = int(prompt_tokens) if prompt_tokens is not None else None
    except Exception:
        prompt_tokens = None
    try:
        response_tokens = int(response_tokens) if response_tokens is not None else None
    except Exception:
        response_tokens = None
    try:
        total_tokens = int(total_tokens) if total_tokens is not None else None
    except Exception:
        total_tokens = None
    if total_tokens is None and (prompt_tokens is not None or response_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (response_tokens or 0)
    return prompt_tokens, response_tokens, total_tokens


def _record_llm_call_log(
    *,
    post_id: Optional[int | str],
    run_id: Optional[str],
    mode: Optional[str],
    model_name: Optional[str],
    status: str,
    latency_ms: Optional[int],
    response: Any = None,
    request_tokens: Optional[int] = None,
    response_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    if response is not None and (request_tokens is None and response_tokens is None and total_tokens is None):
        request_tokens, response_tokens, total_tokens = _extract_usage_tokens(response)
    save_llm_call_log(
        post_id=post_id,
        run_id=run_id,
        mode=mode,
        model_name=model_name,
        status=status,
        latency_ms=latency_ms,
        request_tokens=request_tokens,
        response_tokens=response_tokens,
        total_tokens=total_tokens,
    )


def build_evidence_inputs(
    evidence_set: List[Dict[str, Any]],
    comments_rows: List[Dict[str, Any]],
    *,
    max_items: Optional[int] = None,
    snippet_chars: int = 160,
) -> Dict[str, Any]:
    comment_text_map: Dict[str, str] = {}
    comment_meta_map: Dict[str, Dict[str, Any]] = {}
    for c in comments_rows or []:
        cid = c.get("comment_id") or c.get("id")
        if cid is None:
            continue
        text_val = c.get("text_raw") or c.get("text") or ""
        comment_text_map[str(cid)] = str(text_val)
        comment_meta_map[str(cid)] = {
            "author_handle": c.get("author_handle") or c.get("user"),
            "like_count": c.get("like_count") or c.get("likes") or c.get("like_sum"),
            "cluster_key": c.get("quant_cluster_id") if c.get("quant_cluster_id") is not None else c.get("cluster_key"),
        }

    alias_to_locator: Dict[str, Dict[str, str]] = {}
    alias_to_locator_key: Dict[str, str] = {}
    comment_id_to_alias: Dict[str, str] = {}
    evidence_map: Dict[str, str] = {}
    evidence_rows: Dict[str, Dict[str, Any]] = {}
    catalog_lines: List[str] = []
    counter = 1
    total = 0

    for cluster in sorted(evidence_set or [], key=lambda e: e.get("cluster_id", 0)):
        for item in cluster.get("evidence", []):
            if max_items is not None and total >= max_items:
                break
            cid = str(item.get("comment_id") or "").strip()
            if not cid or cid in comment_id_to_alias:
                continue
            alias = f"e{counter}"
            counter += 1
            comment_id_to_alias[cid] = alias
            locator = {"source": "threads", "type": "comment_id", "value": cid}
            locator_key = f"{locator['source']}:{locator['type']}:{locator['value']}"
            alias_to_locator[alias] = locator
            alias_to_locator_key[alias] = locator_key

            raw_text = comment_text_map.get(cid) or item.get("text") or ""
            normalized = _normalize_text_for_hash(raw_text)
            span_text = normalized[:snippet_chars]
            capture_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None
            cluster_key = item.get("cluster_id")
            meta = comment_meta_map.get(cid) or {}
            evidence_map[locator_key] = raw_text
            evidence_rows[locator_key] = {
                "cluster_key": cluster_key if cluster_key is not None else meta.get("cluster_key"),
                "author_handle": meta.get("author_handle"),
                "like_count": item.get("like_count", meta.get("like_count")),
                "span_text": span_text,
                "capture_hash": capture_hash,
            }
            catalog_lines.append(f"[{alias}] {span_text}")
            total += 1
        if max_items is not None and total >= max_items:
            break

    catalog_for_prompt = "\n".join(catalog_lines) if catalog_lines else "(no evidence available)"
    return {
        "catalog_for_prompt": catalog_for_prompt,
        "alias_to_locator": alias_to_locator,
        "alias_to_locator_key": alias_to_locator_key,
        "comment_id_to_alias": comment_id_to_alias,
        "evidence_map": evidence_map,
        "evidence_rows": evidence_rows,
    }


def _compute_claim_key(claim: Any, evidence_locator_keys: List[str]) -> str:
    cluster_keys = list({int(k) for k in (claim.cluster_keys or []) if k is not None})
    if claim.cluster_key is not None and int(claim.cluster_key) not in cluster_keys:
        cluster_keys.append(int(claim.cluster_key))
    cluster_keys_sorted = sorted(cluster_keys)
    normalized_text = _normalize_text_for_hash(getattr(claim, "text", "") or "")
    evidence_sorted = sorted({str(k) for k in evidence_locator_keys if k})
    payload = {
        "post_id": getattr(claim, "post_id", None),
        "run_id": getattr(claim, "run_id", None),
        "claim_type": getattr(claim, "claim_type", None),
        "scope": getattr(claim, "scope", None),
        "cluster_keys": cluster_keys_sorted,
        "text": normalized_text,
        "evidence": evidence_sorted,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _resolve_claim_aliases(
    claim_pack: Any,
    *,
    alias_to_locator: Dict[str, Dict[str, str]],
    alias_to_locator_key: Dict[str, str],
    evidence_rows: Dict[str, Dict[str, Any]],
) -> None:
    for claim in claim_pack.claims:
        aliases = [str(a).strip() for a in (claim.evidence_ids or []) if str(a).strip()]
        claim.evidence_aliases = list(aliases)
        locator_keys: List[str] = []
        resolved_ids: List[str] = []
        evidence_refs: List[Dict[str, Any]] = []
        alias_unknown: List[str] = []
        for alias in aliases:
            locator = alias_to_locator.get(alias)
            locator_key = alias_to_locator_key.get(alias)
            if not locator or not locator_key:
                alias_unknown.append(alias)
                continue
            locator_keys.append(locator_key)
            resolved_ids.append(locator.get("value") or "")
            row = evidence_rows.get(locator_key) or {}
            evidence_refs.append(
                {
                    "source": locator.get("source") or "threads",
                    "locator": {"type": locator.get("type"), "value": locator.get("value")},
                    "capture_hash": row.get("capture_hash"),
                    "span_text": row.get("span_text"),
                }
            )
        claim.evidence_locator_keys = locator_keys
        claim.evidence_ids = [cid for cid in resolved_ids if cid]
        claim.evidence_refs = evidence_refs
        if alias_unknown:
            claim.evidence_aliases_unknown = alias_unknown
        if not getattr(claim, "cluster_keys", None) and getattr(claim, "cluster_key", None) is not None:
            try:
                claim.cluster_keys = [int(claim.cluster_key)]
            except Exception:
                claim.cluster_keys = []
        if claim.primary_cluster_key is None and claim.cluster_keys:
            claim.primary_cluster_key = min(claim.cluster_keys)
        claim.claim_key = _compute_claim_key(claim, locator_keys)


def _build_llm_stub_json(aliases: List[str], cluster_keys: List[int]) -> Dict[str, Any]:
    if not aliases:
        return {}
    ev = aliases[:2] if len(aliases) >= 2 else aliases[:1]
    claims = [
        {
            "text": "此帖留言主要呈現一致或聚焦的反應方向。",
            "evidence_ids": ev,
            "scope": "post",
            "claim_type": "summarize",
            "tags": ["summary.one_line"],
        },
        {
            "text": "討論重點集中在原帖觀點與其回應延伸。",
            "evidence_ids": ev,
            "scope": "post",
            "claim_type": "interpret",
            "tags": ["narrative_stack.l1"],
        },
    ]
    if cluster_keys:
        claims.append(
            {
                "text": "此群組以回應原帖為主，延伸細節與觀點。",
                "evidence_ids": ev,
                "scope": "cluster",
                "claim_type": "summarize",
                "cluster_key": int(cluster_keys[0]),
                "tags": ["battlefield_map.role"],
            }
        )
    return {
        "summary": {"one_line": "留言呈現一致或聚焦的反應方向", "evidence_comment_ids": ev},
        "claims": claims,
    }


def _build_system_failure_hypothesis(reason: str) -> Dict[str, Any]:
    return {
        "type": "system_failure",
        "reason": reason,
        "confidence_cap": 0.2,
        "retryable": False,
        "source": "STATUS_TO_ACTION_POLICY",
    }


def _reverse_map_evidence_ids(payload: Dict[str, Any], alias_to_locator: Dict[str, Dict[str, str]]) -> tuple[Dict[str, Any], List[str]]:
    """
    Replace ephemeral aliases inside evidence_comment_ids lists. Unknown aliases collected.
    """
    unknown: List[str] = []

    def _walk(node: Any):
        if isinstance(node, dict):
            new_node = {}
            for k, v in node.items():
                if k == "evidence_comment_ids" and isinstance(v, list):
                    mapped: List[Any] = []
                    for ev in v:
                        if isinstance(ev, str):
                            locator = alias_to_locator.get(ev)
                            if locator:
                                mapped.append(locator.get("value"))
                            else:
                                unknown.append(ev)
                        else:
                            mapped.append(ev)
                    new_node[k] = mapped
                else:
                    new_node[k] = _walk(v)
            return new_node
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return node

    return _walk(payload), sorted(set(unknown))


def _validate_explainability_gate(
    analysis_payload: Dict[str, Any],
    bundle: Dict[str, Any],
    quant_calc_data: Dict[str, Any],
    tree_metrics: Dict[str, Any],
) -> List[str]:
    errors: List[str] = []
    comment_ids = {str(c.get("comment_id")) for c in (bundle.get("comments") or []) if c.get("comment_id")}
    hard_metrics = quant_calc_data.get("hard_metrics") if isinstance(quant_calc_data, dict) else {}
    quant_keys = set((hard_metrics or {}).keys())
    tree_keys = set((tree_metrics or {}).keys())
    def _validate_block(block: Dict[str, Any], label: str) -> None:
        evidence = block.get("evidence_comment_ids") or []
        if len(evidence) < 2:
            errors.append(f"{label}.evidence_comment_ids")
        for ev in evidence:
            if str(ev) not in comment_ids:
                errors.append(f"{label}.evidence_missing:{ev}")
        facts = block.get("supporting_quant_facts") or []
        if not facts:
            errors.append(f"{label}.supporting_quant_facts")
            return
        valid_fact = False
        for item in facts:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            key = item.get("key")
            if source == "hard_metrics" and key in quant_keys:
                valid_fact = True
            elif source == "tree_metrics" and key in tree_keys:
                valid_fact = True
        if not valid_fact:
            errors.append(f"{label}.supporting_quant_facts.invalid")

    for idx, entry in enumerate(analysis_payload.get("battlefield_map") or []):
        if isinstance(entry, dict):
            _validate_block(entry, f"battlefield_map[{idx}]")
    for key in ("strategic_verdict", "structural_insight"):
        block = analysis_payload.get(key)
        if isinstance(block, dict):
            _validate_block(block, key)

    return errors


def _evidence_compliance_errors(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    bmap = payload.get("battlefield_map")
    if isinstance(bmap, list):
        for idx, entry in enumerate(bmap):
            evs = entry.get("evidence_comment_ids") if isinstance(entry, dict) else None
            if not (isinstance(evs, list) and len([e for e in evs if e]) >= 2):
                errors.append(f"battlefield_map[{idx}].evidence_comment_ids<2")
    return errors


def _build_novelty_input_text(post_text: str, raw_comments: Optional[List[Dict[str, Any]]], k: int = 3) -> str:
    if not raw_comments:
        return (post_text or "").strip()
    if not isinstance(raw_comments, list):
        return (post_text or "").strip()
    sorted_comments = sorted(raw_comments, key=get_like_count, reverse=True)
    snippets: List[str] = []
    for c in sorted_comments[:k]:
        text = str(c.get("text") or "").strip()
        if text:
            snippets.append(text)
    if not snippets:
        return (post_text or "").strip()
    joined = "\n---\n".join(snippets)
    return joined[:2000]


def _normalize_axis_alignment(
    raw_json: Dict[str, Any],
    axis_manager: AxisManager,
    input_text: str,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    axis_raw = _get_case_insensitive(raw_json, "axis_alignment")
    if axis_raw is None:
        return None, None
    try:
        axis_block = AxisAlignmentBlock.model_validate(axis_raw)
    except ValidationError as e:
        return None, f"axis_alignment_invalid:{e.errors()}"
    except Exception as e:
        return None, f"axis_alignment_invalid:{str(e)}"

    axis_dict = axis_block.model_dump()
    axis_dict.setdefault("meta", {})
    axis_dict["meta"]["library_version"] = axis_manager.library_version

    extension_reason = None
    is_extension_candidate = False
    for axis in axis_dict.get("axes", []):
        score = axis.get("score")
        axis_name = axis.get("axis_name")
        matched_anchor_id = axis.get("matched_anchor_id")
        score_val = float(score) if score is not None else 0.0
        axis["is_affirmative"] = score_val >= 0.6
        if isinstance(axis_name, str) and score_val >= 0.75:
            is_novel, reason = axis_manager.lexical_novelty_heuristic(
                input_text,
                axis_name,
                score_val,
                matched_anchor_id,
            )
            if is_novel:
                is_extension_candidate = True
                if extension_reason is None:
                    extension_reason = reason

    axis_dict["meta"]["is_extension_candidate"] = is_extension_candidate
    axis_dict["meta"]["extension_reason"] = extension_reason
    return axis_dict, None


def _coerce_cluster_key(val: Any) -> Optional[int]:
    """
    Convert cluster identifiers like "Cluster 2" or 2 into ints; return None if not parseable.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return int(val)
        except Exception:
            return None
    try:
        m = re.search(r"(\d+)", str(val))
        if m:
            return int(m.group(1))
    except Exception:
        return None
    return None


def _get_case_insensitive(d: Dict[str, Any], key: str) -> Any:
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return None


def _adapt_battlefield_to_cluster_insights(battlefield_map: Any) -> List[Dict[str, Any]]:
    """
    Map battlefield_map entries into Cluster_Insights-like schema for downstream normalization.
    """
    adapted: List[Dict[str, Any]] = []
    if not isinstance(battlefield_map, list):
        return adapted
    for entry in battlefield_map:
        if not isinstance(entry, dict):
            continue
        cid = _coerce_cluster_key(entry.get("cluster_id") or entry.get("cluster"))
        if cid is None:
            continue
        label = entry.get("label") or entry.get("role")
        summary = entry.get("summary") or entry.get("rationale")
        tactic_val = entry.get("tactic")
        tactics: Optional[List[str]] = None
        if tactic_val is not None:
            tactics = tactic_val if isinstance(tactic_val, list) else [tactic_val]
        adapted.append(
            {
                "cluster_key": cid,
                "label": label,
                "summary": summary,
                "tactics": tactics,
                "tactic_summary": entry.get("tactic_summary"),
            }
        )
    return adapted


def _log_timing(segment: str, start_ts: float):
    """Standardized timing logger in ms."""
    dt_ms = int((perf_counter() - start_ts) * 1000)
    logger.info("[Timing] segment=%s dt_ms=%s", segment, dt_ms)
    return dt_ms


def _comment_key_snapshot(comments: Optional[List[Dict[str, Any]]], limit: int = 3) -> List[Dict[str, Any]]:
    """
    Light-weight debug snapshot of the first few comments to ensure cluster ids persist.
    """
    snapshot: List[Dict[str, Any]] = []
    for c in (comments or [])[:limit]:
        snapshot.append(
            {
                "id": c.get("id"),
                "comment_id": c.get("comment_id"),
                "user": c.get("user"),
                "cluster_key": c.get("cluster_key"),
                "quant_cluster_id": c.get("quant_cluster_id"),
            }
        )
    return snapshot


def _call_gemini_with_retry(
    model,
    payload_str: str,
    max_attempts: int = 3,
    *,
    timeout_seconds: Optional[float] = None,
    generation_config: Optional[Dict[str, Any]] = None,
):
    if timeout_seconds is None:
        try:
            timeout_seconds = float(os.getenv("DL_LLM_TIMEOUT_SECONDS", "90"))
        except Exception:
            timeout_seconds = 90.0
    total_start = perf_counter()
    for attempt in range(1, max_attempts + 1):
        attempt_start = perf_counter()
        try:
            if timeout_seconds and timeout_seconds > 0:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(model.generate_content, payload_str, generation_config=generation_config)
                    result = fut.result(timeout=timeout_seconds)
            else:
                result = model.generate_content(payload_str, generation_config=generation_config)
            logger.info(
                "[Timing] segment=_call_gemini_with_retry.attempt attempt=%s dt_ms=%s",
                attempt,
                int((perf_counter() - attempt_start) * 1000),
            )
            logger.info(
                "[Timing] segment=_call_gemini_with_retry.total dt_ms=%s",
                int((perf_counter() - total_start) * 1000),
            )
            return result
        except Exception as e:
            logger.info(
                "[Timing] segment=_call_gemini_with_retry.attempt attempt=%s dt_ms=%s",
                attempt,
                int((perf_counter() - attempt_start) * 1000),
            )
            msg = str(e)
            transient = any(tok in msg for tok in ["InternalServerError", "500", "Overloaded", "ResourceExhausted", "UNAVAILABLE"])
            if not transient:
                raise
            if attempt == max_attempts:
                total_dt = int((perf_counter() - total_start) * 1000)
                logger.info("[Timing] segment=_call_gemini_with_retry.total dt_ms=%s", total_dt)
                raise RuntimeError(f"Gemini transient error after {max_attempts} attempts: {msg}") from e
            sleep_seconds = (2 ** attempt) + random.uniform(0, 0.3)
            logger.warning(
                f"[Analyst] ⚠️ Gemini transient error (Attempt {attempt}/{max_attempts}). Retrying in {sleep_seconds:.1f}s..."
            )
            time.sleep(sleep_seconds)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

DEFAULT_MODEL_CLAIMS_ONLY = "models/gemini-2.5-flash"
DEFAULT_MODEL_LEGACY_WRITER = "models/gemini-2.5-pro"
ALLOWED_NARRATIVE_MODES = {"disabled", "claims_only", "legacy_writer"}


def _resolve_narrative_mode() -> str:
    explicit = os.getenv("DL_NARRATIVE_MODE")
    if explicit is not None and explicit.strip():
        mode = explicit.strip().lower()
        if mode in ALLOWED_NARRATIVE_MODES:
            return mode
        logger.warning("[Analyst] Invalid DL_NARRATIVE_MODE=%s; falling back to disabled", explicit)
        return "disabled"
    if os.getenv("DL_ENABLE_NARRATIVE", "false").lower() == "true":
        return "legacy_writer"
    return "disabled"


def _resolve_model_name(mode: str) -> Optional[str]:
    if mode == "disabled":
        return None
    mode_key = None
    if mode == "claims_only":
        mode_key = os.getenv("DL_GEMINI_MODEL_CLAIMS_ONLY")
    elif mode == "legacy_writer":
        mode_key = os.getenv("DL_GEMINI_MODEL_LEGACY_WRITER")
    return (
        mode_key
        or os.getenv("GEMINI_MODEL_OVERRIDE")
        or os.getenv("DL_GEMINI_MODEL")
        or (DEFAULT_MODEL_CLAIMS_ONLY if mode == "claims_only" else DEFAULT_MODEL_LEGACY_WRITER)
    )


def _resolve_legacy_claims_enabled() -> bool:
    raw = os.getenv("DL_LEGACY_WRITER_CLAIMS", "off").strip().lower()
    return raw in {"on", "true", "1", "yes"}

phenomenon_enricher: Optional[Any] = None

# --- Helper Functions ---

def load_knowledge_base() -> str:
    """Ingests the 'Brain' of the system."""
    kb = ""
    base_path = "analysis/knowledge_base"
    try:
        with open(f"{base_path}/academic_theory.txt", "r") as f:
            kb += f"\n=== [PART 1: THEORY DEFINITIONS] ===\n{f.read()}\n"
        with open(f"{base_path}/step3_framework.txt", "r") as f:
            kb += f"\n=== [PART 2: ANALYTICAL PROTOCOL] ===\n{f.read()}\n"
    except Exception as e:
        logger.error(f"❌ Failed to load knowledge base: {e}")
        return "ERROR: Knowledge base missing."
    return kb


def get_like_count(comment: Dict[str, Any]) -> int:
    """Return a normalized like_count field from possible sources."""
    try:
        return int(comment.get("like_count", comment.get("likes", 0)) or 0)
    except Exception:
        return 0


def merge_cluster_insights(cluster_summary: Dict[str, Any], cluster_insights: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge optional name/summary/tactics fields into cluster_summary keyed by cluster_key.
    """
    if not cluster_summary or not isinstance(cluster_summary, dict):
        return cluster_summary or {}
    clusters = cluster_summary.get("clusters")
    if not isinstance(clusters, dict):
        return cluster_summary

    normalized: Dict[str, Dict[str, Any]] = {}
    if isinstance(cluster_insights, dict):
        for k, v in cluster_insights.items():
            if isinstance(v, dict):
                normalized[str(k)] = v
    elif isinstance(cluster_insights, list):
        for item in cluster_insights:
            if not isinstance(item, dict):
                continue
            ck = item.get("cluster_key")
            if ck is None:
                ck = item.get("key")
            if ck is None:
                ck = item.get("id")
            if ck is None:
                continue
            try:
                ck_int = int(ck)
            except Exception:
                continue
            normalized[str(ck_int)] = item

    for cid_key, info in clusters.items():
        if not isinstance(info, dict):
            continue
        insight = normalized.get(str(cid_key))
        if insight is None:
            try:
                insight = normalized.get(str(int(cid_key)))
            except Exception:
                insight = None
        if not isinstance(insight, dict):
            continue
        name = insight.get("name") or insight.get("label")
        summary = insight.get("summary") or insight.get("tactic_summary")
        tactics = insight.get("tactics")
        if isinstance(name, str) and name.strip():
            info["name"] = name.strip()
        if isinstance(summary, str) and summary.strip():
            info["summary"] = summary.strip()
        if tactics:
            info["tactics"] = tactics
    return cluster_summary


def normalize_cluster_insights(raw: Any) -> List[Dict[str, Any]]:
    """
    Accepts dict keyed by str/int OR list[dict].
    Returns list[dict] with cluster_key int and normalized fields.
    """
    normalized_list: List[Dict[str, Any]] = []

    def _norm_tactics(val: Any) -> Optional[List[str]]:
        if val is None:
            return None
        if isinstance(val, str):
            return [val]
        if isinstance(val, (list, tuple)):
            return [str(x) for x in val if x is not None]
        if isinstance(val, dict):
            name = val.get("name") or val.get("label") or val.get("tactic")
            return [str(name)] if name else None
        return None

    iterable = []
    if isinstance(raw, dict):
        iterable = [
            {**v, "cluster_key": k} for k, v in raw.items() if isinstance(v, dict)
        ]
    elif isinstance(raw, list):
        iterable = [item for item in raw if isinstance(item, dict)]

    for item in iterable:
        ck = item.get("cluster_key")
        if ck is None:
            ck = item.get("key")
        if ck is None:
            ck = item.get("id")
        if ck is None:
            ck = item.get("cluster_id")
        try:
            ck_int = int(ck)
        except Exception:
            continue
        label = item.get("label") or item.get("name")
        summary = item.get("summary") or item.get("tactic_summary")
        tactics = _norm_tactics(item.get("tactics"))
        tactic_summary = item.get("tactic_summary")
        normalized_list.append(
            {
                "cluster_key": ck_int,
                "label": label,
                "summary": summary,
                "tactics": tactics,
                "tactic_summary": tactic_summary,
            }
        )
    return normalized_list

def fetch_enriched_post(supabase: Client) -> Dict:
    """
    Fetches the latest post that has passed the Vision Worker stage.
    Criteria: images != null AND images[0].visual_rhetoric != null
    """
    # Fetch recent 50 posts to find a valid candidate
    resp = supabase.table("threads_posts").select("*").order("created_at", desc=True).limit(50).execute()
    
    for row in resp.data:
        imgs = row.get('images', [])
        # Check if the first image has been analyzed by Vision Worker
        if imgs and isinstance(imgs, list) and len(imgs) > 0:
            if imgs[0].get('visual_rhetoric'): 
                return row
    return None

def format_comments_for_context(comments: List[Dict]) -> str:
    """Formats comments to highlight HEAD vs TAIL dynamics for L3 Analysis."""
    if not comments: return "No comments available."
    
    # Sort by Likes (Head)
    sorted_likes = sorted(comments, key=lambda x: get_like_count(x), reverse=True)
    head = sorted_likes[:10]
    
    # Sort by Time/Index (Tail - utilizing ingestion order)
    tail = comments[-10:] if len(comments) > 10 else []
    
    txt = "--- [HEAD COMMENTS (Mainstream Consensus)] ---\n"
    for c in head:
        user = c.get('user', 'anon')
        text = str(c.get('text', '')).replace('\n', ' ')
        likes = get_like_count(c)
        txt += f"- [{user}] ({likes} likes): {text}\n"
        
    txt += "\n--- [TAIL COMMENTS (Recent/Emerging Dissent)] ---\n"
    for c in tail:
        user = c.get('user', 'anon')
        text = str(c.get('text', '')).replace('\n', ' ')
        likes = get_like_count(c)
        txt += f"- [{user}] ({likes} likes): {text}\n"
        
    return txt


def format_comments_for_ai(raw_comments: List[Dict[str, Any]], max_count: int = 40) -> str:
    """
    Prepare a compact, popularity-sorted comment list for the LLM, including cluster ids.
    """
    if not raw_comments:
        return "No public comments found."
    if not isinstance(raw_comments, list):
        return "Comments data format error."

    top_comments = heapq.nlargest(max_count, raw_comments, key=get_like_count)
    output = []
    for i, c in enumerate(top_comments):
        user = c.get("user", "Unknown")
        text = str(c.get("text", "")).replace("\n", " ")
        likes = get_like_count(c)
        cluster_id = c.get("quant_cluster_id", -1)
        cluster_tag = f" [Cluster {cluster_id}]" if cluster_id != -1 else ""
        output.append(f"[{i+1}]{cluster_tag} User: {user} | Likes: {likes} | Content: {text}")
    return "\n".join(output)


def format_hard_metrics_context(hard_metrics: Dict[str, Any]) -> str:
    if not hard_metrics:
        return "[HARD METRICS CONTEXT]\n(no data)"
    lines = [
        "[HARD METRICS CONTEXT]",
        f"gini_like_share={hard_metrics.get('gini_like_share', 0)}",
        f"entropy_like_share={hard_metrics.get('entropy_like_share', 0)}",
        f"dominance_ratio_top1={hard_metrics.get('dominance_ratio_top1', 0)}",
    ]
    mdi = hard_metrics.get("minority_dominance_index") or {}
    lines.append(
        "minority_dominance_index: "
        f"top_k={mdi.get('top_k_clusters', 0)} like_share={mdi.get('like_share', 0)} size_share={mdi.get('size_share', 0)}"
    )
    return "\n".join(lines)


def format_claims_only_metrics(hard_metrics: Dict[str, Any]) -> str:
    if not hard_metrics:
        return "(no metrics)"
    keys = [
        "n_comments",
        "n_clusters",
        "dominance_ratio_top1",
        "dominance_ratio_top2",
        "math_homogeneity",
    ]
    lines = ["[MIN HARD METRICS]"]
    for key in keys:
        if key in hard_metrics:
            lines.append(f"{key}={hard_metrics.get(key)}")
    return "\n".join(lines)


def build_claims_only_prompt(
    *,
    post_data: Dict[str, Any],
    cluster_context: str,
    claims_metrics_block: str,
    evidence_catalog: str,
    strict_json_only: bool = False,
) -> tuple[str, str]:
    user_content = f"""
[POST]
post_id={post_data.get('id')}
author={post_data.get('author')}
text="{post_data.get('post_text')}"

[CLUSTERS]
{cluster_context}

[MIN_METRICS]
{claims_metrics_block}

[EVIDENCE_CATALOG]
{evidence_catalog}
"""
    strict_header = "You must output valid JSON only. No prose." if strict_json_only else "Output only auditable claims."
    system_prompt = f"""
You are DiscourseLens Claims Extractor. {strict_header}

Language:
- All output MUST be in Traditional Chinese (zh-Hant). Do NOT output English except proper nouns.

Rules:
1) Output JSON only inside a ```json``` block.
2) Every claim MUST include evidence_ids (aliases only, e1/e2/..). If you cannot cite, omit that claim.
3) Do NOT include raw comment_ids or user handles in the output.
4) Keep each claim as a single sentence.
5) Use tags to indicate where claims should project:
   - summary.one_line
   - narrative_stack.l1
   - narrative_stack.l2
   - narrative_stack.l3
   - strategic_verdict.verdict
   - structural_insight.counterfactual_analysis
   - battlefield_map.role

Output schema:
```json
{{
  "claims": [
    {{
      "text": "一句可審計的陳述",
      "evidence_ids": ["e1","e2"],
      "scope": "post|cluster|cross_cluster",
      "claim_type": "summarize|interpret|infer",
      "cluster_key": 0,
      "tags": ["summary.one_line"]
    }}
  ]
}}
```
"""
    return system_prompt.strip(), user_content.strip()


def build_analysis_skeleton(
    post_data: Dict[str, Any],
    bundle: Dict[str, Any],
    quant_result: Dict[str, Any],
    quant_calc_data: Dict[str, Any],
    tree_metrics: Dict[str, Any],
    cluster_samples: Dict[str, Any],
) -> Dict[str, Any]:
    battlefield_matrix = compute_battlefield_matrix(
        assignments=quant_result.get("assignments") or [],
        comments=bundle.get("comments") or [],
        edges_total_db=bundle.get("edges_total"),
        edges_db=bundle.get("edges_rows"),
        post_root_internal_id=bundle.get("post_root_id"),
    )
    analysis_v4 = build_and_validate_analysis_json(
        post_data=post_data,
        llm_data={},
        cluster_data=cluster_samples or {},
        full_report=None,
    )
    analysis_payload = _safe_dump(analysis_v4)
    hard_metrics = dict(quant_calc_data.get("hard_metrics") or {})
    hard_metrics["tree_metrics"] = tree_metrics or {}
    analysis_payload["hard_metrics"] = hard_metrics
    analysis_payload["per_cluster_metrics"] = quant_calc_data.get("per_cluster_metrics") or []
    analysis_payload["clusters"] = quant_calc_data.get("per_cluster_metrics") or []
    analysis_payload["reply_matrix"] = battlefield_matrix
    meta = analysis_payload.get("meta") or {}
    meta.update(
        {
            "bundle_version": bundle.get("bundle_version"),
            "bundle_id": bundle.get("bundle_id"),
            "ordering_rule": bundle.get("ordering_rule"),
            "ordering_key_hash": bundle.get("ordering_key_hash"),
            "tree_repair_status": bundle.get("tree_repair_status"),
            "cluster_run_id": quant_result.get("cluster_run_id"),
            "cluster_fingerprint": quant_result.get("cluster_fingerprints") or {},
            "stage": "skeleton",
            "narrative_status": "pending",
        }
    )
    analysis_payload["meta"] = meta
    return analysis_payload


def _extract_narrative_patch(analysis_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(analysis_payload, dict):
        return {}
    narrative_keys = [
        "narrative_stack",
        "segments",
        "battlefield_map",
        "strategic_verdict",
        "structural_insight",
        "summary",
        "battlefield",
        "axis_alignment",
        "full_report",
        "emotional_pulse",
        "phenomenon",
        "danger",
    ]
    patch = {k: analysis_payload.get(k) for k in narrative_keys if k in analysis_payload}
    meta = analysis_payload.get("meta") or {}
    patch_meta = {
        "stage": meta.get("stage"),
        "narrative_status": meta.get("narrative_status"),
        "narrative_mode": meta.get("narrative_mode"),
        "llm_status": meta.get("llm_status"),
        "claims_status": meta.get("claims_status"),
        "llm_model": meta.get("llm_model"),
        "llm_payload_chars": meta.get("llm_payload_chars"),
        "llm_latency_ms": meta.get("llm_latency_ms"),
        "llm_error": meta.get("llm_error"),
        "attempt_count": meta.get("attempt_count"),
        "last_action": meta.get("last_action"),
        "retry_history": meta.get("retry_history"),
        "last_error_type": meta.get("last_error_type"),
    }
    if "claims" in meta:
        patch_meta["claims"] = meta.get("claims")
    if meta.get("hypotheses") is not None:
        patch_meta["hypotheses"] = meta.get("hypotheses")
    patch["meta"] = patch_meta
    return patch


def format_evidence_blocks(
    per_cluster_metrics: List[Dict[str, Any]],
    evidence_set: List[Dict[str, Any]],
    comment_id_to_alias: Dict[str, str],
    evidence_rows: Dict[str, Dict[str, Any]],
) -> str:
    if not per_cluster_metrics or not evidence_set:
        return ""
    metrics_map = {m["cluster_id"]: m for m in per_cluster_metrics}
    blocks: List[str] = []
    for cluster in sorted(evidence_set, key=lambda e: e.get("cluster_id", 0)):
        cid = cluster.get("cluster_id")
        m = metrics_map.get(cid, {})
        header = (
            f"[CLUSTER {cid}] size={round(m.get('size_share', 0)*100,1)}% "
            f"like_share={round(m.get('like_share', 0)*100,1)}% "
            f"likes_per_comment={m.get('likes_per_comment', 0)}"
        )
        blocks.append(header)
        for item in cluster.get("evidence", []):
            cid_str = str(item.get("comment_id") or "")
            alias = comment_id_to_alias.get(cid_str, "e?")
            locator_key = f"threads:comment_id:{cid_str}" if cid_str else ""
            row = evidence_rows.get(locator_key) or {}
            text = (row.get("span_text") or item.get("text") or "").replace("\n", " ")
            likes = row.get("like_count") if row.get("like_count") is not None else item.get("like_count", 0)
            blocks.append(f"[{alias}] {text} (Likes: {likes})")
    return "\n".join(blocks)


def build_cluster_summary_and_samples(comments_with_quant: List[Dict[str, Any]], max_samples_per_cluster: int = 5) -> Dict[str, Any]:
    clusters: Dict[int, List[Dict[str, Any]]] = {}
    noise: List[Dict[str, Any]] = []
    total_count = len(comments_with_quant)
    for c in comments_with_quant:
        like_count = get_like_count(c)
        c["like_count"] = like_count
        cid_raw = c.get("quant_cluster_id", -1)
        try:
            cid = int(cid_raw) if cid_raw is not None else -1
        except Exception:
            cid = -1
        if cid >= 0:
            clusters.setdefault(cid, []).append(c)
        else:
            noise.append(c)

    cluster_summary: Dict[str, Any] = {}
    for cid, clist in clusters.items():
        sorted_comments = sorted(clist, key=get_like_count, reverse=True)
        samples = [
            {
                **comment,
                "like_count": get_like_count(comment),
                "cluster_key": cid,
            }
            for comment in sorted_comments[:max_samples_per_cluster]
        ]
        pct = (len(clist) / total_count) if total_count else 0
        cluster_summary[str(cid)] = {
            "cluster_id": cid,
            "cluster_key": cid,
            "count": len(clist),
            "pct": round(pct, 4),
            "pct_label": f"{round(pct * 100, 1)}%" if pct else "0%",
            "samples": samples,
        }

    noise_count = len(noise)
    noise_pct = (noise_count / total_count) if total_count else 0

    return {
        "clusters": cluster_summary,
        "noise": {
            "cluster_id": -1,
            "count": noise_count,
            "pct": round(noise_pct, 4),
            "pct_label": f"{round(noise_pct * 100, 1)}%" if noise_pct else "0%",
            "samples": [{**comment, "like_count": get_like_count(comment)} for comment in noise[:max_samples_per_cluster]],
        },
    }

def format_visuals(images: List[Dict]) -> str:
    """Formats Vision Worker output for the Analyst."""
    if not images: return "No visuals."
    txt = ""
    for i, img in enumerate(images):
        if not isinstance(img, dict):
            continue
        txt += f"[Image {i+1}]\n"
        txt += f"  - Scene Label: {img.get('scene_label', 'N/A')}\n"
        txt += f"  - Visual Rhetoric: {img.get('visual_rhetoric', 'N/A')}\n"
        txt += f"  - OCR Text: {img.get('full_text', 'N/A')}\n"
    return txt

def extract_json_block(text: str) -> Dict:
    """Robustly extracts JSON from Markdown text."""
    try:
        match = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        # Fallback: try finding just the brace structure
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        logger.warning(f"JSON Extraction failed: {e}")
    return {}


def extract_block_between(text: str, start_pattern: str, end_patterns: List[str]) -> str:
    """
    Find first occurrence of start_pattern (regex), and grab everything until
    the first occurrence of any end_pattern or end of string.
    Return a stripped string, or "" if not found.
    """
    try:
        start_match = re.search(start_pattern, text, re.DOTALL)
        if not start_match:
            return ""
        start_idx = start_match.start()

        end_idx = len(text)
        for ep in end_patterns:
            m = re.search(ep, text[start_idx + 1 :], re.DOTALL)
            if m:
                candidate_end = start_idx + 1 + m.start()
                end_idx = min(end_idx, candidate_end)

        block = text[start_idx:end_idx].strip()
        block = re.sub(r"\n{3,}", "\n\n", block)
        if len(block) > 1200:
            block = block[:1200].rstrip() + "..."
        return block
    except Exception:
        return ""


def extract_l1_summary(full_markdown: str) -> str:
    """
    Extract the L1 section (Illocutionary Act) from the markdown.
    """
    return extract_block_between(
        full_markdown,
        r"(?i)(?:^|\n|#|\*+)\s*L1[:\s-].*?",
        [
            r"(?i)(?:^|\n|#|\*+)\s*L2",
            r"(?i)SECTION",
            r"---\n\n####",
        ],
    )


def extract_l2_summary(full_markdown: str) -> str:
    """
    Extract the L2 section (Critical Strategy Analysis) from the markdown.
    """
    return extract_block_between(
        full_markdown,
        r"(?i)(?:^|\n|#|\*+)\s*L2[:\s-].*?",
        [
            r"(?i)(?:^|\n|#|\*+)\s*L3",
            r"(?i)SECTION",
            r"---\n\n####",
        ],
    )


def extract_l3_summary(full_markdown: str) -> str:
    """
    Extract the L3 Battlefield / Faction Analysis section.
    """
    return extract_block_between(
        full_markdown,
        r"(?i)(?:^|\n|#|\*+)\s*L3[:\s-].*?",
        [
            r"(?i)SECTION",
            r"---\n\n####",
            r"#### \*\*",
        ],
    )


def infer_tone_from_primary(primary: str) -> Dict[str, float]:
    """
    Very soft fallback when Tone_Fingerprint is missing.
    Only looks at Quantifiable_Tags.Primary_Emotion, which is a short label
    controlled by our schema (e.g. 'Weary Pride', 'Cynical Anger').
    """
    base = {"cynicism": 0.0, "anger": 0.0, "hope": 0.0, "despair": 0.0}
    if not primary:
        return base

    p = primary.lower().strip()

    if "cynic" in p or "weary" in p:
        base["cynicism"] = 0.7
    if "anger" in p or "indignation" in p:
        base["anger"] = 0.7
    if "hope" in p:
        base["hope"] = 0.7
    if "despair" in p or "hopeless" in p:
        base["despair"] = 0.7

    if all(v == 0.0 for v in base.values()) and primary:
        base["cynicism"] = 0.5
        base["hope"] = 0.5

    return base

# --- The Brain ---

def generate_commercial_report(post_data: Dict, supabase: Client):
    global phenomenon_enricher
    if PHENOMENON_ENRICHMENT_ENABLED and PhenomenonEnricher and phenomenon_enricher is None:
        phenomenon_enricher = PhenomenonEnricher(supabase, enabled=True, run_inline=True)
    assignment_coverage_fail = False
    assignment_coverage_reason = None
    analysis_version = "v6.1"
    analysis_build_id = str(uuid.uuid4())

    def _failure_dict(post_id: str | None, version: str, build_id: str, reason: str, missing: list[str], error_type: str, error_detail: str, raw_preview: str = ""):
        return {
            "post_id": post_id or "",
            "analysis_json": None,
            "analysis_is_valid": False,
            "analysis_invalid_reason": reason,
            "analysis_missing_keys": missing,
            "analysis_version": version,
            "analysis_build_id": build_id,
            "error_type": error_type,
            "error_detail": error_detail,
            "raw_llm_preview": raw_preview[:1200] if raw_preview else "",
        }

    post_row_id = _get_post_id(post_data)
    if not post_row_id:
        return _failure_dict(None, analysis_version, analysis_build_id, "missing_post_id", ["post.id"], "preanalysis_guard", "post_id missing")

    narrative_mode = _resolve_narrative_mode()
    model_name = _resolve_model_name(narrative_mode)
    legacy_claims_enabled = _resolve_legacy_claims_enabled()
    claims_pipeline_enabled = narrative_mode == "claims_only" or (
        narrative_mode == "legacy_writer" and legacy_claims_enabled
    )
    logger.info("[Analyst] Narrative mode resolved: %s", narrative_mode)
    logger.info("[Analyst] Narrative model selected", extra={"mode": narrative_mode, "model": model_name})
    if narrative_mode == "legacy_writer":
        logger.info(
            "🧠 Legacy writer mode: %s",
            "report_plus_claims" if legacy_claims_enabled else "report_only",
        )
    if narrative_mode == "disabled":
        from analysis.preanalysis_runner import run_preanalysis

        run_preanalysis(int(post_row_id), prefer_sot=True, persist_assignments=True)
        preanalysis_json = load_preanalysis_json(supabase, post_row_id)
        run_id = str(((preanalysis_json or {}).get("meta") or {}).get("cluster_run_id") or "")
        logger.info(
            "🧠 Narrative disabled: quant skeleton only (post_id=%s, run_id=%s)",
            post_row_id,
            run_id,
        )
        save_analysis_json(
            post_id=str(post_row_id),
            analysis_build_id=analysis_build_id,
            json_obj={
                "meta": {
                    "stage": "skeleton",
                    "narrative_status": "disabled",
                    "narrative_mode": narrative_mode,
                    "llm_status": "disabled",
                    "claims_status": "disabled",
                    "llm_model": None,
                    "llm_payload_chars": 0,
                }
            },
            mode="mark_timeout",
            analysis_version=analysis_version,
        )
        return {"post_id": str(post_row_id), "preanalysis_only": True, "updated_fields": ["preanalysis_json", "analysis_json"]}

    axis_enabled = narrative_mode == "legacy_writer" and os.getenv("DL_ENABLE_AXIS", "False").lower() == "true"
    axis_manager = AxisManager() if axis_enabled else None

    like_count = int(post_data.get("like_count") or 0)
    is_high_impact = like_count > 500
    reply_count = int(post_data.get("reply_count") or 0)
    view_count = int(
        post_data.get("view_count")
        or post_data.get("metrics", {}).get("views", 0)
        or 0
    )
    bundle = get_canonical_comment_bundle(post_row_id, prefer_sot=True)
    comments_source = bundle.get("source")
    raw_comments = bundle.get("comments") or []
    post_data["comments"] = raw_comments
    post_data["raw_comments"] = raw_comments

    # --- THE COMMERCIAL PROMPT (ENHANCED HUNTER VERSION) ---
    logger.info("Running L0.5 Structure Mapper...")
    t_struct_map = perf_counter()
    quant_result = perform_structure_mapping_bundle(bundle, post_id=post_row_id)
    _log_timing("perform_structure_mapping", t_struct_map)
    quant_summary = {}
    cluster_samples = {}
    quant_calc_data = {}
    hard_metrics: Dict[str, Any] = {}
    cluster_size_share: List[Any] = []
    cluster_size_share_json = "[]"
    n_comments = 0
    n_clusters = 0
    naming_policy_note = (
        f"Naming policy: clusters with size_share>={MIN_CLUSTER_SHARE_FOR_NAMING} must be named; "
        "smaller clusters may remain as Other/Noise (system fills defaults)."
    )
    if quant_result:
        post_data["comments"] = quant_result["node_data"]
        for c in post_data["comments"]:
            if "cluster_key" not in c:
                c["cluster_key"] = c.get("quant_cluster_id")
        try:
            t_quant_calc = perf_counter()
            quant_calc_data = QuantCalculator.compute_from_bundle(post_row_id, bundle)
            _log_timing("QuantCalculator.compute", t_quant_calc)
        except Exception as q_err:
            logger.warning(f"[QuantCalculator] failed: {q_err}")
        stats = quant_result.get("cluster_stats", {})
        echo_count = quant_result.get("high_sim_pairs", 0)
        dominant_cluster = max(stats, key=stats.get) if stats else "None"
        cluster_run_id = quant_result.get("cluster_run_id")
        quant_summary = {
            "cluster_stats": stats,
            "high_sim_pairs": echo_count,
            "math_homogeneity": quant_result.get("math_homogeneity"),
            "clusters_ref": quant_result.get("clusters_ref"),
            "persistence": quant_result.get("persistence"),
            "hard_metrics": quant_calc_data.get("hard_metrics"),
            "per_cluster_metrics": quant_calc_data.get("per_cluster_metrics"),
            "cluster_run_id": cluster_run_id,
        }
        t_cluster_summary = perf_counter()
        cluster_samples = build_cluster_summary_and_samples(post_data.get("comments", []))
        _log_timing("build_cluster_summary_and_samples", t_cluster_summary)
        size_share_map = {}
        for m in quant_calc_data.get("per_cluster_metrics") or []:
            cid = m.get("cluster_id")
            try:
                cid_int = int(cid)
            except Exception:
                continue
            size_share_map[cid_int] = m.get("size_share", 0.0)
        for cid_key, info in (cluster_samples.get("clusters") or {}).items():
            try:
                cid_int = int(cid_key)
            except Exception:
                continue
            share = size_share_map.get(cid_int, info.get("pct", 0.0))
            if share is None:
                share = 0.0
            if share < MIN_CLUSTER_SHARE_FOR_NAMING:
                info.setdefault("name", "Other/Noise")
                info.setdefault("summary", "low-support cluster")
                info["size_share"] = share
        if isinstance(quant_calc_data.get("hard_metrics"), dict):
            hard_metrics = quant_calc_data.get("hard_metrics") or {}
            cluster_size_share = hard_metrics.get("cluster_size_share") or []
            n_comments = int(hard_metrics.get("n_comments") or 0)
            n_clusters = int(hard_metrics.get("n_clusters") or 0)
        cluster_size_share_json = json.dumps(cluster_size_share, ensure_ascii=False)
        logger.info(
            "[Analyst] Hard metrics snapshot",
            extra={
                "hard_metrics_keys": list(hard_metrics.keys()),
                "n_comments": n_comments,
                "n_clusters": n_clusters,
                "cluster_size_share_len": len(cluster_size_share),
            },
        )
        quant_context = f"""
[L0.5 STRUCTURAL SIGNALS]
- Semantic Clusters Detected: {len(stats)} (Heuristic grouping)
- Cluster Sizes: {stats} (Cluster {dominant_cluster} is dominant)
- Math_Homogeneity_Reference: {quant_result.get('math_homogeneity', 'N/A')} (Use this as a baseline for your Homogeneity Score)
- High-Similarity Echo Pairs: {echo_count} (Comments with >94% cosine similarity across different users)
- Note: Comments are tagged with 'quant_cluster_id' and 'is_template_like'.
 - Cluster Size Share: {cluster_size_share_json}
 - {naming_policy_note}
"""
    else:
        quant_context = "[L0.5 SIGNALS]: Insufficient data for structural mapping."

    tree_metrics = bundle.get("tree_metrics") or {}
    tree_metrics_context = (
        "[TREE METRICS]\n"
        f"edge_coverage={tree_metrics.get('edge_coverage')} "
        f"missing_ts_pct={tree_metrics.get('missing_ts_pct')} "
        f"max_depth={tree_metrics.get('max_depth')} "
        f"reply_edges={tree_metrics.get('reply_edges')} "
        f"partial_tree={tree_metrics.get('partial_tree')}"
    )

    skeleton_json = build_analysis_skeleton(
        post_data=post_data,
        bundle=bundle,
        quant_result=quant_result or {},
        quant_calc_data=quant_calc_data or {},
        tree_metrics=tree_metrics,
        cluster_samples=cluster_samples,
    )
    physics_payload, golden_samples_payload, golden_samples_detail = compute_physics_and_golden_samples(
        comments=post_data.get("comments") or [],
        quant_result=quant_result or {},
        quant_calc_data=quant_calc_data or {},
        reply_matrix=skeleton_json.get("reply_matrix"),
    )
    skeleton_json["physics"] = physics_payload
    skeleton_json["golden_samples"] = golden_samples_payload
    skeleton_json["golden_samples_detail"] = golden_samples_detail
    skeleton_json = sanitize_analysis_json(skeleton_json)
    save_analysis_json(
        post_id=str(_get_post_id(post_data) or ""),
        analysis_build_id=analysis_build_id,
        json_obj=skeleton_json,
        mode="skeleton",
        analysis_version=analysis_version,
        analysis_is_valid=True,
        analysis_invalid_reason=None,
        analysis_missing_keys=None,
    )

    assignments = quant_result.get("assignments") if quant_result else []
    cluster_run_id = quant_result.get("cluster_run_id") if quant_result else None
    assignments_total = len(assignments or [])
    if PERSIST_ASSIGNMENTS and assignments_total and post_row_id is not None:
        try:
            assign_start = perf_counter()
            post_id_for_db = post_row_id
            try:
                post_id_for_db = int(post_row_id)
            except Exception:
                post_id_for_db = post_row_id
            res = apply_comment_cluster_assignments(
                post_id_for_db,
                assignments,
                cluster_run_id=cluster_run_id,
                bundle_id=bundle.get("bundle_id"),
                cluster_fingerprints=quant_result.get("cluster_fingerprints") if quant_result else None,
            )
            updated_rows = res.get("updated_rows") or res.get("count") or 0
            target_rows = res.get("target_rows")
            coverage = res.get("coverage")
            logger.info(
                "[Analyst] Comment cluster assignments writeback",
                extra={
                    "assignments_total": assignments_total,
                    "assignments_updated_rows": updated_rows,
                    "target_rows": target_rows,
                    "coverage_pct": round((coverage or 0) * 100, 2) if coverage is not None else None,
                    "coverage_min": ASSIGNMENT_COVERAGE_MIN,
                    "strict": STRICT_CLUSTER_WRITEBACK,
                },
            )
            if coverage is not None and coverage < ASSIGNMENT_COVERAGE_MIN:
                assignment_coverage_fail = True
                assignment_coverage_reason = f"coverage_below_min ({coverage:.3f} < {ASSIGNMENT_COVERAGE_MIN})"
            _log_timing("apply_comment_cluster_assignments", assign_start)
        except Exception:
            logger.exception("[Analyst] Comment cluster assignment writeback failed")
            raise
    else:
        logger.info(
            "[Analyst] Comment cluster assignments skipped",
            extra={
                "assignments_total": assignments_total,
                "persist_flag": PERSIST_ASSIGNMENTS,
                "post_id": post_row_id,
            },
        )

    # Prepare Data Dossier (after quant so comments carry clusters)
    t_fmt_comments = perf_counter()
    attempt_ctx = AttemptContext()
    cb_state = circuit_state(narrative_mode, model_name)
    if cb_state == "open":
        logger.warning("[Analyst] Circuit open; forcing narrative disabled", extra={"mode": narrative_mode, "model": model_name})
        system_hypotheses: List[Dict[str, Any]] = []
        if claims_pipeline_enabled:
            system_hypotheses.append(_build_system_failure_hypothesis("system_disabled"))
        attempt_ctx.last_action = "circuit_open"
        save_analysis_json(
            post_id=str(_get_post_id(post_data) or ""),
            analysis_build_id=analysis_build_id,
            json_obj={
                "meta": {
                    "stage": "final",
                    "narrative_status": "disabled",
                    "narrative_mode": "disabled",
                    "llm_status": "disabled",
                    "claims_status": "disabled",
                    "llm_model": None,
                    "llm_payload_chars": 0,
                    "last_action": attempt_ctx.last_action,
                    "attempt_count": attempt_ctx.attempt_count,
                    "retry_history": attempt_ctx.retry_history,
                    "hypotheses": system_hypotheses if system_hypotheses else None,
                }
            },
            mode="mark_timeout",
            analysis_version=analysis_version,
        )
        return {
            "post_id": str(_get_post_id(post_data) or ""),
            "analysis_build_id": analysis_build_id,
            "stage": "final",
            "narrative_status": "disabled",
            "updated_fields": ["analysis_json", "analysis_build_id"],
        }
    if cb_state == "half_open":
        logger.warning("[Analyst] Circuit half-open; forcing claims_only + flash", extra={"mode": narrative_mode, "model": model_name})
        narrative_mode = "claims_only"
        model_name = _resolve_model_name("claims_only")
        legacy_claims_enabled = False
        claims_pipeline_enabled = True
        attempt_ctx.last_action = "circuit_half_open"

    comments_for_llm = ""
    vox_populi_text = ""
    cluster_payload_for_llm: List[Dict[str, Any]] = []
    cluster_payload_json = "[]"
    cluster_brief_lines: List[str] = []

    knowledge_base = ""
    cluster_context = ""
    if narrative_mode == "legacy_writer":
        comments_for_llm = format_comments_for_ai(post_data.get("comments", []))
        _log_timing("format_comments_for_ai", t_fmt_comments)
        vox_populi_text = comments_for_llm

        if cluster_samples:
            clusters = cluster_samples.get("clusters") or {}
            noise = cluster_samples.get("noise") or {}
            if clusters:
                cluster_lines: List[str] = []
                clusters_sorted = sorted(
                    clusters.items(),
                    key=lambda kv: kv[1].get("count", 0),
                    reverse=True,
                )
                for cid, info in clusters_sorted:
                    cid_label = info.get("cluster_id", cid)
                    count = info.get("count", 0)
                    pct = info.get("pct", 0) or 0
                    pct_label = info.get("pct_label") or f"{round(pct * 100, 1)}%"
                    display_name = info.get("name") or f"Cluster {cid_label}"
                    summary_text = info.get("summary") if isinstance(info, dict) else None
                    cluster_lines.append(f"=== CLUSTER {cid_label} | {display_name} | Size: {count}, {pct_label} ===")
                    cluster_lines.append(f"Summary: {summary_text.strip() if isinstance(summary_text, str) and summary_text.strip() else '暫無摘要。'}")
                    samples = info.get("samples") or []
                    if not samples:
                        cluster_lines.append("(No representative comments captured)")
                    for idx, c in enumerate(samples, start=1):
                        user = c.get("user", "Unknown")
                        like_val = get_like_count(c)
                        text = str(c.get("text", "")).replace("\n", " ").strip()
                        cluster_lines.append(f"[C{cid_label}-{idx}] {user} ❤️ {like_val} | {text}")
                    cluster_lines.append("")
                    cluster_payload_for_llm.append(
                        {
                            "cluster_key": cid_label,
                            "size": count,
                            "keywords": info.get("keywords"),
                            "samples": [
                                {
                                    "cluster_key": cid_label,
                                    "text": s.get("text"),
                                    "likes": s.get("like_count") or s.get("likes"),
                                }
                                for s in samples
                            ],
                        }
                    )
                if noise and noise.get("count", 0) > 0:
                    noise_pct = noise.get("pct", 0) or 0
                    noise_pct_label = noise.get("pct_label") or f"{round(noise_pct * 100, 1)}%"
                    cluster_lines.append(f"=== NOISE / UNCLASSIFIED (Size: {noise.get('count', 0)}, {noise_pct_label}) ===")
                    for idx, c in enumerate(noise.get("samples") or [], start=1):
                        like_val = get_like_count(c)
                        text = str(c.get("text", "")).replace("\n", " ").strip()
                        user = c.get("user", "Unknown")
                        cluster_lines.append(f"[Noise-{idx}] {user} ❤️ {like_val} | {text}")
                vox_populi_text = "\n".join(cluster_lines).strip()
        cluster_payload_json = json.dumps(cluster_payload_for_llm, ensure_ascii=False)
    else:
        _log_timing("format_comments_for_ai", t_fmt_comments)
        if cluster_samples:
            clusters = cluster_samples.get("clusters") or {}
            noise = cluster_samples.get("noise") or {}
            if clusters:
                clusters_sorted = sorted(
                    clusters.items(),
                    key=lambda kv: kv[1].get("count", 0),
                    reverse=True,
                )
                for cid, info in clusters_sorted:
                    try:
                        cid_int = int(info.get("cluster_id", cid))
                    except Exception:
                        cid_int = cid
                    count = info.get("count", 0)
                    pct = info.get("pct", 0) or 0
                    pct_label = info.get("pct_label") or f"{round(pct * 100, 1)}%"
                    display_name = info.get("name") or f"Cluster {cid_int}"
                    summary_text = info.get("summary") if isinstance(info, dict) else None
                    cluster_brief_lines.append(
                        f"Cluster {cid_int}: size={count}, share={pct_label}, name={display_name}, summary={summary_text or '暫無摘要'}"
                    )
                    cluster_payload_for_llm.append(
                        {
                            "cluster_key": cid_int,
                            "size": count,
                            "name": display_name,
                        }
                    )
            if noise and noise.get("count", 0) > 0:
                noise_pct = noise.get("pct", 0) or 0
                noise_pct_label = noise.get("pct_label") or f"{round(noise_pct * 100, 1)}%"
                cluster_brief_lines.append(
                    f"Cluster -1 (noise): size={noise.get('count', 0)}, share={noise_pct_label}"
                )
    payload_build_start = perf_counter()

    alias_to_locator: Dict[str, Dict[str, str]] = {}
    alias_to_locator_key: Dict[str, str] = {}
    comment_id_to_alias: Dict[str, str] = {}
    evidence_map: Dict[str, str] = {}
    evidence_rows: Dict[str, Dict[str, Any]] = {}
    evidence_catalog = ""
    hard_metrics_block = ""
    evidence_blocks = ""
    claims_metrics_block = ""
    if quant_calc_data:
        evidence_inputs = build_evidence_inputs(
            quant_calc_data.get("sampled_evidence_set") or [],
            bundle.get("comments") or [],
        )
        alias_to_locator = evidence_inputs.get("alias_to_locator") or {}
        alias_to_locator_key = evidence_inputs.get("alias_to_locator_key") or {}
        comment_id_to_alias = evidence_inputs.get("comment_id_to_alias") or {}
        evidence_map = evidence_inputs.get("evidence_map") or {}
        evidence_rows = evidence_inputs.get("evidence_rows") or {}
        evidence_catalog = evidence_inputs.get("catalog_for_prompt") or ""
        if narrative_mode == "legacy_writer":
            hard_metrics_block = format_hard_metrics_context(quant_calc_data.get("hard_metrics") or {})
            evidence_blocks = format_evidence_blocks(
                quant_calc_data.get("per_cluster_metrics") or [],
                quant_calc_data.get("sampled_evidence_set") or [],
                comment_id_to_alias,
                evidence_rows,
            )
        else:
            hard_metrics_block = format_claims_only_metrics(quant_calc_data.get("hard_metrics") or {})
        claims_metrics_block = format_claims_only_metrics(quant_calc_data.get("hard_metrics") or {})
    mapping_unknown_aliases: List[str] = []
    evidence_errors: List[str] = []

    if narrative_mode == "legacy_writer":
        knowledge_base = load_knowledge_base()
        logger.info(
            "🧠 Legacy writer mode: loading knowledge_base (bytes=%s)",
            len(knowledge_base or ""),
        )
        dossier = f"""
        POST ID: {post_data['id']}
        AUTHOR: {post_data.get('author')}
        METRICS: Likes {like_count}, Replies {post_data.get('reply_count')}
        HIGH_IMPACT: {is_high_impact}
        POST TEXT: "{post_data.get('post_text')}"
        REAL_METRICS: Likes={like_count}, Replies={reply_count}, Views={view_count}
        
        [VISUAL EVIDENCE (from Vision Worker)]
        {format_visuals(post_data.get('images', []))}
        
        [COLLECTIVE DYNAMICS (Comments)]
        {format_comments_for_context(post_data.get('comments', []))}
        """

        user_content = f"""
SOURCE MATERIAL FOR ANALYSIS:

=== PART 1: THE ARTIFACT (Main Post) ===
**Author:** {post_data.get('author')}
**Post Text:** "{post_data.get('post_text')}"
**Visual Context:** The post contains {len(post_data.get('images', []))} images. (Refer to visual analysis if available).

=== PART 2: THE VOX POPULI (Public Reaction) ===
**Context:** Below are the top {len(raw_comments)} comments from the thread, sorted by engagement/likes.
**Instruction for AI:** Treat this section as the empirical evidence for "L3 Battlefield Analysis". Look for agreement, conflict, mockery, or expansion of the theme.

{vox_populi_text} 
(End of comments)

=== PART 3: STRUCTURED CLUSTERS (for grounding your Cluster_Insights) ===
Use this JSON to map cluster_key → tactics/label/summary. Always emit Cluster_Insights as a list with cluster_key.
{cluster_payload_json}

[HARD METRICS]
{hard_metrics_block}

[EVIDENCE CATALOG (Aliases Only)]
{evidence_catalog}

[CLUSTER EVIDENCE SET]
{evidence_blocks}
"""

        system_prompt = f"""
        You are 'DiscourseLens', an automated Sociological Analyst.
        Your goal is to produce a commercial-grade intelligence report.

        [KNOWLEDGE BASE (THEORY & RULES)]
        {knowledge_base}
        --------------------------------------------------
        [TARGET DATA DOSSIER]
        {dossier}
        --------------------------------------------------
        Structural Signals (L0.5): {quant_context}
        Tree Metrics (Reply Graph): {tree_metrics_context}
        === PUBLIC COMMENTS (VOX POPULI) ===
        The following are real user comments from the post, sorted by popularity.
        Use these to analyze "Collective Dynamics", "Homogeneity Score", and identify any "Spiral of Silence" or conflict.
        {vox_populi_text}

        [PROTOCOL: NOVELTY DETECTION] (!!! CRITICAL !!!)
        1. **Avoid Lazy Categorization**: Do not default to generic tags. If a post feels different, describe WHY.
        2. **Detect Sub-Variants**: Even if a post fits Sector A/B/C/D, you must identify its specific *flavor*. 
           - Example: Instead of just "Sector D (Normalcy)", distinguish between "Compensatory Consumption" vs "Routine Check-in".
        3. **Activate Sector X**: If you see a NEW trend (e.g., a specific new scam, a viral challenge, a new slang), you MUST classify it as [SECTOR X] and propose a name.
        4. **Assess Author Influence**: Classify as Low / Medium / High_KOL based on engagement and tone (HIGH_IMPACT flag indicates likely KOL).

        INSTRUCTIONS:
        1. **Hard Metrics are authoritative**: Use the provided HARD METRICS block; do NOT recompute Gini/entropy/dominance. Cite them directly when reasoning about power.
        2. **Evidence Aliases Only**: All evidence IDs are short aliases (e1, e2…) from EVIDENCE CATALOG. Use only these aliases in JSON/text. Unknown aliases invalidate the output.
        2.5 **Claims Required**: Every interpretive statement you want preserved MUST appear in `claims[]` with evidence_ids (aliases only). Any statement without evidence will be discarded downstream.
        3. **Explainability Gate**: Every cluster/faction explanation must include ≥2 evidence_comment_ids and at least 1 supporting_quant_facts entry from HARD METRICS or TREE METRICS (use source+key+value).
        3. **Apply Theory**: You MUST explicitly cite concepts from Part 1 & 2 (e.g., "This exhibits Ritualistic Reciprocity").
        4. **Analyze L3** with the VOX POPULI section: use public comments as evidence.
        5. Use Structural Signals as evidence only; refer to clusters as "Cluster 0/1/2" with size/tone. If echo pairs are high, describe as template-like/echo effect; do not claim bots unless other evidence exists.
        6. Perform cluster-level reasoning: identify ideological core per major cluster, compare majority vs minority clusters (volume vs engagement), and describe narrative collisions. Use math_homogeneity and cluster distribution to judge unity vs fragmentation.

        ### UPDATED INSTRUCTIONS FOR L3 & QUANTITATIVE METRICS:

        **On L3 Battlefield Analysis (集體動態戰場分析)**
        Do NOT assume the author is isolated. You must analyze the provided "VOX POPULI" (Comments) section:
        1.  Dominant Sentiment: Is the comment section an "Echo Chamber" (reinforcing the author) or a "Battleground" (challenging the author)?
        2.  Top Comment Check: Compare the most liked comment against the original post. Does the top comment "Ratio" the author (have more likes/support)? Or does it amplify the author's point?
        3.  Specific Dynamics: Look for:
            - The Spiral of Silence: Are dissenting views missing or being dog-piled?
            - Topic Hijacking: Are commenters changing the subject (e.g., from "Fire Accident" to "Government Incompetence")?

        [MANDATORY: FACTION ANALYSIS PROTOCOL]
        You must treat the "VOX POPULI" section as a map of factions, not a list of random users.
        - Identify Factions: Explicitly name Cluster 0 and Cluster 1 based on their ideology (e.g., "Cluster 0: The Technocratic Critics" vs "Cluster 1: The Cynical Observers").
        - Power Dynamics: Compare the Population (Size) vs. Engagement (Likes). Does a minority cluster control the most liked comments?
        - Narrative Collision: Describe exactly where the philosophical conflict lies between the clusters.
        - Use Math: Reference the Math_Homogeneity_Reference score to validate if the discourse is unified or fragmented.

        [MANDATORY: FACTION NAMING RULE — HONG KONG EDITION]
        When generating Cluster_Insights, you must name each cluster using clear, neutral Hong Kong written Chinese. The style should resemble Hong Kong newspaper commentary or public policy reports, not social media slang.
        - Avoid PRC/Taiwan internet slang (e.g., 吃瓜、杠精、反串、側翼等用語)
        - Avoid overly academic jargon (e.g., 象徵性抵抗、技術官僚式戲仿、後結構敘事等學術術語)
        - Names should be short (around 3–5 Chinese characters) and descriptive, expressing the cluster’s sentiment, stance or main concern.
        - Tone reference: 明報評論、香港電台時事節目、公共政策研究報告的用語風格。
        Examples of good names (for inspiration): 「本土情懷者」「質疑科技的一群」「對制度感到失望的聲音」「以幽默方式回應的用戶」「關注生活經驗的觀眾」.
        Academic theory (e.g., 技術官僚式戲仿) should stay in the L2/L3 narrative; Cluster_Insights.name must be everyday written labels that readers instantly grasp.

        [FACTION NAMING RULE — HONG KONG WRITTEN CHINESE]
        Cluster names MUST:
        - Be neutral, professional written Chinese
        - Avoid Mainland or Taiwanese slang (e.g., "吃瓜群", "酸民", "XX派")
        - Avoid academic jargon (e.g., "符號抵抗實踐者")
        - Be short (max 5 Chinese characters)
        - Examples of acceptable naming tone: 「政策質疑者」, 「制度關注者」, 「冷感旁觀者」, 「民生抱怨群」
        Cluster summaries should be one professional sentence.

        - When assessing Author Influence, you MUST use the provided REAL_METRICS (Likes/Replies/Views). Do NOT invent or zero-out these numbers.
        - Follower Count is unavailable; explicitly note that the assessment is based solely on post engagement signals.
        When evaluating Author Influence, you MUST cite the following real metrics verbatim:
        - 讚好數：{like_count}
        - 回應數：{reply_count}
        - 觀看次數：{view_count}
        You MUST NOT hallucinate other numbers.
        Follower count is NOT provided; explicitly state: 『本系統未包含作者之追蹤人數，以下評估僅根據可見互動數據。』

        [POWER METRICS CONTRACT]
        For every cluster in battlefield_map, emit power_metrics:
          - population: derive from size_share (LOW <0.15, MEDIUM 0.15-0.40, HIGH >0.40)
          - asymmetry_score: compare like_share vs size_share (if like_share/size_share > 2.0 -> HIGH; >1.2 -> MEDIUM; else LOW)
          - intensity: your judgment of rhetorical force; must cite evidence_comment_ids.

        **On Quantifiable Tags (Sociological Metrics)**
        - Homogeneity_Score (Float 0.0 - 1.0):
            * Theoretical Basis: Based on Sunstein's "Echo Chamber" and Noelle-Neumann's "Spiral of Silence".
            * Definition: Measures the diversity of opinion and the presence of dissenting voices.
            * Scale:
                * 0.8 - 1.0 (Echo Chamber): High consensus. Dissenting views are absent or mocked.
                * 0.4 - 0.6 (Polarization): A divided battlefield. Distinct camps are fighting.
                * 0.0 - 0.3 (Fragmentation): Chaotic/Diverse opinions. No dominant narrative.
                * Reference: Check 'Math_Homogeneity_Reference' in L0.5 signals. If math says 0.9, do not rate it 0.2 unless you detect heavy sarcasm.

        - Civil_Score (Int 0 - 10):
            * Theoretical Basis: Based on Papacharissi's "Online Incivility" and Discourse Ethics.
            * Definition: Measures deliberative quality, distinguishing "impoliteness" from "threats to democracy".
            * Scale:
                * 8 - 10 (Deliberative): Rational exchange. Disagreement focuses on arguments.
                * 4 - 7 (Heated): Emotional, sarcastic, but maintains communicative intent.
                * 0 - 3 (Toxic): Ad hominem attacks, dehumanization, silencing others.

        OUTPUT FORMAT:
        Language: Traditional Chinese (Taiwan/Hong Kong usage).
        You must output a single response containing two sections:
        
        SECTION 1: The Report (Markdown)
        - Executive Summary (執行摘要)
        - **Phenomenon Spotlight (現象焦點)**: Briefly explain the unique sub-variant or flavor of this post (moved up).
        - L1 & L2 Deep Dive (Tone & Strategy)
        - L3 Battlefield / Faction Analysis (Dynamics) — cite evidence ids
        - Strategic Implication
        - Author Influence assessment (Low / Medium / High_KOL) grounded in REAL_METRICS; explicitly state follower count is unavailable.

        SECTION 2: The Data Block (JSON)
        *Must be wrapped in ```json codes*
        {{
          "analysis_meta": {{
            "post_id": "{post_data['id']}",
            "timestamp": "{datetime.now().isoformat()}",
            "high_impact": {str(is_high_impact).lower()}
          }},
          "hard_metrics": {json.dumps(quant_calc_data.get("hard_metrics") or {}, ensure_ascii=False)},
          "per_cluster_metrics": {json.dumps(quant_calc_data.get("per_cluster_metrics") or [], ensure_ascii=False)},
          "battlefield_map": [
            {{
              "cluster_id": 0,
              "role": "短句角色/立場",
              "tactic": "主要戰術/策略",
              "power_metrics": {{
                "intensity": "HIGH|MEDIUM|LOW",
                "population": "HIGH|MEDIUM|LOW",
                "asymmetry_score": "HIGH|MEDIUM|LOW"
              }},
              "evidence_comment_ids": ["e1","e2"],
              "supporting_quant_facts": [
                {{ "source": "hard_metrics", "key": "dominance_ratio_top1", "value": 0.55 }}
              ]
            }}
          ],
          "strategic_verdict": {{
            "verdict": "短句總結",
            "rationale": "核心理由 (cite evidence ids)",
            "evidence_comment_ids": ["e1","e3"],
            "supporting_quant_facts": [
              {{ "source": "tree_metrics", "key": "edge_coverage", "value": 0.9 }}
            ]
          }},
          "structural_insight": {{
            "keystone_cluster_id": 0,
            "counterfactual_analysis": "如果該群沉默/消失，敘事會如何改變？",
            "evidence_comment_ids": ["e2","e4"],
            "supporting_quant_facts": [
              {{ "source": "hard_metrics", "key": "dominance_ratio_top1", "value": 0.55 }}
            ]
          }},
          "claims": [
            {{
              "text": "一條可被審計的論述句",
              "evidence_ids": ["e1","e2"],
              "scope": "post",
              "claim_type": "interpret",
              "cluster_key": 0
            }}
          ],
          "discovery_channel": {{
            "sub_variant_name": "String (e.g., 'Revenge_Consumption')",
            "is_new_phenomenon": Boolean,
            "phenomenon_description": "Specific nuance observed"
          }}
        }}
        """
    else:
        logger.info("🧠 Claims extraction mode: building EVIDENCE_CATALOG (model=%s)", model_name)
        cluster_context = "\n".join(cluster_brief_lines).strip() or "(no clusters)"
        claims_metrics_block = format_claims_only_metrics(quant_calc_data.get("hard_metrics") or {})
        system_prompt, user_content = build_claims_only_prompt(
            post_data=post_data,
            cluster_context=cluster_context,
            claims_metrics_block=claims_metrics_block,
            evidence_catalog=evidence_catalog,
        )
    if axis_enabled and axis_manager:
        axis_context = axis_manager.get_few_shot_context()
        axis_version = axis_manager.library_version
        system_prompt += f"""

    === SEMANTIC AXIS CONSTITUTION (library_version={axis_version}) ===
    {axis_context}

    === MEASUREMENT PROTOCOL (STRICT) ===
    Return an `axis_alignment` JSON object:
    {{
      "meta": {{
        "library_version": "{axis_version}",
        "is_extension_candidate": false,
        "extension_reason": null
      }},
      "axes": [
        {{
          "axis_name": "...",
          "score": 0.0-1.0,
          "reasoning": "...",
          "matched_anchor_id": "..." | null
        }}
      ]
    }}

    Rules:
    1) For EACH axis above, output a semantic alignment score in [0.0, 1.0].
    2) DO NOT force single-label; multi-axis high scores are allowed.
    3) Provide reasoning citing specific cues.
    4) Provide matched_anchor_id if known.
    5) If you suspect novel slang not present in examples but semantically fits, set meta.is_extension_candidate=true and explain briefly.
    """

    if narrative_mode == "claims_only":
        logger.info("🧠 Claims extraction mode: calling LLM (model=%s)", model_name)
    else:
        logger.info("🧠 Legacy writer mode: calling LLM (model=%s)", model_name)

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(model_name) if model_name else None
    
    try:
        # drop heavy fields to avoid oversized prompt
        for k in ["archive_html", "archive_dom_json", "archive_captured_at", "archive_build_id"]:
            post_data.pop(k, None)
        payload_str = system_prompt + "\n\n" + user_content
        block_lengths = {
            "system_prompt": len(system_prompt),
            "user_content": len(user_content),
            "payload_str": len(payload_str),
            "knowledge_base": len(knowledge_base),
            "vox_populi_text": len(vox_populi_text),
            "cluster_payload_json": len(cluster_payload_json),
            "cluster_context": len(cluster_context),
            "hard_metrics_block": len(hard_metrics_block),
            "evidence_catalog": len(evidence_catalog),
            "evidence_blocks": len(evidence_blocks),
        }
        max_block = max(
            [(k, v) for k, v in block_lengths.items() if k not in ("payload_str", "system_prompt", "user_content")],
            key=lambda kv: kv[1],
        )[0]
        block_lengths["max_block"] = max_block
        logger.info("[Analyst] Payload length snapshot", extra=block_lengths)
        _log_timing("payload_build", payload_build_start)
        logger.info(
            "[Analyst] LLM payload approx chars=%s (mode=%s, model=%s)",
            len(payload_str),
            narrative_mode,
            model_name,
        )

        save_analysis_json(
            post_id=str(_get_post_id(post_data) or ""),
            analysis_build_id=analysis_build_id,
            json_obj={
                "meta": {
                    "stage": "narrative",
                    "narrative_status": "pending",
                    "narrative_mode": narrative_mode,
                    "llm_status": "pending",
                    "claims_status": "disabled" if not claims_pipeline_enabled else "pending",
                    "llm_model": model_name,
                    "llm_payload_chars": len(payload_str),
                }
            },
            mode="mark_timeout",
            analysis_version=analysis_version,
        )
        llm_error = None
        llm_latency_ms = 0
        full_text = ""
        llm_status = "ok"
        use_stub = os.getenv("DL_LLM_STUB", "0").lower() in {"1", "true"}
        payload_for_call = payload_str
        model_for_call = model_name
        timeout_override = None
        run_id_for_logs = str(cluster_run_id or "")
        post_id_for_logs = str(post_row_id)

        while True:
            attempt_ctx.attempt_count += 1
            attempt_ctx.last_action = "llm_call"
            attempt_ctx.retry_history.append(
                {
                    "action": "llm_call",
                    "model": model_for_call,
                    "payload_chars": len(payload_for_call),
                    "timeout_s": timeout_override,
                    "ts": datetime.now().isoformat(),
                }
            )
            llm_start = perf_counter()
            if use_stub:
                alias_list = sorted(list(alias_to_locator.keys()))
                if alias_to_locator_key and evidence_map:
                    alias_list = sorted(
                        alias_list,
                        key=lambda a: len(evidence_map.get(alias_to_locator_key.get(a, ""), "")),
                        reverse=True,
                    )
                cluster_keys = sorted(
                    {
                        int(c.get("cluster_key"))
                        for c in cluster_payload_for_llm
                        if isinstance(c, dict) and c.get("cluster_key") is not None
                    }
                )
                stub_json = _build_llm_stub_json(alias_list, cluster_keys)
                full_text = json.dumps(stub_json, ensure_ascii=False)
                llm_latency_ms = int((perf_counter() - llm_start) * 1000)
                llm_status = "ok"
                record_llm_success(narrative_mode, model_for_call)
                _record_llm_call_log(
                    post_id=post_id_for_logs,
                    run_id=run_id_for_logs,
                    mode=narrative_mode,
                    model_name=model_for_call,
                    status="stub",
                    latency_ms=llm_latency_ms,
                )
                break

            try:
                model = genai.GenerativeModel(model_for_call) if model_for_call else None
                response = _call_gemini_with_retry(model, payload_for_call, timeout_seconds=timeout_override)
            except Exception as e:
                llm_latency_ms = int((perf_counter() - llm_start) * 1000)
                llm_error = str(e)
                is_timeout = "Deadline Exceeded" in llm_error or "timeout" in llm_error.lower()
                llm_status = "timeout" if is_timeout else "error"
                attempt_ctx.last_error_type = "timeout" if is_timeout else "exception"
                record_llm_failure(narrative_mode, model_for_call, error_type=llm_status)
                _record_llm_call_log(
                    post_id=post_id_for_logs,
                    run_id=run_id_for_logs,
                    mode=narrative_mode,
                    model_name=model_for_call,
                    status=llm_status,
                    latency_ms=llm_latency_ms,
                )
                action = decide_action(
                    llm_status=llm_status,
                    claims_status="pending",
                    attempt_ctx=attempt_ctx,
                )
                if (
                    action.name == "RETRY_WITH_DOWNGRADE"
                    and attempt_ctx.downgrade_count < 1
                    and model_for_call != DEFAULT_MODEL_CLAIMS_ONLY
                ):
                    attempt_ctx.downgrade_count += 1
                    attempt_ctx.last_action = "retry_with_downgrade"
                    attempt_ctx.retry_history.append(
                        {
                            "action": "retry_with_downgrade",
                            "from_model": model_for_call,
                            "to_model": DEFAULT_MODEL_CLAIMS_ONLY,
                            "ts": datetime.now().isoformat(),
                        }
                    )
                    model_for_call = DEFAULT_MODEL_CLAIMS_ONLY
                    timeout_override = 30.0
                    payload_for_call = payload_str
                    continue

                narrative_status = "error_timeout" if is_timeout else "error_exception"
                system_hypotheses: List[Dict[str, Any]] = []
                if claims_pipeline_enabled:
                    system_hypotheses.append(
                        _build_system_failure_hypothesis("llm_timeout" if is_timeout else "llm_exception")
                    )
                save_analysis_json(
                    post_id=str(_get_post_id(post_data) or ""),
                    analysis_build_id=analysis_build_id,
                    json_obj={
                        "meta": {
                            "stage": "final",
                            "narrative_status": narrative_status,
                            "narrative_mode": narrative_mode,
                            "llm_status": llm_status,
                            "claims_status": "disabled",
                            "llm_model": model_for_call,
                            "llm_payload_chars": len(payload_for_call),
                            "llm_latency_ms": llm_latency_ms,
                            "llm_error": llm_error,
                            "attempt_count": attempt_ctx.attempt_count,
                            "last_action": attempt_ctx.last_action,
                            "retry_history": attempt_ctx.retry_history,
                            "hypotheses": system_hypotheses if system_hypotheses else None,
                        }
                    },
                    mode="mark_timeout",
                    analysis_version=analysis_version,
                    analysis_is_valid=True,
                    analysis_invalid_reason=None,
                    analysis_missing_keys=None,
                )
                try:
                    supabase.table("threads_posts").update(
                        {
                            "raw_json": {
                                "error": "llm_timeout" if is_timeout else "llm_error",
                                "model": model_for_call,
                                "elapsed_ms": llm_latency_ms,
                                "detail": llm_error,
                            }
                        }
                    ).eq("id", str(_get_post_id(post_data) or "")).execute()
                except Exception:
                    logger.exception("[Analyst] Failed to persist raw_json timeout artifact")
                return {
                    "post_id": str(_get_post_id(post_data) or ""),
                    "analysis_build_id": analysis_build_id,
                    "stage": "final",
                    "narrative_status": narrative_status,
                    "updated_fields": ["analysis_json", "analysis_build_id"],
                    "llm_error": llm_error,
                }

            llm_latency_ms = int((perf_counter() - llm_start) * 1000)
            full_text = response.text
            llm_status = "ok"
            record_llm_success(narrative_mode, model_for_call)
            _record_llm_call_log(
                post_id=post_id_for_logs,
                run_id=run_id_for_logs,
                mode=narrative_mode,
                model_name=model_for_call,
                status="ok",
                latency_ms=llm_latency_ms,
                response=response,
            )
            break

        model_name = model_for_call
        payload_str = payload_for_call
        report_model_name = model_name
        report_payload_str = payload_str
        claims_model_name = model_name
        raw_llm_preview = (full_text or "")[:1200]
        
        # 1. Extract JSON
        json_data = extract_json_block(full_text)
        axis_alignment_payload = None
        axis_alignment_invalid_reason = None
        prompt_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        json_data_raw = json_data or {}
        json_data_mapped = json_data_raw
        if json_data_raw:
            json_data_mapped, mapping_unknown_aliases = _reverse_map_evidence_ids(json_data_raw, alias_to_locator)
            evidence_errors = _evidence_compliance_errors(json_data_mapped)
            explainability_errors = _validate_explainability_gate(
                json_data_mapped,
                bundle=bundle,
                quant_calc_data=quant_calc_data,
                tree_metrics=tree_metrics,
            )
            if explainability_errors:
                logger.warning("[Analyst] Explainability gate errors: %s", explainability_errors)
                evidence_errors.extend(explainability_errors)
            novelty_input_text = _build_novelty_input_text(
                post_data.get("post_text") or post_data.get("text") or "",
                raw_comments,
            )
            if axis_enabled and axis_manager:
                axis_alignment_payload, axis_alignment_invalid_reason = _normalize_axis_alignment(
                    json_data_mapped,
                    axis_manager,
                    novelty_input_text,
                )
                if axis_alignment_payload is not None:
                    json_data_mapped["axis_alignment"] = axis_alignment_payload

        json_data_for_claims = json_data_raw
        json_data_for_analysis = json_data_mapped

        valid_claims: List[Any] = []
        dropped_claims: List[Any] = []
        audit_meta: Dict[str, Any] = {}
        hypotheses: List[Dict[str, Any]] = []
        claims_status = "disabled"
        narrative_status_final = "ok"
        preanalysis_json = load_preanalysis_json(supabase, post_data.get("id"))
        run_id = str(((preanalysis_json or {}).get("meta") or {}).get("cluster_run_id") or "")

        def _run_claims_pipeline(
            local_json: Dict[str, Any],
            *,
            local_prompt_hash: str,
            local_model_name: Optional[str],
            full_text_present: bool,
        ):
            local_valid: List[Any] = []
            local_dropped: List[Any] = []
            local_audit: Dict[str, Any] = {}
            local_hypotheses: List[Dict[str, Any]] = []
            local_claims_status = "disabled"
            local_narrative_status = "ok"

            if not claims_pipeline_enabled:
                return local_valid, local_dropped, local_audit, local_hypotheses, "disabled", "ok"

            claim_pack = normalize_analyst_output_to_claims(
                local_json or {},
                post_id=int(post_data.get("id") or 0),
                run_id=run_id,
                prompt_hash=local_prompt_hash,
                model_name=local_model_name,
                build_id=analysis_build_id,
            )
            _resolve_claim_aliases(
                claim_pack,
                alias_to_locator=alias_to_locator,
                alias_to_locator_key=alias_to_locator_key,
                evidence_rows=evidence_rows,
            )
            local_valid, local_dropped, local_audit = audit_claims(
                claim_pack,
                preanalysis_meta=(preanalysis_json or {}).get("meta") or {},
                evidence_map=evidence_map,
            )
            claim_pack.meta.audit_verdict = local_audit.get("verdict") or "fail"
            claim_pack.meta.dropped_claims_count = int(local_audit.get("dropped_claims_count") or 0)
            claim_pack.meta.fail_reasons = local_audit.get("fail_reasons") or []

            parse_failed = bool(full_text_present) and not bool(local_json)
            if parse_failed:
                local_claims_status = "fail_parse"
            elif not claim_pack.claims:
                local_claims_status = "fail_no_claims"
            else:
                verdict = local_audit.get("verdict")
                if verdict == "pass":
                    local_claims_status = "ok"
                elif verdict == "partial":
                    local_claims_status = "partial"
                else:
                    local_claims_status = "fail_audit"

            if local_claims_status in {"partial", "fail_audit"}:
                local_narrative_status = "partial" if local_claims_status == "partial" else "fail_audit"
            elif local_claims_status == "fail_no_claims":
                local_narrative_status = "fail_no_claims"
            elif local_claims_status == "fail_parse":
                local_narrative_status = "fail_parse"
            else:
                local_narrative_status = "ok"

            if narrative_mode == "claims_only":
                logger.info(
                    "🧠 Claims extraction mode: audit_verdict=%s kept=%s dropped=%s",
                    local_audit.get("verdict"),
                    local_audit.get("kept_claims_count"),
                    local_audit.get("dropped_claims_count"),
                )

            if not claim_pack.claims:
                reason = "parse_failed" if parse_failed else "llm_missing_claims"
                failure_key = hashlib.sha256(
                    f"{post_data.get('id')}:{run_id}:{reason}".encode("utf-8")
                ).hexdigest()
                system_hyp = _build_system_failure_hypothesis("parse_error" if parse_failed else "llm_missing_claims")
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
                local_hypotheses.append(system_hyp)
            for claim in local_dropped:
                local_hypotheses.append(
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

            if run_id and claim_pack.claims:
                try:
                    claim_rows = [c.model_dump() if hasattr(c, "model_dump") else c.dict() for c in (local_valid + local_dropped)]
                    save_claim_pack(
                        post_id=int(post_data.get("id") or 0),
                        run_id=run_id,
                        claims=claim_rows,
                        audit_meta=local_audit,
                        preanalysis_json=preanalysis_json or {},
                        prompt_hash=local_prompt_hash,
                        model_name=local_model_name,
                        build_id=analysis_build_id,
                        evidence_rows=evidence_rows,
                    )
                except Exception:
                    logger.exception("[Analyst] Claim persistence failed (non-fatal)")

            return local_valid, local_dropped, local_audit, local_hypotheses, local_claims_status, local_narrative_status

        valid_claims, dropped_claims, audit_meta, hypotheses, claims_status, narrative_status_final = _run_claims_pipeline(
            json_data_for_claims or {},
            local_prompt_hash=prompt_hash,
            local_model_name=claims_model_name,
            full_text_present=bool(full_text),
        )

        if claims_pipeline_enabled and claims_status in {"fail_no_claims", "fail_parse"}:
            action = decide_action(
                llm_status="ok",
                claims_status=claims_status,
                attempt_ctx=attempt_ctx,
            )
            if action.name == "REASK_JSON_ONLY":
                attempt_ctx.reask_count += 1
                attempt_ctx.last_action = "reask_json_only"
                attempt_ctx.retry_history.append(
                    {
                        "action": "reask_json_only",
                        "from_model": model_name,
                        "to_model": DEFAULT_MODEL_CLAIMS_ONLY,
                        "ts": datetime.now().isoformat(),
                    }
                )
                reask_system, reask_user = build_claims_only_prompt(
                    post_data=post_data,
                    cluster_context=cluster_context,
                    claims_metrics_block=claims_metrics_block,
                    evidence_catalog=evidence_catalog,
                    strict_json_only=True,
                )
                reask_payload = reask_system + "\n\n" + reask_user
                reask_prompt_hash = hashlib.sha256(reask_payload.encode("utf-8")).hexdigest()
                try:
                    reask_start = perf_counter()
                    reask_model = genai.GenerativeModel(DEFAULT_MODEL_CLAIMS_ONLY)
                    reask_response = _call_gemini_with_retry(
                        reask_model,
                        reask_payload,
                        timeout_seconds=30.0,
                        generation_config={"temperature": 0},
                    )
                    llm_latency_ms = int((perf_counter() - reask_start) * 1000)
                    full_text = reask_response.text
                    llm_status = "ok"
                    record_llm_success(narrative_mode, DEFAULT_MODEL_CLAIMS_ONLY)
                    _record_llm_call_log(
                        post_id=post_id_for_logs,
                        run_id=run_id_for_logs,
                        mode=narrative_mode,
                        model_name=DEFAULT_MODEL_CLAIMS_ONLY,
                        status="ok",
                        latency_ms=llm_latency_ms,
                        response=reask_response,
                    )
                except Exception as e:
                    llm_latency_ms = int((perf_counter() - reask_start) * 1000)
                    llm_error = str(e)
                    is_timeout = "Deadline Exceeded" in llm_error or "timeout" in llm_error.lower()
                    llm_status = "timeout" if is_timeout else "error"
                    record_llm_failure(narrative_mode, DEFAULT_MODEL_CLAIMS_ONLY, error_type=llm_status)
                    _record_llm_call_log(
                        post_id=post_id_for_logs,
                        run_id=run_id_for_logs,
                        mode=narrative_mode,
                        model_name=DEFAULT_MODEL_CLAIMS_ONLY,
                        status=llm_status,
                        latency_ms=llm_latency_ms,
                    )
                    narrative_status = "error_timeout" if is_timeout else "error_exception"
                    system_hypotheses: List[Dict[str, Any]] = []
                    if claims_pipeline_enabled:
                        system_hypotheses.append(
                            _build_system_failure_hypothesis("llm_timeout" if is_timeout else "llm_exception")
                        )
                    save_analysis_json(
                        post_id=str(_get_post_id(post_data) or ""),
                        analysis_build_id=analysis_build_id,
                        json_obj={
                            "meta": {
                                "stage": "final",
                                "narrative_status": narrative_status,
                                "narrative_mode": narrative_mode,
                                "llm_status": llm_status,
                                "claims_status": "disabled",
                                "llm_model": DEFAULT_MODEL_CLAIMS_ONLY,
                                "llm_payload_chars": len(reask_payload),
                                "llm_latency_ms": llm_latency_ms,
                                "llm_error": llm_error,
                                "attempt_count": attempt_ctx.attempt_count,
                                "last_action": attempt_ctx.last_action,
                                "retry_history": attempt_ctx.retry_history,
                                "hypotheses": system_hypotheses if system_hypotheses else None,
                            }
                        },
                        mode="mark_timeout",
                        analysis_version=analysis_version,
                        analysis_is_valid=True,
                        analysis_invalid_reason=None,
                        analysis_missing_keys=None,
                    )
                    return {
                        "post_id": str(_get_post_id(post_data) or ""),
                        "analysis_build_id": analysis_build_id,
                        "stage": "final",
                        "narrative_status": narrative_status,
                        "updated_fields": ["analysis_json", "analysis_build_id"],
                        "llm_error": llm_error,
                    }

                reask_json = extract_json_block(full_text)
                json_data_for_claims = reask_json or {}
                if narrative_mode == "claims_only":
                    json_data_for_analysis = json_data_for_claims
                    json_data_mapped = json_data_for_claims
                    mapping_unknown_aliases = []
                    if json_data_for_analysis:
                        json_data_mapped, mapping_unknown_aliases = _reverse_map_evidence_ids(
                            json_data_for_analysis, alias_to_locator
                        )

                valid_claims, dropped_claims, audit_meta, hypotheses, claims_status, narrative_status_final = _run_claims_pipeline(
                    json_data_for_claims or {},
                    local_prompt_hash=reask_prompt_hash,
                    local_model_name=DEFAULT_MODEL_CLAIMS_ONLY,
                    full_text_present=True,
                )
                claims_model_name = DEFAULT_MODEL_CLAIMS_ONLY

        # 2. Save to Local File
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("reports", exist_ok=True)
        with open(f"reports/Analysis_{post_data['id']}_{ts}.md", "w", encoding="utf-8") as f:
            f.write(full_text)

        # 3. Save to Supabase (via deterministic builder)
        ai_tags = {}
        cluster_insights = {}
        cluster_insights_list: List[Dict[str, Any]] = []
        raw_discovery_block = _get_case_insensitive(json_data_for_analysis, "Discovery_Channel") or {}
        if json_data_for_analysis:
            tags = json_data_for_analysis.get('Quantifiable_Tags', {}) or {}
            discovery = raw_discovery_block or {}
            raw_insights = _get_case_insensitive(json_data_for_analysis, "Cluster_Insights") or {}
            adapter_used = False
            battlefield_map = _get_case_insensitive(json_data_for_analysis, "battlefield_map") or []
            adapted_battlefield: List[Dict[str, Any]] = []
            if battlefield_map:
                adapter_used = True
                adapted_battlefield = _adapt_battlefield_to_cluster_insights(battlefield_map)
            # Prefer explicit insights; merge battlefield adapter to ensure metadata coverage
            if raw_insights and isinstance(raw_insights, dict):
                merged_list = [{**v, "cluster_key": k} for k, v in raw_insights.items() if isinstance(v, dict)]
                merged_list.extend(adapted_battlefield)
                raw_insights = merged_list
            elif raw_insights and isinstance(raw_insights, list):
                merged_list = [item for item in raw_insights if isinstance(item, dict)]
                merged_list.extend(adapted_battlefield)
                raw_insights = merged_list
            elif adapted_battlefield:
                raw_insights = adapted_battlefield
            cluster_insights_list = normalize_cluster_insights(raw_insights)
            if adapter_used:
                adapter_keys = sorted({ci.get("cluster_key") for ci in cluster_insights_list if ci.get("cluster_key") is not None})
                logger.info(
                    "[Analyst] Adapting battlefield_map -> Cluster_Insights schema",
                    extra={
                        "adapter_hit": True,
                        "battlefield_items": len(battlefield_map) if battlefield_map else 0,
                        "insights_count": len(cluster_insights_list),
                        "cluster_keys_present": adapter_keys,
                    },
                )
            # map for backward-compatible merge
            cluster_insights = {str(item["cluster_key"]): item for item in cluster_insights_list}
            sub_variant_name = (
                discovery.get("Sub_Variant_Name")
                or discovery.get("sub_variant_name")
                or discovery.get("Sub_Variant")
            )
            phenomenon_desc = (
                discovery.get("Phenomenon_Description")
                or discovery.get("phenomenon_description")
                or discovery.get("Phenomenon_Desc")
            )
            channel_present = bool(raw_discovery_block)
            discovery_keys_lower = [k.lower() for k in raw_discovery_block.keys()] if isinstance(raw_discovery_block, dict) else []
            sub_variant_source = "llm_present" if sub_variant_name else ("llm_missing" if channel_present else "parser_missing")
            if not sub_variant_name and channel_present and any("sub_variant" in k for k in discovery_keys_lower):
                sub_variant_source = "parser_missing"
            phenomenon_desc_source = "llm_present" if phenomenon_desc else ("llm_missing" if channel_present else "parser_missing")
            if not phenomenon_desc and channel_present and any("phenomenon" in k for k in discovery_keys_lower):
                phenomenon_desc_source = "parser_missing"
            ai_tags = {
                **tags,
                "Sub_Variant": sub_variant_name,
                "Phenomenon_Desc": phenomenon_desc,
                "Sub_Variant_Source": sub_variant_source,
                "Phenomenon_Desc_Source": phenomenon_desc_source,
            }

        if cluster_samples:
            cluster_samples = merge_cluster_insights(cluster_samples, cluster_insights)

        # First update: ai_tags + full_report
        try:
            first_update = _to_json_safe({"ai_tags": ai_tags, "full_report": full_text})
            t_supabase_first = perf_counter()
            supabase.table("threads_posts").update(first_update).eq("id", post_data.get("id")).execute()
            _log_timing("supabase_update_ai_tags_full_report", t_supabase_first)
            logger.info(f"[Analyst] ✅ Updated ai_tags/full_report for post {post_data.get('id')}")
        except Exception as e:
            if ai_tags:
                ai_tags["Sub_Variant_Source"] = "writeback_missing"
                ai_tags["Phenomenon_Desc_Source"] = "writeback_missing"
            logger.error(f"[Analyst] ❌ Failed to update ai_tags/full_report for post {post_data.get('id')}")
            logger.exception(e)

        # Refresh crawler row to ensure ground-truth metrics/text
        try:
            logger.info(
                "[Analyst] Pre-refresh comment head snapshot",
                extra={"head": _comment_key_snapshot(post_data.get("comments"))},
            )
            t_supabase_refresh = perf_counter()
            post_res = (
                supabase.table("threads_posts")
                .select("*")
                .eq("id", post_data.get("id"))
                .single()
                .execute()
            )
            _log_timing("supabase_refresh_select", t_supabase_refresh)
            if post_res and hasattr(post_res, "data") and post_res.data:
                refreshed = post_res.data or {}
                preserved_comments = post_data.get("comments")
                preserved_raw_comments = post_data.get("raw_comments")
                safe_fields = [
                    "like_count",
                    "view_count",
                    "reply_count",
                    "repost_count",
                    "share_count",
                    "post_text",
                    "images",
                    "author",
                    "reposts",
                    "shares",
                    "title",
                    "metrics",
                ]
                for key in safe_fields:
                    if key in refreshed:
                        post_data[key] = refreshed.get(key)
                # Re-attach preserved comment structures to prevent traceability loss
                post_data["comments"] = preserved_comments
                post_data["raw_comments"] = preserved_raw_comments
                logger.info(
                    "[Analyst] Post-refresh comment head snapshot",
                    extra={"head": _comment_key_snapshot(post_data.get("comments"))},
                )
                logger.info(
                    "[Analyst] Safe refresh complete + comments preserved count=%s",
                    len(post_data.get("comments") or []),
                )
        except Exception as fetch_err:
            logger.warning("Failed to refresh post_data from Supabase; using provided post_data", extra={"error": str(fetch_err)})

        raw_imgs = post_data.get("images") or []
        logger.info(f"[Analyst] Raw crawler images: {len(raw_imgs)}")

        try:
            t_build_analysis = perf_counter()
            analysis_v4 = build_and_validate_analysis_json(
                post_data=post_data,
                llm_data=json_data_for_analysis or {},
                cluster_data=cluster_samples or {},
                full_report=full_text,
            )
            _log_timing("build_and_validate_analysis_json", t_build_analysis)
        except Exception as e:
            logger.error("[Analyst] ❌ AnalysisV4 validation failed")
            logger.exception(e)
            return None

        if quant_calc_data:
            analysis_v4 = analysis_v4.copy(
                update={
                    "hard_metrics": quant_calc_data.get("hard_metrics"),
                    "per_cluster_metrics": quant_calc_data.get("per_cluster_metrics") or [],
                }
            )
        if isinstance(json_data_for_analysis, dict):
            analysis_v4 = analysis_v4.copy(
                update={
                    "battlefield_map": json_data_for_analysis.get("battlefield_map") or [],
                    "structural_insight": json_data_for_analysis.get("structural_insight"),
                    "strategic_verdict": json_data_for_analysis.get("strategic_verdict"),
                }
            )

        # Enforce crawler-first fields
        analysis_v4 = protect_core_fields(post_data, analysis_v4)

        # Sprint 4: project audited claims into analysis_json
        try:
            if claims_pipeline_enabled:
                claim_projection = apply_claims_to_analysis_json(analysis_v4, valid_claims, audit_meta, hypotheses)
                analysis_v4 = analysis_v4.copy(update=claim_projection)
        except Exception:
            logger.exception("[Analyst] Claims projection failed; leaving analysis_json unchanged")

        # Phenomenon is registry-owned; mark pending to avoid LLM free-form drift.
        try:
            phen_dict = _safe_dump(analysis_v4.phenomenon)
            if not phen_dict.get("id") and not phen_dict.get("status"):
                phen_dict["status"] = "pending"
            phen_model = Phenomenon(**phen_dict) if isinstance(phen_dict, dict) else phen_dict
            analysis_v4 = analysis_v4.copy(update={"phenomenon": phen_model})
        except Exception:
            logger.exception("[Analyst] Failed to tag phenomenon pending")

        # Validate completeness
        is_valid, invalid_reason, missing_keys = validate_analysis_json(analysis_v4)
        if mapping_unknown_aliases:
            is_valid = False
            invalid_reason = "evidence_alias_unknown"
            missing_keys = mapping_unknown_aliases
        if evidence_errors:
            is_valid = False
            if any("supporting_quant_facts" in e for e in evidence_errors):
                invalid_reason = "explainability_gate_failed"
            else:
                invalid_reason = "evidence_compliance_failed"
            missing_keys = (missing_keys or []) + evidence_errors
        if assignment_coverage_fail:
            is_valid = False
            invalid_reason = invalid_reason or assignment_coverage_reason or "assignment_coverage_below_min"
        if axis_alignment_invalid_reason:
            is_valid = False
            invalid_reason = (
                f"{invalid_reason};{axis_alignment_invalid_reason}"
                if invalid_reason
                else axis_alignment_invalid_reason
            )
            missing_keys = (missing_keys or []) + ["axis_alignment"]
        # analysis_version/build_id set earlier for skeleton checkpoint

        # Optional fallback: if L1/L2/L3 missing, use regex extraction as last resort
        if analysis_v4.narrative_stack and not any(
            [analysis_v4.narrative_stack.l1, analysis_v4.narrative_stack.l2, analysis_v4.narrative_stack.l3]
        ):
            analysis_v4 = analysis_v4.copy(
                update={
                    "narrative_stack": {
                        "l1": extract_l1_summary(full_text),
                        "l2": extract_l2_summary(full_text),
                        "l3": extract_l3_summary(full_text),
                    }
                }
            )

        analysis_payload = _safe_dump(analysis_v4)
        if axis_alignment_payload is not None:
            analysis_payload["axis_alignment"] = axis_alignment_payload
        analysis_payload["analysis_version"] = analysis_version
        analysis_payload["analysis_build_id"] = analysis_build_id
        analysis_payload["physics"] = physics_payload
        analysis_payload["golden_samples"] = golden_samples_payload
        analysis_payload["golden_samples_detail"] = golden_samples_detail
        meta = analysis_payload.get("meta") or {}
        meta_llm_model = report_model_name if narrative_mode == "legacy_writer" else claims_model_name
        meta_payload_chars = len(report_payload_str) if narrative_mode == "legacy_writer" else len(payload_str)
        meta.update(
            {
                "stage": "final",
                "narrative_status": narrative_status_final,
                "narrative_mode": narrative_mode,
                "llm_status": llm_status,
                "claims_status": claims_status,
                "llm_model": meta_llm_model,
                "llm_payload_chars": meta_payload_chars,
                "llm_latency_ms": llm_latency_ms,
                "llm_error": None,
                "attempt_count": attempt_ctx.attempt_count,
                "last_action": attempt_ctx.last_action,
                "retry_history": attempt_ctx.retry_history,
                "last_error_type": attempt_ctx.last_error_type,
            }
        )
        analysis_payload["meta"] = meta
        meta = analysis_payload.get("meta") or {}
        meta.update(
            {
                "comments_source": comments_source,
                "bundle_version": bundle.get("bundle_version"),
                "bundle_id": bundle.get("bundle_id"),
                "ordering_rule": bundle.get("ordering_rule"),
                "ordering_key_hash": bundle.get("ordering_key_hash"),
                "quality_flags": bundle.get("quality_flags"),
                "tree_metrics": bundle.get("tree_metrics"),
                "cluster_run_id": cluster_run_id,
            }
        )
        if claims_pipeline_enabled and audit_meta:
            meta["claims"] = {
                "total": len(valid_claims) + int(audit_meta.get("dropped_claims_count") or 0),
                "kept": len(valid_claims),
                "dropped": int(audit_meta.get("dropped_claims_count") or 0),
                "audit_verdict": audit_meta.get("verdict"),
            }
        if claims_pipeline_enabled and hypotheses is not None:
            meta["hypotheses"] = hypotheses
        analysis_payload["meta"] = meta
        analysis_payload = sanitize_analysis_json(analysis_payload)
        if missing_keys:
            analysis_payload["missing_keys"] = missing_keys

        logger.info(
            "[Analyst] analysis payload snapshot",
            extra={
                "type": str(type(analysis_payload)),
                "keys": list(analysis_payload.keys()),
                "post_id": post_data.get("id"),
                "is_valid": is_valid,
            },
        )

        print(
            "[ANALYST] Built AnalysisV4 for post",
            post_data.get("id"),
            "segments=",
            len(analysis_payload.get("segments", [])),
        )

        update_data = {
            "ai_tags": ai_tags,
            "full_report": full_text,
            "cluster_summary": cluster_samples if cluster_samples else {},
            "raw_json": json_data_for_claims or {},
            "analysis_version": analysis_version,
            "analysis_build_id": analysis_build_id,
        }
        if comments_source == "raw_fallback":
            update_data["raw_comments"] = post_data.get("comments", [])
        if quant_summary:
            update_data["quant_summary"] = quant_summary

        post_id = str(_get_post_id(post_data) or "")
        logger.info(
            "[Analyst] write payload snapshot",
            extra={
                "payload_type": str(type(update_data)),
                "keys": list(update_data.keys()),
                "post_id": post_id,
                "is_valid": is_valid,
            },
        )
        try:
            if analysis_payload is not None:
                t_store_write = perf_counter()
                narrative_patch = _extract_narrative_patch(analysis_payload)
                save_analysis_json(
                    post_id=post_id,
                    analysis_build_id=analysis_build_id,
                    json_obj=narrative_patch,
                    mode="merge_narrative",
                    analysis_version=analysis_version,
                    analysis_is_valid=is_valid,
                    analysis_invalid_reason=invalid_reason if not is_valid else None,
                    analysis_missing_keys=missing_keys,
                )
                _log_timing("save_analysis_json.merge_narrative", t_store_write)
            json_safe_payload = _to_json_safe(update_data)
            t_supabase_final = perf_counter()
            resp = supabase.table("threads_posts").update(json_safe_payload).eq("id", post_id).execute()
            _log_timing("supabase_update_final", t_supabase_final)
            logger.info(f"✅ Saved to DB: Sector={ai_tags.get('Sector_ID') if ai_tags else 'N/A'}")
            logger.info(f"💾 Supabase update: comments={len(post_data.get('comments', []))}, quant_summary={'present' if quant_summary else 'none'}")
            print(
                "[ANALYST] Supabase update result for post",
                post_id,
                "error=",
                getattr(resp, "error", None),
            )
            # Kick off async phenomenon Match-or-Mint (non-blocking)
            try:
                if PHENOMENON_ENRICHMENT_ENABLED and phenomenon_enricher:
                    phenomenon_enricher.submit(
                        post_row=post_data,
                        analysis_payload=analysis_payload,
                        cluster_summary=cluster_samples or {},
                        comments=post_data.get("comments", []),
                    )
            except Exception:
                logger.exception("[Analyst] Phenomenon enrichment submission failed")
        except Exception as db_err:
            logger.error(f"[Analyst] ❌ Failed to update analysis_json/raw_json for post {post_id}")
            logger.exception(db_err)
            return None

        # Non-blocking cluster metadata writeback (Layer 0.5 registry)
        try:
            if analysis_payload:
                updates: List[Dict[str, Any]] = []

                # Prefer explicit Cluster_Insights with cluster_key
                if cluster_insights_list:
                    for item in cluster_insights_list:
                        ck = item.get("cluster_key")
                        if ck is None:
                            continue
                        try:
                            ck_int = int(ck)
                        except Exception:
                            continue
                        entry: Dict[str, Any] = {"cluster_key": ck_int}
                        if item.get("label"):
                            entry["label"] = item.get("label")
                        if item.get("summary"):
                            entry["summary"] = item.get("summary")
                        # keep only if any meaningful field
                        if any(k in entry for k in ("label", "summary")):
                            updates.append(entry)
                updates_by_ck: Dict[int, Dict[str, Any]] = {}
                for u in updates:
                    ck = u.get("cluster_key")
                    if ck is None:
                        continue
                    try:
                        ck_int = int(ck)
                    except Exception:
                        continue
                    updates_by_ck[ck_int] = {**u, "cluster_key": ck_int}

                # Fallback: derive from segments if no explicit insights
                if not updates:
                    segments = analysis_payload.get("segments") or []

                    def _cluster_key_from_segment(seg: Dict[str, Any]) -> Optional[int]:
                        for field in ("cluster_key", "cluster_id", "key"):
                            val = seg.get(field)
                            if val is not None:
                                try:
                                    return int(val)
                                except Exception:
                                    pass
                        label = seg.get("label")
                        if isinstance(label, str):
                            m = re.search(r"cluster\s*(\d+)", label, flags=re.IGNORECASE)
                            if m:
                                try:
                                    return int(m.group(1))
                                except Exception:
                                    return None
                        return None

                    for seg in segments:
                        if not isinstance(seg, dict):
                            continue
                        ck = _cluster_key_from_segment(seg)
                        if ck is None:
                            continue
                        entry = {"cluster_key": ck}
                        lbl = seg.get("label")
                        if lbl:
                            entry["label"] = lbl
                        summary = seg.get("summary")
                        if summary:
                            entry["summary"] = summary
                        if any(k in entry for k in ("label", "summary")):
                            updates.append(entry)
                    updates_by_ck = {}
                    for u in updates:
                        ck = u.get("cluster_key")
                        if ck is None:
                            continue
                        try:
                            ck_int = int(ck)
                        except Exception:
                            continue
                        updates_by_ck[ck_int] = {**u, "cluster_key": ck_int}

                # Ensure every cluster has label/summary fallback
                clusters_dict = (cluster_samples.get("clusters") or {}) if cluster_samples else {}
                noise_info = (cluster_samples.get("noise") or {}) if cluster_samples else {}
                cluster_entries = list(clusters_dict.values())
                if noise_info:
                    cluster_entries.append(noise_info)
                for info in cluster_entries:
                    if not isinstance(info, dict):
                        continue
                    ck = info.get("cluster_key", info.get("cluster_id"))
                    try:
                        ck_int = int(ck)
                    except Exception:
                        continue
                    count = info.get("count") or 0
                    entry = updates_by_ck.get(ck_int, {"cluster_key": ck_int})
                    existing_label = entry.get("label") or info.get("name") or info.get("label")
                    if ck_int == -1:
                        label = "Unidentified / Context-External"
                        summary_val = entry.get("summary") or "Comments that reference external context or slang not captured by current clustering logic."
                    else:
                        label = existing_label or f"Cluster {ck_int}"
                        if entry.get("summary"):
                            summary_val = entry.get("summary")
                        else:
                            summary_val = (
                                f"Cluster of {count} comments with loosely related reactions or discussion."
                                if count and count >= 5
                                else "Low-support or noise cluster."
                            )
                    entry["label"] = label
                    entry["summary"] = summary_val
                    updates_by_ck[ck_int] = entry

                updates = list(updates_by_ck.values())
                clusters_in_llm = sorted({u["cluster_key"] for u in updates if u.get("cluster_key") is not None})
                clusters_in_db = sorted(
                    [
                        int(k)
                        for k in (cluster_samples.get("clusters") or {}).keys()
                        if str(k).lstrip("-").isdigit()
                    ]
                )
                if updates:
                    logger.info(
                        "[Analyst] Cluster metadata writeback candidates",
                        extra={
                            "clusters_in_db": clusters_in_db,
                            "clusters_in_llm": clusters_in_llm,
                            "clusters_missing_from_llm": [c for c in clusters_in_db if c not in clusters_in_llm],
                            "clusters_to_update": len(updates),
                        },
                    )

                if updates:
                    t_cluster_writeback = perf_counter()
                    preanalysis_json = load_preanalysis_json(supabase, post_id)
                    ok, updated_count = update_cluster_metadata(
                        int(post_id),
                        updates,
                        run_id=cluster_run_id or "",
                        preanalysis_json=preanalysis_json,
                    )
                    _log_timing("update_cluster_metadata", t_cluster_writeback)
                    if not ok:
                        logger.warning(f"[Analyst] Cluster metadata writeback failed for post {post_id}")
                    else:
                        logger.info(
                            f"[Analyst] Cluster metadata writeback post={post_id} attempted={len(updates)} updated={updated_count}"
                        )
                else:
                    logger.info("[Analyst] Cluster writeback skipped: no cluster_insights and no segment mapping")
        except Exception:
            logger.warning(f"[Analyst] Cluster metadata writeback encountered an error for post {post_id}", exc_info=True)

        return {
            "ai_tags": ai_tags,
            "full_report": full_text,
            "quant_summary": quant_summary,
            "comments": post_data.get("comments", []),
            "cluster_summary": cluster_samples,
            "analysis_is_valid": is_valid,
            "analysis_version": analysis_version,
            "analysis_build_id": analysis_build_id,
            "analysis_invalid_reason": invalid_reason,
            "analysis_missing_keys": missing_keys,
            "analysis_json": analysis_payload,
            "post_id": post_id,
            "stage": "final",
            "narrative_status": narrative_status_final,
            "narrative_mode": narrative_mode,
        }

    except Exception as e:
        logger.error(f"❌ Analyst Failed: {e}")
        post_id = str(_get_post_id(post_data) or "")
        llm_error = f"{type(e).__name__}: {e}"
        save_analysis_json(
            post_id=post_id,
            analysis_build_id=analysis_build_id,
            json_obj={
                "meta": {
                    "stage": "final",
                    "narrative_status": "error_exception",
                    "narrative_mode": narrative_mode,
                    "llm_status": "error",
                    "claims_status": "disabled",
                    "llm_model": model_name,
                    "llm_payload_chars": len(payload_str) if "payload_str" in locals() else None,
                    "llm_latency_ms": None,
                    "llm_error": llm_error,
                }
            },
            mode="mark_timeout",
            analysis_version=analysis_version,
            analysis_is_valid=True,
            analysis_invalid_reason=None,
            analysis_missing_keys=None,
        )
        return {
            "post_id": post_id,
            "analysis_build_id": analysis_build_id,
            "stage": "final",
            "narrative_status": "error_exception",
            "updated_fields": ["analysis_json", "analysis_build_id"],
            "llm_error": llm_error,
        }

# --- Main Execution ---

if __name__ == "__main__":
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    print("🔍 Searching for specific posts to analyze...")
    target_post = fetch_enriched_post(supabase)
            
    if target_post:
        print(f"🎯 Target Acquired: Post {target_post['id']} by {target_post['author']}")
        generate_commercial_report(target_post, supabase)
    else:
        print("⚠️ No fully processed posts found.")
        print("💡 Action: Run 'python analysis/vision_worker.py' first to generate L2 data.")
