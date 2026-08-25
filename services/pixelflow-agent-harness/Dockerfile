# 用途：构建仅运行 Linux Sidecar 的独立镜像；影响：不包含 PixelFlow Gateway 或本地开发工具链。
FROM python:3.12-slim-bookworm

# 用途：声明镜像内 Sidecar 工作目录；影响：后续复制和启动命令均在该目录执行。
WORKDIR /app

# 用途：安装 uv 固定版本；影响：按 uv.lock 安装可复现的官方 Linux Runtime。
RUN pip install --no-cache-dir "uv==0.7.20"

# 用途：先复制依赖清单和包元数据说明以利用 Docker 缓存；影响：仅依赖或 README 元数据变更时才重新下载 Runtime。
COPY pyproject.toml uv.lock README.md ./
# 用途：复制部署方预置的受校验 wheel；影响：存在官方 Runtime 本地文件时优先使用，避免受网络波动影响。
COPY wheels/ /wheels/
# 用途：先离线安装 Runtime 和 Linux 平台二进制依赖；影响：服务器包源不可用时仍能完成可复现构建。
RUN uv venv && uv pip install --no-index --find-links /wheels \
    "deepseek-harness-runtime-bin==0.1.1rc1" \
    "pydantic-core==2.46.4" \
    "uvloop==0.22.1"
# 用途：按锁文件同步其余生产依赖；影响：仍严格校验其余依赖，且不重复下载已预置的 Linux wheel。
RUN uv sync --locked --no-dev \
    --no-install-package deepseek-harness-runtime-bin \
    --no-install-package pydantic-core \
    --no-install-package uvloop

# 用途：复制 Sidecar 源码和官方 Composition/Plugin；影响：镜像内 Skill 根仍由运行时卷挂载提供。
COPY src ./src
COPY engines ./engines

# 用途：声明 Sidecar 内部 HTTP 端口；影响：部署编排需仅向 Gateway 所在网络开放该端口。
EXPOSE 8090

# 用途：启动 Sidecar 工厂应用；影响：所有 Secret 与 Linux Skill 根必须由运行环境注入。
CMD ["uv", "run", "--no-sync", "uvicorn", "pixelflow_harness_sidecar.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090"]
