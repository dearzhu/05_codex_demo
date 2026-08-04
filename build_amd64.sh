#!/usr/bin/env bash
set -euo pipefail

# Build linux/amd64 Docker images on Apple Silicon (M1/M2/M3/M4)
#
# China network defaults:
#   - Base image: docker.m.daocloud.io/library/python:3.11-slim
#   - PyPI index: https://pypi.tuna.tsinghua.edu.cn/simple
# Override with env vars, e.g.:
#   BASE_IMAGE=python:3.11-slim PIP_INDEX_URL=https://pypi.org/simple ./build_amd64.sh
#
# Usage:
#   ./build_amd64.sh                                        # build locally
#   PUSH=1 REGISTRY=youruser ./build_amd64.sh               # build and push to Docker Hub
#   REGISTRY=registry.example.com ./build_amd64.sh          # build and push to private registry
#
# After a local build, images are saved to the current directory as:
#   kb-api.tar  (knowledge-base:amd64)
#   kb-ocr.tar  (knowledge-base-ocr:amd64)

PLATFORM="linux/amd64"
REGISTRY="${REGISTRY:-}"
PUSH="${PUSH:-0}"
BASE_IMAGE="${BASE_IMAGE:-docker.m.daocloud.io/library/python:3.11-slim}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
API_IMAGE="${REGISTRY:+${REGISTRY}/}knowledge-base:amd64"
OCR_IMAGE="${REGISTRY:+${REGISTRY}/}knowledge-base-ocr:amd64"

echo "==> Base image: ${BASE_IMAGE}"
echo "==> PyPI index: ${PIP_INDEX_URL}"

echo "==> Using builder for ${PLATFORM}"
docker buildx create --name amd64-builder --use >/dev/null 2>&1 || true
docker buildx use amd64-builder

echo "==> Building API/UI image: ${API_IMAGE}"
if [ "$PUSH" = "1" ]; then
  docker buildx build --platform "${PLATFORM}" --push \
    --build-arg BASE_IMAGE="${BASE_IMAGE}" \
    --build-arg PIP_INDEX_URL="${PIP_INDEX_URL}" \
    -t "${API_IMAGE}" -f Dockerfile .
else
  docker buildx build --platform "${PLATFORM}" --load \
    --build-arg BASE_IMAGE="${BASE_IMAGE}" \
    --build-arg PIP_INDEX_URL="${PIP_INDEX_URL}" \
    -t "${API_IMAGE}" -f Dockerfile .
fi

if [ "$PUSH" = "1" ]; then
  echo "==> PUSH=1: skipping local save for API/UI image"
else
  echo "==> Saving API/UI image to ./kb-api.tar"
  docker save "${API_IMAGE}" -o kb-api.tar
fi

echo "==> Building OCR image: ${OCR_IMAGE}"
if [ "$PUSH" = "1" ]; then
  docker buildx build --platform "${PLATFORM}" --push \
    --build-arg BASE_IMAGE="${BASE_IMAGE}" \
    --build-arg PIP_INDEX_URL="${PIP_INDEX_URL}" \
    -t "${OCR_IMAGE}" -f Dockerfile.ocr .
else
  docker buildx build --platform "${PLATFORM}" --load \
    --build-arg BASE_IMAGE="${BASE_IMAGE}" \
    --build-arg PIP_INDEX_URL="${PIP_INDEX_URL}" \
    -t "${OCR_IMAGE}" -f Dockerfile.ocr .
fi

if [ "$PUSH" = "1" ]; then
  echo "==> PUSH=1: skipping local save for OCR image"
else
  echo "==> Saving OCR image to ./kb-ocr.tar"
  docker save "${OCR_IMAGE}" -o kb-ocr.tar
fi

echo ""
echo "Build complete:"
echo "  ${API_IMAGE}"
echo "  ${OCR_IMAGE}"
if [ "$PUSH" = "1" ]; then
  echo "  Images pushed."
else
  echo "  $(pwd)/kb-api.tar"
  echo "  $(pwd)/kb-ocr.tar"
  echo ""
  echo "  Image tarballs saved in the current directory."
fi
