#!/usr/bin/env bash
# 用途：在 Linux/ARM64 或 Linux/x86_64 镜像内执行 M4 真实 Sidecar 验收；影响：会发起受预算的真实模型请求。
set -euo pipefail

# 用途：要求显式确认真实用例；影响：未确认时拒绝执行，避免本地或 CI 意外消耗模型额度。
: "${PIXELFLOW_RUN_REAL_M4:?请显式设置为 1}"
if [[ "$PIXELFLOW_RUN_REAL_M4" != "1" ]]; then
  echo "PIXELFLOW_RUN_REAL_M4 必须为 1" >&2
  exit 2
fi

# 用途：复用部署环境的 Sidecar Secret；影响：缺少任一项即失败，不打印其值。
: "${DEEPSEEK_API_KEY:?缺少模型密钥}"
: "${DEEPSEEK_BASE_URL:?缺少模型端点}"
: "${PIXELFLOW_AGENT_HOME:?缺少活动 Skill 根}"
: "${PIXELFLOW_REAL_BORGRISE_AUTHORIZATION:?缺少隔离用户 Authorization}"
: "${PIXELFLOW_BACKEND_ROOT:?缺少 Gateway 源码根目录}"

uv sync --locked
PIXELFLOW_RUN_REAL_M0=1 uv run pytest -q -m m0_real tests/test_m0_real_sidecar_http.py
uv run pytest -q tests/test_m0_contracts.py tests/test_m2_sidecar_http.py
# 用途：在同一 Linux 架构启动真实 Gateway、Sidecar、模型和 Tool Broker；影响：验证模型自主多步非计费 Tool、notification 双流、SSE 断线续传与 Gateway 重启。
(
  cd "$PIXELFLOW_BACKEND_ROOT"
  PIXELFLOW_RUN_REAL_M0=1 PIXELFLOW_RUN_REAL_M4=1 uv run pytest -q -m m4_real tests/test_m0_real_public_harness_turn.py
)
