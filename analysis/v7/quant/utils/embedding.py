import hashlib
import json
import os
from typing import Dict, List, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer

_embedder = None

from analysis.v7.utils.text_preprocess import preprocess_for_embedding, PREPROCESS_VERSION

def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        # Align with existing BERTopic/quant encoder
        model_name = "all-MiniLM-L6-v2"
        _embedder = SentenceTransformer(model_name)
    return _embedder


def encode_texts(
    texts: List[str],
    normalize_embeddings: bool = False,
    batch_size: int | None = None,
) -> Tuple[np.ndarray, str, Dict, str]:
    embedder = get_embedder()
    processed_texts = [preprocess_for_embedding(t) for t in (texts or [])]
    cfg = {
        "model_name": getattr(embedder, "model_card", None) or getattr(embedder, "model_name_or_path", None) or "all-MiniLM-L6-v2",
        "normalize_embeddings": bool(normalize_embeddings),
        "batch_size": int(batch_size or 32),
        "device": str(getattr(embedder, "_target_device", None) or getattr(embedder, "device", "")),
        "precision": "fp32",
        "sentence_transformers_version": getattr(__import__("sentence_transformers"), "__version__", "unknown"),
        "torch_version": getattr(__import__("torch"), "__version__", "unknown"),
        "preprocess_version": PREPROCESS_VERSION,
    }
    cfg_hash = hashlib.sha256(
        json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    embeddings = embedder.encode(
        processed_texts,
        normalize_embeddings=normalize_embeddings,
        batch_size=cfg["batch_size"],
    )
    return np.array(embeddings), cfg["model_name"], cfg, cfg_hash
