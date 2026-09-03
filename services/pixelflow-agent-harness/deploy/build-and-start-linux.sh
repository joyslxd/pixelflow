#!/usr/bin/env bash
# 用途：按当前源码构建并仅启动 PixelFlow Gateway 与 Harness Sidecar；影响：不管理 Nginx、数据库或其他 Compose 服务。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/services/pixelflow-agent-harness/deploy"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.linux.yml"
# 用途：为网络较慢的服务器增加 wheel 下载等待；影响：仅在构建阶段生效，默认 300 秒。
UV_HTTP_TIMEOUT_SECONDS="${PIXELFLOW_UV_HTTP_TIMEOUT:-300}"
# 用途：指定 Gateway 构建期 Python 包索引；影响：网络受限服务器可切换镜像源，未设置时保持 PyPI 默认源。
UV_INDEX_URL_VALUE="${PIXELFLOW_UV_INDEX_URL:-https://pypi.org/simple}"
# 用途：指定 Debian 构建依赖镜像源；影响：仅替换镜像构建期 apt 下载地址，默认使用已验证可达的阿里云源。
APT_MIRROR_HOST="${PIXELFLOW_APT_MIRROR:-mirrors.aliyun.com}"

cd "$DEPLOY_DIR"

# 用途：从 Gateway 唯一 Manifest 生成发布摘要；影响：Sidecar 不再接受人工填写的 Manifest digest。
RELEASE_FILE="$DEPLOY_DIR/.env.harness-release"
if [[ ! -f "$RELEASE_FILE" ]]; then
  echo "缺少 .env.harness-release；请从 .env.harness-release.example 创建非敏感发布配置。" >&2
  exit 1
fi
# 用途：将受控发布身份导出给 Docker Compose；影响：镜像名、数据卷与 Skill 根只从该发布文件解析，避免首次发布因变量未注入而失败。
set -a
# shellcheck disable=SC1090
. "$RELEASE_FILE"
set +a
# 用途：读取受控发布 Profile；影响：构建时用同一份 Gateway 配置生成 Sidecar 限额，防止两端漂移。
PROFILE_ENV="$(python3 - "$RELEASE_FILE" <<'PY'
from pathlib import Path
import re
import sys

for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line.startswith("PIXELFLOW_CONFIG_ENV="):
        value = line.split("=", 1)[1].strip()
        if re.fullmatch(r"[a-z0-9-]+", value):
            print(value)
            break
else:
    raise SystemExit("发布配置缺少合法 PIXELFLOW_CONFIG_ENV。")
PY
)"
# 用途：从 Gateway 同一 Profile 生成共享 Run 限额；影响：Sidecar 不再使用过期档案，避免请求阶段配置错误。
RUN_LIMIT_PROFILES="$(cd "$ROOT_DIR/backend" && PIXELFLOW_CONFIG_ENV="$PROFILE_ENV" PYTHONPATH=. uv run python -c 'from app.gateway.profile_config import load_profile_config; import os; load_profile_config(); print(os.environ["PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES"])')"
MANIFEST_DIGEST="$(cd "$ROOT_DIR/backend" && PYTHONPATH=. uv run python -c 'from pixelflow.agent_tools.manifest import manifest; print(manifest().digest)')"
if ! grep -q '^PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST=' "$RELEASE_FILE"; then
  echo "发布配置缺少 PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST。" >&2
  exit 1
fi
sed -i.bak "s|^PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST=.*|PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST=$MANIFEST_DIGEST|" "$RELEASE_FILE"
rm -f "$RELEASE_FILE.bak"
if grep -q '^PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES=' "$RELEASE_FILE"; then
  sed -i.bak "s|^PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES=.*|PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES=$RUN_LIMIT_PROFILES|" "$RELEASE_FILE"
else
  printf '\nPIXELFLOW_HARNESS_RUN_LIMIT_PROFILES=%s\n' "$RUN_LIMIT_PROFILES" >> "$RELEASE_FILE"
fi
rm -f "$RELEASE_FILE.bak"

# 用途：按 Compose 服务名读取解析后的镜像；影响：不依赖 config --images 的非稳定输出顺序，避免镜像错标。
COMPOSE_CONFIG_JSON="$(docker compose -f "$COMPOSE_FILE" config --format json)"
GATEWAY_IMAGE="$(printf '%s' "$COMPOSE_CONFIG_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["gateway"]["image"])')"
SIDECAR_IMAGE="$(printf '%s' "$COMPOSE_CONFIG_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["harness-sidecar"]["image"])')"
if [[ -z "$GATEWAY_IMAGE" || -z "$SIDECAR_IMAGE" ]]; then
  echo "Compose 镜像配置不完整，拒绝构建。" >&2
  exit 1
fi

# 用途：构建 Gateway 当前源码镜像；影响：复用 Docker/uv 缓存，下载超时由上方变量控制。
docker build \
  --build-arg "APT_MIRROR=$APT_MIRROR_HOST" \
  --build-arg "UV_INDEX_URL=$UV_INDEX_URL_VALUE" \
  --build-arg "UV_HTTP_TIMEOUT=$UV_HTTP_TIMEOUT_SECONDS" \
  -t "$GATEWAY_IMAGE" \
  -f "$ROOT_DIR/backend/Dockerfile" \
  "$ROOT_DIR"

# 用途：构建离线 Runtime Sidecar 镜像；影响：官方 Runtime 只从服务目录的已校验 wheel 安装。
docker build \
  --build-arg "UV_INDEX_URL=$UV_INDEX_URL_VALUE" \
  --build-arg "UV_HTTP_TIMEOUT=$UV_HTTP_TIMEOUT_SECONDS" \
  -t "$SIDECAR_IMAGE" \
  -f "$ROOT_DIR/services/pixelflow-agent-harness/Dockerfile" \
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
