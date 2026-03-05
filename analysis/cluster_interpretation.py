import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from database.store import supabase, update_cluster_metadata, upsert_cluster_diagnostics
from database.integrity import guard_semantic_write
from analysis.interpretation_rules import SYSTEM_PROMPT, FORBIDDEN_TERMS, FORBIDDEN_CAUSAL
from analysis.diagnostics.isd import run_isd_for_cluster, apply_evidence_quality_gate

logger = logging.getLogger("ClusterInterpretation")

DEFAULT_MODEL = os.getenv("DL_CIP_MODEL") or os.getenv("DL_GEMINI_MODEL") or "gemini-2.0-flash"

MAX_CARDS_PER_CLUSTER = 6
MAX_CHARS_PER_CARD = 600
FIELD_LIMITS = {
    "focus_text": 200,
    "parent_text": 120,
    "root_text": 120,
    "sibling_text": 80,
}

ROLE_ORDER = ["central", "leader", "bridge", "radical", "counter", "random"]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate(text: str, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _sanitize_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return text.strip()


def _card_char_count(card: Dict[str, Any]) -> int:
    total = 0
    focus = ((card.get("focus_comment") or {}).get("text") or "")
    total += len(str(focus))
    parent = ((card.get("parent_comment") or {}).get("text") or "")
    total += len(str(parent))
    root = ((card.get("root_post") or {}).get("text") or "")
    total += len(str(root))
    for sib in card.get("siblings_sample") or []:
        total += len(str((sib or {}).get("text") or ""))
    return total


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
        logger.warning("[CIP] JSON extraction failed: %s", exc)
    return {}


def _call_gemini_with_retry(model, payload_str: str, max_attempts: int = 3):
    total_start = time.perf_counter()
    for attempt in range(1, max_attempts + 1):
        attempt_start = time.perf_counter()
        try:
            result = model.generate_content(payload_str)
            logger.info(
                "[Timing] segment=cip.gemini attempt=%s dt_ms=%s",
                attempt,
                int((time.perf_counter() - attempt_start) * 1000),
            )
            logger.info(
                "[Timing] segment=cip.gemini.total dt_ms=%s",
                int((time.perf_counter() - total_start) * 1000),
            )
            return result
        except Exception as exc:
            msg = str(exc)
            transient = any(tok in msg for tok in ["InternalServerError", "500", "Overloaded", "ResourceExhausted", "UNAVAILABLE"])
            if not transient or attempt == max_attempts:
                raise
            sleep_seconds = (2 ** attempt) + random.uniform(0, 0.3)
            logger.warning("[CIP] transient error attempt=%s/%s sleep=%.1fs", attempt, max_attempts, sleep_seconds)
            time.sleep(sleep_seconds)


def _select_context_cards(
    cluster_key: int,
    golden_detail: Dict[str, Any],
    comments_by_id: Dict[str, Dict[str, Any]],
    comments_by_parent: Dict[str, List[Dict[str, Any]]],
    root_post_text: str,
    cluster_metrics: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    cards: List[Dict[str, Any]] = []
    used_ids: set[str] = set()
    evidence_ids: List[str] = []
    detail = golden_detail or {}
    for role in ROLE_ORDER:
        item = detail.get(role)
        if not isinstance(item, dict):
            continue
        cid = str(item.get("comment_id") or "").strip()
        if not cid or cid in used_ids:
            continue
        used_ids.add(cid)
        evidence_ids.append(cid)
        comment = comments_by_id.get(cid) or {}
        focus_text = _sanitize_text(comment.get("text") or item.get("text_raw") or "")
        focus_like = int(comment.get("like_count") or item.get("like_count") or 0)
        focus_reply = int(comment.get("reply_count") or item.get("reply_count") or 0)
        parent_id = comment.get("parent_comment_id")
        parent_comment = comments_by_id.get(str(parent_id)) if parent_id else None
        parent_text = _sanitize_text(parent_comment.get("text") if parent_comment else "")
        siblings: List[Dict[str, Any]] = []
        context_integrity = "ok"
        if parent_id and parent_comment:
            siblings_pool = [c for c in (comments_by_parent.get(str(parent_id)) or []) if str(c.get("id")) != cid]
            if siblings_pool:
                by_like = sorted(siblings_pool, key=lambda c: int(c.get("like_count") or 0), reverse=True)
                by_reply = sorted(siblings_pool, key=lambda c: int(c.get("reply_count") or 0), reverse=True)
                picks: List[Dict[str, Any]] = []
                if by_like:
                    picks.append(by_like[0])
                if by_reply:
                    if not picks or str(by_reply[0].get("id")) != str(picks[0].get("id")):
                        picks.append(by_reply[0])
                for sib in picks[:2]:
                    siblings.append(
                        {
                            "internal_id": str(sib.get("id") or ""),
                            "text": _sanitize_text(sib.get("text") or ""),
                        }
                    )
        else:
            context_integrity = "weak"

        root_text = _sanitize_text(root_post_text)
        truncated = False
        focus_text_t = _truncate(focus_text, FIELD_LIMITS["focus_text"])
        parent_text_t = _truncate(parent_text, FIELD_LIMITS["parent_text"])
        root_text_t = _truncate(root_text, FIELD_LIMITS["root_text"])
        siblings_t: List[Dict[str, Any]] = []
        for sib in siblings:
            sib_text = _truncate(sib.get("text") or "", FIELD_LIMITS["sibling_text"])
            siblings_t.append({"internal_id": sib.get("internal_id"), "text": sib_text})
            if sib_text != (sib.get("text") or ""):
                truncated = True
        if focus_text_t != focus_text or parent_text_t != parent_text or root_text_t != root_text:
            truncated = True

        card = {
            "focus_comment": {
                "internal_id": cid,
                "text": focus_text_t,
                "like_count": focus_like,
                "reply_count": focus_reply,
            },
            "parent_comment": {
                "internal_id": str(parent_id) if parent_id else None,
                "text": parent_text_t if parent_text_t else None,
            }
            if parent_id
            else None,
            "root_post": {"text": root_text_t if root_text_t else None},
            "siblings_sample": siblings_t,
            "cluster_metrics": cluster_metrics,
            "context_integrity": context_integrity,
        }
        if truncated:
            card["truncation"] = True
        if _card_char_count(card) > MAX_CHARS_PER_CARD:
            # Hard cap per card to keep CIP deterministic and bounded.
            card["root_post"]["text"] = _truncate((card.get("root_post") or {}).get("text") or "", 80)
            for sib in card.get("siblings_sample") or []:
                sib["text"] = _truncate(sib.get("text") or "", 60)
            card["truncation"] = True

        cards.append(card)
        if len(cards) >= MAX_CARDS_PER_CLUSTER:
            break
    return cards, evidence_ids


def _build_prompt(
    cluster_key: int,
    cluster_metrics: Dict[str, Any],
    context_cards: List[Dict[str, Any]],
    allowed_evidence_ids: List[str],
    required_evidence_ids: List[str],
) -> str:
    schema = {
        "cluster_id": "int",
        "label": "string",
        "one_liner": "string",
        "label_style": "descriptive",
        "label_confidence": "float(0-1)",
        "evidence_ids": ["string"],
    }
    context = {
        "cluster_id": cluster_key,
        "cluster_metrics": cluster_metrics,
        "context_cards": context_cards,
        "allowed_evidence_ids": allowed_evidence_ids,
        "required_evidence_ids": required_evidence_ids,
        "constraints": {
            "label_style": "descriptive",
            "forbidden_terms": FORBIDDEN_TERMS,
            "forbidden_causal": FORBIDDEN_CAUSAL,
            "min_evidence_ids": 2,
            "language": "Traditional Chinese (zh-Hant). No English except proper nouns.",
        },
    }

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        "Return ONLY valid JSON matching this schema (no markdown):\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Context JSON:\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n"
    )
    return prompt


def _validate_label_payload(
    payload: Dict[str, Any],
    allowed_ids: List[str],
    required_ids: List[str],
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        return False, ["payload_not_dict"]
    label = _sanitize_text(payload.get("label") or "")
    one_liner = _sanitize_text(payload.get("one_liner") or "")
    label_style = _sanitize_text(payload.get("label_style") or "")
    evidence_ids = payload.get("evidence_ids") or []
    if not label or not one_liner:
        errors.append("missing_label_or_one_liner")
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


def _clamp_confidence(val: Any) -> Optional[float]:
    try:
        num = float(val)
    except Exception:
        return None
    if num < 0:
        return 0.0
    if num > 1:
        return 1.0
    return num


def run_cluster_interpretation(
    post_id: int,
    writeback: bool = True,
    run_tag: Optional[str] = None,
    require_run_match: bool = True,
    isd_k: Optional[int] = None,
    context_mode: str = "card",
) -> Dict[str, Any]:
    if not require_run_match:
        logger.warning("[CIP] require_run_match disabled; enforcing per G-2 assignment integrity")
        require_run_match = True
    row_resp = supabase.table("threads_posts").select("id, preanalysis_json, post_text, post_text_raw").eq("id", post_id).limit(1).execute()
    row = (row_resp.data or [None])[0]
    if not row or not isinstance(row.get("preanalysis_json"), dict):
        raise RuntimeError("preanalysis_json missing; run preanalysis first")

    preanalysis = row.get("preanalysis_json") or {}
    meta = preanalysis.get("meta") or {}
    run_id = str(meta.get("cluster_run_id") or "").strip()
    if not run_id:
        raise RuntimeError("cluster_run_id missing; cannot write CIP")

    comments_resp = (
        supabase.table("threads_comments")
        .select("id, text, like_count, reply_count, parent_comment_id, cluster_key")
        .eq("post_id", post_id)
        .execute()
    )
    comments = comments_resp.data or []
    comments_by_id = {str(c.get("id")): c for c in comments if c.get("id")}
    comments_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for c in comments:
        parent_id = c.get("parent_comment_id")
        if parent_id:
            comments_by_parent.setdefault(str(parent_id), []).append(c)

    root_text = row.get("post_text") or row.get("post_text_raw") or ""
    per_cluster_metrics = preanalysis.get("per_cluster_metrics") or []
    physics = preanalysis.get("physics") or {}
    homogeneity = physics.get("cluster_homogeneity") or {}
    metrics_by_cluster: Dict[int, Dict[str, Any]] = {}
    for m in per_cluster_metrics:
        try:
            key_int = int(m.get("cluster_id"))
        except Exception:
            continue
        metrics_by_cluster[key_int] = {
            "cluster_id": key_int,
            "size_share": m.get("size_share"),
            "like_share": m.get("like_share"),
            "homogeneity": homogeneity.get(str(key_int)),
        }

    golden_detail = preanalysis.get("golden_samples_detail") or {}
    cluster_keys = [k for k in golden_detail.keys() if k != "golden_samples_meta"]
    cluster_keys_sorted: List[int] = []
    for k in cluster_keys:
        try:
            cluster_keys_sorted.append(int(k))
        except Exception:
            continue
    cluster_keys_sorted.sort()

    model_name = os.getenv("DL_GEMINI_MODEL", DEFAULT_MODEL)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("missing GEMINI_API_KEY/GOOGLE_API_KEY")

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    llm_model = genai.GenerativeModel(model_name)

    results: List[Dict[str, Any]] = []
    try:
        isd_k = int(isd_k) if isd_k is not None else int(os.getenv("DL_ISD_K") or 3)
    except Exception:
        isd_k = int(os.getenv("DL_ISD_K") or 3)
    stats = {
        "clusters": 0,
        "stable": 0,
        "non_convergent": 0,
        "underspecified": 0,
        "soft_fail": 0,
        "hard_fail": 0,
    }

    for ck in cluster_keys_sorted:
        detail = golden_detail.get(str(ck)) or {}
        cluster_metrics = metrics_by_cluster.get(ck) or {"cluster_id": ck}
        cards, allowed_ids = _select_context_cards(
            ck,
            detail,
            comments_by_id,
            comments_by_parent,
            root_text,
            cluster_metrics,
        )

        role_ids = {}
        for role in ROLE_ORDER:
            item = detail.get(role)
            if isinstance(item, dict) and item.get("comment_id"):
                role_ids[role] = str(item.get("comment_id"))
        required_ids = []
        if role_ids.get("leader"):
            required_ids.append(role_ids.get("leader"))
        if role_ids.get("counter"):
            required_ids.append(role_ids.get("counter"))

        prompt = _build_prompt(ck, cluster_metrics, cards, allowed_ids, required_ids)
        prompt_hash = _sha256(prompt)

        isd_report, label_payload = run_isd_for_cluster(
            post_id=post_id,
            cluster_key=ck,
            run_id=run_id,
            prompt=prompt,
            allowed_ids=allowed_ids,
            required_ids=required_ids,
            llm_model=llm_model,
            k=isd_k,
            context_mode=context_mode,
            prompt_hash=prompt_hash,
            model_name=model_name,
        )
        evidence_texts: List[str] = []
        evidence_ids = label_payload.get("evidence_ids") or []
        if isinstance(evidence_ids, list):
            for eid in evidence_ids:
                key = str(eid).strip()
                if not key:
                    continue
                comment = comments_by_id.get(key) or {}
                text_val = (comment.get("text") or "").strip()
                if text_val:
                    evidence_texts.append(text_val)
        isd_report = apply_evidence_quality_gate(isd_report, evidence_texts)
        upsert_cluster_diagnostics(isd_report, preanalysis_json=preanalysis)

        status = isd_report.verdict
        label_text = _sanitize_text(label_payload.get("label") or "")
        one_liner = _sanitize_text(label_payload.get("one_liner") or "")
        label_style = _sanitize_text(label_payload.get("label_style") or "descriptive") or "descriptive"
        label_conf = _clamp_confidence(label_payload.get("label_confidence"))
        evidence_ids = label_payload.get("evidence_ids") or []
        if isinstance(evidence_ids, list):
            evidence_ids = [str(e).strip() for e in evidence_ids if isinstance(e, (str, int))]
        else:
            evidence_ids = []
        evidence_ids = list(dict.fromkeys(evidence_ids))

        label_unstable = status != "stable"
        if status == "stable":
            stats["stable"] += 1
        elif status == "non_convergent":
            stats["non_convergent"] += 1
            stats["soft_fail"] += 1
        else:
            stats["underspecified"] += 1
            stats["hard_fail"] += 1

        cip_id = f"{post_id}::c{ck}::{run_id}"
        semantic_payload = {
            "run_id": run_id,
            "label": label_text or None,
            "label_confidence": label_conf,
            "label_unstable": label_unstable,
            "evidence_ids": evidence_ids,
            "context_cards": cards,
            "prompt_hash": prompt_hash,
            "model_name": model_name,
        }
        system_payload = {
            "id": cip_id,
            "post_id": post_id,
            "cluster_key": ck,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        cip_row = {**system_payload, **semantic_payload}

        guard_semantic_write(
            preanalysis,
            run_id,
            "threads_cluster_interpretations",
            semantic_payload.keys(),
            context={"post_id": post_id, "cluster_key": ck, "caller": "cluster_interpretation.upsert"},
        )
        supabase.table("threads_cluster_interpretations").upsert(
            cip_row,
            on_conflict="post_id,cluster_key,run_id",
        ).execute()

        if writeback and not label_unstable and label_text and one_liner:
            guard_semantic_write(
                preanalysis,
                run_id,
                "threads_comment_clusters",
                ["label", "summary", "run_id"],
                context={"post_id": post_id, "cluster_key": ck, "caller": "cluster_interpretation.writeback"},
            )
            update_cluster_metadata(
                post_id,
                [{"cluster_key": ck, "label": label_text, "summary": one_liner}],
                run_id=run_id,
                preanalysis_json=preanalysis,
            )

        results.append(
            {
                "cluster_key": ck,
                "status": status,
                "label": label_text,
                "label_unstable": label_unstable,
                "isd": {
                    "stability_avg": isd_report.stability_avg,
                    "stability_min": isd_report.stability_min,
                    "drift_avg": isd_report.drift_avg,
                    "drift_max": isd_report.drift_max,
                    "verdict": isd_report.verdict,
                },
            }
        )
        stats["clusters"] += 1

    return {
        "post_id": post_id,
        "run_id": run_id,
        "model_name": model_name,
        "stats": stats,
        "results": results,
        "run_tag": run_tag,
    }
