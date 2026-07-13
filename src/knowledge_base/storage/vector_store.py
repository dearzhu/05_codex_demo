"""ChromaDB vector store with write lock for concurrency"""

import logging
import threading
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..config import get_settings

logger = logging.getLogger(__name__)


class VectorStore:
    """ChromaDB wrapper with thread-safe writes"""

    def __init__(self):
        settings = get_settings()
        self._client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._write_lock = threading.Lock()
        # ChromaDB 1.5.x only supports hnsw:space in metadata.
        # Default HNSW parameters (M=16, ef_construction=200) are used automatically.
        self._chunks_collection = self._client.get_or_create_collection(
            name=settings.chroma_collection_chunks,
            metadata={"hnsw:space": "cosine"} if settings.chroma_collection_chunks else None,
        )
        self._docs_collection = self._client.get_or_create_collection(
            name=settings.chroma_collection_documents,
            metadata={"hnsw:space": "cosine"} if settings.chroma_collection_documents else None,
        )
        logger.info(f"VectorStore ready ({settings.chroma_persist_dir})")

    def add_chunks(self, chunks: list):
        """Thread-safe batch insert for chunks"""
        if not chunks:
            return
        with self._write_lock:
            self._chunks_collection.add(
                ids=[c.chunk_id for c in chunks],
                embeddings=[c.embedding for c in chunks],
                metadatas=[{
                    "doc_id": c.doc_id,
                    "heading": c.metadata.get("heading", ""),
                    "page": c.metadata.get("estimated_pages", 0),
                    "filetype": c.metadata.get("filetype", ""),
                } for c in chunks],
                documents=[c.content[:1000] for c in chunks],
            )
        logger.debug(f"Added {len(chunks)} chunks to vector store")

    def add_document_vector(self, doc_id: str, embedding: list[float],
                             metadata: Optional[dict] = None):
        with self._write_lock:
            self._docs_collection.add(
                ids=[doc_id],
                embeddings=[embedding],
                metadatas=[metadata or {}],
            )

    def search_chunks(self, query_embedding: list[float], top_k: int = 30,
                       where: Optional[dict] = None) -> list[dict]:
        """Search chunks by vector similarity"""
        kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": min(top_k, 100),
        }
        if where:
            kwargs["where"] = where

        results = self._chunks_collection.query(**kwargs)

        items = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                items.append({
                    "id": results["ids"][0][i],
                    "document": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else 0.0,
                })
        return items

    def search_documents(self, query_embedding: list[float], top_k: int = 10) -> list[dict]:
        kwargs = {"query_embeddings": [query_embedding], "n_results": min(top_k, 50)}
        results = self._docs_collection.query(**kwargs)

        items = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                items.append({
                    "id": results["ids"][0][i],
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else 0.0,
                })
        return items

    def delete_document_chunks(self, doc_id: str):
        with self._write_lock:
            self._chunks_collection.delete(where={"doc_id": doc_id})
            self._docs_collection.delete(ids=[doc_id])

    def count(self) -> int:
        return self._chunks_collection.count()


_default_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _default_store
    if _default_store is None:
        _default_store = VectorStore()
    return _default_store
