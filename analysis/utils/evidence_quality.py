import os
import re
from typing import Any, Dict, List

MIN_EVIDENCE_CONTENT_TOKENS = int(
    os.getenv("DL_ISD_MIN_EVIDENCE_CONTENT_TOKENS")
    or os.getenv("DL_ISD_MIN_CONTENT_TOKENS")
    or 6
)

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "are",
    "was",
    "were",
    "about",
    "some",
    "their",
    "they",
    "you",
    "your",
    "into",
    "over",
    "under",
    "more",
    "less",
    "have",
    "has",
    "had",
    "but",
    "not",
    "all",
    "any",
    "many",
    "various",
    "different",
    "mixed",
}

PHATIC_SHORT = {
    "same",
    "agree",
    "true",
    "exactly",
    "right",
    "indeed",
    "ok",
    "okay",
    "k",
    "yes",
    "yep",
    "yup",
    "y",
    "lol",
    "lmao",
    "haha",
}

PHATIC_SHORT_ZH = {
    "係囉",
    "對",
    "對啊",
    "沒錯",
    "是的",
    "嗯",
    "嗯嗯",
    "好",
    "好啊",
    "可以",
    "同意",
    "沒問題",
    "笑死",
    "哈哈",
    "呵呵",
    "kkk",
    "wkwk",
}

EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]+", flags=re.UNICODE)
URL_RE = re.compile(r"https?://\\S+")
LAUGHTER_RE = re.compile(r"^(\\W|[ha]+|lol|lmao|kkk|wkwk|哈哈|呵呵|笑死)+$", flags=re.IGNORECASE)


def strip_urls(text: str) -> str:
    if not text:
        return ""
    return URL_RE.sub("", text)


def strip_emoji(text: str) -> str:
    if not text:
        return ""
    return EMOJI_RE.sub("", text)


def content_token_count(text: str) -> int:
    if not text:
        return 0
    lower = text.lower()
    tokens = re.findall(r"[a-z0-9]+", lower)
    content = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    count = len(content)
    if count == 0:
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
        if len(cjk_chars) >= 6:
            count = max(count, len(cjk_chars) // 2)
    return count


def emoji_or_laughter_only(text: str) -> bool:
    if not text:
        return True
    cleaned = strip_emoji(text)
    cleaned = re.sub(r"\\s+", "", cleaned)
    if not cleaned:
        return True
    return bool(LAUGHTER_RE.match(cleaned))


def is_phatic_short(text: str) -> bool:
    if not text:
        return True
    norm = re.sub(r"\\s+", "", text.lower())
    if norm in PHATIC_SHORT:
        return True
    for item in PHATIC_SHORT_ZH:
        if item in text:
            return True
    return False


def is_low_information_text(text: str, min_tokens: int | None = None) -> bool:
    if not text:
        return True
    cleaned = strip_urls(text)
    cleaned = strip_emoji(cleaned)
    token_count = content_token_count(cleaned)
    threshold = min_tokens if min_tokens is not None else MIN_EVIDENCE_CONTENT_TOKENS
    return (
        emoji_or_laughter_only(text)
        or token_count < threshold
        or is_phatic_short(cleaned)
    )


def evidence_quality_summary(texts: List[str]) -> Dict[str, Any]:
    low_info = 0
    token_counts: List[int] = []
    for text in texts:
        cleaned = strip_urls(text or "")
        cleaned = strip_emoji(cleaned)
        token_count = content_token_count(cleaned)
        token_counts.append(token_count)
        if is_low_information_text(text):
            low_info += 1
    total = len(texts)
    min_tokens = min(token_counts) if token_counts else 0
    max_tokens = max(token_counts) if token_counts else 0
    low_info_ratio = (low_info / total) if total else 1.0
    return {
        "total": total,
        "low_info": low_info,
        "low_info_ratio": low_info_ratio,
        "min_tokens": min_tokens,
        "max_tokens": max_tokens,
        "min_evidence_tokens": MIN_EVIDENCE_CONTENT_TOKENS,
    }
