"""SQLite-based metadata store for documents, chunks, logs"""

import json
import uuid
import logging
from datetime import datetime
from typing import Optional

from ..models.database import get_read_conn, write_transaction

logger = logging.getLogger(__name__)


class MetadataStore:
    """CRUD operations for document and chunk metadata"""

    # ── Document ──

    def create_document(self, filename: str, filetype: str, size: int,
                        tags: list[str] | None = None,
                        creator_id: str = "anonymous",
                        sha256: str = "") -> str:
        doc_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with write_transaction() as conn:
            conn.execute(
                """INSERT INTO documents (id, filename, filetype, size, upload_time,
                   status, tags, creator_id, sha256)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (doc_id, filename, filetype, size, now,
                 json.dumps(tags or [], ensure_ascii=False), creator_id, sha256),
            )
        return doc_id

    def update_document_status(self, doc_id: str, status: str,
                                error_message: str = "", chunk_count: int = 0):
        with write_transaction() as conn:
            now = datetime.now().isoformat()
            fields = ["status = ?"]
            params = [status]

            if status == "parsing":
                fields.append("started_at = ?")
                params.append(now)
            if status in ("completed", "failed"):
                fields.append("completed_at = ?")
                params.append(now)
                if status == "failed":
                    fields.append("retry_count = retry_count + 1")

            if error_message:
                fields.append("error_message = ?")
                params.append(error_message)
            if chunk_count:
                fields.append("chunk_count = ?")
                params.append(chunk_count)

            params.append(doc_id)
            conn.execute(f"UPDATE documents SET {', '.join(fields)} WHERE id = ?", params)

    def get_document(self, doc_id: str) -> Optional[dict]:
        with get_read_conn() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def get_all_documents(self, status: str = "") -> list[dict]:
        with get_read_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM documents WHERE status = ? ORDER BY upload_time DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM documents ORDER BY upload_time DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_document(self, doc_id: str):
        with write_transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    # ── Chunk ──

    def save_chunks(self, chunks: list):
        with write_transaction() as conn:
            for c in chunks:
                conn.execute(
                    """INSERT OR IGNORE INTO chunks (id, doc_id, content, metadata_json, token_count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (c.chunk_id, c.doc_id, c.content,
                     json.dumps(c.metadata, ensure_ascii=False), c.token_count),
                )

    def get_chunks_by_doc(self, doc_id: str) -> list[dict]:
        with get_read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE doc_id = ? ORDER BY rowid", (doc_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Tag ──

    def get_all_tags(self) -> list[str]:
        with get_read_conn() as conn:
            rows = conn.execute("SELECT tags FROM documents").fetchall()
            tags = set()
            for r in rows:
                try:
                    for t in json.loads(r["tags"]):
                        tags.add(t)
                except Exception:
                    pass
            return sorted(tags)

    # ── Search Log ──

    def log_search(self, query: str, results_count: int, latency_ms: float,
                   user_id: str = "anonymous"):
        with write_transaction() as conn:
            conn.execute(
                "INSERT INTO search_logs (query, results_count, latency_ms, user_id) VALUES (?, ?, ?, ?)",
                (query, results_count, latency_ms, user_id),
            )


_default_store = None


def get_metadata_store() -> MetadataStore:
    global _default_store
    if _default_store is None:
        _default_store = MetadataStore()
    return _default_store
