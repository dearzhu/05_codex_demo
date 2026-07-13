"""Query rewriting for multi-turn conversation and short queries"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def rewrite_query(query: str, history: Optional[list[dict]] = None) -> str:
    """Rewrite query for better retrieval.

    For multi-turn conversations, prepend context from history.
    For short queries (<3 words), expand to a more complete question.
    """
    if not query or not query.strip():
        return ""

    query = query.strip()

    # History-aware rewrite (multi-turn)
    if history and len(history) > 1:
        last_q = ""
        for msg in reversed(history):
            if msg.get("role") == "user":
                last_q = msg.get("content", "")
                break
        if last_q and query != last_q and len(query) < 10:
            query = f"{last_q} {query}"
            logger.debug(f"Query rewritten with history: {query}")

    # Short query expansion
    if len(query) < 5:
        query = f"关于{query}的相关信息"
        logger.debug(f"Query expanded: {query}")

    return query
