"""Cross-encoder reranking"""

import asyncio
import logging
from typing import Optional

from ..config import get_settings

logger = logging.getLogger(__name__)

_model = None


def get_reranker():
    global _model
    if _model is None:
        try:
            from sentence_transformers import CrossEncoder
            logger.info("Loading reranker model...")
            _model = CrossEncoder("BAAI/bge-reranker-v2-m3")
            logger.info("Reranker ready")
        except Exception as e:
            logger.warning(f"Reranker model load failed: {e}. Using score-based sort only.")
    return _model


async def rerank(query: str, results: list[dict], top_k: int = 10,
                 timeout: float = 2.0) -> list[dict]:
    """Rerank results using cross-encoder"""
    if not results:
        return results

    model = get_reranker()
    if model is None:
        return results[:top_k]

    try:
        pairs = [(query, r.get("document", "") or r.get("content", "")) for r in results]
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, _rerank_sync, model, pairs, results, top_k
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("Reranker timed out, using original order")
        return results[:top_k]
    except Exception as e:
        logger.error(f"Reranker error: {e}")
        return results[:top_k]


def _rerank_sync(model, pairs: list[tuple], results: list[dict],
                 top_k: int) -> list[dict]:
    scores = model.predict(pairs)
    scored = list(zip(results, scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [{**r, "score": round(float(s), 4)} for r, s in scored[:top_k]]
