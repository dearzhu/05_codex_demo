"""Local file storage for uploaded documents"""

import os
import shutil
import uuid
import logging
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)


class FileStore:
    """Manage uploaded files on local filesystem"""

    def __init__(self, base_dir: str = ""):
        self.base_dir = Path(base_dir or get_settings().upload_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, filename: str, data: bytes, file_id: str = "") -> str:
        """Save uploaded file. If file_id is provided, use it; otherwise generate."""
        file_id = file_id or str(uuid.uuid4())
        dest = self.base_dir / file_id
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / filename
        with open(path, "wb") as f:
            f.write(data)
        logger.info(f"File saved: {path} ({len(data)} bytes)")
        return file_id

    def get_path(self, file_id: str, filename: str) -> Path:
        return self.base_dir / file_id / filename

    def delete(self, file_id: str):
        path = self.base_dir / file_id
        if path.exists():
            shutil.rmtree(path)
            logger.info(f"File deleted: {path}")

    def get_size(self, file_id: str, filename: str) -> int:
        path = self.get_path(file_id, filename)
        return path.stat().st_size if path.exists() else 0

    def exists(self, file_id: str, filename: str) -> bool:
        return self.get_path(file_id, filename).exists()
