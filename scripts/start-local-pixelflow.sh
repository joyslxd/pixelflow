#!/usr/bin/env bash
# 用途：在本机一次性装配并启动 PixelFlow Gateway 与真实 Harness Sidecar；影响：仅创建本地进程和临时日志，不触发模型请求。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
SIDECAR_DIR="$ROOT_DIR/services/pixelflow-agent-harness"
LOG_DIR="${PIXELFLOW_LOCAL_LOG_DIR:-${TMPDIR:-/tmp}/pixelflow-local}"
DATA_DIR="${PIXELFLOW_LOCAL_DATA_DIR:-$ROOT_DIR/.pixelflow/dev-data}"
GATEWAY_LOG="$LOG_DIR/gateway.log"
SIDECAR_LOG="$LOG_DIR/sidecar.log"
GATEWAY_PID_FILE="$LOG_DIR/gateway.pid"
SIDECAR_PID_FILE="$LOG_DIR/sidecar.pid"
GATEWAY_ENV_FILE="$BACKEND_DIR/.env"
SIDECAR_ENV_FILE="$SIDECAR_DIR/.env"
GATEWAY_PID=""
SIDECAR_PID=""

cleanup_on_failure=1

stop_started_process() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  if [[ "$cleanup_on_failure" -eq 1 ]]; then
    stop_started_process "$GATEWAY_PID"
    stop_started_process "$SIDECAR_PID"
  fi
}

load_service_env() {
  local service_name="$1"
  local env_file="$2"
  local example_file="${env_file}.example"
  # 用途：保留开发者在终端临时注入的 Secret；影响：模板中的空占位不会覆盖手工输入。
  local inherited_deepseek_api_key="${DEEPSEEK_API_KEY:-}"
  local inherited_mem0_api_key="${PIXELFLOW_VOLCENGINE_MEM0_API_KEY:-}"
  local inherited_memory_user_salt="${PIXELFLOW_LONG_TERM_MEMORY_USER_SALT:-}"
  local inherited_memory_enabled="${PIXELFLOW_LONG_TERM_MEMORY_ENABLED:-}"
  local inherited_mem0_base_url="${PIXELFLOW_VOLCENGINE_MEM0_BASE_URL:-}"
  if [[ ! -f "$example_file" ]]; then
    echo "缺少 ${service_name} 配置模板：${example_file}" >&2
    exit 2
  fi
  if [[ ! -f "$env_file" ]]; then
    echo "缺少 ${service_name} 本地配置：${env_file}；请从同目录 .env.example 复制后填写 Secret。" >&2
    exit 2
  fi
  set -a
  # .env.example 只保存非敏感默认值；.env 为开发者私有受保护覆盖，禁止提交。
  # shellcheck disable=SC1090
  source "$example_file"
  # shellcheck disable=SC1090
  source "$env_file"
  set +a

  # 终端临时注入优先于文件：便于使用 read -r -s 输入 Key，且不把 Secret 落盘。
  if [[ "$service_name" == "Sidecar" && -n "$inherited_deepseek_api_key" ]]; then
    export DEEPSEEK_API_KEY="$inherited_deepseek_api_key"
  fi
  if [[ "$service_name" == "Gateway" ]]; then
    if [[ -n "$inherited_mem0_api_key" ]]; then
      export PIXELFLOW_VOLCENGINE_MEM0_API_KEY="$inherited_mem0_api_key"
    fi
    if [[ -n "$inherited_memory_user_salt" ]]; then
      export PIXELFLOW_LONG_TERM_MEMORY_USER_SALT="$inherited_memory_user_salt"
    fi
    if [[ -n "$inherited_memory_enabled" ]]; then
      export PIXELFLOW_LONG_TERM_MEMORY_ENABLED="$inherited_memory_enabled"
    fi
    if [[ -n "$inherited_mem0_base_url" ]]; then
      export PIXELFLOW_VOLCENGINE_MEM0_BASE_URL="$inherited_mem0_base_url"
    fi
  fi
}

require_env() {
  local variable_name="$1"
  if [[ -z "${!variable_name:-}" ]]; then
    echo "缺少必填配置：${variable_name}" >&2
    exit 2
  fi
}

validate_gateway_env() {
  require_env PIXELFLOW_CONFIG_ENV
  require_env PIXELFLOW_HARNESS_SIDECAR_BASE_URL
  require_env PIXELFLOW_GATEWAY_INSTANCE_ID
  require_env PIXELFLOW_HARNESS_PROFILE_NAME
  require_env PIXELFLOW_HARNESS_MODEL_ID
  require_env PIXELFLOW_HARNESS_CAPABILITY_VERSION
  require_env PIXELFLOW_HARNESS_BUDGET_VERSION
}

warn_gateway_memory_unavailable() {
  case "${PIXELFLOW_LONG_TERM_MEMORY_ENABLED:-true}" in
    1|true|TRUE|yes|YES|on|ON)
      if [[ -z "${PIXELFLOW_VOLCENGINE_MEM0_BASE_URL:-}" || -z "${PIXELFLOW_VOLCENGINE_MEM0_API_KEY:-}" || -z "${PIXELFLOW_LONG_TERM_MEMORY_USER_SALT:-}" ]]; then
        echo "Mem0 配置不完整，Gateway 将以 fail-open 模式启动；如需启用请在 backend/.env 填写 Base URL、API Key 和用户匿名化盐。" >&2
      fi
      ;;
    0|false|FALSE|no|NO|off|OFF)
      ;;
    *)
      echo "PIXELFLOW_LONG_TERM_MEMORY_ENABLED 必须为 true 或 false。" >&2
      exit 2
      ;;
  esac
}

validate_sidecar_env() {
  require_env PIXELFLOW_AGENT_HOME
  require_env PIXELFLOW_TOOL_BROKER_BASE_URL
  require_env PIXELFLOW_SIDECAR_INSTANCE_ID
  require_env PIXELFLOW_HARNESS_PROFILE_NAME
  require_env PIXELFLOW_HARNESS_MODEL_ID
  require_env PIXELFLOW_HARNESS_CAPABILITY_VERSION
  require_env PIXELFLOW_HARNESS_BUDGET_VERSION
  require_env DEEPSEEK_API_KEY
  require_env DEEPSEEK_BASE_URL
}

profile_signature() {
  printf '%s|%s|%s|%s' \
    "$PIXELFLOW_HARNESS_PROFILE_NAME" \
    "$PIXELFLOW_HARNESS_MODEL_ID" \
    "$PIXELFLOW_HARNESS_CAPABILITY_VERSION" \
    "$PIXELFLOW_HARNESS_BUDGET_VERSION"
}

mkdir -p "$LOG_DIR" "$DATA_DIR"

GATEWAY_PROFILE_SIGNATURE="$(
  (
    load_service_env "Gateway" "$GATEWAY_ENV_FILE"
    validate_gateway_env
    warn_gateway_memory_unavailable
    profile_signature
  )
)"
SIDECAR_PROFILE_SIGNATURE="$(
  (
    load_service_env "Sidecar" "$SIDECAR_ENV_FILE"
    validate_sidecar_env
    profile_signature
  )
)"
if [[ "$GATEWAY_PROFILE_SIGNATURE" != "$SIDECAR_PROFILE_SIGNATURE" ]]; then
  echo "Gateway 与 Sidecar 的 Harness 模型档案配置不一致。" >&2
  exit 2
fi

# 用途：使用本地临时服务身份；影响：每次启动生成新值，不持久化共享 JWT。
GATEWAY_JWT_KEY="${PIXELFLOW_GATEWAY_JWT_SIGNING_KEY:-$(openssl rand -hex 32)}"
TOOL_BROKER_JWT_KEY="${PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY:-$(openssl rand -hex 32)}"

# 用途：从 Gateway profile 读取唯一 Run limits；影响：Sidecar 使用同一份结果，避免在启动脚本重复维护配置。
RUN_LIMIT_PROFILES="$(
  (
    load_service_env "Gateway" "$GATEWAY_ENV_FILE"
    cd "$BACKEND_DIR"
    PYTHONPATH=. uv run --no-sync python -c 'from app.gateway.profile_config import load_profile_config; import os; load_profile_config(); print(os.environ["PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES"])'
  )
)"

# 用途：从当前源码计算唯一 Tool Manifest 摘要；影响：Gateway/Sidecar 拒绝旧进程或旧 Tool 集参与本次联调。
MANIFEST_DIGEST="$(
  (
    load_service_env "Gateway" "$GATEWAY_ENV_FILE"
    cd "$BACKEND_DIR"
    PYTHONPATH=. uv run --no-sync python -c 'from pixelflow.agent_tools.manifest import manifest; print(manifest().digest)'
  )
)"

if lsof -nP -iTCP:8001 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 8001 已被占用；请先确认并停止属于 PixelFlow Gateway 的进程。" >&2
  exit 3
fi
if lsof -nP -iTCP:8090 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 8090 已被占用；请先确认并停止属于 PixelFlow Sidecar 的进程。" >&2
  exit 3
fi

# 用途：仅在配置、摘要与端口预检完成后才清理本次新进程；影响：缺少配置不会停止已有服务。
trap cleanup EXIT
rm -f "$GATEWAY_LOG" "$SIDECAR_LOG"

(
  load_service_env "Sidecar" "$SIDECAR_ENV_FILE"
  validate_sidecar_env
  # 用途：把 Sidecar .env 中的相对 Skill 根解析为当前仓库绝对路径；影响：不扫描宿主其它目录。
  if [[ "$PIXELFLOW_AGENT_HOME" != /* ]]; then
    export PIXELFLOW_AGENT_HOME="$ROOT_DIR/$PIXELFLOW_AGENT_HOME"
  fi
  export PIXELFLOW_HARNESS_RUN_STORE="$DATA_DIR/sidecar-runs.sqlite3"
  export PIXELFLOW_GATEWAY_JWT_VERIFY_KEY="$GATEWAY_JWT_KEY"
  export PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY="$TOOL_BROKER_JWT_KEY"
  export PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST="$MANIFEST_DIGEST"
  export PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES="$RUN_LIMIT_PROFILES"
  cd "$SIDECAR_DIR"
  exec env PYTHONPATH=src uv run --no-sync uvicorn pixelflow_harness_sidecar.app:create_app \
    --factory --host 127.0.0.1 --port 8090
) >"$SIDECAR_LOG" 2>&1 < /dev/null &
SIDECAR_PID=$!
printf '%s\n' "$SIDECAR_PID" > "$SIDECAR_PID_FILE"

(
  load_service_env "Gateway" "$GATEWAY_ENV_FILE"
  validate_gateway_env
  warn_gateway_memory_unavailable
  export PIXELFLOW_GATEWAY_JWT_SIGNING_KEY="$GATEWAY_JWT_KEY"
  export PIXELFLOW_TOOL_BROKER_JWT_VERIFY_KEY="$TOOL_BROKER_JWT_KEY"
  export PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST="$MANIFEST_DIGEST"
  export PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES="$RUN_LIMIT_PROFILES"
  cd "$BACKEND_DIR"
  exec env PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
    uv run --no-sync python -m app.gateway.run
) >"$GATEWAY_LOG" 2>&1 < /dev/null &
GATEWAY_PID=$!
printf '%s\n' "$GATEWAY_PID" > "$GATEWAY_PID_FILE"

wait_ready() {
  local url="$1"
  local attempt
  for attempt in $(seq 1 30); do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "健康检查失败：$url；请查看 $LOG_DIR 中对应日志。" >&2
  return 1
}

wait_ready "http://127.0.0.1:8090/live"
wait_ready "http://127.0.0.1:8090/ready"
wait_ready "http://127.0.0.1:8001/live"
wait_ready "http://127.0.0.1:8001/ready"

cleanup_on_failure=0
echo "PixelFlow Gateway 与真实 Harness Sidecar 已启动。"
echo "Gateway PID: ${GATEWAY_PID}，端口：8001"
echo "Sidecar PID: ${SIDECAR_PID}，端口：8090"
echo "日志目录：$LOG_DIR"
echo "本次启动未发起 DeepSeek 或任何 Provider 请求。"
