import re
from typing import Optional

import emoji


_WHITESPACE_RE = re.compile(r"\s+")
PREPROCESS_VERSION = "emoji_demojize_v1"


def preprocess_for_embedding(text: Optional[str]) -> str:
    if not text:
        return ""
    demojized = emoji.demojize(text, language="en")
    demojized = demojized.replace(":", " ").replace("_", " ")
    return _WHITESPACE_RE.sub(" ", demojized).strip()
