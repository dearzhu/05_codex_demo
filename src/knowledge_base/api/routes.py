"""FastAPI routes"""

import os
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import asyncio
from ..config import get_settings
from ..models.schemas import (
    DocumentOut, DocumentUploadResponse, DocumentStatusResponse,
    SearchRequest, SearchResponse, QueryRequest, QueryResponse,
    TagListResponse, TokenResponse, LoginRequest,
)
from ..storage.metadata_store import get_metadata_store
from ..storage.file_store import FileStore
from ..storage.vector_store import get_vector_store
from ..ingestion.parser import parse_document
from ..ingestion.metadata import extract_metadata
from ..processing.chunker import chunk_document
from ..processing.embedder import embed_texts, embed_text
from ..rag.pipeline import rag_query, search_only
from ..rate_limiter import get_rate_limiter, RateLimiter
from ..task_manager import Task, TaskManager
from ..api.auth import create_token, decode_token, hash_password, verify_password
from ..models.database import write_transaction, get_read_conn
import asyncio
from ..config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")
security = HTTPBearer(auto_error=False)

# Shared instances
_file_store = FileStore()


def _get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> str:
    if credentials is None:
        return "anonymous"
    payload = decode_token(credentials.credentials)
    return payload.get("sub", "anonymous") if payload else "anonymous"


def _rate_limit(rate_name: str):
    """Dependency factory for rate limiting"""
    settings = get_settings()
    limits = {
        "search": (settings.rate_limit_search, settings.rate_limit_search * 2),
        "query": (settings.rate_limit_query, settings.rate_limit_query * 2),
        "upload": (settings.rate_limit_upload, settings.rate_limit_upload * 2),
    }
    rate, cap = limits.get(rate_name, (10, 20))

    async def limiter(request: Request):
        ip = request.client.host if request.client else "unknown"
        limiter_obj = get_rate_limiter()
        if not limiter_obj.check(ip, rate=rate, capacity=cap):
            raise HTTPException(status_code=429, detail="Too many requests")
        return True

    return limiter


# ── Health ──

@router.get("/health")
async def health():
    return {"status": "ok", "service": "knowledge-base"}


# ── Auth ──

@router.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    with get_read_conn() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?",
            (req.username,),
        ).fetchone()
    if not row or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(req.username)
    return TokenResponse(access_token=token, username=req.username)


@router.post("/auth/register")
async def register(req: LoginRequest):
    with write_transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (req.username,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="User already exists")
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (req.username, hash_password(req.password)),
        )
    return {"message": "User created"}


# ── Document Upload ──

@router.post("/documents/upload", response_model=DocumentUploadResponse,
             dependencies=[Depends(_rate_limit("upload"))])
async def upload_document(
    file: UploadFile = File(...),
    tags: str = Form("[]"),
    user: str = Depends(_get_current_user),
):
    # Read file
    data = await file.read()
    sha256 = hashlib.sha256(data).hexdigest()
    size = len(data)
    ext = Path(file.filename).suffix.lower().lstrip(".")

    # Check duplicate (same SHA256)
    meta_store = get_metadata_store()
    with get_read_conn() as conn:
        existing = conn.execute(
            "SELECT id, status FROM documents WHERE sha256 = ?", (sha256,)
        ).fetchone()
    if existing:
        return DocumentUploadResponse(
            id=existing["id"],
            filename=file.filename,
            status=existing["status"],
            message="duplicate file, returning existing document",
        )

    # Parse tags
    parsed_tags = json.loads(tags) if isinstance(tags, str) else tags
    # Create document record first
    doc_id = meta_store.create_document(
        filename=file.filename,
        filetype=ext,
        size=size,
        tags=parsed_tags,
        creator_id=user,
        sha256=sha256,
    )

    # Save file using the same doc_id
    _file_store.save(file.filename, data, file_id=doc_id)

    # Enqueue processing task
    file_path = str(_file_store.get_path(doc_id, file.filename))
    _enqueue_processing(doc_id, file_path)

    return DocumentUploadResponse(
        id=doc_id,
        filename=file.filename,
        status="pending",
        message="enqueued for processing",
    )


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(status: str = ""):
    meta_store = get_metadata_store()
    docs = meta_store.get_all_documents(status)
    result = []
    for d in docs:
        try:
            tags = json.loads(d.get("tags", "[]"))
        except Exception:
            tags = []
        result.append(DocumentOut(
            id=d["id"],
            filename=d["filename"],
            filetype=d["filetype"],
            size=d["size"],
            upload_time=d.get("upload_time", ""),
            status=d.get("status", "unknown"),
            error_message=d.get("error_message"),
            chunk_count=d.get("chunk_count", 0),
            tags=tags,
            creator_id=d.get("creator_id", "anonymous"),
        ))
    return result


@router.get("/documents/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(doc_id: str):
    meta_store = get_metadata_store()
    doc = meta_store.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    chunk_count = doc.get("chunk_count", 0)
    status = doc.get("status", "unknown")
    progress = ""
    if status == "embedding":
        progress = f"{chunk_count} chunks processed"
    elif status == "completed":
        progress = f"{chunk_count} chunks indexed"
    return DocumentStatusResponse(
        id=doc_id,
        status=status,
        progress=progress,
        error_message=doc.get("error_message"),
    )


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    meta_store = get_metadata_store()
    doc = meta_store.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete from vector store
    try:
        get_vector_store().delete_document_chunks(doc_id)
    except Exception as e:
        logger.warning(f"Vector store delete error: {e}")

    # Delete from file store
    _file_store.delete(doc_id)

    # Delete from metadata store
    meta_store.delete_document(doc_id)

    return {"message": "Document deleted"}


# ── Tags ──

@router.get("/tags", response_model=TagListResponse)
async def list_tags():
    meta_store = get_metadata_store()
    return TagListResponse(tags=meta_store.get_all_tags())


# ── Search ──

@router.post("/search", response_model=SearchResponse,
             dependencies=[Depends(_rate_limit("search"))])
async def search(req: SearchRequest):
    from ..rag.pipeline import search_only
    result = await search_only(req.query, req.top_k)
    return SearchResponse(results=result.get("results", []))


# ── Query (RAG) ──

@router.post("/query", response_model=QueryResponse,
             dependencies=[Depends(_rate_limit("query"))])
async def query(req: QueryRequest, user: str = Depends(_get_current_user)):
    result = await rag_query(
        query=req.query,
        top_k=req.top_k,
        stream=req.stream,
        history=req.history,
        user_id=user,
    )
    return QueryResponse(
        answer=result["answer"],
        sources=result["sources"],
        tokens_used=result["tokens_used"],
    )


# ── Processing helper ──

_active_tasks: dict[str, asyncio.Task] = {}


def _enqueue_processing(doc_id: str, file_path: str):
    """Enqueue document processing in the background"""
    import asyncio

    async def process():
        loop = asyncio.get_event_loop()
        meta_store = get_metadata_store()

        try:
            meta_store.update_document_status(doc_id, "parsing")

            # 1. Parse document (CPU-bound, run in thread pool)
            content = await loop.run_in_executor(None, parse_document, file_path)

            # 2. Extract metadata
            doc_meta = await loop.run_in_executor(None, extract_metadata, file_path, content)

            meta_store.update_document_status(doc_id, "chunking")

            # 3. Chunk (CPU-bound)
            chunks = await loop.run_in_executor(
                None, lambda: chunk_document(doc_id=doc_id, content=content, doc_meta=doc_meta)
            )

            meta_store.update_document_status(doc_id, "embedding",
                                              chunk_count=len(chunks))

            # 4. Embed (batch, CPU/GPU-bound)
            texts = [c.content for c in chunks]
            embeddings = await loop.run_in_executor(None, embed_texts, texts)

            for c, emb in zip(chunks, embeddings):
                c.embedding = emb

            meta_store.update_document_status(doc_id, "storing",
                                              chunk_count=len(chunks))

            # 5. Store in vector DB
            await loop.run_in_executor(None, get_vector_store().add_chunks, chunks)

            # 6. Store in SQLite
            meta_store.save_chunks(chunks)

            # 7. Mark complete
            meta_store.update_document_status(doc_id, "completed",
                                              chunk_count=len(chunks))

            # 8. Rebuild BM25 index (CPU-bound)
            await loop.run_in_executor(None, _rebuild_bm25_index)

            logger.info(f"Document processed: {doc_id} ({len(chunks)} chunks)")

        except Exception as e:
            meta_store.update_document_status(doc_id, "failed", error_message=str(e))
            logger.error(f"Document processing failed: {doc_id}: {e}")
        finally:
            _active_tasks.pop(doc_id, None)

    task = asyncio.create_task(process())
    _active_tasks[doc_id] = task


def _rebuild_bm25_index():
    """Rebuild BM25 index from all chunks"""
    from ..retrieval.hybrid_search import rebuild_bm25
    from ..models.database import get_read_conn

    with get_read_conn() as conn:
        rows = conn.execute(
            "SELECT content FROM chunks WHERE content IS NOT NULL"
        ).fetchall()
    chunks_data = [{"content": r["content"]} for r in rows]
    rebuild_bm25(chunks_data)
