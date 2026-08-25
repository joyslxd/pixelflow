# 用途：构建仅运行 Linux Sidecar 的独立镜像；影响：不包含 PixelFlow Gateway 或本地开发工具链。
FROM python:3.12-slim-bookworm

# 用途：声明镜像内 Sidecar 工作目录；影响：后续复制和启动命令均在该目录执行。
WORKDIR /app

# 用途：先复制依赖清单和预置 wheel 以复用离线依赖缓存；影响：源码变更不会重新下载生产依赖。
COPY pyproject.toml uv.lock README.md ./
# 用途：复制部署方预置的受校验 wheel；影响：存在官方 Runtime 本地文件时优先使用，避免受网络波动影响。
COPY wheels/ /wheels/
# 用途：离线安装固定 uv 构建工具；影响：服务器无法访问 Python 包源时仍可进入锁文件同步步骤。
RUN pip install --no-index --find-links /wheels "uv==0.7.20"
# 用途：直接从受校验 wheel 目录离线解析全部生产依赖；影响：构建不读取 uv 缓存或 Python 包源。
RUN uv venv && uv pip install --no-index --find-links /wheels \
    "deepseek-harness-sdk==0.1.1rc1" \
    "fastapi==0.141.1" \
    "httpx==0.28.1" \
    "pydantic==2.13.4" \
    "PyJWT==2.13.0" \
    "uvicorn[standard]==0.52.4"

# 用途：复制 Sidecar 源码和官方 Composition/Plugin；影响：镜像内 Skill 根仍由运行时卷挂载提供。
COPY src ./src
COPY engines ./engines
# 用途：声明源码布局的运行导入路径；影响：生产镜像不安装项目自身 wheel，避免构建期重复解析依赖。
ENV PYTHONPATH=/app/src

# 用途：声明 Sidecar 内部 HTTP 端口；影响：部署编排需仅向 Gateway 所在网络开放该端口。
EXPOSE 8090

# 用途：启动 Sidecar 工厂应用；影响：所有 Secret 与 Linux Skill 根必须由运行环境注入。
CMD ["uv", "run", "--no-sync", "uvicorn", "pixelflow_harness_sidecar.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090"]
