"""SQLite database schema and connection pool"""

import os
import sqlite3
import queue
import threading
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

from ..config import get_settings

logger = logging.getLogger(__name__)

_READ_POOL: queue.Queue[sqlite3.Connection] = queue.Queue()
_WRITE_LOCK = threading.Lock()
_db_path: Optional[str] = None


def init_db(db_path: Optional[str] = None, pool_size: int = 4):
    global _db_path
    _db_path = db_path or get_settings().db_path
    os.makedirs(Path(_db_path).parent, exist_ok=True)

    for _ in range(pool_size):
        conn = sqlite3.connect(_db_path, check_same_thread=False, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        _READ_POOL.put(conn)

    _create_tables()
    logger.info(f"Database initialized: {_db_path} (pool={pool_size})")


def _create_tables():
    conn = get_write_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                filetype TEXT,
                size INTEGER,
                upload_time TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                chunk_count INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                creator_id TEXT DEFAULT 'anonymous',
                started_at TEXT,
                completed_at TEXT,
                sha256 TEXT,
                retry_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                token_count INTEGER DEFAULT 0,
                FOREIGN KEY (doc_id) REFERENCES documents(id)
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                department TEXT DEFAULT 'default',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS search_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                results_count INTEGER DEFAULT 0,
                latency_ms REAL DEFAULT 0,
                timestamp TEXT DEFAULT (datetime('now')),
                user_id TEXT DEFAULT 'anonymous'
            );

            CREATE TABLE IF NOT EXISTS embedding_cache (
                text_hash TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                model_version TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS ocr_cache (
                image_hash TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
            CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
        """)
        conn.commit()
    finally:
        release_write_conn(conn)


@contextmanager
def get_read_conn():
    conn = _READ_POOL.get()
    try:
        yield conn
    finally:
        _READ_POOL.put(conn)


def get_write_conn() -> sqlite3.Connection:
    _WRITE_LOCK.acquire()
    conn = sqlite3.connect(_db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def release_write_conn(conn: sqlite3.Connection):
    try:
        conn.close()
    finally:
        _WRITE_LOCK.release()


@contextmanager
def write_transaction():
    conn = get_write_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_write_conn(conn)
