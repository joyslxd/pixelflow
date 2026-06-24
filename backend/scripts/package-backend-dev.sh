#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$0"
SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_DIR="$(cd "${BACKEND_DIR}/.." && pwd)"

PACKAGE_NAME="${1:-pixelflow-backend-dev.tar.gz}"
PACKAGE_DIR="${BACKEND_DIR}/dist"
PACKAGE_PATH="${PACKAGE_DIR}/${PACKAGE_NAME}"

mkdir -p "${PACKAGE_DIR}"
rm -f "${PACKAGE_PATH}"

cd "${PROJECT_DIR}"

LC_ALL=C LANG=C tar \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='.DS_Store' \
  -czf "${PACKAGE_PATH}" \
  backend/app \
  backend/pixelflow \
  backend/packages \
  backend/skills \
  backend/config.dev.yml \
  backend/pyproject.toml \
  backend/uv.lock \
  backend/.python-version \
  backend/Makefile \
  backend/langgraph.json \
  backend/README.md

echo "PixelFlow dev backend package created:"
echo "${PACKAGE_PATH}"
