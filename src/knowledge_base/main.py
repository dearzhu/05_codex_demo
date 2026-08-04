"""FastAPI application entry point"""

import sys
import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deferred imports to avoid circular dependencies at module level
    from .config import get_settings
    from .models.database import init_db
    from .api.auth import hash_password
    from .models.database import write_transaction

    settings = get_settings()
    logger.info(f"Starting Knowledge Base API...")

    # Init database
    init_db()

    # Create default admin user
    try:
        with write_transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = 'admin'"
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
                    ("admin", hash_password("admin123")),
                )
                logger.info("Default admin user created (admin / admin123)")
    except Exception as e:
        logger.warning(f"Admin user creation skipped: {e}")

    # Build initial BM25 index
    try:
        from .retrieval.hybrid_search import rebuild_bm25
        import json
        with get_read_conn() as conn:
            rows = conn.execute(
                "SELECT content, doc_id, metadata_json FROM chunks"
            ).fetchall()
        if rows:
            chunks = []
            for r in rows:
                try:
                    meta = json.loads(r["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                chunks.append({
                    "content": r["content"],
                    "doc_id": r["doc_id"],
                    "metadata": meta,
                })
            rebuild_bm25(chunks)
            logger.info(f"BM25 index built: {len(rows)} chunks")
    except Exception as e:
        logger.debug(f"BM25 initial build skipped: {e}")

    # Start rate limiter cleanup task
    async def cleanup_loop():
        from .rate_limiter import get_rate_limiter
        while True:
            await asyncio.sleep(300)
            get_rate_limiter().cleanup()

    cleanup_task = asyncio.create_task(cleanup_loop())
    logger.info("Knowledge Base API ready")

    yield  # Server is running

    # Shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutting down...")


# Need get_read_conn for BM25 rebuild
from .models.database import get_read_conn

app = FastAPI(
    title="Knowledge Base API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
from .api.routes import router
app.include_router(router)


# Global error handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"},
    )
