"""Multi-level chunking strategies"""

import re
import uuid
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Chunk:
    def __init__(self, chunk_id: str, doc_id: str, content: str,
                 metadata: dict[str, Any], token_count: int = 0):
        self.chunk_id = chunk_id
        self.doc_id = doc_id
        self.content = content
        self.metadata = metadata
        self.token_count = token_count
        self.embedding: list[float] = []

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "content": self.content,
            "metadata": self.metadata,
            "token_count": self.token_count,
        }

    def __repr__(self):
        return f"Chunk(id={self.chunk_id[:8]}, {len(self.content)} chars, doc={self.doc_id[:8]})"


def chunk_document(doc_id: str, content: str, doc_meta: dict | None = None,
                   chunk_size: int = 512, chunk_overlap: int = 128) -> list[Chunk]:
    """Multi-level chunking: semantic (heading) → paragraph → recursive character.

    Level 1: Split by Markdown headings (##, ###, ####).
    Level 2: Split by double newlines (paragraphs).
    Level 3: Recursive character split with overlap.
    """
    doc_meta = doc_meta or {}
    chunks = []

    # Level 1: semantic split by headings
    sections = _split_by_headings(content)
    if len(sections) <= 1:
        # No headings found, try paragraph split
        sections = _split_by_paragraphs(content)

    for heading, text in sections:
        if not text.strip():
            continue

        meta = dict(doc_meta)
        if heading:
            meta["heading"] = heading
            meta["heading_level"] = _heading_level(heading)

        # Level 2: if section is still too long, split by paragraphs
        if len(text) > chunk_size * 1.5:
            paragraphs = _split_by_paragraphs(text)
            for sub_heading, para in paragraphs:
                if not para.strip():
                    continue
                sub_meta = dict(meta)
                if sub_heading:
                    sub_meta["heading"] = sub_heading
                # Level 3: recursive split if still too long
                for piece in _recursive_split(para, chunk_size, chunk_overlap):
                    chunk = Chunk(
                        chunk_id=str(uuid.uuid4()),
                        doc_id=doc_id,
                        content=piece,
                        metadata=dict(sub_meta),
                        token_count=len(piece) // 2,  # rough token count
                    )
                    chunks.append(chunk)
        else:
            for piece in _recursive_split(text, chunk_size, chunk_overlap):
                chunk = Chunk(
                    chunk_id=str(uuid.uuid4()),
                    doc_id=doc_id,
                    content=piece,
                    metadata=dict(meta),
                    token_count=len(piece) // 2,
                )
                chunks.append(chunk)

    if not chunks:
        # Fallback: whole content as one chunk
        chunks.append(Chunk(
            chunk_id=str(uuid.uuid4()),
            doc_id=doc_id,
            content=content[:chunk_size],
            metadata=dict(doc_meta),
            token_count=len(content[:chunk_size]) // 2,
        ))

    logger.info(f"Chunked doc {doc_id[:8]}: {len(chunks)} chunks (size={chunk_size}, overlap={chunk_overlap})")
    return chunks


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """Split by markdown headings (## to ######)"""
    pattern = r"^(#{2,6})\s+(.+)$"
    lines = text.split("\n")
    sections = []
    current_heading = ""
    current_text: list[str] = []

    for line in lines:
        match = re.match(pattern, line.strip())
        if match:
            if current_text:
                sections.append((current_heading, "\n".join(current_text).strip()))
            current_heading = match.group(2).strip()
            current_text = []
        else:
            current_text.append(line)

    if current_text:
        sections.append((current_heading, "\n".join(current_text).strip()))

    return sections


def _split_by_paragraphs(text: str) -> list[tuple[str, str]]:
    """Split by double newlines"""
    paragraphs = re.split(r"\n\s*\n", text)
    return [("", p.strip()) for p in paragraphs if p.strip()]


def _recursive_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Recursive character split with overlap"""
    if len(text) <= chunk_size:
        return [text.strip()]

    # Try to split at sentence boundaries first
    for sep in ["\n\n", "\n", ". ", "。", "，", " "]:
        if sep in text:
            parts = text.rsplit(sep, 1)
            if len(parts) == 2 and len(parts[0]) <= chunk_size and len(parts[0]) > chunk_size // 2:
                first = parts[0].strip()
                rest = parts[1].strip()
                if first:
                    result = [first]
                    result.extend(_recursive_split(rest, chunk_size, overlap))
                    return result

    # Fallback: hard split
    first = text[:chunk_size].strip()
    rest = text[chunk_size - overlap:]
    if first:
        return [first] + _recursive_split(rest, chunk_size, overlap)
    return []


def _heading_level(heading: str) -> int:
    """Infer heading level from heading text context"""
    return 2  # default
