import hashlib
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from analysis.quant_engine import get_embedder
from analysis.interpretation_rules import FORBIDDEN_CAUSAL, FORBIDDEN_TERMS
from analysis.diagnostics.models import ISDLabelItem, ISDReport
from analysis.utils.evidence_quality import content_token_count, evidence_quality_summary


logger = logging.getLogger("ISD")

DEFAULT_K = int(os.getenv("DL_ISD_K") or 3)
MAX_RAW_LEN = 2000
MIN_CONTENT_TOKENS = int(os.getenv("DL_ISD_MIN_CONTENT_TOKENS") or 4)

GENERIC_HEDGE_PATTERNS = [
    "mixed opinions",
    "some agree",
    "some disagree",
    "people are divided",
    "divided opinions",
    "various opinions",
    "different opinions",
    "it depends",
    "depends on",
    "hard to say",
    "unclear",
    "ambiguous",
    "no consensus",
    "both sides",
    "some think",
    "others think",
    "not sure",
    "varied views",
    "mixed reactions",
    "neutral",
]

GENERIC_HEDGE_PATTERNS_ZH = [
    "看法不一",
    "意見不一",
    "兩邊",
    "兩派",
    "有人",
    "有些人",
    "各種",
    "不同意見",
    "很難說",
    "難以判斷",
    "沒有共識",
    "模糊",
    "不清楚",
    "說不準",
    "意見分歧",
]



def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return text.strip()


def _has_cjk(text: str, min_chars: int = 2) -> bool:
    if not text:
        return False
    count = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            count += 1
            if count >= min_chars:
                return True
    return False


def _cap_text(text: str, limit: int = MAX_RAW_LEN) -> str:
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _contains_forbidden(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    for term in FORBIDDEN_TERMS:
        if term.lower() in lower:
            return True
    for term in FORBIDDEN_CAUSAL:
        if term.lower() in lower:
            return True
    return False


def _has_generic_hedge(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    for pat in GENERIC_HEDGE_PATTERNS:
        if pat in lower:
            return True
    for pat in GENERIC_HEDGE_PATTERNS_ZH:
        if pat in text:
            return True
    return False


def apply_evidence_quality_gate(report: ISDReport, evidence_texts: List[str]) -> ISDReport:
    summary = evidence_quality_summary(evidence_texts or [])
    all_low = summary.get("low_info_ratio", 1.0) >= 1.0
    reason = "low_info_evidence" if all_low else None
    # Attach diagnostics into labels for persistence (labels is JSONB).
    for item in report.labels:
        try:
            item.evidence_quality = summary
            item.evidence_gate_reason = reason
        except Exception:
            pass
    try:
        report.evidence_quality = summary
        report.evidence_gate_reason = reason
    except Exception:
        pass
    if all_low:
        report.verdict = "underspecified"
    return report


def _extract_json_block(text: str) -> Dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        start = text.find("```json")
        if start >= 0:
            end = text.find("```", start + 7)
            if end > start:
                block = text[start + 7 : end]
                return json.loads(block)
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            return json.loads(text[brace_start : brace_end + 1])
    except Exception as exc:
        logger.warning("[ISD] JSON extraction failed: %s", exc)
    return {}


def _call_gemini_with_retry(model, payload_str: str, max_attempts: int = 3):
    total_start = time.perf_counter()
    for attempt in range(1, max_attempts + 1):
        attempt_start = time.perf_counter()
        try:
            result = model.generate_content(payload_str)
            logger.info(
                "[Timing] segment=isd.gemini attempt=%s dt_ms=%s",
                attempt,
                int((time.perf_counter() - attempt_start) * 1000),
            )
            logger.info(
                "[Timing] segment=isd.gemini.total dt_ms=%s",
                int((time.perf_counter() - total_start) * 1000),
            )
            return result
        except Exception as exc:
            msg = str(exc)
            transient = any(tok in msg for tok in ["InternalServerError", "500", "Overloaded", "ResourceExhausted", "UNAVAILABLE"])
            if not transient or attempt == max_attempts:
                raise
            sleep_seconds = (2 ** attempt) + random.uniform(0, 0.3)
            logger.warning("[ISD] transient error attempt=%s/%s sleep=%.1fs", attempt, max_attempts, sleep_seconds)
            time.sleep(sleep_seconds)


def _validate_label_payload(payload: Dict[str, Any], allowed_ids: List[str], required_ids: List[str]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        return False, ["payload_not_dict"]
    label = _sanitize_text(payload.get("label") or "")
    one_liner = _sanitize_text(payload.get("one_liner") or "")
    label_style = _sanitize_text(payload.get("label_style") or "")
    evidence_ids = payload.get("evidence_ids") or []
    if not label or not one_liner:
        errors.append("missing_label_or_one_liner")
    if label and not _has_cjk(label):
        errors.append("label_not_traditional_chinese")
    if one_liner and not _has_cjk(one_liner):
        errors.append("one_liner_not_traditional_chinese")
    if label_style.lower() != "descriptive":
        errors.append("label_style_not_descriptive")
    if _contains_forbidden(label) or _contains_forbidden(one_liner):
        errors.append("forbidden_terms_or_causal")
    if not isinstance(evidence_ids, list) or len(evidence_ids) < 2:
        errors.append("insufficient_evidence_ids")
    else:
        evidence_ids = [str(e).strip() for e in evidence_ids if isinstance(e, (str, int))]
        if any(eid not in allowed_ids for eid in evidence_ids):
            errors.append("evidence_id_not_allowed")
        for req in required_ids:
            if req and req not in evidence_ids:
                errors.append("missing_required_evidence")
                break
    return len(errors) == 0, errors


def _normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embeddings / norms


def _pairwise_stats(embeddings: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    if embeddings.shape[0] < 2:
        return None, None
    sims: List[float] = []
    for i in range(embeddings.shape[0]):
        for j in range(i + 1, embeddings.shape[0]):
            sims.append(float(np.dot(embeddings[i], embeddings[j])))
    if not sims:
        return None, None
    return float(np.mean(sims)), float(np.min(sims))


def _centroid_drift(embeddings: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    if embeddings.shape[0] == 0:
        return None, None
    centroid = np.mean(embeddings, axis=0)
    denom = np.linalg.norm(centroid)
    if denom == 0:
        return None, None
    centroid = centroid / denom
    sims = embeddings.dot(centroid)
    drifts = 1.0 - sims
    return float(np.mean(drifts)), float(np.max(drifts))


def _verdict_for(
    stability_avg: Optional[float],
    stability_min: Optional[float],
    *,
    k: int,
    valid_count: int,
    has_empty: bool,
    low_info: bool,
) -> str:
    if k < 3 or valid_count < 3 or has_empty:
        return "underspecified"
    if low_info:
        return "underspecified"
    if stability_avg is None or stability_min is None:
        return "underspecified"
    if stability_avg >= 0.85 and stability_min >= 0.80:
        return "stable"
    if stability_avg >= 0.60:
        return "non_convergent"
    return "underspecified"


def run_isd_for_cluster(
    *,
    post_id: int,
    cluster_key: int,
    run_id: str,
    prompt: str,
    allowed_ids: List[str],
    required_ids: List[str],
    llm_model: Any,
    k: Optional[int] = None,
    context_mode: str = "card",
    prompt_hash: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Tuple[ISDReport, Dict[str, Any]]:
    if k is None:
        k = DEFAULT_K
    try:
        k = int(k)
    except Exception:
        k = DEFAULT_K
    if k < 1:
        k = 1

    if prompt_hash is None:
        prompt_hash = _sha256(prompt)

    labels: List[ISDLabelItem] = []
    valid_payloads: List[Dict[str, Any]] = []
    gate_texts: List[str] = []
    has_empty = False

    for run_idx in range(k):
        raw_text = ""
        parsed: Dict[str, Any] = {}
        errors: List[str] = []
        ok = False
        try:
            response = _call_gemini_with_retry(llm_model, prompt)
            raw_text = response.text if response else ""
            parsed = _extract_json_block(raw_text)
        except Exception as exc:
            errors = [str(exc)]
            parsed = {}
        ok, validation_errors = _validate_label_payload(parsed, allowed_ids, required_ids)
        errors.extend(validation_errors)

        label = _sanitize_text(parsed.get("label") or "")
        one_liner = _sanitize_text(parsed.get("one_liner") or "")
        label_conf = parsed.get("label_confidence")
        evidence_ids = parsed.get("evidence_ids") or []
        if isinstance(evidence_ids, list):
            evidence_ids = [str(e).strip() for e in evidence_ids if isinstance(e, (str, int))]
        else:
            evidence_ids = []

        if not label or not one_liner or not ok:
            has_empty = True

        if ok:
            gate_texts.append(f"{label} | {one_liner}")
            valid_payloads.append(parsed)

        labels.append(
            ISDLabelItem(
                run=run_idx,
                raw=_cap_text(raw_text),
                parsed=parsed or {},
                valid=ok,
                errors=errors,
                label=label or None,
                one_liner=one_liner or None,
                label_confidence=label_conf if isinstance(label_conf, (int, float)) else None,
                evidence_ids=evidence_ids,
            )
        )

    stability_avg = None
    stability_min = None
    drift_avg = None
    drift_max = None
    low_info = False
    if gate_texts:
        min_tokens = None
        for text in gate_texts:
            if _has_generic_hedge(text):
                low_info = True
                break
            count = content_token_count(text)
            min_tokens = count if min_tokens is None else min(min_tokens, count)
        if min_tokens is not None and min_tokens < MIN_CONTENT_TOKENS:
            low_info = True

    if gate_texts and not low_info:
        embedder = get_embedder()
        embeddings = embedder.encode(gate_texts)
        if not isinstance(embeddings, np.ndarray):
            embeddings = np.array(embeddings)
        embeddings = _normalize_embeddings(embeddings)
        stability_avg, stability_min = _pairwise_stats(embeddings)
        drift_avg, drift_max = _centroid_drift(embeddings)

    verdict = _verdict_for(
        stability_avg,
        stability_min,
        k=k,
        valid_count=len(gate_texts),
        has_empty=has_empty,
        low_info=low_info,
    )
    if int(cluster_key) == -1:
        verdict = "underspecified"

    report_id = f"isd:{post_id}:{cluster_key}:{run_id}:{context_mode}"
    report = ISDReport(
        id=report_id,
        post_id=post_id,
        cluster_key=cluster_key,
        run_id=run_id,
        verdict=verdict,
        k=k,
        labels=labels,
        stability_avg=stability_avg,
        stability_min=stability_min,
        drift_avg=drift_avg,
        drift_max=drift_max,
        context_mode=context_mode,
        prompt_hash=prompt_hash,
        model_name=model_name,
    )

    primary_payload: Dict[str, Any] = valid_payloads[0] if valid_payloads else {}
    return report, primary_payload
