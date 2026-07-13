"""Hybrid search: dense vector + sparse BM25, with query rewriting"""

import asyncio
import logging
import math
import time
from typing import Optional

import jieba
from rank_bm25 import BM25Okapi

from ..config import get_settings
from ..processing.embedder import embed_text
from ..storage.vector_store import get_vector_store

logger = logging.getLogger(__name__)

# Stop words for Chinese and English
_STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "can",
    "could", "may", "might", "shall", "should", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
}

# Global BM25 index (rebuilt when documents change)
_bm25_index: Optional[BM25Okapi] = None
_bm25_corpus: list[dict] = []


def rebuild_bm25(chunks: list[dict]):
    """Rebuild BM25 index from chunk corpus"""
    global _bm25_index, _bm25_corpus
    corpus = []
    metadata = []
    for c in chunks:
        tokens = _tokenize(c["content"])
        if tokens:
            corpus.append(tokens)
            metadata.append(c)
    if corpus:
        _bm25_index = BM25Okapi(corpus)
        _bm25_corpus = metadata
        logger.info(f"BM25 index rebuilt: {len(corpus)} documents")
    else:
        _bm25_index = None
        _bm25_corpus = []


def _tokenize(text: str) -> list[str]:
    """Tokenize Chinese + English text"""
    tokens = jieba.lcut(text)
    return [t.strip().lower() for t in tokens
            if t.strip() and t.strip() not in _STOP_WORDS
            and len(t.strip()) > 0]


async def hybrid_search(query: str, top_k: int = 30, alpha: float = 0.7,
                        where: Optional[dict] = None
                        ) -> tuple[list[dict], float]:
    """Hybrid search: concurrent vector + BM25, then fusion"""
    settings = get_settings()
    t0 = time.monotonic()

    vector_task = asyncio.create_task(
        _vector_search(query, top_k, where, settings.vector_search_timeout)
    )
    bm25_task = asyncio.create_task(
        _bm25_search(query, top_k, settings.bm25_search_timeout)
    )

    vector_results, bm25_results = await asyncio.gather(vector_task, bm25_task)
    elapsed = time.monotonic() - t0

    # Fusion
    merged = _fusion(vector_results, bm25_results, alpha)
    merged = merged[:settings.rerank_top_k]

    return merged, elapsed


async def _vector_search(query: str, top_k: int, where: Optional[dict],
                         timeout: float) -> list[dict]:
    try:
        query_emb = embed_text(query)
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: get_vector_store().search_chunks(query_emb, top_k, where),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Vector search timed out ({timeout}s)")
        return []
    except Exception as e:
        logger.error(f"Vector search error: {e}")
        return []


async def _bm25_search(query: str, top_k: int, timeout: float) -> list[dict]:
    if _bm25_index is None:
        return []

    try:
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, _bm25_search_sync, query, top_k
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"BM25 search timed out ({timeout}s)")
        return []
    except Exception as e:
        logger.error(f"BM25 search error: {e}")
        return []


def _bm25_search_sync(query: str, top_k: int) -> list[dict]:
    if _bm25_index is None or not _bm25_corpus:
        return []
    tokens = _tokenize(query)
    if not tokens:
        return []
    scores = _bm25_index.get_scores(tokens)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    results = []
    for idx in top_indices:
        if scores[idx] > 0:
            results.append({**_bm25_corpus[idx], "bm25_score": float(scores[idx])})
    return results


def _fusion(vector_results: list[dict], bm25_results: list[dict],
            alpha: float) -> list[dict]:
    """Reciprocal Rank Fusion with alpha weighting"""
    seen = set()
    combined = []

    # Score normalization
    def normalize(items: list[dict], score_key: str):
        if not items:
            return
        scores = [abs(i.get(score_key, 0)) for i in items]
        max_s = max(scores) if scores else 1
        for i in items:
            i[score_key] = i.get(score_key, 0) / max_s

    normalize(vector_results, "distance")
    normalize(bm25_results, "bm25_score")

    for v in vector_results:
        doc_id = v.get("id", "")
        score = alpha * (1.0 - v.get("distance", 0))
        if bm25_results:
            for b in bm25_results:
                if b.get("id") == doc_id or b.get("chunk_id") == doc_id:
                    score += (1 - alpha) * b.get("bm25_score", 0)
                    break
        combined.append({
            "id": doc_id,
            "document": v.get("document", ""),
            "metadata": v.get("metadata", {}),
            "score": round(score, 4),
        })
        seen.add(doc_id)

    # Add BM25-only results
    for b in bm25_results:
        bid = b.get("id") or b.get("chunk_id", "")
        if bid not in seen:
            combined.append({
                "id": bid,
                "document": b.get("content", ""),
                "metadata": b.get("metadata", {}),
                "score": round((1 - alpha) * b.get("bm25_score", 0), 4),
            })

    return sorted(combined, key=lambda x: x["score"], reverse=True)
