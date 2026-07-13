"""
PaddleOCR Service v4 — 基于 Python http.server（不依赖 FastAPI，更稳定）
PaddleOCR 3.7 API: 使用 predict() 方法，通过 dict-like 接口访问结果。
"""
import os, sys, json, io, tempfile, time, logging, traceback, signal
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ocr-service")

# Global OCR instance
_ocr = None

def get_ocr():
    global _ocr
    if _ocr is None:
        logger.info("Loading PaddleOCR (may take ~10s on first launch)...")
        from paddleocr import PaddleOCR
        _ocr = PaddleOCR(lang="ch")
        logger.info("PaddleOCR ready")
    return _ocr

class OCRHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(f"{self.client_address[0]} - {fmt % args}")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_file(self):
        """Parse multipart file upload"""
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)
        ctype = self.headers.get("Content-Type", "")
        boundary = ctype.split("boundary=")[-1].strip().strip('"').encode() if "boundary=" in ctype else b
        if not boundary:
            return None, None
        parts = body.split(b"--" + boundary)
        for part in parts:
            if b'Content-Disposition' in part and b'filename=' in part:
                header_end = part.find(b"\r\n\r\n") + 4
                file_data = part[header_end:part.rfind(b"\r\n--")]
                name_part = part[:header_end].decode("utf-8", errors="ignore")
                fn_start = name_part.find('filename="') + 10
                fn_end = name_part.find('"', fn_start)
                fname = name_part[fn_start:fn_end]
                return fname, file_data
        return None, None

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok", "service": "paddleocr"})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/ocr/image":
            self._handle_ocr_image()
        elif self.path == "/ocr/pdf":
            self._handle_ocr_pdf()
        else:
            self._send_json({"error": "not found"}, 404)

    def _run_ocr(self, image_path):
        """Run OCR on a single image, return (items, full_text)"""
        ocr = get_ocr()
        start = time.time()
        result = ocr.predict(image_path)
        elapsed = time.time() - start

        items = []
        text_parts = []

        if result and len(result) > 0:
            page = result[0]
            texts = page.get("rec_texts") or []
            scores = page.get("rec_scores") or []
            polys = page.get("dt_polys") or []

            for i, text in enumerate(texts):
                score = float(scores[i]) if i < len(scores) else 0.0
                poly = polys[i] if i < len(polys) else []
                bbox = []
                if poly is not None and len(poly) > 0:
                    import numpy as np
                    bbox = [[float(x), float(y)] for x, y in np.array(poly)]
                items.append({
                    "text": text,
                    "confidence": round(score, 4),
                    "bbox": bbox,
                })
                text_parts.append(text)

            full_text = "\n".join(text_parts)
            logger.info(f"OCR: {len(items)} lines in {elapsed:.1f}s")
            return items, full_text
        else:
            logger.info(f"OCR: 0 lines in {elapsed:.1f}s")
            return [], ""

    def _handle_ocr_image(self):
        tmp_path = None
        try:
            fname, data = self._read_file()
            if not data:
                self._send_json({"success": False, "error": "no file"}, 400)
                return

            suffix = Path(fname or "image.png").suffix or ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

            logger.info(f"Processing: {fname} ({len(data)} bytes)")
            items, full_text = self._run_ocr(tmp_path)
            self._send_json({"success": True, "results": items, "full_text": full_text, "pages": 1})

        except Exception as e:
            logger.error(f"OCR error: {e}\n{traceback.format_exc()}")
            self._send_json({"success": False, "results": [], "full_text": "", "error": str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _handle_ocr_pdf(self):
        pdf_path = None
        try:
            fname, data = self._read_file()
            if not data:
                self._send_json({"success": False, "error": "no file"}, 400)
                return

            import fitz
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(data)
                pdf_path = tmp.name

            doc = fitz.open(pdf_path)
            all_items = []
            all_parts = []

            for pn in range(len(doc)):
                pix = doc.load_page(pn).get_pixmap(dpi=300)
                img_bytes = pix.tobytes("png")
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as it:
                    it.write(img_bytes)
                    ipath = it.name
                try:
                    items, _ = self._run_ocr(ipath)
                    all_items.extend(items)
                    all_parts.append(f"--- Page {pn+1} ---\n" + "\n".join(i["text"] for i in items))
                finally:
                    os.unlink(ipath)

            doc.close()
            full_text = "\n\n".join(all_parts)
            logger.info(f"PDF done: {len(doc)} pages, {len(all_items)} lines")
            self._send_json({"success": True, "results": all_items, "full_text": full_text, "pages": len(doc)})

        except ImportError:
            self._send_json({"success": False, "error": "PyMuPDF not installed. pip install PyMuPDF"})
        except Exception as e:
            logger.error(f"PDF OCR error: {e}\n{traceback.format_exc()}")
            self._send_json({"success": False, "error": str(e)})
        finally:
            if pdf_path and os.path.exists(pdf_path):
                os.unlink(pdf_path)


def run_server(port=8521):
    server = HTTPServer(("0.0.0.0", port), OCRHandler)
    logger.info(f"PaddleOCR Service running on http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        logger.info("Server stopped")

if __name__ == "__main__":
    port = int(os.environ.get("OCR_SERVICE_PORT", 8521))
    run_server(port)
