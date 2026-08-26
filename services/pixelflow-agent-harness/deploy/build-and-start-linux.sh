#!/usr/bin/env bash
# 用途：按当前源码构建并仅启动 PixelFlow Gateway 与 Harness Sidecar；影响：不管理 Nginx、数据库或其他 Compose 服务。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/services/pixelflow-agent-harness/deploy"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.linux.yml"
# 用途：为网络较慢的服务器增加 wheel 下载等待；影响：仅在构建阶段生效，默认 300 秒。
UV_HTTP_TIMEOUT_SECONDS="${PIXELFLOW_UV_HTTP_TIMEOUT:-300}"

cd "$DEPLOY_DIR"

mapfile -t IMAGES < <(docker compose -f "$COMPOSE_FILE" config --images)
if [[ "${#IMAGES[@]}" -ne 2 || -z "${IMAGES[0]}" || -z "${IMAGES[1]}" ]]; then
  echo "Compose 镜像配置不完整，拒绝构建。" >&2
  exit 1
fi

GATEWAY_IMAGE="${IMAGES[0]}"
SIDECAR_IMAGE="${IMAGES[1]}"

# 用途：构建 Gateway 当前源码镜像；影响：复用 Docker/uv 缓存，下载超时由上方变量控制。
docker build \
  --build-arg "UV_HTTP_TIMEOUT=$UV_HTTP_TIMEOUT_SECONDS" \
  -t "$GATEWAY_IMAGE" \
  -f "$ROOT_DIR/backend/Dockerfile" \
  "$ROOT_DIR"

# 用途：构建离线 Runtime Sidecar 镜像；影响：官方 Runtime 只从服务目录的已校验 wheel 安装。
docker build \
  -t "$SIDECAR_IMAGE" \
  "$ROOT_DIR/services/pixelflow-agent-harness"

# 用途：只重建两个新架构服务；影响：不停止同一 Compose 项目中的其他服务。
docker compose -f "$COMPOSE_FILE" up -d --force-recreate gateway harness-sidecar

# 用途：确认进程与 Harness 装配均已就绪；影响：任一端点未就绪时脚本失败，不报告成功切流。
for URL in \
  "http://127.0.0.1:8001/live" \
  "http://127.0.0.1:8001/ready" \
  "http://127.0.0.1:8090/live" \
  "http://127.0.0.1:8090/ready"; do
  for ATTEMPT in $(seq 1 30); do
    if curl -fsS --max-time 5 "$URL" >/dev/null; then
      break
    fi
    if [[ "$ATTEMPT" -eq 30 ]]; then
      echo "健康检查失败：$URL" >&2
      exit 1
    fi
    sleep 2
  done
done

echo "Gateway 与 Harness Sidecar 已构建、启动并通过健康检查。"
