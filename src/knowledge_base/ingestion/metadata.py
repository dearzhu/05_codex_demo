"""Metadata extraction from documents"""

import re
from pathlib import Path


def extract_metadata(file_path: str, content: str = "") -> dict:
    """Extract basic metadata from file path and content"""
    path = Path(file_path)

    meta = {
        "filename": path.name,
        "filetype": path.suffix.lower().lstrip("."),
        "size": path.stat().st_size if path.exists() else 0,
    }

    # Extract title from first heading / first line
    if content:
        lines = content.strip().split("\n")
        title = ""
        for line in lines:
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                break
            if line.startswith("=== ") or line.startswith("--- "):
                title = line.strip("=- ").strip()
                break
        if not title:
            title = lines[0].strip()[:100]
        meta["title"] = title

        # Date patterns
        date_patterns = [
            r"\d{4}-\d{2}-\d{2}",
            r"\d{4}/\d{2}/\d{2}",
            r"\d{4}年\d{1,2}月\d{1,2}日",
        ]
        for pat in date_patterns:
            match = re.search(pat, content)
            if match:
                meta["date"] = match.group()
                break

        # Estimate page count (rough: ~2000 chars per page)
        meta["estimated_pages"] = max(1, len(content) // 2000)

    return meta
