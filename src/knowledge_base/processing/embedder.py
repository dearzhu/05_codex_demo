"""Embedding generation with batch processing and caching"""

import os
import gc
import hashlib
import json
import logging
import threading
from functools import lru_cache
from typing import Optional

from ..config import get_settings

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()
_model_version = ""
_CACHE_SCHEMA_VERSION = "2"


def get_model():
    global _model, _model_version
    if _model is None:
        settings = get_settings()
        with _model_lock:
            if _model is None:
                logger.info(f"Loading embedding model: {settings.embedding_model} "
                            f"(device={settings.embedding_device})")
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(
                    settings.embedding_model,
                    device=settings.embedding_device,
                )
                _model_version = f"{settings.embedding_model}_v{_get_embedding_dim(_model)}_{_CACHE_SCHEMA_VERSION}"
                logger.info(f"Embedding model ready, dim={_get_embedding_dim(_model)}")
    return _model


def get_model_version() -> str:
    get_model()
    return _model_version


def embed_texts(texts: list[str], batch_size: int = 0) -> list[list[float]]:
    """Batch embed texts with caching"""
    settings = get_settings()
    bs = batch_size or settings.embedding_batch_size
    model = get_model()

    # Check cache for each text
    cache = _EmbeddingCache()
    results: list[Optional[list[float]]] = [None] * len(texts)
    to_encode = []
    to_encode_indices = []

    for i, text in enumerate(texts):
        cached = cache.get(text, _model_version)
        if cached is not None:
            results[i] = _flatten_embedding(cached)
        else:
            to_encode.append(text)
            to_encode_indices.append(i)

    # Encode uncached texts in batch
    if to_encode:
        try:
            embeddings = model.encode(
                to_encode,
                batch_size=bs,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            # model.encode(list) always returns shape (n, dim), even for n == 1.
            for pos, idx in enumerate(to_encode_indices):
                emb = embeddings[pos]
                emb_list = emb.tolist() if hasattr(emb, "tolist") else list(emb)
                results[idx] = _flatten_embedding(emb_list)
                cache.set(to_encode[pos], _flatten_embedding(emb_list), _model_version)

        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise

    final = [r for r in results if r is not None]
    if len(final) != len(texts):
        logger.warning(f"Embedding mismatch: {len(final)}/{len(texts)}")
    return final


def _get_embedding_dim(model) -> int:
    """Return embedding dimension, handling sentence-transformers API renames."""
    for method in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        if hasattr(model, method):
            return int(getattr(model, method)())
    return 1024


def _flatten_embedding(emb):
    """Unwrap legacy nested embeddings stored by an earlier buggy version."""
    while isinstance(emb, list) and emb and isinstance(emb[0], list):
        emb = emb[0]
    return emb


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def clear_gpu_cache():
    try:
        import torch
        torch.cuda.empty_cache()
        gc.collect()
    except Exception:
        pass


class _EmbeddingCache:
    """LRU + SQLite persistence cache for embeddings"""

    def __init__(self):
        self._memory: dict[str, tuple[str, list[float]]] = {}
        self._max_size = get_settings().embedding_cache_size
        self._db_initialized = False

    def _text_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str, model_version: str) -> Optional[list[float]]:
        h = self._text_hash(text)
        # Memory cache
        if h in self._memory:
            ver, emb = self._memory[h]
            if ver == model_version:
                return emb

        # SQLite persistence cache
        try:
            from ..models.database import get_read_conn
            with get_read_conn() as conn:
                row = conn.execute(
                    "SELECT embedding, model_version FROM embedding_cache WHERE text_hash = ?",
                    (h,)
                ).fetchone()
                if row and row["model_version"] == model_version:
                    import pickle
                    emb = pickle.loads(row["embedding"])
                    self._memory[h] = (model_version, emb)
                    return emb
        except Exception:
            pass
        return None

    def set(self, text: str, embedding: list[float], model_version: str):
        h = self._text_hash(text)

        # Memory cache with LRU eviction
        if len(self._memory) >= self._max_size:
            # Remove oldest entry
            if self._memory:
                self._memory.pop(next(iter(self._memory)))
        self._memory[h] = (model_version, embedding)

        # SQLite persistence (async preferred but sync is simpler)
        try:
            from ..models.database import get_write_conn, release_write_conn
            conn = get_write_conn()
            try:
                import pickle
                conn.execute(
                    "INSERT OR REPLACE INTO embedding_cache (text_hash, embedding, model_version) VALUES (?, ?, ?)",
                    (h, pickle.dumps(embedding), model_version),
                )
                conn.commit()
            finally:
                release_write_conn(conn)
        except Exception:
            pass
