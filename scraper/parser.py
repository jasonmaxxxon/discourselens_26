from bs4 import BeautifulSoup
import json
import re
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# UI 垃圾字，不能當作者 / user / 內容
UI_TOKENS = {
    "follow",
    "following",
    "more",
    "top",
    "translate",
    "verified",
    "edited",
    "author",
    "liked by original author",
}
FIRST_THREAD_TOKENS = {"first thread", "first threads"}


def _parse_human_number(value: str) -> int:
    if not value:
        return 0
    s = value.strip().lower().replace(",", "")
    try:
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        if s.endswith("m"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("b"):
            return int(float(s[:-1]) * 1_000_000_000)
        return int(float(s))
    except Exception:
        return 0


def _find_reply_counts(obj) -> list[int]:
    hits: list[int] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k)
            if lk in {
                "reply_count",
                "replyCount",
                "comment_count",
                "commentsCount",
                "comments_count",
                "repliesCount",
                "replyCount",
            }:
                try:
                    hits.append(int(v))
                except Exception:
                    pass
            if lk == "interactionStatistic" and isinstance(v, list):
                for entry in v:
                    if isinstance(entry, dict) and str(entry.get("interactionType", "")).lower().endswith("comment"):
                        try:
                            hits.append(int(entry.get("userInteractionCount") or 0))
                        except Exception:
                            pass
            hits.extend(_find_reply_counts(v))
    elif isinstance(obj, list):
        for item in obj:
            hits.extend(_find_reply_counts(item))
    return hits


def _extract_counts_from_script_text(raw: str) -> tuple[list[int], list[int]]:
    if not raw:
        return [], []
    primary_keys = (
        "reply_count",
        "replyCount",
        "comment_count",
        "commentsCount",
        "comments_count",
        "repliesCount",
    )
    direct_keys = ("direct_reply_count",)
    primary_hits: list[int] = []
    direct_hits: list[int] = []
    for key in primary_keys:
        pattern = re.compile(rf'"{re.escape(key)}"\s*:\s*(\d+)', flags=re.IGNORECASE)
        for match in pattern.finditer(raw):
            try:
                primary_hits.append(int(match.group(1)))
            except Exception:
                continue
    for key in direct_keys:
        pattern = re.compile(rf'"{re.escape(key)}"\s*:\s*(\d+)', flags=re.IGNORECASE)
        for match in pattern.finditer(raw):
            try:
                direct_hits.append(int(match.group(1)))
            except Exception:
                continue
    return primary_hits, direct_hits


def _extract_reply_count_from_embedded_json(html: str) -> tuple[int | None, str, float]:
    if not html:
        return None, "embedded_json", 0.0
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE)
    best_primary = 0
    best_direct = 0
    saw_json = False
    for raw in scripts:
        raw = raw.strip()
        if not raw:
            continue
        candidate = None
        if raw.startswith("{") or raw.startswith("["):
            candidate = raw
        if candidate:
            try:
                payload = json.loads(candidate)
            except Exception:
                payload = None
            if payload is not None:
                hits = _find_reply_counts(payload)
                if hits:
                    best_primary = max(best_primary, max(hits))
                    saw_json = True
        primary_hits, direct_hits = _extract_counts_from_script_text(raw)
        if primary_hits:
            best_primary = max(best_primary, max(primary_hits))
        if direct_hits:
            best_direct = max(best_direct, max(direct_hits))
    if best_primary > 0:
        return best_primary, "embedded_json", 1.0 if saw_json else 0.9
    if best_direct > 0:
        return best_direct, "embedded_json_direct_reply_count", 0.6
    return None, "embedded_json", 0.0


def _extract_reply_count_from_dom_text(html: str) -> tuple[int | None, str, float]:
    if not html:
        return None, "dom_text", 0.0
    patterns = [
        r"(\\d[\\d,\\.]*\\s*[KMB]?)\\s+repl(?:y|ies)\\b",
        r"(\\d[\\d,\\.]*\\s*[KMB]?)\\s+comment(?:s)?\\b",
        r"(\\d[\\d,\\.]*\\s*[KMB]?)\\s*(回覆|回复|留言|評論|评论)",
    ]
    for pat in patterns:
        match = re.search(pat, html, flags=re.IGNORECASE)
        if match:
            return _parse_human_number(match.group(1)), "dom_text", 0.7
    return None, "dom_text", 0.0


def extract_reply_count_from_html(html: str) -> tuple[int | None, str, float]:
    count, src, conf = _extract_reply_count_from_embedded_json(html)
    if count:
        return count, src, conf
    return _extract_reply_count_from_dom_text(html)

# 留言 / 主文 footer 區的 token
FOOTER_TOKENS = {"translate", "like", "reply", "repost", "share"}

# 時間格式：2d, 17h, 5m, 3w
TIME_PATTERN = re.compile(r"^\d+\s*[smhdw]$")
COMMENT_ID_PATTERNS = [
    re.compile(r'"comment_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"feedback_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"id"\s*:\s*"([^"]+)"'),
    re.compile(r'"pk"\s*:\s*"([^"]+)"'),
    re.compile(r'"media_id"\s*:\s*"([^"]+)"'),
]
PARENT_ID_PATTERNS = [
    re.compile(r'"parent_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"parent_comment_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"thread_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"parent_source_comment_id"\s*:\s*"([^"]+)"'),
]
AUTHOR_ID_PATTERNS = [
    re.compile(r'"user_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"author_id"\s*:\s*"([^"]+)"'),
]
CREATED_AT_PATTERNS = [
    re.compile(r'"created_at"\s*:\s*"([^"]+)"'),
    re.compile(r'"timestamp"\s*:\s*"([^"]+)"'),
]


def parse_number(text: str) -> int:
    """
    安全解析 like / view / reply / repost / share 數：
    - 支援: '1', '12', '1.2K', '3.4M'
    - 忽略: 沒數字的字串
    """
    if not text:
        return 0

    clean = text.replace(",", "").upper()
    m = re.search(r"([\d\.]+)\s*([KM]?)", clean)
    if not m:
        return 0

    num = float(m.group(1))
    suffix = m.group(2)

    if suffix == "K":
        num *= 1000
    elif suffix == "M":
        num *= 1_000_000

    return int(num)


def extract_block_user(lines) -> str:
    """
    從一個 block（主文 / 留言）裡抽出 user：
    - 跳過 Follow / More / Translate 等 UI
    - 跳過時間 2d / 17h
    - 跳過 Verified / Edited / Author / Liked by original author
    """
    for line in lines:
        candidate = line.strip()
        if not candidate:
            continue
        lower = candidate.lower()
        if lower in UI_TOKENS:
            continue
        if lower in FIRST_THREAD_TOKENS:
            continue
        if TIME_PATTERN.match(lower):
            continue
        # 柔性處理 header 分隔符（Threads 可能用 · 或 •）
        for sep in ("·", "•"):
            if sep in candidate:
                parts = candidate.split(sep, 1)
                return parts[0].strip()
        # 時間格式視為 meta，不當 user
        if TIME_PATTERN.match(candidate.lower()):
            continue
        return candidate
    logger.warning("Header parse fallback: unable to find user; lines=%s", lines[:3])
    return "Unknown"


def extract_block_likes(lines) -> int:
    """
    從 block 的行裡找 Like 數：
    - 找到第一個 'Like' 行 → 下一行當作數字
    """
    for i, line in enumerate(lines):
        if line.strip().lower() == "like":
            if i + 1 >= len(lines):
                return 0
            next_line = lines[i + 1]
            if not re.search(r"\d", next_line):
                return 0
            return parse_number(next_line)
    return 0


def extract_block_body(lines) -> str:
    """
    從 block（主文 / 留言）中抽出「純內容」：
    - tolerant: 如果沒偵測到 footer，就保留 header 以外的全部內容
    """
    start_idx = 0
    found_more = False
    for i, line in enumerate(lines):
        if line.strip().lower() == "more":
            start_idx = i + 1
            found_more = True
            break

    if not found_more:
        for i, line in enumerate(lines):
            candidate = line.strip()
            if not candidate:
                continue
            lower = candidate.lower()
            if lower in UI_TOKENS:
                continue
            if TIME_PATTERN.match(lower):
                continue
            start_idx = i
            break

    def soft_match(text: str) -> bool:
        low = text.strip().lower()
        return low in FOOTER_TOKENS or low in UI_TOKENS

    body_lines = []
    for line in lines[start_idx:]:
        if soft_match(line):
            break
        body_lines.append(line)

    return "\n".join(body_lines).strip()


def extract_metrics_from_lines(lines) -> dict:
    """
    Fallback-only metrics extractor for主文 lines:
    - Only parses likes / reply_count / repost_count / share_count when present
      as "<token>\\n<number>" pairs.
    """
    likes = reply_count = repost_count = share_count = 0

    for i, line in enumerate(lines):
        lower = line.strip().lower()
        if lower == "like" and i + 1 < len(lines):
            likes = parse_number(lines[i + 1])
        if lower in ("reply", "replies") and i + 1 < len(lines):
            reply_count = parse_number(lines[i + 1])
        if lower == "repost" and i + 1 < len(lines):
            repost_count = parse_number(lines[i + 1])
        if lower == "share" and i + 1 < len(lines):
            share_count = parse_number(lines[i + 1])

    return {"likes": likes, "reply_count": reply_count, "repost_count": repost_count, "share_count": share_count}


def _extract_comment_meta(block) -> Dict[str, Any]:
    """
    Best-effort extraction of native comment identifiers from a comment block.
    Looks at attributes and embedded JSON strings.
    """
    meta: Dict[str, Any] = {}
    if not block:
        return meta

    # Direct attributes (id, data-*). Threads sometimes nests ids on the element.
    attr_candidates = []
    try:
        for k, v in (block.attrs or {}).items():
            if isinstance(v, list):
                attr_candidates.extend([str(x) for x in v])
            else:
                attr_candidates.append(str(v))
    except Exception:
        pass

    text_blob = ""
    try:
        text_blob = block.decode()  # includes inner HTML
    except Exception:
        try:
            text_blob = str(block)
        except Exception:
            text_blob = ""

    def _search(patterns):
        for p in patterns:
            for hay in attr_candidates + [text_blob]:
                m = p.search(hay)
                if m:
                    return m.group(1)
        return None

    meta["source_comment_id"] = _search(COMMENT_ID_PATTERNS)
    meta["parent_comment_id"] = _search(PARENT_ID_PATTERNS)
    meta["author_id"] = _search(AUTHOR_ID_PATTERNS)
    meta["created_at"] = _search(CREATED_AT_PATTERNS)
    return meta


def _parse_single_html(html: str, url: str) -> dict:
    """
    單次 HTML → 結構化資料。
    用來支援：
      1) 初始畫面 (Top comments snapshot)
      2) 深度捲動後畫面
    """
    soup = BeautifulSoup(html, "html.parser")

    data = {
        "url": url,
        "author": "",
        "post_text": "",
        "post_text_raw": "",
        "is_first_thread": False,
        "metrics": {
            "likes": 0,
            "views": 0,
            "reply_count": 0,
            "repost_count": 0,
            "share_count": 0,
        },
        "images": [],
        "comments": [],
    }

    posts = soup.find_all("div", {"data-pressable-container": "true"})
    if not posts:
        return data

    # 主文
    main_post = posts[0]
    full_text = main_post.get_text("\n", strip=True)
    data["post_text_raw"] = full_text
    lines = full_text.split("\n")
    lower_lines = [ln.lower().strip() for ln in lines if ln.strip()]
    if any(ln in FIRST_THREAD_TOKENS for ln in lower_lines):
        data["is_first_thread"] = True

    # 圖片
    for img in main_post.find_all("img"):
        alt = img.get("alt", "") or ""
        if "profile picture" in alt.lower():
            continue
        src = img.get("src", "").strip()
        if not src:
            srcset = img.get("srcset", "")
            if srcset:
                src = srcset.split(" ", 1)[0].strip()
        if not src:
            continue
        if "s150x150" in src:
            continue
        data["images"].append({"src": src, "alt": alt})

    # 作者 + 主文內容 + 互動數
    data["author"] = extract_block_user(lines)
    data["post_text"] = extract_block_body(lines)

    m = extract_metrics_from_lines(lines)
    data["metrics"]["likes"] = m["likes"]
    data["metrics"]["reply_count"] = m["reply_count"]
    data["metrics"]["repost_count"] = m["repost_count"]
    data["metrics"]["share_count"] = m["share_count"]

    # Views（fallback: 只用於 live DOM metrics 缺失時）
    views = 0
    for text_node in soup.stripped_strings:
        low = text_node.lower()
        # 避免吃到 "View 3 more replies"
        if "views" in low and "reply" not in low and "view more" not in low:
            views = parse_number(text_node)
            break
    data["metrics"]["views"] = views

    # 留言區：posts[1:] 每一個都是一個留言 block
    dom_candidate_count = max(len(posts) - 1, 0)
    drop_reasons = {
        "missing_like_number": 0,
        "pattern_mismatch": 0,
    }
    for block in posts[1:]:
        raw_block = block.get_text("\n", strip=True)
        if not raw_block:
            continue

        block_lines = raw_block.split("\n")
        c_user = extract_block_user(block_lines)
        c_likes = extract_block_likes(block_lines)
        c_body = extract_block_body(block_lines)
        meta = _extract_comment_meta(block)

        missing_like_number = False
        for i, line in enumerate(block_lines):
            if line.strip().lower() == "like":
                if i + 1 >= len(block_lines) or not re.search(r"\d", block_lines[i + 1]):
                    missing_like_number = True
                break
        if missing_like_number:
            drop_reasons["missing_like_number"] += 1

        if not c_user and not c_body:
            drop_reasons["pattern_mismatch"] += 1
            continue

        data["comments"].append(
            {
                "user": c_user,
                "text": c_body,
                "likes": c_likes,
                "raw": raw_block,
                "source_comment_id": meta.get("source_comment_id"),
                "parent_comment_id": meta.get("parent_comment_id"),
                "parent_source_comment_id": meta.get("parent_source_comment_id"),
                "author_id": meta.get("author_id"),
                "created_at": meta.get("created_at"),
            }
        )

    dropped_count = max(dom_candidate_count - len(data["comments"]), 0)
    logger.info(
        "[Parser] dom_candidate_count=%s accepted=%s dropped=%s reasons=%s",
        dom_candidate_count,
        len(data["comments"]),
        dropped_count,
        drop_reasons,
    )

    return data


def extract_data_from_html(html_or_bundle, url: str, **kwargs) -> dict:
    """
    將 Threads 單帖的 HTML 解析成結構化 dict。
    支援兩種輸入：
      1) 舊版：單一 HTML 字串
      2) 新版：{"initial_html": ..., "scrolled_html": ...}

    新版策略：
      - initial_html：畫面剛載入時的 DOM → 一定包含 UI 顯示的 Top comments
      - scrolled_html：深度捲動後的 DOM → 載入更多留言
      - 兩者的 comments 會合併去重，並標記 from_top_snapshot=True/False
      - comments_by_likes：根據 likes 排序好的視圖，用於「高讚好留言 Top 5」
    """
    def _normalize_metrics(raw: dict | None) -> dict:
        defaults = {
            "likes": 0,
            "reply_count": 0,
            "repost_count": 0,
            "share_count": 0,
            "views": 0,
        }
        if not raw:
            return defaults
        normalized = defaults.copy()
        normalized["likes"] = int(raw.get("likes") or 0)
        normalized["reply_count"] = int(raw.get("reply_count") or raw.get("replies") or 0)
        normalized["repost_count"] = int(raw.get("repost_count") or raw.get("reposts") or 0)
        normalized["share_count"] = int(raw.get("share_count") or raw.get("shares") or 0)
        normalized["views"] = int(raw.get("views") or 0)
        return normalized

    if isinstance(html_or_bundle, dict):
        initial_html = html_or_bundle.get("initial_html") or ""
        scrolled_html = html_or_bundle.get("scrolled_html") or ""
        fetcher_metrics = _normalize_metrics(html_or_bundle.get("metrics"))
    else:
        # 向下兼容：只給一份 HTML 的舊用法
        initial_html = ""
        scrolled_html = html_or_bundle or ""
        fetcher_metrics = _normalize_metrics(None)
    fetcher_metrics_override = kwargs.get("fetcher_metrics")
    if isinstance(fetcher_metrics_override, dict):
        fetcher_metrics = _normalize_metrics(fetcher_metrics_override)

    # 先用「優先 scrolled_html，沒有就用 initial_html」當主資料
    main_html = scrolled_html or initial_html
    base = _parse_single_html(main_html, url)

    # 從深度捲動後 HTML 抓到的留言
    comments_scrolled = list(base.get("comments", []))

    # 再從 initial_html 再解析一次，專門抓「剛開頁時的 Top comments」
    comments_initial = []
    if initial_html:
        top_struct = _parse_single_html(initial_html, url)
        comments_initial = top_struct.get("comments", [])

        # 若 main_html 其實就是 initial_html（沒有 scrolled_html），則不需要再合併一次
        if not scrolled_html:
            comments_scrolled = []

    # 合併兩邊留言，去重，並標示來源
    merged_comments = []
    seen_keys = set()

    for src_list, is_top in ((comments_initial, True), (comments_scrolled, False)):
        for c in src_list:
            key = (c.get("user", ""), c.get("text", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            c["from_top_snapshot"] = is_top
            merged_comments.append(c)

    base["comments"] = merged_comments

    # 重新計算「抓到的實際留言數」與按讚排序視圖
    comments_sorted = sorted(
        merged_comments,
        key=lambda c: c.get("likes", 0),
        reverse=True,
    )
    base["comments_by_likes"] = comments_sorted

    # reply_count 至少不會小於實際抓到的留言樣本數
    fallback_metrics = base.get("metrics") or {}
    fallback_metrics["reply_count"] = max(
        fallback_metrics.get("reply_count", 0),
        len(merged_comments),
    )

    # Metrics merge: prefer fetcher metrics, fill zeros from parser fallback
    final_metrics = (fetcher_metrics or {}).copy()
    for key in ("likes", "reply_count", "repost_count", "share_count", "views"):
        if not final_metrics.get(key):
            final_metrics[key] = int(fallback_metrics.get(key, 0) or 0)
    reply_count = int(final_metrics.get("reply_count") or 0)
    if reply_count <= 0:
        rc, src, conf = extract_reply_count_from_html(initial_html or scrolled_html)
        if rc:
            reply_count = int(rc)
            base["metrics_meta"] = {"reply_count_source": src, "reply_count_confidence": conf}
    final_metrics["reply_count"] = max(reply_count, len(merged_comments))
    base["metrics"] = final_metrics

    # Debug coverage report for native ids (only when present)
    total_comments = len(merged_comments)
    if total_comments:
        with_source = sum(1 for c in merged_comments if c.get("source_comment_id"))
        if with_source:
            pct = round((with_source / total_comments) * 100, 1)
            print(f"[Parser] source_comment_id coverage: {with_source}/{total_comments} ({pct}%)")

    return base
