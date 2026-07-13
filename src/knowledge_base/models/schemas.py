"""Pydantic schemas for API requests / responses"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ── Document ──
class DocumentOut(BaseModel):
    id: str
    filename: str
    filetype: str
    size: int
    upload_time: str
    status: str  # pending | parsing | chunking | embedding | storing | completed | failed
    error_message: Optional[str] = None
    chunk_count: int = 0
    tags: list[str] = []
    creator_id: str = "anonymous"

    class Config:
        from_attributes = True


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    status: str
    message: str = "enqueued"


class DocumentStatusResponse(BaseModel):
    id: str
    status: str
    progress: str = ""
    error_message: Optional[str] = None


# ── Search ──
class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    alpha: Optional[float] = None
    tags: Optional[list[str]] = None
    document_ids: Optional[list[str]] = None


class SearchSource(BaseModel):
    doc_name: str
    chunk: str
    score: float
    page: Optional[int] = None


class SearchResponse(BaseModel):
    results: list[SearchSource]


# ── Query (RAG) ──
class QueryRequest(BaseModel):
    query: str
    top_k: int = 10
    stream: bool = False
    history: Optional[list[dict]] = None  # [{"role": "user"/"assistant", "content": ...}]


class QueryResponse(BaseModel):
    answer: str
    sources: list[SearchSource]
    tokens_used: int = 0


# ── Tag ──
class TagListResponse(BaseModel):
    tags: list[str]


# ── Auth ──
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class UserInfo(BaseModel):
    username: str
    role: str = "user"
    department: str = "default"
