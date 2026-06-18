import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.config import get_gateway_config
from app.gateway.csrf_middleware import CSRFMiddleware, get_configured_cors_origins
from app.gateway.deps import langgraph_runtime
from app.gateway.routers import (
    agents,
    artifacts,
    assistants_compat,
    auth,
    feedback,
    mcp,
    memory,
    models,
    pixelflow_preferences,
    pixelflow_tasks,
    runs,
    skills,
    suggestions,
    thread_runs,
    threads,
    uploads,
)
from deerflow.config import app_config as deerflow_app_config
from deerflow.config.app_config import apply_logging_level

AppConfig = deerflow_app_config.AppConfig
get_app_config = deerflow_app_config.get_app_config

# 默认日志配置；lifespan 会根据 config.yaml 的 log_level 覆盖。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def _ensure_admin_user(app: FastAPI) -> None:
    """启动钩子：处理首次启动和历史无主线程迁移。

    管理员创建后，会把 LangGraph store 中没有 ``metadata.user_id`` 的历史线程迁移
    到管理员账号下。这是 “无认证 → 有认证” 升级路径：以前未启用鉴权时创建的
    LangGraph thread 数据需要补一个 owner。

    首次启动（没有管理员）：
      - 不自动创建账号。
      - 运维/用户需要访问 ``/setup`` 创建第一个管理员。

    后续启动（管理员已存在）：
      - 执行一次历史无主 LangGraph thread 元数据迁移。

    SQL 持久化不需要额外迁移：threads_meta、runs、run_events、feedback 四个
    user_id 列会随 auth 模块 create_all 一起出现，新建表不会有 NULL owner 历史行。
    """
    from sqlalchemy import select

    from app.gateway.deps import get_local_provider
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.user.model import UserRow

    try:
        provider = get_local_provider()
    except RuntimeError:
        # 某些测试或启动路径下鉴权持久化尚未初始化；跳过管理员迁移，避免网关启动失败。
        logger.warning("Auth persistence not ready; skipping admin bootstrap check")
        return

    sf = get_session_factory()
    if sf is None:
        return

    admin_count = await provider.count_admin_users()

    if admin_count == 0:
        logger.info("=" * 60)
        logger.info("  First boot detected — no admin account exists.")
        logger.info("  Visit /setup to complete admin account creation.")
        logger.info("=" * 60)
        return

    # 管理员已存在：迁移 auth 模块引入前留下的无主 LangGraph thread 元数据。
    async with sf() as session:
        stmt = select(UserRow).where(UserRow.system_role == "admin").limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()

    if row is None:
        return  # Should not happen (admin_count > 0 above), but be safe.

    admin_id = str(row.id)

    # LangGraph store 无主数据迁移是非致命步骤，失败时只记录日志。
    store = getattr(app.state, "store", None)
    if store is not None:
        try:
            migrated = await _migrate_orphaned_threads(store, admin_id)
            if migrated:
                logger.info("Migrated %d orphan LangGraph thread(s) to admin", migrated)
        except Exception:
            logger.exception("LangGraph thread migration failed (non-fatal)")


async def _iter_store_items(store, namespace, *, page_size: int = 500):
    """分页遍历 LangGraph store 的某个 namespace。

    这里用 offset 分页替代旧的 ``limit=1000`` 固定读取，避免无主数据超过一页时
    静默漏迁。空页或短页都表示已经到达最后一页。
    """
    offset = 0
    while True:
        batch = await store.asearch(namespace, limit=page_size, offset=offset)
        if not batch:
            return
        for item in batch:
            yield item
        if len(batch) < page_size:
            return
        offset += page_size


async def _migrate_orphaned_threads(store, admin_user_id: str) -> int:
    """把没有 user_id 的 LangGraph store threads 迁移给指定管理员。

    使用分页遍历，保证无论数量多少都能迁移。返回迁移条数。
    """
    migrated = 0
    async for item in _iter_store_items(store, ("threads",)):
        metadata = item.value.get("metadata", {})
        if not metadata.get("user_id"):
            metadata["user_id"] = admin_user_id
            item.value["metadata"] = metadata
            await store.aput(("threads",), item.key, item.value)
            migrated += 1
    return migrated


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI 应用生命周期处理器。"""

    # 启动时加载配置并检查必要环境变量。startup_config 是一次性启动快照，只用于
    # 日志级别、LangGraph runtime 引擎和 channels 等必须重启才生效的基础设施。
    # 请求期配置读取始终走 deps.get_config() -> get_app_config()，让 config.yaml 修改
    # 可以在无需重启的情况下对请求生效。因此这里刻意不把 startup_config 缓存在
    # app.state 上，避免破坏热加载边界。
    try:
        startup_config = get_app_config()
        apply_logging_level(startup_config.log_level)
        logger.info("Configuration loaded successfully")
    except Exception as e:
        error_msg = f"Failed to load configuration during gateway startup: {e}"
        logger.exception(error_msg)
        raise RuntimeError(error_msg) from e
    config = get_gateway_config()
    logger.info(f"Starting API Gateway on {config.host}:{config.port}")

    # 初始化 LangGraph runtime 组件：StreamBridge、RunManager、checkpointer、store。
    async with langgraph_runtime(app, startup_config):
        logger.info("LangGraph runtime initialised")

        # 检查管理员初始化状态，并在管理员存在后迁移无主线程。必须在
        # langgraph_runtime 之后执行，因为迁移需要 app.state.store。
        await _ensure_admin_user(app)

        from deerflow.persistence.engine import get_session_factory
        from pixelflow.tasks import MemoryPixelFlowTaskStore, SQLPixelFlowTaskStore

        pixelflow_mysql_url = os.environ.get("PIXELFLOW_MYSQL_URL", "").strip()
        if pixelflow_mysql_url:
            from pixelflow.preferences.mysql import make_mysql_preference_store
            from pixelflow.tasks.mysql import make_mysql_task_store

            app.state.pixelflow_task_store, app.state.pixelflow_mysql_engine = await make_mysql_task_store(pixelflow_mysql_url)
            app.state.pixelflow_preference_store, app.state.pixelflow_preference_mysql_engine = await make_mysql_preference_store(pixelflow_mysql_url)
            logger.info("PixelFlow task store initialised: mysql")
        else:
            sf = get_session_factory()
            from pixelflow.preferences import MemoryUserPreferenceStore, SQLUserPreferenceStore

            app.state.pixelflow_task_store = SQLPixelFlowTaskStore(sf) if sf is not None else MemoryPixelFlowTaskStore()
            app.state.pixelflow_preference_store = SQLUserPreferenceStore(sf) if sf is not None else MemoryUserPreferenceStore()
            logger.info("PixelFlow task store initialised: %s", "sql" if sf is not None else "memory")

        try:
            yield
        finally:
            pixelflow_mysql_engine = getattr(app.state, "pixelflow_mysql_engine", None)
            if pixelflow_mysql_engine is not None:
                await pixelflow_mysql_engine.dispose()
                logger.info("PixelFlow MySQL task store closed")
            pixelflow_preference_mysql_engine = getattr(app.state, "pixelflow_preference_mysql_engine", None)
            if pixelflow_preference_mysql_engine is not None:
                await pixelflow_preference_mysql_engine.dispose()
                logger.info("PixelFlow MySQL preference store closed")

    logger.info("Shutting down API Gateway")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。

    返回已挂载中间件、router 和生命周期处理器的 FastAPI 实例。
    """
    config = get_gateway_config()
    docs_url = "/docs" if config.enable_docs else None
    redoc_url = "/redoc" if config.enable_docs else None
    openapi_url = "/openapi.json" if config.enable_docs else None

    app = FastAPI(
        title="DeerFlow API Gateway",
        description="""
## DeerFlow API Gateway

API Gateway for DeerFlow - A LangGraph-based AI agent backend with sandbox execution capabilities.

### Features

- **Models Management**: Query and retrieve available AI models
- **MCP Configuration**: Manage Model Context Protocol (MCP) server configurations
- **Memory Management**: Access and manage global memory data for personalized conversations
- **Skills Management**: Query and manage skills and their enabled status
- **Artifacts**: Access thread artifacts and generated files
- **Health Monitoring**: System health check endpoints

### Architecture

LangGraph-compatible requests are routed through nginx to this gateway.
This gateway provides runtime endpoints for agent runs plus custom endpoints for models, MCP configuration, skills, and artifacts.
        """,
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        openapi_tags=[
            {
                "name": "models",
                "description": "Operations for querying available AI models and their configurations",
            },
            {
                "name": "mcp",
                "description": "Manage Model Context Protocol (MCP) server configurations",
            },
            {
                "name": "memory",
                "description": "Access and manage global memory data for personalized conversations",
            },
            {
                "name": "skills",
                "description": "Manage skills and their configurations",
            },
            {
                "name": "artifacts",
                "description": "Access and download thread artifacts and generated files",
            },
            {
                "name": "uploads",
                "description": "Upload and manage user files for threads",
            },
            {
                "name": "threads",
                "description": "Manage DeerFlow thread-local filesystem data",
            },
            {
                "name": "agents",
                "description": "Create and manage custom agents with per-agent config and prompts",
            },
            {
                "name": "suggestions",
                "description": "Generate follow-up question suggestions for conversations",
            },
            {
                "name": "assistants-compat",
                "description": "LangGraph Platform-compatible assistants API (stub)",
            },
            {
                "name": "runs",
                "description": "LangGraph Platform-compatible runs lifecycle (create, stream, cancel)",
            },
            {
                "name": "pixelflow-tasks",
                "description": "PixelFlow e-commerce video task API and progress events",
            },
            {
                "name": "pixelflow-preferences",
                "description": "PixelFlow structured user preferences",
            },
            {
                "name": "health",
                "description": "Health check and system status endpoints",
            },
        ],
    )

    # Auth 中间件：非公开路径必须鉴权，作为 fail-closed 安全兜底。
    app.add_middleware(AuthMiddleware)

    # CSRF 中间件：对会改变状态的请求使用 Double Submit Cookie 模式。
    app.add_middleware(CSRFMiddleware)

    # CORS：统一 nginx 入口默认同源。前后端分离部署时，浏览器来源必须显式写入
    # Gateway allowlist，让 CORS 和 CSRF origin 校验共享同一份来源配置。
    cors_origins = sorted(get_configured_cors_origins())
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # 挂载各业务 router。
    # 模型 API：/api/models。
    app.include_router(models.router)

    # MCP API：/api/mcp。
    app.include_router(mcp.router)

    # Memory API：/api/memory。
    app.include_router(memory.router)

    # Skills API：/api/skills。
    app.include_router(skills.router)

    # Artifacts API：/api/threads/{thread_id}/artifacts。
    app.include_router(artifacts.router)

    # Uploads API：/api/threads/{thread_id}/uploads。
    app.include_router(uploads.router)

    # Thread 管理 API：/api/threads/{thread_id}。
    app.include_router(threads.router)

    # 自定义 Agents API：/api/agents。
    app.include_router(agents.router)

    # Suggestions API：/api/threads/{thread_id}/suggestions。
    app.include_router(suggestions.router)

    # Assistants 兼容 API：LangGraph Platform stub。
    app.include_router(assistants_compat.router)

    # Auth API：/api/v1/auth。
    app.include_router(auth.router)

    # Feedback API：/api/threads/{thread_id}/runs/{run_id}/feedback。
    app.include_router(feedback.router)

    # Thread Runs API：兼容 LangGraph Platform 的 runs 生命周期。
    app.include_router(thread_runs.router)

    # Stateless Runs API：无需预先存在 thread 的 stream/wait。
    app.include_router(runs.router)

    # PixelFlow 业务任务 API：/api/tasks。
    app.include_router(pixelflow_tasks.router)

    # PixelFlow 结构化偏好 API：/api/users/{user_id}/preferences。
    app.include_router(pixelflow_preferences.router)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        """健康检查端点。

        返回服务健康状态。
        """
        return {"status": "healthy", "service": "deer-flow-gateway"}

    return app


# 供 uvicorn 导入的应用实例。
app = create_app()
