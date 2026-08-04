# Knowledge Base - API + Streamlit UI shared image
# Build for linux/amd64 on Apple Silicon:
#   docker buildx build --platform linux/amd64 -t knowledge-base:amd64 .
#
# China network: use a reachable base-image mirror by default:
#   docker buildx build --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim ...

ARG BASE_IMAGE=python:3.11-slim

FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# System libraries required by lxml, numpy/torch, opencv and PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# Optional PyPI mirror for China network (e.g. https://pypi.tuna.tsinghua.edu.cn/simple)
ARG PIP_INDEX_URL=https://pypi.org/simple

# Install Python dependencies first (cache-friendly layer)
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt --index-url ${PIP_INDEX_URL}

# Application source
COPY src/ ./src/
COPY start_api.sh start_ui.sh ./
RUN chmod +x start_api.sh start_ui.sh

# Runtime data directories (mounted as volumes in compose)
RUN mkdir -p /app/data /app/uploads /root/.cache

# Default entrypoint: API server. UI service overrides command in compose.
EXPOSE 8000 8501

CMD ["uvicorn", "knowledge_base.main:app", "--host", "0.0.0.0", "--port", "8000"]
