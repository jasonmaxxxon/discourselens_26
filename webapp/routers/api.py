import os
import traceback
import asyncio
import re
import importlib
import time
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Any, Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
import math
import json as _json
import numpy as np

router = APIRouter()


_runner_mod = None
_ops_metrics_mod = None
_axis_sanitize_mod = None
_job_manager_cls = None


def _load_runner():
    global _runner_mod
    if _runner_mod is None:
        _runner_mod = importlib.import_module("webapp.services.pipeline_runner")
    return _runner_mod


def _load_ops_metrics():
    global _ops_metrics_mod
    if _ops_metrics_mod is None:
        _ops_metrics_mod = importlib.import_module("webapp.services.ops_metrics")
    return _ops_metrics_mod


class _LazyModuleProxy:
    def __init__(self, loader):
        self._loader = loader

    def __getattr__(self, name):
        return getattr(self._loader(), name)


def _get_job_manager_cls():
    global _job_manager_cls
    if _job_manager_cls is None:
        _job_manager_cls = importlib.import_module("webapp.services.job_manager").JobManager
    return _job_manager_cls


def _sanitize_analysis_json(value):
    global _axis_sanitize_mod
    if _axis_sanitize_mod is None:
        _axis_sanitize_mod = importlib.import_module("analysis.axis_sanitize")
    return _axis_sanitize_mod.sanitize_analysis_json(value)


runner = _LazyModuleProxy(_load_runner)
ops_metrics = _LazyModuleProxy(_load_ops_metrics)


def _set_degraded_response(response: Optional[Response]) -> None:
    if response is None:
        return
    response.headers["x-ops-degraded"] = "1"
    # Short cache keeps UI stable without hammering DB in degraded windows.
    response.headers["Cache-Control"] = "max-age=2"


def _run_with_retry(label: str, fn, attempts: int = 2, base_delay: float = 0.15):
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            runner.logger.warning(
                f"{label} transient error",
                extra={"attempt": attempt + 1, "error": str(e)},
            )
            if attempt < attempts - 1:
                time.sleep(base_delay * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{label} failed without explicit error")


def _trace_id_from_request(request: Optional[Request]) -> str:
    if request is not None:
        state = getattr(request, "state", None)
        if state is not None:
            value = getattr(state, "trace_id", None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        header = request.headers.get("x-request-id")
        if isinstance(header, str) and header.strip():
            return header.strip()
    return uuid.uuid4().hex


def _attach_trace_id(response: Optional[Response], trace_id: str) -> None:
    if response is None:
        return
    response.headers["X-Request-ID"] = trace_id


def _json_with_trace(payload: Dict[str, Any], status_code: int, trace_id: str) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers={"X-Request-ID": trace_id})


def _pending_payload(
    trace_id: str,
    reason_code: str,
    detail: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "status": "pending",
        "reason": reason_code,
        "reason_code": reason_code,
        "detail": detail,
        "trace_id": trace_id,
    }
    if extra:
        out.update(extra)
    return out


def _post_not_found_payload(trace_id: str, post_id: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "status": "not_found",
        "reason": "post_not_found",
        "reason_code": "post_not_found",
        "detail": "Post not found",
        "post_id": str(post_id),
        "trace_id": trace_id,
    }
    if extra:
        out.update(extra)
    return out


def _classify_exception(exc: Exception) -> Dict[str, Any]:
    name = type(exc).__name__
    msg = str(exc or '').lower()
    if isinstance(exc, (ValidationError, ValueError, TypeError)):
        return {'kind': 'VALIDATION_ERROR', 'retriable': False, 'message_safe': str(exc) or name}
    validation_tokens = (
        '22p02',
        'invalid input syntax',
        'invalid uuid',
        'invalid literal',
        'badly formed hexadecimal uuid string',
    )
    if any(token in msg for token in validation_tokens):
        return {'kind': 'VALIDATION_ERROR', 'retriable': False, 'message_safe': 'Validation error'}
    transport_tokens = (
        'remoteprotocolerror',
        'readtimeout',
        'connecttimeout',
        'connecterror',
        'connection reset',
        'connection aborted',
        'temporarily unavailable',
        'server disconnected',
        'econnreset',
    )
    if any(token in msg for token in transport_tokens) or 'timeout' in name.lower() or 'httpx' in name.lower():
        return {'kind': 'UPSTREAM_TRANSPORT', 'retriable': True, 'message_safe': 'Upstream transport error'}
    if isinstance(exc, HTTPException):
        code = int(getattr(exc, 'status_code', 500) or 500)
        if code == 404:
            return {'kind': 'NOT_FOUND', 'retriable': False, 'message_safe': 'Resource not found'}
        if code < 500:
            return {'kind': 'VALIDATION_ERROR', 'retriable': False, 'message_safe': str(getattr(exc, 'detail', 'Validation error'))}
    return {'kind': 'INTERNAL_BUG', 'retriable': False, 'message_safe': str(exc) or name}


def _pending_response(
    response: Optional[Response],
    trace_id: str,
    reason: str,
    detail: str,
    retry_after_ms: int,
    extra: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    _set_degraded_response(response)
    payload: Dict[str, Any] = {
        'status': 'pending',
        'reason': reason,
        'reason_code': reason,
        'detail': detail,
        'retry_after_ms': retry_after_ms,
        'trace_id': trace_id,
    }
    if extra:
        payload.update(extra)
    return JSONResponse(payload, status_code=202, headers={'X-Request-ID': trace_id, 'Retry-After': '2'})


def _empty_response(trace_id: str, detail: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        'status': 'empty',
        'reason': 'no_data',
        'reason_code': 'no_data',
        'detail': detail,
        'trace_id': trace_id,
    }
    if extra:
        payload.update(extra)
    return payload


def _error_response(response: Optional[Response], trace_id: str, detail: str, status_code: int = 500) -> JSONResponse:
    _set_degraded_response(response)
    payload = {
        'status': 'error',
        'reason': 'internal_bug',
        'reason_code': 'internal_bug',
        'detail': detail,
        'trace_id': trace_id,
    }
    return JSONResponse(payload, status_code=status_code, headers={'X-Request-ID': trace_id})


def _response_for_exception(
    exc: Exception,
    response: Optional[Response],
    trace_id: str,
    pending_reason: str,
    pending_detail: str,
    extra: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    classified = _classify_exception(exc)
    kind = classified.get('kind')
    if kind == 'UPSTREAM_TRANSPORT':
        return _pending_response(
            response=response,
            trace_id=trace_id,
            reason='upstream_transport',
            detail=pending_detail,
            retry_after_ms=1500,
            extra=extra,
        )
    if kind == 'NOT_FOUND':
        payload: Dict[str, Any] = {
            'status': 'not_found',
            'reason': 'not_found',
            'reason_code': 'not_found',
            'detail': classified.get('message_safe') or pending_reason,
            'trace_id': trace_id,
        }
        if extra:
            payload.update(extra)
        return JSONResponse(payload, status_code=404, headers={'X-Request-ID': trace_id})
    if kind == 'VALIDATION_ERROR':
        payload: Dict[str, Any] = {
            'status': 'error',
            'reason': 'validation_error',
            'reason_code': 'validation_error',
            'detail': classified.get('message_safe') or pending_reason,
            'trace_id': trace_id,
        }
        if extra:
            payload.update(extra)
        return JSONResponse(payload, status_code=400, headers={'X-Request-ID': trace_id})
    return _error_response(response, trace_id=trace_id, detail=f"{pending_reason}: {classified.get('message_safe') or 'internal error'}")




def _parse_iso(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    return int(dt.timestamp() * 1000)


def _truncate_text(text: str, limit: int = 160) -> str:
    if not isinstance(text, str):
        return ""
    clean = " ".join(text.split()).strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "…"


def _coerce_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = _json.loads(text)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v is not None and str(v).strip()]
        except Exception:
            pass
        # best-effort comma split
        return [v.strip() for v in text.split(",") if v.strip()]
    return []


def _coerce_id_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = _json.loads(text)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v is not None and str(v).strip()]
        except Exception:
            pass
        return [v.strip() for v in text.split(",") if v.strip()]
    return []


def _parse_vector(val: Any) -> Optional[List[float]]:
    if val is None:
        return None
    if isinstance(val, list):
        try:
            return [float(x) for x in val]
        except Exception:
            return None
    if isinstance(val, str):
        text = val.strip()
        if not text:
            return None
        try:
            parsed = _json.loads(text)
            if isinstance(parsed, list):
                return [float(x) for x in parsed]
        except Exception:
            return None
    return None


_KEYWORD_STOPWORDS = {
    "reply",
    "replies",
    "replying",
    "replyto",
    "replyingto",
    "send",
    "sent",
    "rt",
    "thread",
    "threads",
    "comment",
    "comments",
    "post",
    "posts",
    "https",
    "http",
    "amp",
}

_CJK_STOPWORDS = {
    "回覆",
    "回應",
    "留言",
    "分享",
    "轉發",
    "多謝",
    "其實",
    "不過",
    "唔知",
    "唔係",
    "呢個",
    "一個",
    "自己",
    "覺得",
    "覺住",
    "應該",
    "問題",
    "因為",
}

_MOJIBAKE_MARKERS = ("Ã", "Â", "â", "ð", "æ", "å", "ç", "ï", "œ", "ž", "™", "‹", "‰")


def _has_cjk(text: str) -> bool:
    if not text:
        return False
    count = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            count += 1
            if count >= 2:
                return True
    return False


def _cjk_count(text: str) -> int:
    if not text:
        return 0
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def _marker_count(text: str) -> int:
    if not text:
        return 0
    return sum(text.count(m) for m in _MOJIBAKE_MARKERS)


def _repair_mojibake(value: Any) -> Any:
    """
    Best-effort fix for strings decoded with a wrong single-byte codec
    (e.g. UTF-8 bytes interpreted as latin-1 / cp1252).
    """
    if not isinstance(value, str) or not value:
        return value
    if not any(m in value for m in _MOJIBAKE_MARKERS):
        return value
    try:
        fixed = value.encode("latin-1").decode("utf-8")
    except Exception:
        return value
    if not fixed:
        return value
    before_score = _cjk_count(value) - _marker_count(value)
    after_score = _cjk_count(fixed) - _marker_count(fixed)
    return fixed if after_score >= before_score else value


def _extract_keywords(texts: List[str], top_n: int = 4) -> List[str]:
    counter: Counter[str] = Counter()
    for t in texts:
        if not t:
            continue
        lower = t.lower()
        for tok in re.findall(r"[A-Za-z0-9#@']{3,}", lower):
            if tok.startswith("@") or tok.startswith("http"):
                continue
            if tok in _KEYWORD_STOPWORDS:
                continue
            if tok.isdigit():
                continue
            if any(ch.isdigit() for ch in tok):
                continue
            counter[tok] += 1
        # CJK bigrams for Chinese/Japanese/Korean
        chars = [c for c in t if "\u4e00" <= c <= "\u9fff"]
        for i in range(len(chars) - 1):
            bigram = chars[i] + chars[i + 1]
            if bigram in _CJK_STOPWORDS:
                continue
            counter[bigram] += 1
    return [k for k, _ in counter.most_common(top_n)]


def _infer_cluster_role(sample_texts: List[str]) -> tuple[str, str]:
    if not sample_texts:
        return ("討論群", "以一般觀點補充討論內容。")

    joined = " ".join(sample_texts)
    def _count(tokens: List[str]) -> int:
        return sum(joined.count(tok) for tok in tokens)

    experience = _count(["我", "自己", "本人", "我哋", "我係", "以前", "曾經", "經歷", "試過"])
    advice = _count(["建議", "可以", "應該", "最好", "不如", "試下", "要", "記住", "最緊要"])
    policy = _count(["政府", "政策", "budget", "外勞", "大陸", "移民", "醫院", "供過於求", "制度", "經濟", "市場", "資源"])

    if experience >= advice and experience >= policy:
        return ("經驗共鳴者", "以個人經歷回應，提供情緒支持與共鳴。")
    if advice >= experience and advice >= policy:
        return ("專業建議者", "提出可行方案或路徑，具有指導性。")
    if policy >= experience and policy >= advice:
        return ("制度討論群", "聚焦制度/資源/政策層面的原因與影響。")
    return ("討論群", "補充觀點與背景，推進討論。")


def _format_cluster_summary(role_summary: str, keywords: List[str], influence_tag: str) -> str:
    parts = [role_summary]
    if keywords:
        parts.append("焦點：" + "、".join(keywords[:3]))
    if influence_tag:
        parts.append(influence_tag)
    return " ".join([p for p in parts if p])


def _normalize_comment_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    clean = " ".join(text.split()).strip().lower()
    clean = re.sub(r"^replying to @\w+[:：]?\s*", "", clean)
    clean = re.sub(r"^回覆 @\w+[:：]?\s*", "", clean)
    return clean


def _normalize_coords(points: List[List[float]]) -> List[List[float]]:
    if not points:
        return []
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_range = x_max - x_min
    y_range = y_max - y_min
    normed = []
    for x, y in points:
        nx = 0.5 if x_range == 0 else (x - x_min) / x_range
        ny = 0.5 if y_range == 0 else (y - y_min) / y_range
        # keep dots away from edges
        normed.append([0.1 + nx * 0.8, 0.1 + ny * 0.8])
    return normed


def _window_hours_from_text(window: Optional[str], default_hours: int = 24) -> int:
    text = str(window or "").strip().lower()
    if not text:
        return default_hours
    match = re.match(r"^(\d+)\s*h$", text)
    if not match:
        return default_hours
    try:
        hours = int(match.group(1))
    except Exception:
        return default_hours
    return max(1, min(hours, 168))


def _extract_behavior_missing_ts_pct(analysis_json: Any) -> Optional[float]:
    if not isinstance(analysis_json, dict):
        return None
    meta = analysis_json.get("meta") or {}
    if not isinstance(meta, dict):
        return None
    behavior = meta.get("behavior") or {}
    if not isinstance(behavior, dict):
        return None
    quality = behavior.get("quality_flags") or {}
    if not isinstance(quality, dict):
        return None
    raw = quality.get("missing_ts_pct")
    try:
        val = float(raw)
    except Exception:
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return max(0.0, min(1.0, val))


def _extract_behavior_risk(analysis_json: Any) -> Optional[float]:
    if not isinstance(analysis_json, dict):
        return None
    meta = analysis_json.get("meta") or {}
    if not isinstance(meta, dict):
        return None
    behavior = meta.get("behavior") or {}
    if not isinstance(behavior, dict):
        return None
    raw = behavior.get("overall_behavior_risk")
    try:
        val = float(raw)
    except Exception:
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return max(0.0, min(1.0, val))


def _coerce_post_id(post_id: str) -> Any:
    try:
        return int(post_id)
    except Exception:
        return post_id


def _load_post_row(post_id: str, select_fields: str = "id") -> tuple[Optional[Dict[str, Any]], bool]:
    """
    Returns (row, degraded):
    - row is dict when found, None when not found or unavailable.
    - degraded=True means backend/read path unavailable (pending state).
    """
    pid = _coerce_post_id(post_id)
    try:
        resp = _run_with_retry(
            "api/post lookup",
            lambda: runner.supabase.table("threads_posts").select(select_fields).eq("id", pid).limit(1).execute(),
        )
    except Exception:
        return (None, True)
    rows = resp.data or []
    if not rows:
        return (None, False)
    row = rows[0] if isinstance(rows[0], dict) else None
    return (row, False)

# --- Pydantic models (unchanged) ---


class PipelineBBatchRequest(BaseModel):
    keyword: Optional[str] = None
    urls: Optional[List[str]] = None
    max_posts: int = 20
    exclude_existing: bool = True
    reprocess_policy: str = "skip_if_exists"
    ingest_source: str = "B"
    mode: str = "run"  # "preview" | "run"
    preview: Optional[bool] = None  # legacy field
    pipeline_mode: str = "full"  # "full" | "ingest"
    concurrency: Optional[int] = None
    vision_mode: Optional[str] = None
    vision_stage_cap: Optional[str] = None


class ReviewRequest(BaseModel):
    post_id: str
    bundle_id: str
    analysis_build_id: Optional[str] = None
    label_type: str
    schema_version: str = "v1"
    decision: Dict[str, Any]
    comment_id: str
    cluster_key: Optional[int] = None
    notes: Optional[str] = None
    axis_id: Optional[str] = None
    axis_version: Optional[str] = None


class CasebookBucket(BaseModel):
    t0: str
    t1: str


class CasebookMetricsSnapshot(BaseModel):
    bucket_comment_count: int
    prev_bucket_comment_count: int
    momentum_pct: Optional[float] = None
    dominant_cluster_id: Optional[int] = None
    dominant_cluster_share: Optional[float] = None


class CasebookCoverageSnapshot(BaseModel):
    comments_loaded: int
    comments_total: Optional[int] = None
    is_truncated: bool


class CasebookFilterSnapshot(BaseModel):
    author: Optional[str] = None
    cluster_key: Optional[int] = None
    query: Optional[str] = None
    sort: Optional[str] = None


class CasebookCreateRequest(BaseModel):
    evidence_id: str
    comment_id: str
    evidence_text: str
    post_id: str
    captured_at: str
    bucket: CasebookBucket
    metrics_snapshot: CasebookMetricsSnapshot
    coverage: CasebookCoverageSnapshot
    summary_version: str = "casebook_summary_v1"
    filters: CasebookFilterSnapshot
    analyst_note: Optional[str] = None


class AcademicReference(BaseModel):
    author: str
    year: str
    note: str


class SectionOne(BaseModel):
    executive_summary: str
    phenomenon_spotlight: str
    l1_analysis: str
    l2_analysis: str
    l3_analysis: str
    faction_analysis: str
    strategic_implication: str
    author_influence: str | None = None
    academic_references: list[AcademicReference] | None = None


class AnalysisMeta(BaseModel):
    Post_ID: str
    Timestamp: str
    High_Impact: bool


class QuantifiableTags(BaseModel):
    Sector_ID: str
    Primary_Emotion: str
    Strategy_Code: str
    Civil_Score: int
    Homogeneity_Score: float
    Author_Influence: str


class PostStats(BaseModel):
    Likes: int
    Replies: int
    Views: int


class ClusterInsight(BaseModel):
    name: str
    summary: str
    pct: float | None = None


class DiscoveryChannel(BaseModel):
    Sub_Variant_Name: str
    Is_New_Phenomenon: bool
    Phenomenon_Description: str


class StrategySnippetModel(BaseModel):
    name: str
    intensity: float
    description: str
    example: str
    citation: str


class ToneFingerprintModel(BaseModel):
    assertiveness: float
    cynicism: float
    playfulness: float
    contempt: float
    description: str
    example: str


class FactionSummaryModel(BaseModel):
    name: str
    share: float
    members: List[str]
    sentiment: Optional[float] = None


class CommentSampleModel(BaseModel):
    user: str
    text: str
    likes: int


class NarrativeShiftNodeModel(BaseModel):
    ts: str
    likes: int
    sample_text: str


class SectionTwo(BaseModel):
    narrative_shifts: Optional[List[NarrativeShiftNodeModel]] = None
    competing_frames: Optional[List[str]] = None
    infiltration_signals: Optional[List[str]] = None


class PostAnalysisResponse(BaseModel):
    section_one: SectionOne
    section_two: SectionTwo
    meta: AnalysisMeta
    quantifiable_tags: QuantifiableTags
    post_stats: PostStats
    cluster_insights: List[ClusterInsight]
    discovery_channels: List[DiscoveryChannel]
    strategy_snippets: List[StrategySnippetModel]
    tone_fingerprint: ToneFingerprintModel
    faction_summary: Optional[List[FactionSummaryModel]] = None
    comment_samples: Optional[List[CommentSampleModel]] = None


class RawAnalysisResponse(BaseModel):
    post_id: str
    full_report_markdown: str


class PostListItem(BaseModel):
    id: str
    snippet: str
    url: Optional[str] = None
    created_at: str | None
    author: Optional[str] = None
    like_count: Optional[int] = None
    view_count: Optional[int] = None
    reply_count: Optional[int] = None
    share_count: Optional[int] = None
    repost_count: Optional[int] = None
    has_analysis: bool = False
    analysis_is_valid: Optional[bool] = None
    analysis_version: Optional[str] = None
    analysis_build_id: Optional[str] = None
    archive_captured_at: Optional[str] = None
    archive_build_id: Optional[str] = None
    has_archive: Optional[bool] = None
    ai_tags: Optional[List[str]] = None
    phenomenon_id: Optional[str] = None
    phenomenon_status: Optional[str] = None
    phenomenon_case_id: Optional[str] = None
    phenomenon_name: Optional[str] = None


class JobPostResult(BaseModel):
    post_id: Optional[str] = None
    has_analysis: Optional[bool] = None
    analysis_is_valid: Optional[bool] = None
    analysis_version: Optional[str] = None
    analysis_build_id: Optional[str] = None
    invalid_reason: Optional[str] = None


class JobResult(BaseModel):
    status: str
    pipeline: str
    job_id: str
    mode: Optional[str] = None
    post_id: Optional[str] = None
    posts: Optional[List[JobPostResult]] = None
    summary: Optional[str] = None
    logs: Optional[List[str]] = None
    error_stage: Optional[str] = None


@router.get("/health")
def api_health():
    return {"status": "ok"}


@router.get("/ops/kpi")
async def get_ops_kpi(response: Response, request: Request, range: Optional[str] = "7d"):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)
    range_days = ops_metrics._parse_range_days(range)
    try:
        data = await ops_metrics.get_ops_kpi(range_days)
        if isinstance(data, dict):
            data.setdefault("status", "ready")
            data.setdefault("trace_id", trace_id)
        return data
    except Exception:
        runner.logger.exception("api/ops/kpi failed")
        _set_degraded_response(response)
        return _pending_payload(
            trace_id=trace_id,
            reason_code="ops_kpi_pending",
            detail="Ops KPI source temporarily unavailable.",
            extra={
                "range_days": range_days,
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "summary": {},
                "trends": [],
            },
        )
    error_message: Optional[str] = None


@router.get("/overview/telemetry")
def get_overview_telemetry(response: Response, window: Optional[str] = "24h"):
    hours = _window_hours_from_text(window, default_hours=24)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    degraded = False

    jobs: List[Dict[str, Any]] = []
    latest_job_id: Optional[str] = None
    try:
        jobs_resp = _run_with_retry(
            "api/overview/telemetry jobs",
            lambda: runner.supabase.table("job_batches")
            .select("id,status,updated_at,created_at")
            .order("updated_at", desc=True)
            .limit(25)
            .execute(),
        )
        jobs = jobs_resp.data or []
    except Exception:
        degraded = True
        jobs = []

    if jobs:
        rank = {
            "processing": 0,
            "discovering": 0,
            "queued": 1,
            "pending": 1,
            "failed": 3,
            "stale": 3,
            "canceled": 4,
        }
        jobs_sorted = sorted(
            jobs,
            key=lambda row: (
                rank.get(str(row.get("status") or "").lower(), 2),
                -(_parse_iso(row.get("updated_at")) or 0),
            ),
        )
        latest_job_id = str((jobs_sorted[0] or {}).get("id") or "")

    posts: List[Dict[str, Any]] = []
    try:
        posts_resp = _run_with_retry(
            "api/overview/telemetry posts",
            lambda: runner.supabase.table("threads_posts")
            .select("id,created_at,phenomenon_id,analysis_json")
            .order("created_at", desc=True)
            .limit(max(120, hours * 10))
            .execute(),
        )
        posts = posts_resp.data or []
    except Exception:
        degraded = True
        posts = []

    bucket_starts: List[datetime] = [start + timedelta(hours=i) for i in range(hours)]
    bucket_values: List[List[float]] = [[] for _ in range(hours)]
    bucket_samples: List[int] = [0 for _ in range(hours)]
    latest_post_id: Optional[str] = None
    latest_phenomenon_id: Optional[str] = None

    for idx, row in enumerate(posts):
        post_ts_text = row.get("created_at")
        post_ms = _parse_iso(post_ts_text)
        if post_ms is None:
            continue
        post_dt = datetime.fromtimestamp(post_ms / 1000, tz=timezone.utc)
        if post_dt < start or post_dt > now:
            continue
        if idx == 0:
            latest_post_id = str(row.get("id") or "")
            latest_phenomenon_id = str(row.get("phenomenon_id") or "") or None
        bucket_index = int((post_dt - start).total_seconds() // 3600)
        if bucket_index < 0 or bucket_index >= hours:
            continue
        bucket_samples[bucket_index] += 1
        missing_pct = _extract_behavior_missing_ts_pct(row.get("analysis_json"))
        if missing_pct is not None:
            bucket_values[bucket_index].append(missing_pct * 100.0)

    all_values = [v for values in bucket_values for v in values]
    baseline = round(sum(all_values) / max(1, len(all_values)), 2) if all_values else 0.0

    drift_buckets: List[Dict[str, Any]] = []
    for idx in range(hours):
        values = bucket_values[idx]
        score = round(sum(values) / len(values), 2) if values else baseline
        drift_buckets.append(
            {
                "ts_hour": bucket_starts[idx].isoformat().replace("+00:00", "Z"),
                "drift_score": score,
                "baseline": baseline,
                "sample_n": bucket_samples[idx],
            }
        )

    momentum_events: List[Dict[str, Any]] = []
    if latest_job_id:
        try:
            items_resp = _run_with_retry(
                "api/overview/telemetry job items",
                lambda: runner.supabase.table("job_items")
                .select("id,status,stage,target_id,updated_at")
                .eq("job_id", latest_job_id)
                .order("updated_at", desc=True)
                .limit(24)
                .execute(),
            )
            for row in items_resp.data or []:
                status = str(row.get("status") or "").lower()
                level = "info"
                if status in {"failed", "stale", "canceled"}:
                    level = "bad"
                elif status in {"queued", "pending"}:
                    level = "warn"
                elif status in {"completed", "done"}:
                    level = "good"
                momentum_events.append(
                    {
                        "ts": row.get("updated_at"),
                        "level": level,
                        "actor": "job-worker",
                        "action": f"{row.get('stage') or 'stage'} · {row.get('status') or 'pending'}",
                        "ref_type": "job_item",
                        "ref_id": str(row.get("id") or row.get("target_id") or ""),
                    }
                )
        except Exception:
            degraded = True

    # Add lightweight post-level telemetry for context continuity.
    for row in posts[:16]:
        missing_pct = _extract_behavior_missing_ts_pct(row.get("analysis_json"))
        if missing_pct is None:
            continue
        level = "warn" if missing_pct >= 0.5 else "info"
        momentum_events.append(
            {
                "ts": row.get("created_at"),
                "level": level,
                "actor": "analysis",
                "action": f"post_quality missing_ts {round(missing_pct * 100)}%",
                "ref_type": "post",
                "ref_id": str(row.get("id") or ""),
            }
        )

    momentum_events = sorted(
        [e for e in momentum_events if e.get("ts")],
        key=lambda row: _parse_iso(str(row.get("ts") or "")) or 0,
        reverse=True,
    )[:40]

    if degraded:
        _set_degraded_response(response)

    return {
        "window": f"{hours}h",
        "drift_buckets": drift_buckets,
        "momentum_events": momentum_events,
        "active_context": {
            "job_id": latest_job_id,
            "post_id": latest_post_id,
            "phenomenon_id": latest_phenomenon_id,
        },
        "meta": {
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "degraded": degraded,
            "source": ["job_batches", "job_items", "threads_posts"],
        },
    }


class RunRequest(BaseModel):
    url: str


# --- API endpoints (unchanged logic) ---


@router.post("/run/batch")
async def run_pipeline_b_backend_api(req: PipelineBBatchRequest):
    try:
        mode = req.mode or "run"
        if req.preview is True:
            mode = "preview"
        summary = await runner.process_pipeline_b_backend(
            keyword=req.keyword,
            urls=req.urls,
            max_posts=req.max_posts,
            exclude_existing=req.exclude_existing,
            reprocess_policy=req.reprocess_policy,
            ingest_source=req.ingest_source,
            mode=mode,
            concurrency=getattr(req, "concurrency", None) or 2,
            pipeline_mode=req.pipeline_mode or "full",
            vision_mode=getattr(req, "vision_mode", None) or os.environ.get("VISION_MODE") or "auto",
            vision_stage_cap=getattr(req, "vision_stage_cap", None) or os.environ.get("VISION_STAGE_CAP") or "auto",
        )
        return JSONResponse(summary)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# DEPRECATED: Prefer /api/jobs/ (Supabase JobManager). Legacy wrapper for Pipeline A.
@router.post("/run")
async def api_run_default(req: RunRequest, background_tasks: BackgroundTasks):
    """
    Legacy endpoint: defaults to Pipeline A.
    Now delegates to JobManager (Supabase) to avoid in-memory ghost jobs.
    """
    manager = _get_job_manager_cls()()
    pipeline_type = "A"
    mode = "analyze"
    input_config = {"url": req.url, "target": req.url, "targets": [req.url]}

    job_id = await manager.create_job_from_payload(pipeline_type, mode, input_config)
    await manager.start_discovery(job_id)
    loop = asyncio.get_event_loop()
    background_tasks.add_task(loop.create_task, manager.run_worker_mock(job_id))

    return {"job_id": job_id, "status": "pending", "pipeline": pipeline_type}


# DEPRECATED: Prefer /api/jobs/ (Supabase JobManager). Legacy wrapper for Pipeline A/B/C.
@router.post("/run/{pipeline}")
async def api_run_pipeline(pipeline: str, background_tasks: BackgroundTasks, payload: Dict[str, Any]):
    p = (pipeline or "").strip().lower().replace("-", "_")

    alias = {
        "a": "A",
        "pipeline_a": "A",
        "pipelinea": "A",
        "b": "B",
        "pipeline_b": "B",
        "pipelineb": "B",
        "c": "C",
        "pipeline_c": "C",
        "pipelinec": "C",
    }

    pipeline = alias.get(p)
    if pipeline is None:
        raise HTTPException(status_code=400, detail="Unsupported pipeline")

    manager = _get_job_manager_cls()()

    if pipeline == "A":
        url = payload.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="Missing url")
        mode = payload.get("mode") or "analyze"
        input_config = {"url": url, "target": url, "targets": [url]}
    elif pipeline == "B":
        keyword = payload.get("keyword")
        urls = payload.get("urls") or []
        lines = payload.get("lines") or []
        targets: list[str] = []
        if keyword:
            targets.append(str(keyword))
        for u in urls:
            if isinstance(u, str):
                targets.append(u)
        for l in lines:
            if isinstance(l, str):
                targets.append(l)
        if not keyword and not targets:
            raise HTTPException(status_code=400, detail="Missing keyword/targets")
        max_posts = int(payload.get("max_posts") or 50)
        mode = payload.get("mode") or "ingest"
        input_config = {"keyword": keyword, "targets": targets, "max_posts": max_posts}
    else:
        max_posts = int(payload.get("max_posts") or 50)
        threshold = int(payload.get("threshold") or 0)
        mode = payload.get("mode") or "ingest"
        input_config = {"max_posts": max_posts, "threshold": threshold}

    job_id = await manager.create_job_from_payload(pipeline, mode, input_config)
    await manager.start_discovery(job_id)
    loop = asyncio.get_event_loop()
    background_tasks.add_task(loop.create_task, manager.run_worker_mock(job_id))

    return {"job_id": job_id, "status": "pending", "pipeline": pipeline}


# DEPRECATED: Prefer /api/jobs/{job_id} (+ /items, /summary). Legacy compatibility endpoint.
@router.get("/status/{job_id}")
async def api_status(job_id: str):
    """
    Legacy compatibility endpoint.
    Delegates to Supabase-backed JobManager; returns a JobResult-shaped payload.
    """
    manager = _get_job_manager_cls()()
    header = await manager._table_select_single("job_batches", job_id)
    if not header:
        raise HTTPException(status_code=404, detail="job not found")

    items, _ = await manager.get_job_items(job_id, limit=20)
    summary, _ = await manager.get_job_summary(job_id)

    result = {
        "status": (summary or {}).get("status") or header.get("status"),
        "pipeline": header.get("pipeline_type"),
        "job_id": job_id,
        "mode": header.get("mode"),
        "post_id": None,
        "posts": None,
        "summary": (summary or {}).get("error_summary") or header.get("error_summary") or "",
        "logs": [],
    }

    try:
        return JobResult(**result).model_dump()
    except ValidationError:
        return result


@router.get("/posts", response_model=List[PostListItem])
def list_posts(response: Response, request: Request):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)
    degraded = False
    no_cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    for k, v in no_cache_headers.items():
        response.headers[k] = v
    resp = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = (
                runner.supabase.table("threads_posts")
                .select("id, url, post_text, created_at, captured_at, ai_tags, like_count, reply_count, view_count, share_count, repost_count, analysis_json, author, analysis_is_valid, analysis_version, analysis_build_id, archive_captured_at, archive_build_id, phenomenon_id, phenomenon_status, phenomenon_case_id")
                .not_.is_("analysis_json", None)
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
            last_error = None
            break
        except Exception as e:
            last_error = e
            runner.logger.warning("api/posts transient fetch error", extra={"attempt": attempt + 1, "error": str(e)})
            # Supabase/PostgREST can intermittently fail under short bursts; retry quickly.
            if attempt < 2:
                time.sleep(0.35 * (attempt + 1))

    if resp is None and last_error is not None:
        runner.logger.exception("api/posts failed after retries")
        degraded = True
        return JSONResponse(
            [],
            headers={
                **no_cache_headers,
                "X-Request-ID": trace_id,
                "x-ops-degraded": "1",
            },
            status_code=200,
        )

    rows = resp.data or []
    if rows is None:
        rows = []

    items: list[Dict[str, Any]] = []
    for row in rows:
        if row is None:
            continue
        if not isinstance(row, dict):
            try:
                row = dict(row)
            except Exception:
                runner.logger.warning("api/posts skipping non-dict row", extra={"row_type": str(type(row))})
                continue
        try:
            created_at = row.get("created_at") or row.get("captured_at")
            snippet = _repair_mojibake(runner.clean_snippet(row.get("post_text", "")))
            raw_tags = row.get("ai_tags")
            tags_list: list[str] | None = None
            if isinstance(raw_tags, list):
                tags_list = [_repair_mojibake(str(t)) for t in raw_tags]
            elif isinstance(raw_tags, dict):
                tags_list = [_repair_mojibake(str(v)) for v in raw_tags.values() if v is not None]
            elif raw_tags is not None:
                tags_list = [_repair_mojibake(str(raw_tags))]
            aj_raw = row.get("analysis_json") or {}
            aj = aj_raw if isinstance(aj_raw, dict) else {}
            phen_meta = runner.merge_phenomenon_meta(row, aj)
            items.append(
                {
                    "id": str(row.get("id")),
                    "snippet": snippet,
                    "url": row.get("url"),
                    "created_at": created_at,
                    "author": _repair_mojibake(row.get("author")),
                    "like_count": row.get("like_count"),
                    "reply_count": row.get("reply_count"),
                    "view_count": row.get("view_count"),
                    "share_count": row.get("share_count"),
                    "repost_count": row.get("repost_count"),
                    "has_analysis": row.get("analysis_json") is not None,
                    "analysis_is_valid": row.get("analysis_is_valid"),
                    "analysis_version": row.get("analysis_version"),
                    "analysis_build_id": row.get("analysis_build_id"),
                    "archive_captured_at": row.get("archive_captured_at"),
                    "archive_build_id": row.get("archive_build_id"),
                    "has_archive": bool(row.get("archive_captured_at")),
                    "ai_tags": tags_list,
                    "phenomenon_id": phen_meta["id"],
                    "phenomenon_status": phen_meta["status"],
                    "phenomenon_case_id": phen_meta["case_id"],
                    "phenomenon_name": phen_meta.get("canonical_name"),
                }
            )
        except Exception as e:
            dev_context = {
                "phase": "build_item",
                "row_id": row.get("id") if isinstance(row, dict) else None,
                "row_type": str(type(row)),
                "has_analysis_json": isinstance(row.get("analysis_json"), dict),
                "has_phenomenon_json": isinstance((row.get("analysis_json") or {}).get("phenomenon"), dict)
                if isinstance(row.get("analysis_json"), dict)
                else False,
                "trace": traceback.format_exc(),
            }
            runner.logger.exception("api/posts skipping broken row", extra=dev_context)
            degraded = True
            continue
    payload = _json.dumps(items, ensure_ascii=False)
    headers = {**no_cache_headers, "X-Request-ID": trace_id}
    if degraded:
        headers["x-ops-degraded"] = "1"
    return Response(
        content=payload,
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


@router.get("/analysis-json/{post_id}", response_model=Dict[str, Any])
def get_analysis_json(post_id: str, response: Response, request: Request):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)

    try:
        pid = _coerce_post_id(post_id)
        resp = (
            runner.supabase.table("threads_posts")
            .select(
                "id, analysis_json, analysis_is_valid, analysis_version, analysis_build_id, analysis_invalid_reason, analysis_missing_keys, full_report, updated_at, phenomenon_id, phenomenon_status, phenomenon_case_id"
            )
            .eq("id", pid)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        runner.logger.exception("api/analysis-json failed")
        return _response_for_exception(
            exc=exc,
            response=response,
            trace_id=trace_id,
            pending_reason="analysis_backend_unavailable",
            pending_detail="Analysis backend temporarily unavailable.",
            extra={
                "post_id": str(post_id),
                "analysis_json": {},
                "analysis_is_valid": None,
                "analysis_version": None,
                "analysis_build_id": None,
                "analysis_invalid_reason": None,
                "analysis_missing_keys": [],
                "phenomenon": None,
            },
        )

    if not resp.data:
        return _json_with_trace(
            _post_not_found_payload(
                trace_id=trace_id,
                post_id=post_id,
                extra={
                    "analysis_json": {},
                    "analysis_is_valid": None,
                    "analysis_version": None,
                    "analysis_build_id": None,
                    "analysis_invalid_reason": None,
                    "analysis_missing_keys": [],
                    "phenomenon": None,
                },
            ),
            status_code=404,
            trace_id=trace_id,
        )

    row = resp.data[0]
    analysis_json = _sanitize_analysis_json(row.get("analysis_json"))
    phen_meta = runner.merge_phenomenon_meta(row, analysis_json or {})
    if not analysis_json:
        return _pending_response(
            response=response,
            trace_id=trace_id,
            reason="asset_not_ready",
            detail="Analysis artifact not generated yet for this post.",
            retry_after_ms=2000,
            extra={
                "asset": "analysis_json",
                "post_id": str(post_id),
                "analysis_json": {},
                "analysis_is_valid": row.get("analysis_is_valid"),
                "analysis_version": row.get("analysis_version"),
                "analysis_build_id": row.get("analysis_build_id"),
                "analysis_invalid_reason": row.get("analysis_invalid_reason"),
                "analysis_missing_keys": row.get("analysis_missing_keys"),
                "phenomenon": phen_meta,
                "has_full_report": bool(row.get("full_report")),
            },
        )

    return {
        "status": "ready",
        "reason": None,
        "reason_code": None,
        "trace_id": trace_id,
        "post_id": str(post_id),
        "analysis_json": analysis_json,
        "analysis_is_valid": row.get("analysis_is_valid"),
        "analysis_version": row.get("analysis_version"),
        "analysis_build_id": row.get("analysis_build_id"),
        "analysis_invalid_reason": row.get("analysis_invalid_reason"),
        "analysis_missing_keys": row.get("analysis_missing_keys"),
        "phenomenon": phen_meta,
    }


@router.get("/library/phenomena")
def list_library_phenomena(response: Response, status: Optional[str] = None, q: Optional[str] = None, limit: int = 200):
    q_limit = max(1, min(limit or 200, 500))
    try:
        def _fetch_phenomena():
            query = runner.supabase.table("narrative_phenomena").select(
                "id, canonical_name, description, status, created_at"
            ).limit(q_limit)
            if status:
                query = query.eq("status", status)
            return query.execute()

        resp = _run_with_retry("api/library/phenomena", _fetch_phenomena)
    except Exception as e:
        runner.logger.exception("api/library/phenomena failed after retries")
        _set_degraded_response(response)
        return []

    try:
        stats_map = _run_with_retry("api/library/phenomena stats", runner.build_phenomenon_post_stats_map)
    except Exception:
        _set_degraded_response(response)
        stats_map = {}
    results = []
    for row in resp.data or []:
        canon = row.get("canonical_name")
        desc = row.get("description")
        if q:
            needle = q.lower()
            hay = f"{canon or ''} {desc or ''}".lower()
            if needle not in hay:
                continue
        pid = row.get("id")
        pst = stats_map.get(pid, {"total_posts": 0, "total_likes": 0, "last_seen_at": None})
        results.append(
            {
                "id": pid,
                "canonical_name": canon,
                "description": desc,
                "status": row.get("status") or "unknown",
                "total_posts": pst.get("total_posts", 0),
                "last_seen_at": pst.get("last_seen_at"),
            }
        )
    return results


@router.get("/claims")
def list_claims(
    post_id: str,
    response: Response,
    request: Request,
    limit: int = 200,
    cluster_key: Optional[int] = None,
    status: Optional[str] = None,
):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)
    if not post_id:
        raise HTTPException(status_code=400, detail="post_id required")
    limit = max(1, min(limit, 500))
    pid: Any = _coerce_post_id(post_id)

    post_row, post_lookup_degraded = _load_post_row(post_id, select_fields="id")
    if post_lookup_degraded:
        return _pending_response(
            response=response,
            trace_id=trace_id,
            reason="upstream_transport",
            detail="Claims source temporarily unavailable.",
            retry_after_ms=1500,
            extra={"asset": "claims", "post_id": str(post_id), "claims": [], "audit": None},
        )
    if post_row is None:
        return _json_with_trace(
            _post_not_found_payload(
                trace_id=trace_id,
                post_id=post_id,
                extra={"claims": [], "audit": None},
            ),
            status_code=404,
            trace_id=trace_id,
        )

    try:
        query = runner.supabase.table("threads_claims").select(
            "id, post_id, run_id, claim_type, scope, text, status, audit_reason, confidence, confidence_cap, primary_cluster_key, cluster_key, cluster_keys, tags, created_at"
        )
        query = query.eq("post_id", pid)
        if status:
            query = query.eq("status", status)
        if cluster_key is not None:
            query = query.or_(f"primary_cluster_key.eq.{cluster_key},cluster_key.eq.{cluster_key}")
        claims_resp = query.order("created_at", desc=True).limit(limit).execute()
    except Exception as exc:
        runner.logger.exception("api/claims failed")
        return _response_for_exception(
            exc=exc,
            response=response,
            trace_id=trace_id,
            pending_reason="claims_backend_unavailable",
            pending_detail="Claims source temporarily unavailable.",
            extra={"asset": "claims", "post_id": str(post_id), "claims": [], "audit": None},
        )

    degraded = False
    try:
        audit_resp = (
            runner.supabase.table("threads_claim_audits")
            .select("id, verdict, kept_claims_count, dropped_claims_count, total_claims_count, created_at, reasons, run_id")
            .eq("post_id", pid)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        audit = (audit_resp.data or [None])[0]
    except Exception:
        audit = None
        degraded = True

    if degraded:
        _set_degraded_response(response)
    claims = claims_resp.data or []
    if not claims:
        return _empty_response(
            trace_id=trace_id,
            detail="No claims available for this post.",
            extra={"post_id": str(post_id), "claims": [], "audit": audit},
        )
    return {
        "status": "ready",
        "reason": None,
        "reason_code": None,
        "trace_id": trace_id,
        "post_id": str(post_id),
        "claims": claims,
        "audit": audit,
    }


@router.get("/evidence")
def list_evidence(
    response: Response,
    request: Request,
    post_id: Optional[str] = None,
    claim_id: Optional[str] = None,
    cluster_key: Optional[int] = None,
    limit: int = 200,
):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)
    if not post_id and not claim_id:
        raise HTTPException(status_code=400, detail="post_id or claim_id required")
    limit = max(1, min(limit, 500))

    if post_id and not claim_id:
        post_row, post_lookup_degraded = _load_post_row(str(post_id), select_fields="id")
        if post_lookup_degraded:
            return _pending_response(
                response=response,
                trace_id=trace_id,
                reason="upstream_transport",
                detail="Evidence source temporarily unavailable.",
                retry_after_ms=1500,
                extra={"asset": "evidence", "post_id": str(post_id), "items": [], "claims": []},
            )
        if post_row is None:
            return _json_with_trace(
                _post_not_found_payload(
                    trace_id=trace_id,
                    post_id=str(post_id),
                    extra={"items": [], "claims": []},
                ),
                status_code=404,
                trace_id=trace_id,
            )

    claim_rows: list[Dict[str, Any]] = []
    claim_ids: list[str] = []

    try:
        if claim_id:
            claim_resp = _run_with_retry(
                "api/evidence claim-by-id",
                lambda: runner.supabase.table("threads_claims")
                .select("id, text, status, audit_reason, claim_type, scope, primary_cluster_key, cluster_key")
                .eq("id", claim_id)
                .limit(1)
                .execute(),
            )
            claim_rows = claim_resp.data or []
            if claim_rows:
                claim_ids = [str(claim_rows[0].get("id"))]
        else:
            try:
                pid: Any = int(post_id) if post_id is not None else post_id
            except Exception:
                pid = post_id

            def _fetch_claims_by_post():
                query = runner.supabase.table("threads_claims").select(
                    "id, text, status, audit_reason, claim_type, scope, primary_cluster_key, cluster_key"
                )
                query = query.eq("post_id", pid)
                if cluster_key is not None:
                    query = query.or_(f"primary_cluster_key.eq.{cluster_key},cluster_key.eq.{cluster_key}")
                return query.order("created_at", desc=True).limit(200).execute()

            claim_resp = _run_with_retry("api/evidence claim-by-post", _fetch_claims_by_post)
            claim_rows = claim_resp.data or []
            claim_ids = [str(r.get("id")) for r in claim_rows if r.get("id")]
    except Exception as exc:
        runner.logger.exception("api/evidence claim lookup failed after retries")
        return _response_for_exception(
            exc=exc,
            response=response,
            trace_id=trace_id,
            pending_reason="evidence_backend_unavailable",
            pending_detail="Evidence source temporarily unavailable.",
            extra={"asset": "claims", "post_id": str(post_id or ""), "items": [], "claims": []},
        )

    if not claim_ids:
        return _empty_response(
            trace_id=trace_id,
            detail="No evidence available for this query.",
            extra={"post_id": str(post_id or ""), "items": [], "claims": claim_rows},
        )

    try:
        def _fetch_evidence():
            ev_query = runner.supabase.table("threads_claim_evidence").select(
                "id, claim_id, evidence_type, evidence_id, span_text, author_handle, like_count, cluster_key, evidence_ref, created_at, locator_type, locator_value, locator_key"
            )
            ev_query = ev_query.in_("claim_id", claim_ids)
            if cluster_key is not None:
                ev_query = ev_query.eq("cluster_key", cluster_key)
            return ev_query.order("created_at", desc=True).limit(limit).execute()

        ev_resp = _run_with_retry("api/evidence rows", _fetch_evidence)
    except Exception as exc:
        runner.logger.exception("api/evidence fetch failed after retries")
        return _response_for_exception(
            exc=exc,
            response=response,
            trace_id=trace_id,
            pending_reason="evidence_backend_unavailable",
            pending_detail="Evidence source temporarily unavailable.",
            extra={"asset": "evidence", "post_id": str(post_id or ""), "items": [], "claims": claim_rows},
        )

    rows = ev_resp.data or []
    missing_comment_ids: list[str] = []
    for row in rows:
        if row is None:
            continue
        has_text = bool(row.get("span_text")) or bool((row.get("evidence_ref") or {}).get("span_text"))
        locator_type = row.get("locator_type") or row.get("evidence_type")
        if not has_text and locator_type == "comment_id":
            ev_id = row.get("evidence_id")
            if ev_id is not None:
                missing_comment_ids.append(str(ev_id))

    comment_map: Dict[str, Dict[str, Any]] = {}
    if missing_comment_ids:
        try:
            comment_resp = _run_with_retry(
                "api/evidence comment backfill",
                lambda: runner.supabase.table("threads_comments")
                .select("id, text, author_handle, like_count, created_at")
                .in_("id", list(set(missing_comment_ids)))
                .execute(),
            )
            comment_map = {str(row.get("id")): row for row in (comment_resp.data or []) if isinstance(row, dict)}
        except Exception:
            _set_degraded_response(response)
            comment_map = {}

    claim_map = {str(r.get("id")): r for r in claim_rows if isinstance(r, dict) and r.get("id")}
    items: list[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence_ref = row.get("evidence_ref") or {}
        text = row.get("span_text") or evidence_ref.get("span_text") or ""
        if not text:
            c = comment_map.get(str(row.get("evidence_id")))
            if c:
                text = c.get("text") or ""
                if not row.get("author_handle"):
                    row["author_handle"] = c.get("author_handle")
                if row.get("like_count") is None:
                    row["like_count"] = c.get("like_count")
        claim = claim_map.get(str(row.get("claim_id"))) or {}
        items.append(
            {
                "id": row.get("id"),
                "claim_id": row.get("claim_id"),
                "claim_text": claim.get("text"),
                "claim_status": claim.get("status"),
                "claim_scope": claim.get("scope"),
                "evidence_type": row.get("evidence_type"),
                "evidence_id": row.get("evidence_id"),
                "cluster_key": row.get("cluster_key"),
                "author_handle": row.get("author_handle"),
                "like_count": row.get("like_count"),
                "created_at": row.get("created_at"),
                "text": text,
                "locator_key": row.get("locator_key"),
            }
        )

    if not items:
        return _empty_response(
            trace_id=trace_id,
            detail="No evidence rows matched this query.",
            extra={"post_id": str(post_id or ""), "items": [], "claims": claim_rows},
        )
    return {"status": "ready", "reason": None, "reason_code": None, "trace_id": trace_id, "post_id": str(post_id or ""), "items": items, "claims": claim_rows}


@router.get("/clusters")
def list_clusters(post_id: str, response: Response, request: Request, limit: int = 20, sample_limit: int = 10):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)
    if not post_id:
        raise HTTPException(status_code=400, detail="post_id required")
    limit = max(1, min(limit, 60))
    sample_limit = max(1, min(sample_limit, 12))
    pid: Any = _coerce_post_id(post_id)

    post_row, post_lookup_degraded = _load_post_row(post_id, select_fields="id")
    if post_lookup_degraded:
        return _pending_response(
            response=response,
            trace_id=trace_id,
            reason="upstream_transport",
            detail="Cluster source temporarily unavailable.",
            retry_after_ms=1500,
            extra={
                "asset": "clusters",
                "post_id": str(post_id),
                "clusters": [],
                "total_comments": 0,
                "engagement_truncated": False,
            },
        )
    if post_row is None:
        return _json_with_trace(
            _post_not_found_payload(
                trace_id=trace_id,
                post_id=post_id,
                extra={
                    "clusters": [],
                    "total_comments": 0,
                    "engagement_truncated": False,
                },
            ),
            status_code=404,
            trace_id=trace_id,
        )

    degraded = False

    try:
        cluster_resp = (
            runner.supabase.table("threads_comment_clusters")
            .select(
                "id, post_id, cluster_key, label, summary, size, keywords, top_comment_ids, tactics, tactic_summary, centroid_embedding_384, updated_at"
            )
            .eq("post_id", pid)
            .order("size", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        runner.logger.exception("api/clusters fetch failed")
        return _response_for_exception(
            exc=exc,
            response=response,
            trace_id=trace_id,
            pending_reason="clusters_backend_unavailable",
            pending_detail="Cluster source temporarily unavailable.",
            extra={
                "asset": "clusters",
                "post_id": str(post_id),
                "clusters": [],
                "total_comments": 0,
                "engagement_truncated": False,
            },
        )

    rows = cluster_resp.data or []
    if not rows:
        return _empty_response(
            trace_id=trace_id,
            detail="No clusters available for this post.",
            extra={
                "post_id": str(post_id),
                "clusters": [],
                "total_comments": 0,
                "engagement_truncated": False,
            },
        )

    total_comments = 0
    try:
        count_resp = runner.supabase.table("threads_comments").select("id", count="exact").eq("post_id", pid).execute()
        total_comments = int(getattr(count_resp, "count", 0) or 0)
    except Exception:
        total_comments = 0
        degraded = True

    interp_map: Dict[int, Dict[str, Any]] = {}
    try:
        interp_resp = (
            runner.supabase.table("threads_cluster_interpretations")
            .select("cluster_key, label, one_liner, label_confidence, label_unstable, evidence_ids, run_id, model_name, created_at")
            .eq("post_id", pid)
            .order("created_at", desc=True)
            .limit(limit * 3)
            .execute()
        )
        for row in interp_resp.data or []:
            try:
                ck = int(row.get("cluster_key"))
            except Exception:
                continue
            if ck not in interp_map:
                interp_map[ck] = row
    except Exception:
        interp_map = {}
        degraded = True

    cluster_rows: List[Dict[str, Any]] = []
    all_top_ids: List[str] = []
    for row in rows:
        top_ids = list(dict.fromkeys(_coerce_id_list(row.get("top_comment_ids"))))
        if top_ids:
            all_top_ids.extend(top_ids)
        cluster_rows.append({**row, "_top_ids": top_ids})

    comment_map: Dict[str, Dict[str, Any]] = {}
    if all_top_ids:
        unique_ids = list(dict.fromkeys(all_top_ids))
        try:
            comment_resp = (
                runner.supabase.table("threads_comments")
                .select("id, text, author_handle, like_count, reply_count, created_at")
                .in_("id", unique_ids)
                .execute()
            )
            comment_map = {str(row.get("id")): row for row in (comment_resp.data or []) if isinstance(row, dict)}
        except Exception:
            comment_map = {}
            degraded = True

    # aggregate engagement per cluster
    cluster_keys = [int(r.get("cluster_key")) for r in cluster_rows if r.get("cluster_key") is not None]
    engagement_map: Dict[int, Dict[str, Any]] = {}
    total_like_sum = 0
    total_engagement_sum = 0
    engagement_truncated = False
    if cluster_keys:
        try:
            offset = 0
            page_size = 1000
            max_rows = 12000
            while offset < max_rows:
                comment_resp = (
                    runner.supabase.table("threads_comments")
                    .select("id, cluster_key, like_count, reply_count, text, author_handle, created_at")
                    .eq("post_id", pid)
                    .in_("cluster_key", cluster_keys)
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                page = comment_resp.data or []
                if not page:
                    break
                for c in page:
                    try:
                        ck = int(c.get("cluster_key"))
                    except Exception:
                        continue
                    entry = engagement_map.setdefault(ck, {"comment_count": 0, "likes": 0, "replies": 0})
                    entry["comment_count"] += 1
                    try:
                        entry["likes"] += int(c.get("like_count") or 0)
                    except Exception:
                        pass
                    try:
                        entry["replies"] += int(c.get("reply_count") or 0)
                    except Exception:
                        pass
                if len(page) < page_size:
                    break
                offset += page_size
            if offset >= max_rows:
                engagement_truncated = True
            total_like_sum = sum(v.get("likes", 0) for v in engagement_map.values())
            total_engagement_sum = sum((v.get("likes", 0) or 0) + (v.get("replies", 0) or 0) for v in engagement_map.values())
        except Exception:
            engagement_map = {}
            degraded = True

    prepared: List[Dict[str, Any]] = []
    vectors: List[List[float]] = []
    vector_idx: List[int] = []

    for idx, row in enumerate(cluster_rows):
        try:
            ck = int(row.get("cluster_key"))
        except Exception:
            ck = idx
        label = row.get("label") or ""
        summary = row.get("summary") or ""
        size_val = row.get("size")
        try:
            size_int = int(size_val) if size_val is not None else 0
        except Exception:
            size_int = 0
        keywords = _coerce_str_list(row.get("keywords"))
        tactics = _coerce_str_list(row.get("tactics"))
        tactic_summary = row.get("tactic_summary")

        interp = interp_map.get(ck)
        label_source = "heuristic"
        summary_source = "heuristic"
        cip_meta: Dict[str, Any] = {}
        if interp:
            cip_meta = {
                "run_id": interp.get("run_id"),
                "model_name": interp.get("model_name"),
                "label_confidence": interp.get("label_confidence"),
                "label_unstable": interp.get("label_unstable"),
                "evidence_ids": _coerce_id_list(interp.get("evidence_ids")),
            }
            if interp.get("label") and _has_cjk(str(interp.get("label"))):
                label = interp.get("label") or label
                label_source = "cip"
            if interp.get("one_liner") and _has_cjk(str(interp.get("one_liner"))):
                summary = interp.get("one_liner") or summary
                summary_source = "cip"

        top_ids = row.get("_top_ids") or []
        samples: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_texts: set[str] = set()
        for cid in top_ids:
            c = comment_map.get(str(cid))
            if not c:
                continue
            cid_str = str(c.get("id") or cid)
            if cid_str in seen_ids:
                continue
            text_norm = _normalize_comment_text(str(c.get("text") or ""))
            if text_norm and text_norm in seen_texts:
                continue
            samples.append(
                {
                    "id": c.get("id"),
                    "text": c.get("text"),
                    "author_handle": c.get("author_handle"),
                    "like_count": c.get("like_count"),
                    "reply_count": c.get("reply_count"),
                    "created_at": c.get("created_at"),
                }
            )
            seen_ids.add(cid_str)
            if text_norm:
                seen_texts.add(text_norm)
            if len(samples) >= sample_limit:
                break

        if len(samples) < sample_limit and sample_limit > 0:
            try:
                fallback_resp = (
                    runner.supabase.table("threads_comments")
                    .select("id, text, author_handle, like_count, reply_count, created_at")
                    .eq("post_id", pid)
                    .eq("cluster_key", ck)
                    .order("like_count", desc=True)
                    .limit(sample_limit * 3)
                    .execute()
                )
                for c in fallback_resp.data or []:
                    if len(samples) >= sample_limit:
                        break
                    cid_str = str(c.get("id") or "")
                    if cid_str in seen_ids:
                        continue
                    text_norm = _normalize_comment_text(str(c.get("text") or ""))
                    if text_norm and text_norm in seen_texts:
                        continue
                    samples.append(
                        {
                            "id": c.get("id"),
                            "text": c.get("text"),
                            "author_handle": c.get("author_handle"),
                            "like_count": c.get("like_count"),
                            "reply_count": c.get("reply_count"),
                            "created_at": c.get("created_at"),
                        }
                    )
                    if cid_str:
                        seen_ids.add(cid_str)
                    if text_norm:
                        seen_texts.add(text_norm)
            except Exception:
                pass

        # Always recompute keywords from samples for quality (ignore DB keywords)
        keywords = list(dict.fromkeys(_extract_keywords([_normalize_comment_text(s.get("text") or "") for s in samples], top_n=6)))[:4]

        label_lower = str(label).lower()
        role_label, role_summary = _infer_cluster_role([s.get("text") or "" for s in samples])
        if label_source != "cip":
            if not label or label_lower.startswith("cluster") or label_lower.startswith("other"):
                if keywords:
                    label = f"{role_label} · {keywords[0]}"
                else:
                    label = role_label
            if label and (label_lower.startswith("cluster") or label_lower.startswith("other")):
                label = role_label

        share = round(size_int / total_comments, 4) if total_comments and size_int else None

        engagement = engagement_map.get(ck, {"comment_count": size_int, "likes": 0, "replies": 0})
        comment_count = engagement.get("comment_count", size_int)
        if size_int <= 0 and comment_count:
            size_int = comment_count
        like_share = None
        if total_like_sum > 0:
            like_share = round((engagement.get("likes", 0) or 0) / total_like_sum, 4)
        engagement_share = None
        if total_engagement_sum > 0:
            engagement_share = round(
                ((engagement.get("likes", 0) or 0) + (engagement.get("replies", 0) or 0)) / total_engagement_sum, 4
            )
        likes_per_comment = None
        if comment_count:
            likes_per_comment = round((engagement.get("likes", 0) or 0) / max(1, comment_count), 2)
        influence_tag = ""
        if likes_per_comment is not None:
            if likes_per_comment >= 8:
                influence_tag = "影響力高"
            elif likes_per_comment <= 2:
                influence_tag = "影響力偏低"

        if summary_source != "cip":
            if not summary or summary == role_summary:
                summary = _format_cluster_summary(role_summary, keywords, influence_tag)

        vec = _parse_vector(row.get("centroid_embedding_384"))
        if vec and len(vec) >= 2:
            vectors.append(vec)
            vector_idx.append(len(prepared))

        sample_total = max(len(top_ids), size_int, comment_count, len(samples))
        prepared.append(
            {
                "cluster_key": ck,
                "label": label,
                "summary": summary,
                "size": size_int,
                "share": share,
                "keywords": keywords,
                "tactics": tactics,
                "tactic_summary": tactic_summary,
                "sample_total": sample_total,
                "samples": samples,
                "comment_count": comment_count,
                "engagement": {
                    "likes": engagement.get("likes", 0),
                    "replies": engagement.get("replies", 0),
                    "like_share": like_share,
                    "engagement_share": engagement_share,
                    "likes_per_comment": likes_per_comment,
                },
                "label_source": label_source,
                "summary_source": summary_source,
                "cip": cip_meta or None,
            }
        )

    if len(vectors) >= 2:
        try:
            mat = np.array(vectors, dtype=float)
            mat = mat - np.mean(mat, axis=0)
            _, _, vh = np.linalg.svd(mat, full_matrices=False)
            coords_raw = mat @ vh[:2].T
            coords = _normalize_coords(coords_raw.tolist())
            for i, idx in enumerate(vector_idx):
                if idx < len(prepared):
                    prepared[idx]["coords"] = {"x": coords[i][0], "y": coords[i][1]}
        except Exception:
            pass

    total = max(1, len(prepared))
    for idx, row in enumerate(prepared):
        if "coords" not in row:
            angle = (idx / total) * (math.tau if hasattr(math, "tau") else math.pi * 2)
            row["coords"] = {"x": 0.5 + 0.32 * math.cos(angle), "y": 0.5 + 0.32 * math.sin(angle)}

    if degraded:
        _set_degraded_response(response)

    return {
        "status": "ready",
        "reason": None,
        "reason_code": None,
        "trace_id": trace_id,
        "post_id": str(post_id),
        "clusters": prepared,
        "total_comments": total_comments,
        "engagement_truncated": engagement_truncated,
        "degraded": degraded,
    }


@router.get("/clusters/{post_id}/graph")
def get_cluster_graph(post_id: str, response: Response, request: Request, limit: int = 24):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)

    clusters_payload = list_clusters(
        post_id=post_id,
        response=response,
        request=request,
        limit=max(1, min(limit, 60)),
        sample_limit=10,
    )
    if isinstance(clusters_payload, JSONResponse):
        return clusters_payload

    cluster_state = str((clusters_payload or {}).get("status") or "ready").strip().lower()
    if cluster_state != "ready":
        reason_code = str((clusters_payload or {}).get("reason_code") or "").strip() or (
            "clusters_not_ready" if cluster_state == "pending" else "clusters_empty"
        )
        detail = str((clusters_payload or {}).get("detail") or "").strip() or (
            "Cluster graph pending backend artifacts." if cluster_state == "pending" else "No cluster graph for this post."
        )
        return {
            "status": cluster_state,
            "reason": reason_code,
            "reason_code": reason_code,
            "detail": detail,
            "trace_id": str((clusters_payload or {}).get("trace_id") or trace_id),
            "post_id": str(post_id),
            "nodes": [],
            "links": [],
            "coords": [],
            "meta": {
                "run_id": None,
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "layout_version": "v1",
                "degraded": bool((clusters_payload or {}).get("degraded") or cluster_state == "pending"),
                "source": ["threads_comment_clusters", "threads_claims"],
            },
        }

    clusters = (clusters_payload or {}).get("clusters") or []
    if not isinstance(clusters, list):
        clusters = []

    nodes: List[Dict[str, Any]] = []
    for row in clusters:
        ck = row.get("cluster_key")
        try:
            cluster_key = int(ck)
        except Exception:
            continue
        coords = row.get("coords") or {}
        x = coords.get("x")
        y = coords.get("y")
        node = {
            "id": f"c-{cluster_key}",
            "cluster_key": cluster_key,
            "weight": int(row.get("size") or 0),
            "label": row.get("label") or f"C-{cluster_key:03d}",
            "share": row.get("share"),
            "coords": {
                "x": float(x) if isinstance(x, (int, float)) else None,
                "y": float(y) if isinstance(y, (int, float)) else None,
            },
            "metrics": row.get("engagement") or {},
            "cip": row.get("cip") or None,
        }
        nodes.append(node)

    no_coord = [i for i, node in enumerate(nodes) if node["coords"]["x"] is None or node["coords"]["y"] is None]
    if no_coord:
        total = max(1, len(no_coord))
        for idx, node_idx in enumerate(no_coord):
            angle = (idx / total) * (math.tau if hasattr(math, "tau") else math.pi * 2)
            nodes[node_idx]["coords"]["x"] = 0.5 + 0.35 * math.cos(angle)
            nodes[node_idx]["coords"]["y"] = 0.5 + 0.35 * math.sin(angle)

    links: List[Dict[str, Any]] = []
    degraded = False
    try:
        try:
            pid: Any = int(post_id)
        except Exception:
            pid = post_id
        claims_resp = _run_with_retry(
            "api/clusters graph claims",
            lambda: runner.supabase.table("threads_claims")
            .select("cluster_key,primary_cluster_key")
            .eq("post_id", pid)
            .limit(800)
            .execute(),
        )
        edge_map: Dict[str, int] = {}
        for row in claims_resp.data or []:
            a = row.get("cluster_key")
            b = row.get("primary_cluster_key")
            if a is None or b is None:
                continue
            try:
                ca = int(a)
                cb = int(b)
            except Exception:
                continue
            if ca == cb:
                continue
            left, right = (ca, cb) if ca < cb else (cb, ca)
            key = f"{left}:{right}"
            edge_map[key] = edge_map.get(key, 0) + 1
        for key, weight in edge_map.items():
            left, right = key.split(":")
            links.append(
                {
                    "source": f"c-{left}",
                    "target": f"c-{right}",
                    "weight": weight,
                    "type": "claim_coupling",
                }
            )
        links.sort(key=lambda row: int(row.get("weight") or 0), reverse=True)
    except Exception as exc:
        return _response_for_exception(
            exc=exc,
            response=response,
            trace_id=trace_id,
            pending_reason="cluster_graph_backend_unavailable",
            pending_detail="Cluster graph edges are pending backend transport.",
            extra={
                "asset": "cluster_graph",
                "post_id": str(post_id),
                "nodes": [],
                "links": [],
                "coords": [],
                "meta": {
                    "run_id": None,
                    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "layout_version": "v1",
                    "degraded": True,
                    "source": ["threads_comment_clusters", "threads_claims"],
                },
            },
        )

    if degraded:
        _set_degraded_response(response)

    run_id = None
    for node in nodes:
        cip = node.get("cip") or {}
        if isinstance(cip, dict) and cip.get("run_id"):
            run_id = cip.get("run_id")
            break

    graph_status = "ready" if links else "empty"
    graph_reason = None if links else "no_relation_edges_yet"
    return {
        "status": graph_status,
        "reason": graph_reason,
        "reason_code": graph_reason,
        "trace_id": trace_id,
        "post_id": str(post_id),
        "nodes": nodes,
        "links": links,
        "coords": [{"id": n["id"], "x": n["coords"]["x"], "y": n["coords"]["y"]} for n in nodes],
        "meta": {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "layout_version": "v1",
            "degraded": degraded,
            "source": ["threads_comment_clusters", "threads_claims"],
        },
    }

@router.get("/library/phenomena/{phenomenon_id}")
def get_library_phenomenon(phenomenon_id: str, response: Response, request: Request, limit: int = 20):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)

    try:
        meta_resp = (
            runner.supabase.table("narrative_phenomena")
            .select("id, canonical_name, description, status")
            .eq("id", phenomenon_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        return _response_for_exception(
            exc=exc,
            response=response,
            trace_id=trace_id,
            pending_reason="phenomenon_backend_unavailable",
            pending_detail="Phenomenon metadata source temporarily unavailable.",
            extra={
                "asset": "phenomenon",
                "phenomenon_id": str(phenomenon_id),
                "meta": {"id": str(phenomenon_id), "canonical_name": None, "description": None, "status": "unknown"},
                "stats": {"total_posts": 0, "total_likes": 0, "last_seen_at": None},
                "recent_posts": [],
            },
        )

    if not meta_resp.data:
        return _json_with_trace(
            {
                "status": "not_found",
                "reason": "phenomenon_not_found",
                "reason_code": "phenomenon_not_found",
                "detail": "Phenomenon not found",
                "trace_id": trace_id,
                "phenomenon_id": str(phenomenon_id),
                "meta": {"id": str(phenomenon_id), "canonical_name": None, "description": None, "status": "unknown"},
                "stats": {"total_posts": 0, "total_likes": 0, "last_seen_at": None},
                "recent_posts": [],
            },
            status_code=404,
            trace_id=trace_id,
        )

    meta = meta_resp.data[0]

    try:
        posts_resp = (
            runner.supabase.table("threads_posts")
            .select("id, created_at, post_text, like_count, phenomenon_status")
            .eq("phenomenon_id", phenomenon_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 100)))
            .execute()
        )
    except Exception as exc:
        return _response_for_exception(
            exc=exc,
            response=response,
            trace_id=trace_id,
            pending_reason="phenomenon_posts_pending",
            pending_detail="Phenomenon posts are not available yet.",
            extra={
                "asset": "phenomenon_posts",
                "phenomenon_id": str(phenomenon_id),
                "meta": meta,
                "stats": {"total_posts": 0, "total_likes": 0, "last_seen_at": None},
                "recent_posts": [],
            },
        )

    posts = posts_resp.data or []
    total_likes = 0
    last_seen_at = None
    for p in posts:
        try:
            total_likes += int(p.get("like_count") or 0)
        except Exception:
            pass
        ts = p.get("created_at")
        if ts and (last_seen_at is None or ts > last_seen_at):
            last_seen_at = ts

    recent_posts = []
    for p in posts:
        recent_posts.append(
            {
                "id": p.get("id"),
                "created_at": p.get("created_at"),
                "snippet": runner.clean_snippet(p.get("post_text") or ""),
                "like_count": p.get("like_count") or 0,
                "phenomenon_status": p.get("phenomenon_status"),
            }
        )

    stats = {
        "total_posts": len(posts),
        "total_likes": total_likes,
        "last_seen_at": last_seen_at,
    }

    if not recent_posts:
        return _empty_response(
            trace_id=trace_id,
            detail="No posts recorded for this phenomenon yet.",
            extra={
                "phenomenon_id": str(phenomenon_id),
                "meta": meta,
                "stats": stats,
                "recent_posts": [],
            },
        )
    return {
        "status": "ready",
        "reason": None,
        "reason_code": None,
        "trace_id": trace_id,
        "phenomenon_id": str(phenomenon_id),
        "meta": meta,
        "stats": stats,
        "recent_posts": recent_posts,
    }


@router.get("/library/phenomena/{phenomenon_id}/signals")
def get_library_phenomenon_signals(
    phenomenon_id: str,
    response: Response,
    request: Request,
    window: Optional[str] = "24h",
):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)
    hours = _window_hours_from_text(window, default_hours=24)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    degraded = False

    try:
        meta_resp = _run_with_retry(
            "api/library/phenomena/signals meta",
            lambda: runner.supabase.table("narrative_phenomena")
            .select("id")
            .eq("id", phenomenon_id)
            .limit(1)
            .execute(),
        )
    except Exception:
        _set_degraded_response(response)
        return _pending_payload(
            trace_id=trace_id,
            reason_code="phenomenon_signals_pending",
            detail="Phenomenon signals source temporarily unavailable.",
            extra={
                "phenomenon_id": str(phenomenon_id),
                "window": f"{hours}h",
                "occurrence_timeline": [],
                "related_signals": [],
                "supporting_refs": {"latest_post_id": None, "latest_run_id": None},
                "meta": {
                    "computed_at": now.isoformat().replace("+00:00", "Z"),
                    "version": "v0",
                    "degraded": True,
                },
            },
        )

    if not meta_resp.data:
        return _json_with_trace(
            {
                "status": "not_found",
                "reason_code": "phenomenon_not_found",
                "detail": "Phenomenon not found",
                "trace_id": trace_id,
                "phenomenon_id": str(phenomenon_id),
                "window": f"{hours}h",
                "occurrence_timeline": [],
                "related_signals": [],
                "supporting_refs": {"latest_post_id": None, "latest_run_id": None},
                "meta": {
                    "computed_at": now.isoformat().replace("+00:00", "Z"),
                    "version": "v0",
                    "degraded": False,
                },
            },
            status_code=404,
            trace_id=trace_id,
        )

    posts: List[Dict[str, Any]] = []
    try:
        posts_resp = _run_with_retry(
            "api/library/phenomena/signals posts",
            lambda: runner.supabase.table("threads_posts")
            .select("id,created_at,like_count,analysis_json")
            .eq("phenomenon_id", phenomenon_id)
            .order("created_at", desc=True)
            .limit(300)
            .execute(),
        )
        posts = posts_resp.data or []
    except Exception:
        degraded = True
        posts = []

    bucket_starts = [start + timedelta(hours=i) for i in range(hours)]
    post_counts = [0 for _ in range(hours)]
    comment_counts = [0 for _ in range(hours)]
    risk_max = [0.0 for _ in range(hours)]

    post_ids: List[str] = []
    post_bucket_idx: Dict[str, int] = {}
    latest_post_id: Optional[str] = None
    for idx, row in enumerate(posts):
        post_id = str(row.get("id") or "").strip()
        if not post_id:
            continue
        post_ids.append(post_id)
        if idx == 0:
            latest_post_id = post_id
        ts_ms = _parse_iso(row.get("created_at"))
        if ts_ms is None:
            continue
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        if ts < start or ts > now:
            continue
        bucket_idx = int((ts - start).total_seconds() // 3600)
        if bucket_idx < 0 or bucket_idx >= hours:
            continue
        post_bucket_idx[post_id] = bucket_idx
        post_counts[bucket_idx] += 1
        risk = _extract_behavior_risk(row.get("analysis_json"))
        if risk is not None:
            risk_max[bucket_idx] = max(risk_max[bucket_idx], risk)

    if post_ids:
        try:
            comments_resp = _run_with_retry(
                "api/library/phenomena/signals comments",
                lambda: runner.supabase.table("threads_comments")
                .select("post_id,created_at")
                .in_("post_id", post_ids[:200])
                .limit(2000)
                .execute(),
            )
            for row in comments_resp.data or []:
                post_id = str(row.get("post_id") or "").strip()
                ts_ms = _parse_iso(row.get("created_at"))
                if ts_ms is None:
                    bucket_idx = post_bucket_idx.get(post_id)
                    if bucket_idx is not None:
                        comment_counts[bucket_idx] += 1
                    continue
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                if ts < start or ts > now:
                    continue
                bucket_idx = int((ts - start).total_seconds() // 3600)
                if 0 <= bucket_idx < hours:
                    comment_counts[bucket_idx] += 1
        except Exception:
            degraded = True

    occurrence_timeline = [
        {
            "ts_hour": bucket_starts[idx].isoformat().replace("+00:00", "Z"),
            "post_count": post_counts[idx],
            "comment_count": comment_counts[idx],
            "risk_max": round(risk_max[idx], 4),
        }
        for idx in range(hours)
    ]

    related_signals: List[Dict[str, Any]] = []
    latest_run_id: Optional[str] = None
    if post_ids:
        try:
            claims_resp = _run_with_retry(
                "api/library/phenomena/signals claims",
                lambda: runner.supabase.table("threads_claims")
                .select("id,post_id,run_id,text,status,claim_type,scope,created_at")
                .in_("post_id", post_ids[:200])
                .limit(1500)
                .execute(),
            )
            claims_rows = claims_resp.data or []
            claim_ids = [str(row.get("id")) for row in claims_rows if row.get("id") is not None]
            evidence_by_claim: Dict[str, Dict[str, Any]] = {}
            if claim_ids:
                try:
                    ev_resp = _run_with_retry(
                        "api/library/phenomena/signals evidence",
                        lambda: runner.supabase.table("threads_claim_evidence")
                        .select("claim_id,evidence_id,created_at")
                        .in_("claim_id", claim_ids[:500])
                        .limit(3000)
                        .execute(),
                    )
                    for row in ev_resp.data or []:
                        claim_id = str(row.get("claim_id") or "").strip()
                        if not claim_id:
                            continue
                        slot = evidence_by_claim.get(claim_id) or {"count": 0, "last_seen": None}
                        slot["count"] += 1
                        ts = row.get("created_at")
                        if ts and (slot["last_seen"] is None or str(ts) > str(slot["last_seen"])):
                            slot["last_seen"] = ts
                        evidence_by_claim[claim_id] = slot
                except Exception:
                    degraded = True

            for row in claims_rows:
                claim_id = str(row.get("id") or "").strip()
                if not claim_id:
                    continue
                status = str(row.get("status") or "").lower()
                base = 56 if status == "audited" else 38 if status == "hypothesis" else 44
                ev = evidence_by_claim.get(claim_id) or {"count": 0, "last_seen": None}
                strength = base + min(28, int(ev["count"]) * 7)
                strength = max(8, min(99, strength))
                last_seen = ev["last_seen"] or row.get("created_at")
                title = _truncate_text(str(row.get("text") or ""), 80) or f"Signal {claim_id[:8]}"
                related_signals.append(
                    {
                        "signal_id": f"sig-{claim_id[:8]}",
                        "title": title,
                        "strength_pct": strength,
                        "source_type": str(row.get("scope") or row.get("claim_type") or "claim"),
                        "source_ref": claim_id,
                        "evidence_count": int(ev["count"]),
                        "last_seen": last_seen,
                    }
                )
                run_id = row.get("run_id")
                if run_id and latest_run_id is None:
                    latest_run_id = str(run_id)

            related_signals.sort(
                key=lambda item: (
                    -int(item.get("strength_pct") or 0),
                    -int(item.get("evidence_count") or 0),
                    -(_parse_iso(item.get("last_seen")) or 0),
                )
            )
            related_signals = related_signals[:12]
        except Exception:
            degraded = True

    if degraded:
        _set_degraded_response(response)

    return {
        "status": "ready" if (occurrence_timeline or related_signals) else "empty",
        "reason_code": None if (occurrence_timeline or related_signals) else "phenomenon_signals_empty",
        "trace_id": trace_id,
        "phenomenon_id": phenomenon_id,
        "window": f"{hours}h",
        "occurrence_timeline": occurrence_timeline,
        "related_signals": related_signals,
        "supporting_refs": {
            "latest_post_id": latest_post_id,
            "latest_run_id": latest_run_id,
        },
        "meta": {
            "computed_at": now.isoformat().replace("+00:00", "Z"),
            "version": "v0",
            "degraded": degraded,
        },
    }


@router.post("/library/phenomena/{phenomenon_id}/promote")
def promote_phenomenon(phenomenon_id: str, response: Response, request: Request):
    trace_id = _trace_id_from_request(request)
    _attach_trace_id(response, trace_id)
    try:
        meta_resp = (
            runner.supabase.table("narrative_phenomena")
            .select("id, status")
            .eq("id", phenomenon_id)
            .limit(1)
            .execute()
        )
    except Exception:
        _set_degraded_response(response)
        return _pending_payload(
            trace_id=trace_id,
            reason_code="phenomenon_promote_pending",
            detail="Phenomenon source temporarily unavailable.",
            extra={"id": phenomenon_id, "ok": False},
        )

    if not meta_resp.data:
        raise HTTPException(status_code=404, detail="Phenomenon not found")

    current_status = (meta_resp.data[0] or {}).get("status", "unknown")
    if current_status and str(current_status).lower() != "provisional":
        raise HTTPException(status_code=409, detail=f"Cannot promote from status '{current_status}'")

    try:
        runner.supabase.table("narrative_phenomena").update({"status": "active"}).eq("id", phenomenon_id).execute()
    except Exception as e:
        runner.logger.exception("api/library/phenomena promote failed")
        _set_degraded_response(response)
        return _pending_payload(
            trace_id=trace_id,
            reason_code="phenomenon_promote_pending",
            detail=f"Promotion pending backend retry: {type(e).__name__}",
            extra={"id": phenomenon_id, "ok": False},
        )

    return {"ok": True, "id": phenomenon_id, "status": "active", "trace_id": trace_id}


@router.post("/reviews")
def create_review(payload: ReviewRequest):
    allowed_label_types = {"golden_sample", "stance", "speech_act", "mft", "other"}
    if payload.label_type not in allowed_label_types:
        raise HTTPException(status_code=400, detail="invalid label_type")
    try:
        post_resp = (
            runner.supabase.table("threads_posts")
            .select("analysis_json")
            .eq("id", payload.post_id)
            .limit(1)
            .execute()
        )
        post_row = (post_resp.data or [None])[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"bundle lookup failed: {type(e).__name__}")
    if not post_row or not post_row.get("analysis_json"):
        raise HTTPException(status_code=404, detail="analysis_json not found")
    meta = (post_row.get("analysis_json") or {}).get("meta") or {}
    if meta.get("bundle_id") != payload.bundle_id:
        raise HTTPException(status_code=400, detail="bundle_id mismatch")
    # Deprecated for axis; repurposed as generic labeling.
    if payload.axis_id or payload.axis_version:
        runner.logger.warning("[Axis] deprecated fields ignored: axis_id/axis_version")
    row = {
        "post_id": int(payload.post_id),
        "bundle_id": payload.bundle_id,
        "analysis_build_id": payload.analysis_build_id,
        "label_type": payload.label_type,
        "schema_version": payload.schema_version,
        "decision": payload.decision,
        "comment_id": payload.comment_id,
        "cluster_key": payload.cluster_key,
        "notes": payload.notes,
    }
    try:
        runner.supabase.table("analysis_reviews").insert(row).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"insert failed: {type(e).__name__}")
    return {"status": "recorded"}


@router.post("/casebook")
def create_casebook_entry(payload: CasebookCreateRequest):
    t0_ms = _parse_iso(payload.bucket.t0)
    t1_ms = _parse_iso(payload.bucket.t1)
    if t0_ms is None or t1_ms is None or t1_ms <= t0_ms:
        raise HTTPException(status_code=400, detail="invalid bucket range")
    if payload.summary_version != "casebook_summary_v1":
        raise HTTPException(status_code=400, detail="unsupported summary_version")

    loaded = payload.coverage.comments_loaded
    total = payload.coverage.comments_total
    if loaded < 0 or (total is not None and total < 0):
        raise HTTPException(status_code=400, detail="coverage values must be non-negative")
    truncated_expected = bool(total is not None and total > loaded)
    if payload.coverage.is_truncated != truncated_expected:
        raise HTTPException(status_code=400, detail="coverage.is_truncated mismatch")

    row = {
        "evidence_id": payload.evidence_id,
        "comment_id": payload.comment_id,
        "evidence_text": payload.evidence_text,
        "post_id": str(payload.post_id),
        "captured_at": payload.captured_at,
        "bucket": payload.bucket.model_dump(),
        "metrics_snapshot": payload.metrics_snapshot.model_dump(),
        "coverage": payload.coverage.model_dump(),
        "summary_version": payload.summary_version,
        "filters": payload.filters.model_dump(),
        "analyst_note": payload.analyst_note,
    }
    try:
        resp = _run_with_retry(
            "api/casebook insert",
            lambda: runner.supabase.table("analyst_casebook").insert(row).execute(),
        )
        inserted = (resp.data or [{}])[0] if hasattr(resp, "data") else {}
    except Exception as e:
        runner.logger.exception("api/casebook insert failed")
        return JSONResponse({"detail": f"casebook insert failed: {type(e).__name__}: {str(e)}"}, status_code=500)

    return {"status": "recorded", "id": inserted.get("id"), "created_at": inserted.get("created_at")}


@router.get("/casebook")
def list_casebook(response: Response, post_id: Optional[str] = None, limit: int = 200):
    q_limit = max(1, min(limit, 500))
    try:
        def _fetch():
            query = runner.supabase.table("analyst_casebook").select(
                "id, evidence_id, comment_id, evidence_text, post_id, captured_at, bucket, metrics_snapshot, coverage, summary_version, filters, analyst_note, created_at"
            )
            if post_id:
                query = query.eq("post_id", str(post_id))
            return query.order("created_at", desc=True).limit(q_limit).execute()

        resp = _run_with_retry("api/casebook list", _fetch)
    except Exception as e:
        runner.logger.exception("api/casebook list failed")
        _set_degraded_response(response)
        return {"items": []}
    return {"items": resp.data or []}


@router.get("/debug/phenomenon/match/{post_id}")
def debug_phenomenon_match(post_id: str, k: int = 5):
    if not runner.build_evidence_bundle or not runner.embed_text:
        raise HTTPException(status_code=500, detail="Fingerprint/embedding modules unavailable")
    try:
        resp = (
            runner.supabase.table("threads_posts")
            .select("id, post_text, images, raw_comments, cluster_summary")
            .eq("id", post_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    if not resp.data:
        raise HTTPException(status_code=404, detail="Post not found")
    row = resp.data[0]
    comments = row.get("raw_comments") or []
    bundle = runner.build_evidence_bundle(
        post_text=row.get("post_text") or "",
        ocr_full_text=None,
        comments=comments if isinstance(comments, list) else [],
        cluster_summary=row.get("cluster_summary") or {},
        images=row.get("images") or [],
    )
    emb = runner.embed_text(bundle.fingerprint)
    try:
        match_resp = (
            runner.supabase.rpc(
                "match_phenomena",
                {
                    "query_embedding": emb,
                    "match_threshold": 0.0,
                    "match_count": max(1, min(k, 20)),
                },
            ).execute()
        )
        candidates = match_resp.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"match_phenomena failed: {e}")

    return {
        "post_id": post_id,
        "fingerprint_hash": runner.embedding_hash(emb) if runner.embedding_hash else None,
        "candidates": candidates,
    }


@router.get("/comments/by-post/{post_id}")
def comments_by_post(post_id: str, response: Response, limit: int = 50, offset: int = 0, sort: str = "likes"):
    limit = max(1, min(limit, 500))
    try:
        pid: Any = int(post_id)
    except Exception:
        pid = post_id
    degraded = False
    total = 0
    try:
        count_resp = _run_with_retry(
            "api/comments/by-post count",
            lambda: runner.supabase.table("threads_comments")
            .select("id")
            .eq("post_id", pid)
            .execute(),
        )
        total = len(count_resp.data or [])
    except Exception:
        degraded = True

    try:
        def _fetch_comments():
            query = runner.supabase.table("threads_comments").select(
                "id, post_id, text, author_handle, like_count, reply_count, cluster_key, created_at"
            ).eq("post_id", pid)
            if sort == "time":
                query = query.order("created_at", desc=True)
            else:
                query = query.order("like_count", desc=True)
            return query.range(offset, offset + limit - 1).execute()

        resp = _run_with_retry("api/comments/by-post rows", _fetch_comments)
    except Exception:
        runner.logger.exception("api/comments/by-post failed after retries")
        _set_degraded_response(response)
        return {"post_id": post_id, "total": 0, "items": []}

    if degraded:
        _set_degraded_response(response)
    if total == 0:
        total = len(resp.data or [])

    return {
        "post_id": post_id,
        "total": total,
        "items": resp.data or [],
    }


@router.get("/comments/search")
def comments_search(q: Optional[str] = None, author_handle: Optional[str] = None, post_id: Optional[str] = None, limit: int = 50):
    limit = max(1, min(limit, 200))
    pid: Any = None
    if post_id is not None:
        try:
            pid = int(post_id)
        except Exception:
            pid = post_id
    try:
        query = runner.supabase.table("threads_comments").select(
            "id, post_id, text, author_handle, like_count, reply_count, cluster_key, created_at"
        )
        if q:
            query = query.ilike("text", f"%{q}%")
        if author_handle:
            query = query.eq("author_handle", author_handle)
        if pid is not None:
            query = query.eq("post_id", pid)
        resp = query.order("like_count", desc=True).limit(limit).execute()
    except Exception as e:
        runner.logger.exception("api/comments/search failed")
        return JSONResponse({"detail": f"comments search failed: {type(e).__name__}: {str(e)}"}, status_code=500)
    return {"items": resp.data or []}


@router.get("/debug/latest-post")
def get_latest_post_debug():
    try:
        resp = (
            runner.supabase.table("threads_posts")
            .select("*")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")

    if not resp.data:
        return {"error": "no rows in threads_posts"}

    row = resp.data[0] or {}
    full_report = row.get("full_report") or ""
    preview = full_report[:500] + ("..." if len(full_report) > 500 else "")
    analysis_json = _sanitize_analysis_json(row.get("analysis_json"))

    return {
        "id": row.get("id"),
        "url": row.get("url"),
        "created_at": row.get("created_at"),
        "captured_at": row.get("captured_at"),
        "full_report_preview": preview,
        "analysis_json": analysis_json,
    }


@router.post("/debug/phenomenon/backfill_from_json")
def backfill_phenomenon_from_json(limit: int = 500):
    try:
        resp = (
            runner.supabase.table("threads_posts")
            .select("id, analysis_json, phenomenon_id, phenomenon_status, phenomenon_case_id")
            .is_("phenomenon_id", None)
            .limit(max(1, min(limit, 1000)))
            .execute()
        )
    except Exception as e:
        runner.logger.exception("backfill fetch failed")
        return JSONResponse({"detail": f"backfill fetch failed: {type(e).__name__}: {str(e)}"}, status_code=500)

    rows = resp.data or []
    updated = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        aj = row.get("analysis_json")
        if not isinstance(aj, dict):
            continue
        phen = aj.get("phenomenon")
        if not isinstance(phen, dict):
            continue
        pid = phen.get("id")
        if not pid:
            continue
        status = phen.get("status") or "pending"
        case_id = aj.get("phenomenon_case_id") or phen.get("case_id")
        try:
            runner.supabase.table("threads_posts").update(
                {
                    "phenomenon_id": pid,
                    "phenomenon_status": status,
                    "phenomenon_case_id": case_id,
                }
            ).eq("id", row.get("id")).execute()
            updated += 1
        except Exception:
            runner.logger.exception("backfill update failed", extra={"post_id": row.get("id")})
            continue

    return {"ok": True, "rows_scanned": len(rows), "rows_updated": updated}


@router.get("/analysis/{post_id}", response_model=RawAnalysisResponse)
def get_analysis(post_id: str):
    try:
        resp = (
            runner.supabase.table("threads_posts")
            .select("id, full_report")
            .eq("id", post_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")

    if not resp.data:
        raise HTTPException(status_code=404, detail="Post not found")

    row = resp.data[0]
    full_report = row.get("full_report")

    if not full_report:
        raise HTTPException(status_code=404, detail="AI analysis still pending")

    return RawAnalysisResponse(post_id=post_id, full_report_markdown=full_report)


@router.get("/run/batch")
def deprecated_run_batch():
    raise HTTPException(status_code=404, detail="Deprecated")
