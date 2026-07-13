#!/bin/bash
set -e
PORT=${API_PORT:-8000}
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "================================================"
echo "  企业知识库检索系统 - API 服务"
echo "  Port: $PORT"
echo "================================================"
echo ""

# Ensure dependencies installed
if [ ! -d ".venv" ]; then
    echo "[setup] Installing dependencies..."
    uv sync
fi

# Clean old data for fresh start (optional: remove this for production)
# rm -f data/kb.db 2>/dev/null
# rm -rf data/chromadb 2>/dev/null

echo "[start] Starting FastAPI server..."
echo ""

PYTHONPATH=src exec uv run uvicorn knowledge_base.main:app \
    --host 0.0.0.0 --port "$PORT" --reload
