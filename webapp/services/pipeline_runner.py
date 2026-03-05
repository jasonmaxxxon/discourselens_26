import os
import threading
import uuid
import logging
import random
import asyncio
from datetime import datetime, timezone
from time import perf_counter
from typing import Optional, List, Any, Dict
from starlette.concurrency import run_in_threadpool

from dotenv import load_dotenv

load_dotenv()
PIPELINES_AVAILABLE = True
try:
    from pipelines.core import run_pipeline, run_pipelines  # type: ignore
except Exception as e:
    PIPELINES_AVAILABLE = False

    def run_pipeline(*args, **kwargs):  # type: ignore
        raise RuntimeError("pipelines.core missing; Pipeline B/C disabled")

    def run_pipelines(*args, **kwargs):  # type: ignore
        raise RuntimeError("pipelines.core missing; Pipeline B/C disabled")

from database.store import supabase

EVENT_CRAWLER_AVAILABLE = True
try:
    from event_crawler import discover_thread_urls, rank_posts, save_hotlist  # type: ignore
except Exception:
    EVENT_CRAWLER_AVAILABLE = False

    def discover_thread_urls(*args, **kwargs):  # type: ignore
        raise RuntimeError("event_crawler missing; Pipeline B disabled")

    def rank_posts(*args, **kwargs):  # type: ignore
        raise RuntimeError("event_crawler missing; Pipeline B disabled")

    def save_hotlist(*args, **kwargs):  # type: ignore
        raise RuntimeError("event_crawler missing; Pipeline B disabled")

HOME_CRAWLER_AVAILABLE = True
try:
    from home_crawler import (  # type: ignore
        collect_home_posts,
        filter_posts_by_threshold,
        save_home_hotlist,
    )
except Exception:
    HOME_CRAWLER_AVAILABLE = False

    def collect_home_posts(*args, **kwargs):  # type: ignore
        raise RuntimeError("home_crawler missing; Pipeline C disabled")

    def filter_posts_by_threshold(*args, **kwargs):  # type: ignore
        raise RuntimeError("home_crawler missing; Pipeline C disabled")

    def save_home_hotlist(*args, **kwargs):  # type: ignore
        raise RuntimeError("home_crawler missing; Pipeline C disabled")
from scraper.fetcher import normalize_url, run_fetcher_test
from webapp.services import job_store
from webapp.services.ingest_sql import ingest_run
try:
    from fastapi.encoders import jsonable_encoder  # type: ignore
except Exception:
    from datetime import date, datetime

    def jsonable_encoder(obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {k: jsonable_encoder(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [jsonable_encoder(v) for v in obj]
        if isinstance(obj, tuple):
            return [jsonable_encoder(v) for v in obj]
        return obj

# --- Preanalysis / CIP modules (lazy import to avoid startup stalls) ---
PREANALYSIS_AVAILABLE = False
CIP_AVAILABLE = False
run_preanalysis = None
run_cluster_interpretation = None
_PREANALYSIS_LOADED = False
_CIP_LOADED = False


def _ensure_preanalysis_loaded() -> bool:
    global PREANALYSIS_AVAILABLE, run_preanalysis, _PREANALYSIS_LOADED
    if _PREANALYSIS_LOADED:
        return PREANALYSIS_AVAILABLE and run_preanalysis is not None
    _PREANALYSIS_LOADED = True
    try:
        from analysis.preanalysis_runner import run_preanalysis as _run_preanalysis
        run_preanalysis = _run_preanalysis
        PREANALYSIS_AVAILABLE = True
        logger.info("Preanalysis module loaded successfully.")
    except Exception as e:
        PREANALYSIS_AVAILABLE = False
        run_preanalysis = None
        logger.warning("Preanalysis module unavailable: %s", e)
    return PREANALYSIS_AVAILABLE and run_preanalysis is not None


def _ensure_cip_loaded() -> bool:
    global CIP_AVAILABLE, run_cluster_interpretation, _CIP_LOADED
    if _CIP_LOADED:
        return CIP_AVAILABLE and run_cluster_interpretation is not None
    _CIP_LOADED = True
    try:
        from analysis.cluster_interpretation import run_cluster_interpretation as _run_cluster_interpretation
        run_cluster_interpretation = _run_cluster_interpretation
        CIP_AVAILABLE = True
        logger.info("Cluster Interpretation module loaded successfully.")
    except Exception as e:
        CIP_AVAILABLE = False
        run_cluster_interpretation = None
        logger.warning("Cluster Interpretation module unavailable: %s", e)
    return CIP_AVAILABLE and run_cluster_interpretation is not None

# --- AI modules (legacy analyst removed; claims runner is used instead) ---
build_evidence_bundle = None
embed_text = None
embedding_hash = None

logger = logging.getLogger("dl")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
_JOB_BATCH_EXISTS_CACHE: set[str] = set()


def canonicalize_url(url: str) -> str:
    try:
        base = url.split("?")[0]
    except Exception:
        base = url
    return normalize_url(base)


def _maybe_launch_cip(post_id: str) -> None:
    if os.getenv("DL_ENABLE_CIP", "1") not in {"1", "true", "TRUE"}:
        return
    if not _ensure_cip_loaded() or not run_cluster_interpretation:
        logger.warning("[CIP] module unavailable; skipping post_id=%s", post_id)
        return
    try:
        post_int = int(post_id)
    except Exception:
        logger.warning("[CIP] invalid post_id=%s; skipping", post_id)
        return

    def _runner():
        try:
            run_cluster_interpretation(post_id=post_int, writeback=True)
            logger.info("[CIP] completed post_id=%s", post_id)
        except Exception as exc:
            logger.warning("[CIP] failed post_id=%s err=%s", post_id, exc)

    threading.Thread(target=_runner, daemon=True).start()


def fetch_existing_post_ids(urls: List[str]) -> Dict[str, str]:
    if not urls:
        return {}
    existing: Dict[str, str] = {}
    unique_urls = list({u for u in urls if u})
    for i in range(0, len(unique_urls), 200):
        chunk = unique_urls[i : i + 200]
        try:
            resp = supabase.table("threads_posts").select("id,url").in_("url", chunk).execute()
            for row in getattr(resp, "data", None) or []:
                url_val = row.get("url")
                pid = row.get("id")
                if url_val and pid:
                    existing[canonicalize_url(url_val)] = pid
        except Exception as e:
            logger.warning(f"[Pipeline B] fetch existing posts failed: {e}")
    return existing


def build_batch_summary(
    discovery_count: int,
    deduped_count: int,
    selected_count: int,
    skipped_exists: int,
    skipped_policy: int,
    success_count: int,
    fail_count: int,
    logs: List[str],
    failures: List[str],
) -> Dict[str, Any]:
    return {
        "discovery_count": discovery_count,
        "deduped_count": deduped_count,
        "selected_count": selected_count,
        "skipped_exists": skipped_exists,
        "skipped_policy": skipped_policy,
        "success_count": success_count,
        "fail_count": fail_count,
        "failures": failures[:20],
        "logs": logs,
    }


def clean_snippet(text: str, limit: int = 180) -> str:
    if not text:
        return ""
    normalized = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if len(normalized) > limit:
        return normalized[:limit].rstrip() + "…"
    return normalized


def normalize_like_counts(comments: list) -> list:
    if not comments:
        return comments
    for c in comments:
        if not isinstance(c, dict):
            continue
        val = c.get("like_count")
        if val is None:
            val = c.get("likes", 0)
        try:
            c["like_count"] = int(val)
        except Exception:
            c["like_count"] = 0
    return comments


def merge_phenomenon_meta(row: dict, analysis_json: dict | None) -> dict:
    if not isinstance(row, dict):
        row = {}
    if not isinstance(analysis_json, dict):
        analysis_json = {}
    db_id = row.get("phenomenon_id")
    db_status = row.get("phenomenon_status") or row.get("phenomenon_state")
    db_case = row.get("phenomenon_case_id") or row.get("case_id")

    aj_phen = analysis_json.get("phenomenon") if isinstance(analysis_json, dict) else {}
    if not isinstance(aj_phen, dict):
        aj_phen = {}
    aj_id = aj_phen.get("id")
    aj_status = aj_phen.get("status")
    aj_case = analysis_json.get("phenomenon_case_id") if isinstance(analysis_json, dict) else None
    if aj_case is None:
        aj_case = aj_phen.get("case_id")
    aj_name = aj_phen.get("canonical_name") or aj_phen.get("name")

    source = "default"
    phen_id = None
    phen_status = "pending"
    phen_case = None
    phen_name = None

    if db_id or db_status or db_case:
        phen_id = db_id
        phen_status = db_status or phen_status
        phen_case = db_case
        source = "db_columns"
    elif aj_id or aj_status or aj_case:
        phen_id = aj_id
        phen_status = aj_status or phen_status
        phen_case = aj_case
        phen_name = aj_name
        source = "analysis_json"

    if db_id and aj_id and db_id != aj_id:
        logger.warning(
            "[PhenomenonMeta] DB vs analysis_json id mismatch",
            extra={"db_id": db_id, "aj_id": aj_id, "post_id": row.get("id")},
        )

    return {
        "id": phen_id,
        "status": phen_status or "pending",
        "case_id": phen_case,
        "canonical_name": phen_name,
        "source": source,
    }


def make_job_logger(job_id: str):
    def _logger(message: str) -> None:
        job_store.append_job_log(job_id, message)
        print(f"[{job_id[:8]}] {message}")

    return _logger


def _safe_log_url(url: str) -> str:
    try:
        return (url or "").split("?")[0]
    except Exception:
        return str(url)


def _log_comments_summary(logger_obj: logging.Logger, comments: list | None) -> None:
    if comments and isinstance(comments, list):
        try:
            logger_obj.info("📦 comments_ready count=%s (bulk write candidate)", len(comments))
        except Exception:
            pass


def _update_stage(item_id: str | None, stage: str) -> None:
    """
    Best-effort stage update when no stage_cb is provided or it fails.
    """
    if not item_id or not supabase:
        return
    try:
        supabase.rpc("set_job_item_stage", {"p_item_id": item_id, "p_stage": stage}).execute()
    except Exception as e:
        logger.warning("[Runner] stage update failed item_id=%s stage=%s err=%s", item_id, stage, e)


def _job_batch_exists(job_id: Optional[str]) -> bool:
    if not job_id or not supabase:
        return False
    if job_id in _JOB_BATCH_EXISTS_CACHE:
        return True
    try:
        resp = supabase.table("job_batches").select("id").eq("id", job_id).limit(1).execute()
        exists = bool(resp.data)
        if exists:
            _JOB_BATCH_EXISTS_CACHE.add(job_id)
        return exists
    except Exception as e:
        logger.debug("[Runner] job_batch existence check failed job_id=%s err=%s", job_id, e)
        return False


def _progressive_job_item_update(
    job_id: Optional[str],
    target: str,
    stage: str,
    status: str = "processing",
    result_post_id: Any = None,
    error: Optional[str] = None,
) -> None:
    """
    Best-effort: ensure job_items reflects incremental progress so UI can stream results.
    Only runs when job_id exists in job_batches to avoid polluting unrelated runs.
    """
    if not _job_batch_exists(job_id):
        return
    if not supabase:
        return

    patch: Dict[str, Any] = {
        "stage": stage,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if result_post_id is not None:
        patch["result_post_id"] = str(result_post_id)
    if error:
        patch["error_log"] = error[:500]

    try:
        res = supabase.table("job_items").update(patch).eq("job_id", job_id).eq("target_id", target).execute()
        if not res.data:
            supabase.table("job_items").insert(
                {
                    "job_id": job_id,
                    "target_id": target,
                    "status": status,
                    "stage": stage,
                    "result_post_id": str(result_post_id) if result_post_id is not None else None,
                    "error_log": error[:500] if error else None,
                }
            ).execute()
    except Exception as e:
        logger.warning("[ops] job_item stage set failed (non-fatal): %s", e)


def build_phenomenon_post_stats_map() -> dict[str, dict[str, Any]]:
    try:
        resp = (
            supabase.table("threads_posts")
            .select("phenomenon_id, created_at, like_count")
            .not_.is_("phenomenon_id", None)
            .execute()
        )
    except Exception as e:
        logger.warning("Failed to fetch phenomenon post stats", extra={"error": str(e)})
        return {}

    stats: dict[str, dict[str, Any]] = {}
    for row in resp.data or []:
        pid = row.get("phenomenon_id")
        if not pid:
            continue
        entry = stats.setdefault(pid, {"total_posts": 0, "total_likes": 0, "last_seen_at": None})
        entry["total_posts"] += 1
        try:
            entry["total_likes"] += int(row.get("like_count") or 0)
        except Exception:
            pass
        ts = row.get("created_at")
        if ts and (entry["last_seen_at"] is None or ts > entry["last_seen_at"]):
            entry["last_seen_at"] = ts
    return stats


def should_reprocess(reprocess_policy: str, keyword_hit: bool) -> bool:
    if reprocess_policy == "force_all":
        return True
    if reprocess_policy == "force_if_keyword_hit" and keyword_hit:
        return True
    return False


def run_pipeline_a_job(job_id: str, url: str, item_id: str | None = None, stage_cb=None) -> str:
    """
    Blocking runner orchestrator for Pipeline A (Fetch -> Vision -> Analyst -> Store).
    - Uses stage_cb(stage) if provided to report stage transitions (fetch, vision, analyst, store).
    - Returns deterministic post_id (string) from run_pipeline result.
    - Does not swallow exceptions; re-raises after logging.
    """
    safe_url = _safe_log_url(url)
    logger.info("[Runner] ENTER job_id=%s item_id=%s url=%s", job_id, item_id, safe_url)

    def _stage(stage: str):
        if stage_cb:
            try:
                stage_cb(stage)
                return
            except Exception:
                logger.warning("[Runner] stage_cb failed stage=%s item_id=%s", stage, item_id, exc_info=True)
        _update_stage(item_id, stage)

    def _logger(message: str) -> None:
        logger.info("[RunnerLog][%s] %s", job_id, message)

    t0 = perf_counter()

    # Fetch + Ingest (SoT)
    _stage("fetch")
    start = perf_counter()
    try:
        logger.info("[Runner] DISPATCH start job_id=%s url=%s", job_id, safe_url)
        fetch_summary = run_fetcher_test(url, headless=True)
        run_dir = (fetch_summary.get("summary") or {}).get("output_dir")
        if not run_dir:
            raise RuntimeError("FETCH_NO_RUN_DIR")
        ingest_info = ingest_run(run_dir)
        duration = perf_counter() - start
        logger.info(
            "[Runner] DISPATCH end job_id=%s url=%s dur=%.2fs run_dir=%s",
            job_id,
            safe_url,
            duration,
            run_dir,
        )
    except Exception:
        duration = perf_counter() - start
        logger.exception("[Runner] EXCEPTION job_id=%s url=%s dur=%.2fs", job_id, safe_url, duration)
        raise

    post_id = ingest_info.get("post_id")
    if not post_id:
        raise RuntimeError("INGEST_NO_POST_ID")
    post_id = str(post_id)

    metrics_quality = ingest_info.get("metrics_quality") or {}
    job_patch = {
        "run_id": ingest_info.get("run_id"),
        "result_post_id": post_id,
        "crawled_at_utc": ingest_info.get("crawled_at_utc"),
        "comment_count": ingest_info.get("comment_count"),
        "edge_count": ingest_info.get("edge_count"),
        "runtime_seconds": round(duration, 2),
        "metrics_quality": metrics_quality,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if _job_batch_exists(job_id):
        try:
            res = supabase.table("job_items").update(job_patch).eq("job_id", job_id).eq("target_id", safe_url).execute()
            if not res.data:
                supabase.table("job_items").insert({"job_id": job_id, "target_id": safe_url, **job_patch}).execute()
        except Exception as e:
            logger.warning("[Runner] job_items patch failed job_id=%s url=%s err=%s", job_id, safe_url, e)

    _stage("analyst")
    _progressive_job_item_update(job_id, safe_url, stage="analyst", status="processing", result_post_id=post_id)
    if not _ensure_preanalysis_loaded() or not run_preanalysis:
        raise RuntimeError("PREANALYSIS_MODULES_UNAVAILABLE")
    try:
        run_preanalysis(int(post_id), prefer_sot=True, persist_assignments=True)
    except Exception:
        logger.exception("[Runner] Preanalysis failed post_id=%s", post_id)
        raise
    _maybe_launch_cip(post_id)

    # Claims + Evidence only (no legacy narrative / academic base)
    try:
        from analysis.claims_runner import run_claims_only_for_post

        use_stub = os.getenv("DL_LLM_STUB", "").lower() in {"1", "true"}
        if not os.getenv("GEMINI_API_KEY") and not use_stub:
            os.environ["DL_LLM_STUB"] = "1"
            use_stub = True
            logger.warning("[Runner] GEMINI_API_KEY missing; enabling DL_LLM_STUB for claims_only.")
        run_claims_only_for_post(int(post_id), use_stub=use_stub)
    except Exception:
        logger.exception("[Runner] Claims runner failed post_id=%s", post_id)

    _stage("store")
    _progressive_job_item_update(job_id, safe_url, stage="store", status="processing", result_post_id=post_id)
    logger.info("[Runner] STORE stage post_id=%s dur=%.2fs", post_id, perf_counter() - t0)
    return post_id


def run_pipeline_b_job(job_id: str, keyword: str, max_posts: int, mode: str, reprocess_policy: str = "skip_if_exists"):
    log = make_job_logger(job_id)
    job = job_store.get_job(job_id)
    if not job:
        return

    try:
        job_store.set_job_status(job_id, "running")
        log(f"🧵 Pipeline B 任務開始，keyword = {keyword}")

        discovered = discover_thread_urls(keyword, max_posts * 2)
        discovery_count = len(discovered)
        ranked = rank_posts(discovered)
        selected = ranked[:max_posts]
        log(f"📥 本次發現 {discovery_count} 篇，選取 {len(selected)} 篇貼文")

        if mode == "hotlist":
            filepath = save_hotlist(selected, keyword)
            job_store.set_job_result(
                job_id,
                {
                    "posts": [],
                    "summary": f"Pipeline B 完成，已輸出 hotlist（{len(selected)} 篇，關鍵字：{keyword}）",
                },
            )
            job_store.set_job_status(job_id, "done")
            log(f"✅ Pipeline B 完成，hotlist 已輸出：{filepath}")
            return

        urls = []
        canonical_to_raw: Dict[str, str] = {}
        for p in selected:
            canon = canonicalize_url(p.url)
            if canon in canonical_to_raw:
                continue
            canonical_to_raw[canon] = p.url
            urls.append(canon)

        existing_map = fetch_existing_post_ids(urls)
        scheduled: List[str] = []
        skipped: List[str] = []

        for canon in urls:
            exists = canon in existing_map
            if not exists:
                scheduled.append(canon)
            else:
                if should_reprocess(reprocess_policy, keyword_hit=True):
                    scheduled.append(canon)
                else:
                    skipped.append(canon)

        log(
            f"🧮 Discovery={discovery_count}, deduped={len(urls)}, scheduled={len(scheduled)}, skipped={len(skipped)} policy={reprocess_policy}"
        )

        posts: List[dict] = []
        success = 0
        failures: List[str] = []
        for idx, url in enumerate(scheduled, start=1):
            try:
                _progressive_job_item_update(job_id, url, "running", status="processing")
                log(f"[{idx}/{len(scheduled)}] 🔗 Processing {url}")
                data = run_pipeline(url, ingest_source="B", return_data=True, logger=log)
                if data:
                    post_id = data.get("id") or data.get("post_id")
                    data["snippet"] = clean_snippet(data.get("post_text", ""))
                    data["images"] = data.get("images") or []
                    posts.append(data)
                    success += 1
                    if post_id:
                        _progressive_job_item_update(job_id, url, "completed_post", status="processing", result_post_id=post_id)
                else:
                    failures.append(url)
            except Exception as e:
                failures.append(f"{url} ({e})")
                _progressive_job_item_update(job_id, url, "failed_post", status="processing", error=str(e))

        summary = (
            f"Pipeline B 完成，已處理 {success}/{len(scheduled)} 篇（關鍵字：{keyword}, 跳過 {len(skipped)}）"
        )
        job_store.set_job_result(
            job_id,
            {
                "posts": posts or [],
                "summary": summary or "",
            },
        )
        job_store.set_job_status(job_id, "done")
        log(f"✅ Pipeline B 完成，success={success}, failed={len(failures)}, skipped={len(skipped)}")
        if failures:
            log(f"❗ 失敗列表: {failures[:5]}")
    except Exception as e:
        job_store.set_job_status(job_id, "error")
        log(f"❌ Pipeline B 任務失敗：{e}")


async def process_pipeline_b_backend(
    keyword: Optional[str],
    urls: Optional[List[str]],
    max_posts: int,
    exclude_existing: bool,
    reprocess_policy: str,
    ingest_source: str = "B",
    mode: str = "run",
    concurrency: int = 2,
    pipeline_mode: str = "full",
    vision_mode: str = "auto",
    vision_stage_cap: str = "auto",
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    # Vision/OCR disabled; keep params for API compatibility.
    logs: List[str] = []
    max_posts = max(1, min(max_posts or 20, 20))
    concurrency = max(1, min(concurrency or 2, 3))

    candidates: List[str] = []
    if keyword:
        discovered = await run_in_threadpool(discover_thread_urls, keyword, max_posts * 2)
        logs.append(f"discovered_via_keyword={len(discovered)}")
        for p in discovered:
            canon = canonicalize_url(p.url)
            candidates.append(canon)

    for url in urls or []:
        candidates.append(canonicalize_url(url))

    deduped = list({u for u in candidates if u})
    discovery_count = len(candidates)
    deduped_count = len(deduped)
    if deduped_count > max_posts:
        deduped = deduped[:max_posts]

    existing_map = fetch_existing_post_ids(deduped)
    scheduled: List[str] = []
    skipped_exists: List[str] = []
    skipped_policy: List[str] = []
    items: List[Dict[str, Any]] = []
    for canon in deduped:
        exists = canon in existing_map
        keyword_hit = True if keyword else False
        if exists and exclude_existing and not should_reprocess(reprocess_policy, keyword_hit):
            skipped_exists.append(canon)
            items.append(
                {
                    "canonical_url": canon,
                    "decision": "skipped_exists",
                    "reason": "exists",
                    "existing_post_id": existing_map.get(canon),
                }
            )
            continue
        if exists and not should_reprocess(reprocess_policy, keyword_hit):
            skipped_policy.append(canon)
            items.append(
                {
                    "canonical_url": canon,
                    "decision": "skipped_policy",
                    "reason": "policy_skip",
                    "existing_post_id": existing_map.get(canon),
                }
            )
            continue
        scheduled.append(canon)
        items.append(
            {
                "canonical_url": canon,
                "decision": "selected",
                "reason": None,
                "existing_post_id": existing_map.get(canon),
            }
        )

    logs.append(
        f"deduped={deduped_count}, selected={len(scheduled)}, skipped_exists={len(skipped_exists)}, skipped_policy={len(skipped_policy)}, policy={reprocess_policy}, exclude_existing={exclude_existing}"
    )

    if mode == "preview":
        summary = build_batch_summary(
            discovery_count=discovery_count,
            deduped_count=deduped_count,
            selected_count=len(scheduled),
            skipped_exists=len(skipped_exists),
            skipped_policy=len(skipped_policy),
            success_count=0,
            fail_count=0,
            logs=logs,
            failures=[],
        )
        summary["items"] = items[:max_posts]
        summary["posts"] = []
        return summary

    success = 0
    fail = 0
    failures: List[str] = []
    posts: List[dict] = []

    async def run_one(idx: int, url: str, sem: asyncio.Semaphore):
        nonlocal success, fail
        async with sem:
            try:
                logs.append(f"[{idx}/{len(scheduled)}] BEGIN {url}")
                _progressive_job_item_update(job_id, url, "running", status="processing")
                ingest_res = await run_in_threadpool(
                    run_pipeline,
                    url,
                    ingest_source,
                    True,
                    logs.append,
                )
                await asyncio.sleep(random.uniform(0.5, 1.0))
                if not ingest_res:
                    raise RuntimeError("run_pipeline returned None")
                post_id = ingest_res.get("id")
                item_base = {
                    "canonical_url": url,
                    "post_id": post_id,
                }

                if pipeline_mode == "ingest":
                    posts.append(ingest_res)
                    success += 1
                    items.append({**item_base, "status": "succeeded", "reason": None, "stage": "ingest"})
                    logs.append(f"[{idx}/{len(scheduled)}] OK ingest {url} post_id={post_id}")
                    if post_id:
                        _progressive_job_item_update(job_id, url, "completed_post", status="processing", result_post_id=post_id)
                    return

                # Vision/OCR disabled in this repo build.

                logs.append(f"[{idx}/{len(scheduled)}] stage=preanalysis start url={url}")
                if not _ensure_preanalysis_loaded() or not run_preanalysis:
                    raise RuntimeError("PREANALYSIS_MODULES_UNAVAILABLE")
                preanalysis_res = await run_in_threadpool(
                    run_preanalysis,
                    int(post_id),
                    True,
                    True,
                )
                logs.append(f"[{idx}/{len(scheduled)}] stage=preanalysis end url={url}")
                _maybe_launch_cip(str(post_id))
                posts.append(preanalysis_res or ingest_res)
                success += 1
                items.append(
                    {
                        **item_base,
                        "status": "succeeded",
                        "reason": None,
                        "stage": "full",
                        "phenomenon_id": ingest_res.get("phenomenon_id"),
                    }
                )
                logs.append(f"[{idx}/{len(scheduled)}] OK full {url} post_id={post_id}")
                if post_id:
                    _progressive_job_item_update(job_id, url, "completed_post", status="processing", result_post_id=post_id)
            except Exception as e:
                fail += 1
                failures.append(f"{url} ({e})")
                items.append(
                    {
                        "canonical_url": url,
                        "decision": "selected",
                        "status": "failed",
                        "stage": "ingest" if "run_pipeline" in str(e) else "full",
                        "reason": str(e),
                    }
                )
                logs.append(f"[{idx}/{len(scheduled)}] FAIL {url}: {e}")
                _progressive_job_item_update(job_id, url, "failed_post", status="processing", error=str(e))

    async def run_all():
        sem = asyncio.Semaphore(concurrency)
        tasks = []
        for idx, url in enumerate(scheduled, start=1):
            await asyncio.sleep(random.uniform(0.2, 0.6))
            tasks.append(asyncio.create_task(run_one(idx, url, sem)))
        if tasks:
            await asyncio.gather(*tasks)

    if scheduled:
        await run_all()

    summary = build_batch_summary(
        discovery_count=discovery_count,
        deduped_count=deduped_count,
        selected_count=len(scheduled),
        skipped_exists=len(skipped_exists),
        skipped_policy=len(skipped_policy),
        success_count=success,
        fail_count=fail,
        logs=logs,
        failures=failures,
    )
    summary["posts"] = posts
    summary["items"] = items
    return summary


def run_pipeline_c_job(job_id: str, max_posts: int, threshold: int, mode: str):
    log = make_job_logger(job_id)
    job = job_store.get_job(job_id)
    if not job:
        return

    try:
        job_store.set_job_status(job_id, "running")
        log(f"🧵 Pipeline C 任務開始，max_posts = {max_posts}, threshold = {threshold}")

        posts = collect_home_posts(max_posts)
        filtered = filter_posts_by_threshold(posts, threshold)
        log(f"📥 Home 抽樣 {len(posts)} 篇，門檻後剩 {len(filtered)} 篇")

        if mode == "hotlist":
            filepath = save_home_hotlist(filtered)
            job_store.set_job_result(
                job_id,
                {
                    "posts": [],
                    "summary": f"Pipeline C 完成，已輸出 hotlist（{len(filtered)} 篇樣本，threshold={threshold}）",
                },
            )
            job_store.set_job_status(job_id, "done")
            log(f"✅ Pipeline C 完成，hotlist 已輸出：{filepath}")
            return

        urls = [p.url for p in filtered]
        posts = run_pipelines(urls, ingest_source="C", logger=log)
        summary = f"Pipeline C 完成，已抓取 {len(posts)} 篇個人主頁樣本（threshold={threshold}）"

        normalized_posts: list[dict] = []
        for p in posts:
            p["images"] = p.get("images") or []
            normalized_posts.append(p)
        job_store.set_job_result(
            job_id,
            {
                "posts": normalized_posts,
                "summary": summary or "",
            },
        )
        job_store.set_job_status(job_id, "done")
        log(f"✅ Pipeline C 完成，共 {len(normalized_posts)} 篇。")
    except Exception as e:
        job_store.set_job_status(job_id, "error")
        log(f"❌ Pipeline C 任務失敗：{e}")


__all__ = [
    "canonicalize_url",
    "fetch_existing_post_ids",
    "build_batch_summary",
    "clean_snippet",
    "normalize_like_counts",
    "merge_phenomenon_meta",
    "build_phenomenon_post_stats_map",
    "should_reprocess",
    "run_pipeline_a_job",
    "run_pipeline_b_job",
    "process_pipeline_b_backend",
    "run_pipeline_c_job",
]
