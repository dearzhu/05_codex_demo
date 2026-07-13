#!/bin/bash
set -e
PORT=${UI_PORT:-8501}
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "================================================"
echo "  企业知识库检索系统 - Web UI"
echo "  Port: $PORT"
echo "================================================"
echo ""

# Ensure dependencies installed
if [ ! -d ".venv" ]; then
    echo "[setup] Installing dependencies..."
    uv sync
fi

# Need API to be running first
echo "[info] Make sure API server is running on port ${API_PORT:-8000}"
echo ""

exec uv run streamlit run src/knowledge_base/ui/app.py \
    --server.port "$PORT"
