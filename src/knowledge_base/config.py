"""Configuration via environment variables + .env"""

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings


from pydantic import Field

class Settings(BaseSettings):
    # LLM
    openai_api_base: str = "http://localhost:11434/v1"
    llm_api_key: str = "sk-placeholder"
    openai_model: str = "qwen2.5:7b"

    # Embedding
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32
    embedding_cache_size: int = 1000

    # ChromaDB
    chroma_persist_dir: str = str(Path(__file__).parent.parent.parent / "data" / "chromadb")
    chroma_collection_chunks: str = "chunks"
    chroma_collection_documents: str = "documents"

    # SQLite
    db_path: str = str(Path(__file__).parent.parent.parent / "data" / "kb.db")

    # Upload
    upload_dir: str = str(Path(__file__).parent.parent.parent / "uploads")

    # OCR Service
    ocr_service_url: str = "http://127.0.0.1:8521"

    # JWT
    jwt_secret: str = "change-this-to-a-random-secret-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # Server
    api_port: int = 8000
    ui_port: int = 8501

    # Rate limiting
    rate_limit_search: int = 10
    rate_limit_query: int = 5
    rate_limit_upload: int = 2

    # Concurrency
    max_parse_workers: int = 4
    max_ocr_concurrency: int = 2
    max_llm_connections: int = 8
    chroma_batch_size: int = 100

    # Search
    hybrid_search_alpha: float = 0.7
    hybrid_search_top_k: int = 30
    rerank_top_k: int = 10
    vector_search_timeout: float = 3.0
    bm25_search_timeout: float = 1.0
    rerank_timeout: float = 2.0
    llm_timeout: float = 30.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
