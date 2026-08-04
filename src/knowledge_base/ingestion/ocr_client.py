"""
PaddleOCR Client — 调用本地 OCR 服务
"""

import os
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OCR_SERVICE_URL = os.environ.get("OCR_SERVICE_URL", "http://127.0.0.1:8521")


class OCRClient:
    """调用本地部署的 PaddleOCR HTTP 服务"""

    def __init__(self, base_url: str = OCR_SERVICE_URL, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, endpoint: str, file_path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        with open(file_path, "rb") as f:
            files = {"file": (Path(file_path).name, f, "application/octet-stream")}
            resp = httpx.post(url, files=files, params=params or {},
                              timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def ocr_image(self, image_path: str) -> dict:
        """OCR 识别图片"""
        logger.info(f"OCR image: {image_path}")
        return self._call("/ocr/image", image_path)

    def ocr_pdf(self, pdf_path: str, dpi: int = 300) -> dict:
        """OCR 识别 PDF"""
        logger.info(f"OCR PDF: {pdf_path} (dpi={dpi})")
        return self._call("/ocr/pdf", pdf_path, {"dpi": dpi})

    def health(self) -> bool:
        """检查服务是否可用"""
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=5)
            data = resp.json()
            return isinstance(data, dict) and data.get("status") == "ok"
        except Exception:
            return False


# Singleton
_ocr_client: Optional[OCRClient] = None


def get_ocr_client() -> OCRClient:
    global _ocr_client
    if _ocr_client is None:
        _ocr_client = OCRClient()
        if not _ocr_client.health():
            logger.warning("OCR service is not available at %s. "
                           "Run 'bash ocr_service/start.sh' to start it.", OCR_SERVICE_URL)
    return _ocr_client
