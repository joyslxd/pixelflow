#!/usr/bin/env bash
set -euo pipefail

APP_NAME="agent-dev"
APP_DIR="/home/devops/apps/test/agent"
BACKEND_DIR="${APP_DIR}/backend"
LOG_DIR="${APP_DIR}/logs"
RUN_DIR="${APP_DIR}/run"
PID_FILE="${RUN_DIR}/${APP_NAME}.pid"
LOG_FILE="${LOG_DIR}/${APP_NAME}.log"

stop_pid() {
  local pid="$1"

  if [ -z "${pid}" ]; then
    return 0
  fi

  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[INFO] process not running, pid=${pid}"
    return 0
  fi

  echo "[INFO] stopping ${APP_NAME}, pid=${pid}"
  kill "${pid}" >/dev/null 2>&1 || true

  for _ in $(seq 1 20); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      echo "[INFO] ${APP_NAME} stopped"
      return 0
    fi
    sleep 1
  done

  echo "[WARN] process still alive after 20s, force killing pid=${pid}"
  kill -9 "${pid}" >/dev/null 2>&1 || true
}

if [ -f "${PID_FILE}" ]; then
  PID="$(cat "${PID_FILE}" || true)"
  stop_pid "${PID}"
  rm -f "${PID_FILE}"
  echo "[INFO] log file: ${LOG_FILE}"
  exit 0
fi

echo "[WARN] pid file not found: ${PID_FILE}"
echo "[WARN] trying to find process by backend cwd: ${BACKEND_DIR}"

FOUND=0

for PID in $(pgrep -f "app.gateway.run" || true); do
  if [ -e "/proc/${PID}/cwd" ]; then
    CWD="$(readlink -f "/proc/${PID}/cwd" || true)"
    if [ "${CWD}" = "${BACKEND_DIR}" ]; then
      FOUND=1
      stop_pid "${PID}"
    fi
  fi
done

if [ "${FOUND}" = "0" ]; then
  echo "[INFO] ${APP_NAME} is not running"
fi

rm -f "${PID_FILE}"
echo "[INFO] log file: ${LOG_FILE}"
