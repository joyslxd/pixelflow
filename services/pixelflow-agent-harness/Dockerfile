# 用途：构建仅运行 Linux Sidecar 的独立镜像；影响：不包含 PixelFlow Gateway 或本地开发工具链。
FROM ghcr.io/astral-sh/uv:0.7.20 AS uv-source

FROM python:3.12-slim-bookworm

# 用途：声明镜像内 Sidecar 工作目录；影响：后续复制和启动命令均在该目录执行。
WORKDIR /app

# 用途：接收受控 Python 包索引；影响：网络受限 Linux 构建可切换镜像源，未传入时保持 PyPI 默认源。
ARG UV_INDEX_URL=https://pypi.org/simple
# 用途：接收包下载等待秒数；影响：仅影响镜像构建期的锁文件同步超时。
ARG UV_HTTP_TIMEOUT=300
# 用途：将构建参数传给 uv；影响：锁文件依赖下载使用指定镜像源和超时，不写入运行期 Secret。
ENV UV_INDEX_URL=$UV_INDEX_URL
ENV UV_HTTP_TIMEOUT=$UV_HTTP_TIMEOUT

# 用途：先复制依赖清单以复用 Docker 依赖层；影响：源码变更不会重新解析锁定生产依赖。
COPY pyproject.toml uv.lock README.md ./
# 用途：从固定官方镜像复制 uv；影响：不依赖仓库中不存在的本地 wheel 才能启动构建。
COPY --from=uv-source /uv /uvx /usr/local/bin/
# 用途：冻结已提交 lock 同步生产依赖；影响：版本与哈希固定，但允许构建期切换受控包镜像而不重写 lock。
RUN uv sync --frozen --no-dev

# 用途：复制 Sidecar 源码和官方 Composition/Plugin；影响：镜像内 Skill 根仍由运行时卷挂载提供。
COPY src ./src
COPY engines ./engines
# 用途：声明源码布局与受控虚拟环境路径；影响：启动时只使用锁文件同步后的生产依赖。
ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app/src

# 用途：声明 Sidecar 内部 HTTP 端口；影响：部署编排需仅向 Gateway 所在网络开放该端口。
EXPOSE 8090

# 用途：启动 Sidecar 工厂应用；影响：所有 Secret 与 Linux Skill 根必须由运行环境注入。
CMD ["uv", "run", "--no-sync", "uvicorn", "pixelflow_harness_sidecar.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090"]
