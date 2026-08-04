"""RAG pipeline: context assembly → LLM call → response"""

import asyncio
import logging
import time
from typing import Optional

from ..config import get_settings
from ..retrieval.hybrid_search import hybrid_search
from ..retrieval.reranker import rerank
from ..retrieval.query_rewriter import rewrite_query
from .prompts import build_prompt
from ..storage.metadata_store import get_metadata_store

logger = logging.getLogger(__name__)


async def rag_query(query: str, top_k: int = 10, stream: bool = False,
                    history: Optional[list[dict]] = None,
                    user_id: str = "anonymous") -> dict:
    """Full RAG pipeline: rewrite → search → rerank → assemble → LLM"""
    settings = get_settings()
    t0 = time.monotonic()

    # 1. Query rewriting
    rewritten = rewrite_query(query, history)

    # 2. Hybrid search (vector + BM25)
    search_results, search_time = await hybrid_search(
        rewritten,
        top_k=settings.hybrid_search_top_k,
        alpha=settings.hybrid_search_alpha,
    )

    # 3. Reranking
    reranked = await rerank(
        rewritten,
        search_results,
        top_k=min(top_k, settings.rerank_top_k),
        timeout=settings.rerank_timeout,
    )

    # 4. Context assembly
    context_parts = []
    sources = []
    for r in reranked:
        meta = r.get("metadata") or {}
        doc_id = meta.get("doc_id") or r.get("doc_id") or "unknown"
        doc_name = _get_doc_name(doc_id)
        chunk_text = r.get("document", "")
        if chunk_text:
            context_parts.append(f"[来源: {doc_name}]\n{chunk_text}")
            sources.append({
                "doc_name": doc_name,
                "chunk": chunk_text[:200],
                "score": r.get("score", 0),
                "page": meta.get("page"),
            })

    context = "\n\n".join(context_parts)

    # 5. LLM call
    answer, tokens = await _call_llm(
        query=rewritten,
        context=context,
        stream=stream,
        timeout=settings.llm_timeout,
    )

    elapsed = time.monotonic() - t0

    # Log search
    try:
        get_metadata_store().log_search(
            query=rewritten,
            results_count=len(search_results),
            latency_ms=round(elapsed * 1000, 2),
            user_id=user_id,
        )
    except Exception as e:
        logger.debug(f"Search log failed: {e}")

    logger.info(f"RAG query '{query[:50]}' → {elapsed:.2f}s, {len(sources)} sources, {tokens} tokens")
    return {"answer": answer, "sources": sources, "tokens_used": tokens}


async def search_only(query: str, top_k: int = 10) -> dict:
    """Search without LLM generation"""
    settings = get_settings()
    rewritten = rewrite_query(query)
    search_results, search_time = await hybrid_search(
        rewritten,
        top_k=settings.hybrid_search_top_k,
        alpha=settings.hybrid_search_alpha,
    )
    reranked = await rerank(rewritten, search_results, top_k=min(top_k, settings.rerank_top_k))
    sources = []
    for r in reranked:
        meta = r.get("metadata") or {}
        doc_id = meta.get("doc_id") or r.get("doc_id") or "unknown"
        doc_name = _get_doc_name(doc_id)
        sources.append({
            "doc_name": doc_name,
            "chunk": (r.get("document", "") or "")[:200],
            "score": r.get("score", 0),
        })
    return {"results": sources}


async def _call_llm(query: str, context: str, stream: bool = False,
                     timeout: float = 30.0) -> tuple[str, int]:
    """Call LLM via OpenAI-compatible API"""
    settings = get_settings()

    prompt = build_prompt(context, query)

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=settings.openai_api_base,
            api_key=settings.llm_api_key,
        )

        if stream:
            # For stream, we collect full response (API layer handles streaming separately)
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2048,
                timeout=timeout,
            )
        else:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2048,
                timeout=timeout,
            )

        answer = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else len(prompt) // 2
        return answer, tokens

    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        # Fallback: return context-based answer
        if context:
            return (
                f"根据知识库检索到以下相关内容（LLM 暂不可用）：\n\n{context[:500]}...",
                len(context) // 2,
            )
        return ("抱歉，LLM 服务当前不可用，且未检索到相关内容。", 0)


_doc_name_cache = {}
_user_meta_store = get_metadata_store()


def _get_doc_name(doc_id: str) -> str:
    if doc_id in _doc_name_cache:
        return _doc_name_cache[doc_id]
    try:
        doc = _user_meta_store.get_document(doc_id)
        if doc:
            name = doc.get("filename", doc_id)
        else:
            name = doc_id[:8]
    except Exception:
        name = doc_id[:8]
    _doc_name_cache[doc_id] = name
    return name
