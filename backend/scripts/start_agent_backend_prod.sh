#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${AGENT_APP_NAME:-agent-prod}"
APP_DIR="${AGENT_APP_DIR:-/home/devops/apps/prod/agent}"
BACKEND_DIR="${APP_DIR}/backend"
LOG_DIR="${APP_DIR}/logs"
RUN_DIR="${APP_DIR}/run"
PID_FILE="${RUN_DIR}/${APP_NAME}.pid"
LOG_FILE="${LOG_DIR}/${APP_NAME}.log"

mkdir -p "${LOG_DIR}" "${RUN_DIR}"

if [ ! -d "${BACKEND_DIR}" ]; then
  echo "[ERROR] backend directory not found: ${BACKEND_DIR}"
  exit 1
fi

if [ ! -f "${BACKEND_DIR}/config.prod.yml" ]; then
  echo "[ERROR] config.prod.yml not found: ${BACKEND_DIR}/config.prod.yml"
  exit 1
fi

if [ -f "${PID_FILE}" ]; then
  OLD_PID="$(cat "${PID_FILE}" || true)"
  if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" >/dev/null 2>&1; then
    echo "[INFO] ${APP_NAME} is already running, pid=${OLD_PID}"
    echo "[INFO] log file: ${LOG_FILE}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
elif [ -x "/home/devops/.local/bin/uv" ]; then
  UV_BIN="/home/devops/.local/bin/uv"
else
  echo "[ERROR] uv not found. Please install uv first."
  exit 1
fi

cd "${BACKEND_DIR}"

{
  echo ""
  echo "========== $(date '+%Y-%m-%d %H:%M:%S') starting ${APP_NAME} =========="
  echo "[INFO] backend dir: ${BACKEND_DIR}"
  echo "[INFO] uv: ${UV_BIN}"
  echo "[INFO] running uv sync..."
} >> "${LOG_FILE}" 2>&1

"${UV_BIN}" sync >> "${LOG_FILE}" 2>&1

nohup env \
  PIXELFLOW_CONFIG_ENV=prod \
  PYTHONPATH=. \
  PYTHONIOENCODING=utf-8 \
  PYTHONUTF8=1 \
  "${UV_BIN}" run --no-sync python -m app.gateway.run \
  >> "${LOG_FILE}" 2>&1 &

PID="$!"
echo "${PID}" > "${PID_FILE}"

sleep 2

if kill -0 "${PID}" >/dev/null 2>&1; then
  echo "[INFO] ${APP_NAME} started, pid=${PID}"
  echo "[INFO] log file: ${LOG_FILE}"
  echo "[INFO] health check: curl http://127.0.0.1:8001/health"
else
  echo "[ERROR] ${APP_NAME} failed to start. Recent logs:"
  tail -n 80 "${LOG_FILE}"
  rm -f "${PID_FILE}"
  exit 1
fi
