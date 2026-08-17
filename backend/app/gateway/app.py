# ruff: noqa: E402

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.gateway.profile_config import load_profile_config
from pixelflow.agent_runtime.config import validate_agent_runtime_startup_config

# 在导入会触发 DeerFlow/Skill 初始化副作用的 router 之前加载 profile YAML，
# 确保 DeerFlow 使用 DEER_FLOW_CONFIG_PATH，不再回退查找旧 config.yaml。
load_profile_config()
validate_agent_runtime_startup_config()

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.config import get_gateway_config
from app.gateway.csrf_middleware import get_configured_cors_origins
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
    pixelflow_conversations,
    pixelflow_image,
    pixelflow_intake,
    pixelflow_planning,
    pixelflow_ppt,
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

# 默认日志配置；lifespan 会根据当前 profile YAML 的 log_level 覆盖。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


class _GatewayClock:
    """为 Gateway 内同一组 live 组件提供统一的 UTC 时间。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


def _build_jianying_draft_skill(runtime_config):
    """按内部开关与 Provider 配置选择剪映草稿 Skill。"""

    from pixelflow.jianying_draft import (
        DisabledJianyingDraftSkill,
        HttpJianyingDraftSkill,
        MissingProviderJianyingDraftSkill,
    )

    if not runtime_config.enabled:
        return DisabledJianyingDraftSkill()

    if runtime_config.base_url and runtime_config.token:
        return HttpJianyingDraftSkill(
            base_url=runtime_config.base_url,
            token=runtime_config.token,
            poll_interval_seconds=runtime_config.poll_interval_seconds,
            max_retries=runtime_config.max_retries,
            connect_timeout_seconds=runtime_config.connect_timeout_seconds,
            create_read_timeout_seconds=runtime_config.create_read_timeout_seconds,
            query_read_timeout_seconds=runtime_config.query_read_timeout_seconds,
        )

    logger.warning("PixelFlow Jianying draft is enabled but Provider URL/token is incomplete; using unavailable skill")
    return MissingProviderJianyingDraftSkill()


def _configure_jianying_draft_service(app: FastAPI) -> None:
    """按 profile 环境变量注入剪映草稿 Service 与轮询合同参数。"""

    from pixelflow.jianying_draft import (
        JianyingDraftService,
        load_jianying_draft_runtime_config,
    )

    runtime_config = load_jianying_draft_runtime_config()
    app.state.pixelflow_jianying_draft_service = JianyingDraftService(
        skill=_build_jianying_draft_skill(runtime_config),
        timeout_seconds=runtime_config.timeout_seconds,
        max_retries=runtime_config.max_retries,
        poll_interval_seconds=runtime_config.poll_interval_seconds,
    )
    app.state.jianying_draft_poll_interval_seconds = runtime_config.poll_interval_seconds


def _status_service_authorization_from_env() -> str:
    """即时读取服务状态凭据，禁止把值缓存到Gateway对象或日志。

    优先读 PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION_FILE（每次轮询重读，
    便于本地刷新 JWT 后无需重启网关）；否则读环境变量。
    """

    authorization = ""
    file_path = os.environ.get(
        "PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION_FILE",
        "",
    ).strip()
    if file_path:
        try:
            with open(file_path, encoding="utf-8") as handle:
                authorization = handle.read().strip()
        except OSError:
            authorization = ""
    if not authorization:
        authorization = os.environ.get(
            "PIXELFLOW_CONTENT_APP_STATUS_AUTHORIZATION",
            "",
        ).strip()
    if authorization and not authorization.startswith("Bearer "):
        authorization = f"Bearer {authorization}"
    if (
        not authorization.startswith("Bearer ")
        or len(authorization) <= len("Bearer ")
        or "\r" in authorization
        or "\n" in authorization
    ):
        raise RuntimeError("content_app_status_authorization_unavailable")
    return authorization


def _configure_content_app_provider_services(
    app: FastAPI,
):
    """仅在独立服务Authorization存在时装配可恢复content-app Client。"""

    import httpx

    from app.gateway.content_app_auth import get_content_app_auth_config
    from pixelflow.agent_runtime.jobs import ProviderJobAdapter
    from pixelflow.jianying_draft import load_jianying_draft_runtime_config
    from pixelflow.jianying_draft.provider_jobs import (
        JianyingDraftProviderJobService,
    )
    from pixelflow.skills.borgrise import (
        make_merge_video_job_service,
        make_quality_review_job_service,
        make_reference_analysis_job_service,
        make_scene_video_job_service,
    )

    try:
        _status_service_authorization_from_env()
    except RuntimeError:
        app.state.pixelflow_reference_analysis_job_service = None
        app.state.pixelflow_reference_analysis_provider_adapter = None
        app.state.pixelflow_generate_scene_video_job_service = None
        app.state.pixelflow_merge_video_job_service = None
        app.state.pixelflow_quality_review_job_service = None
        app.state.pixelflow_jianying_draft_job_service = None
        app.state.pixelflow_reference_analysis_provider_reason = (
            "content_app_status_authorization_unavailable"
        )
        return None

    config = get_content_app_auth_config()
    client = httpx.AsyncClient(
        timeout=config.verify_timeout_seconds,
        verify=not config.skip_ssl_verify,
    )
    service = make_reference_analysis_job_service(
        client=client,
        base_url=config.base_url,
        status_headers_provider=lambda: {
            "Authorization": _status_service_authorization_from_env(),
        },
        status_auth_mode="service_authorization",
    )
    app.state.pixelflow_reference_analysis_job_service = service
    app.state.pixelflow_reference_analysis_provider_adapter = ProviderJobAdapter(
        service
    )
    app.state.pixelflow_reference_analysis_provider_reason = None
    app.state.pixelflow_generate_scene_video_job_service = (
        make_scene_video_job_service(
            client=client,
            base_url=config.base_url,
            status_headers_provider=lambda: {
                "Authorization": _status_service_authorization_from_env(),
            },
            status_auth_mode="service_authorization",
        )
    )
    app.state.pixelflow_merge_video_job_service = make_merge_video_job_service(
        client=client,
        base_url=config.base_url,
        request_timeout_seconds=float(
            os.environ.get("BORGRISE_VIDEO_MERGE_REQUEST_TIMEOUT", "3600")
        ),
    )
    app.state.pixelflow_quality_review_job_service = (
        make_quality_review_job_service(
            client=client,
            base_url=config.base_url,
            status_headers_provider=lambda: {
                "Authorization": _status_service_authorization_from_env(),
            },
            status_auth_mode="service_authorization",
        )
    )
    jianying_config = load_jianying_draft_runtime_config()
    app.state.pixelflow_jianying_draft_job_service = (
        JianyingDraftProviderJobService(
            client=client,
            provider_base_url=jianying_config.base_url,
            provider_token=jianying_config.token,
            content_app_base_url=config.base_url,
            service_authorization_provider=(
                _status_service_authorization_from_env
            ),
            create_timeout_seconds=jianying_config.create_read_timeout_seconds,
            query_timeout_seconds=jianying_config.query_read_timeout_seconds,
        )
        if (
            jianying_config.enabled
            and jianying_config.base_url
            and jianying_config.token
            and os.environ.get(
                "PIXELFLOW_CONTENT_APP_INTERNAL_UPLOAD_ENABLED",
                "false",
            ).strip().lower()
            in {"1", "true", "yes", "on"}
        )
        else None
    )
    return client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI 应用生命周期处理器。"""

    # lifespan 入口再调用一次，保证测试或特殊 ASGI 加载路径也已经完成 profile 初始化。
    load_profile_config()
    agent_runtime_config = validate_agent_runtime_startup_config()

    # 启动时加载配置并检查必要环境变量。startup_config 是一次性启动快照，只用于
    # 日志级别、LangGraph runtime 引擎和 channels 等必须重启才生效的基础设施。
    # 请求期配置读取始终走 deps.get_config() -> get_app_config()，让当前 profile YAML
    # 的可热加载字段可以在无需重启的情况下对请求生效。因此这里刻意不把 startup_config 缓存在
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

        from deerflow.persistence.engine import get_engine, get_session_factory
        from pixelflow.memory import PowerMemService, load_power_mem_config_from_env
        from pixelflow.tasks import MemoryPixelFlowTaskStore, SQLPixelFlowTaskStore

        # SQLite/DeerFlow 复用旧库时不会自动执行 PixelFlow 业务 Alembic 迁移。
        # 先幂等补齐对话列，再把同一 session factory 注入 Repository，避免新旧
        # 对话接口在 ORM 查询阶段因缺列统一返回 500。
        persistence_engine = get_engine()
        if persistence_engine is not None:
            from pixelflow.tasks import ensure_sql_conversation_schema

            await ensure_sql_conversation_schema(persistence_engine)

        app.state.pixelflow_power_mem_service = PowerMemService(load_power_mem_config_from_env())
        logger.info("PixelFlow semantic memory initialised: %s", app.state.pixelflow_power_mem_service.status_snapshot())
        # Provider 关闭或配置缺失时仍注入安全不可用实现。
        _configure_jianying_draft_service(app)
        content_app_provider_client = _configure_content_app_provider_services(app)

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

        from pixelflow.agent_runtime.context import ContextBudgetPolicyProvider
        from pixelflow.agent_runtime.conversation_router import ConversationRouteService
        from pixelflow.agent_runtime.persistence import (
            MemoryCompactionQueueRepository,
            SQLCompactionQueueRepository,
        )
        from pixelflow.agent_runtime.runtime_compaction import (
            build_agent_context_compactor,
        )
        from pixelflow.agent_runtime.service import AgentRuntimeService
        from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint
        from pixelflow.video_agent.runner import VideoAgentRunner
        from pixelflow.video_agent.workspace import (
            MemoryVideoAgentRepository,
            SQLVideoAgentRepository,
        )

        task_store = app.state.pixelflow_task_store
        if isinstance(task_store, SQLPixelFlowTaskStore):
            agent_runtime_repository = SQLCompactionQueueRepository(
                task_store.session_factory,
            )
            video_agent_repository = SQLVideoAgentRepository(
                task_store.session_factory,
            )
        elif isinstance(task_store, MemoryPixelFlowTaskStore):
            agent_runtime_repository = MemoryCompactionQueueRepository()
            video_agent_repository = MemoryVideoAgentRepository(
                event_repository=agent_runtime_repository,
            )
        else:
            # MySQL 对话 Store 尚无同事务 Runtime Repository，保持 R1 压缩并固定关闭V2执行。
            agent_runtime_repository = MemoryCompactionQueueRepository()
            video_agent_repository = None
        video_agent_entrypoint = None
        context_compactor = (
            build_agent_context_compactor(
                task_store=task_store,
                repository=agent_runtime_repository,
                app_config=startup_config,
                agent_runtime_config=agent_runtime_config,
            )
            if agent_runtime_config.context_compaction_enabled
            else None
        )
        from pixelflow.agent_runtime.jobs import (
            ExistingJobService,
            OperationRecoveryRuntime,
            ProviderJobAdapter,
        )
        from pixelflow.video_agent.operation_resume import (
            VideoAgentOperationResumer,
            VideoAgentQuotaResumer,
        )
        from pixelflow.video_agent.runtime import make_video_agent_runtime_assembly
        from pixelflow.video_agent.adapters.domain_jobs import (
            make_generate_scene_assets_runner,
            make_scene_assets_workspace_progress,
        )
        from pixelflow.skills import get_image_skill
        from pixelflow.skills.base import is_quota_insufficient

        live_clock = _GatewayClock()

        def _optional_provider_adapter(
            service: object,
        ) -> ProviderJobAdapter | None:
            """只包装符合M06合同的Service，构造期不触发Provider调用。"""

            return (
                ProviderJobAdapter(service)
                if isinstance(service, ExistingJobService)
                else None
            )

        video_agent_runtime = make_video_agent_runtime_assembly(
            operation_repository=(
                agent_runtime_repository
                if video_agent_repository is not None
                else None
            ),
            video_repository=video_agent_repository,
            reference_adapter=getattr(
                app.state,
                "pixelflow_reference_analysis_provider_adapter",
                None,
            ),
            scene_adapter=_optional_provider_adapter(
                getattr(
                    app.state,
                    "pixelflow_generate_scene_video_job_service",
                    None,
                )
            ),
            merge_adapter=_optional_provider_adapter(
                getattr(
                    app.state,
                    "pixelflow_merge_video_job_service",
                    None,
                )
            ),
            jianying_adapter=_optional_provider_adapter(
                getattr(
                    app.state,
                    "pixelflow_jianying_draft_job_service",
                    None,
                )
            ),
            scene_assets_runner=make_generate_scene_assets_runner(
                image_skill_factory=get_image_skill,
                quota_checker=is_quota_insufficient,
                workspace_progress=(
                    make_scene_assets_workspace_progress(
                        video_agent_repository,
                        clock=live_clock.now,
                    )
                    if video_agent_repository is not None
                    else None
                ),
            ),
            lease_owner=f"gateway-video-agent:{os.getpid()}",
            clock=live_clock.now,
        )
        app.state.pixelflow_video_agent_runtime = video_agent_runtime
        native_invoker = None
        video_agent_entrypoint = None
        if video_agent_repository is not None:
            from deerflow.config.memory_config import MemoryConfig
            from deerflow.models import create_chat_model
            from pixelflow.video_agent.native_invoke import NativeVideoAgentInvoker
            from pixelflow.video_agent.skills import SkillCatalog

            skill_catalog = SkillCatalog()
            if (
                video_agent_runtime.registry is not None
                and video_agent_runtime.executor is not None
            ):
                native_invoker = NativeVideoAgentInvoker(
                    model=create_chat_model(
                        thinking_enabled=True,
                        app_config=startup_config,
                    ),
                    registry=video_agent_runtime.registry,
                    executor=video_agent_runtime.executor,
                    video_repository=video_agent_repository,
                    runtime_repository=agent_runtime_repository,
                    skill_catalog=skill_catalog,
                    checkpointer=getattr(app.state, "checkpointer", None),
                    app_config=startup_config,
                    memory_config=getattr(startup_config, "memory", None)
                    or MemoryConfig(enabled=True),
                )
                video_agent_entrypoint = VideoAgentEntrypoint(
                    runtime_repository=agent_runtime_repository,
                    video_repository=video_agent_repository,
                    native_invoker=native_invoker,
                    clock=live_clock.now,
                )
        video_agent_runner = (
            VideoAgentRunner(
                repository=video_agent_repository,
                native_invoker=native_invoker,
            )
            if video_agent_repository is not None and native_invoker is not None
            else None
        )
        native_resume_handler = None
        if native_invoker is not None and video_agent_repository is not None:
            from pixelflow.video_agent.native_operation_resume import (
                NativeOperationResumeHandler as _NativeOperationResumeHandler,
            )

            native_resume_handler = _NativeOperationResumeHandler(
                repository=video_agent_repository,
                native_invoker=native_invoker,
            )
        video_agent_operation_recovery = (
            OperationRecoveryRuntime(
                agent_runtime_repository,
                resolver=video_agent_runtime.operation_resolver,
                resumer=VideoAgentOperationResumer(
                    repository=video_agent_repository,
                    executor=video_agent_runtime.executor,
                    native_resume=native_resume_handler,
                ),
                quota_resumer=VideoAgentQuotaResumer(
                    repository=video_agent_repository,
                ),
                worker_id=f"gateway-video-agent:{os.getpid()}:recovery",
                clock=live_clock.now,
            )
            if video_agent_repository is not None
            and video_agent_runtime.executor is not None
            and video_agent_runtime.operation_resolver is not None
            else None
        )
        if video_agent_operation_recovery is not None:
            await video_agent_operation_recovery.start()
        app.state.pixelflow_video_agent_operation_recovery = (
            video_agent_operation_recovery
        )
        app.state.pixelflow_agent_runtime_service = AgentRuntimeService(
            config=agent_runtime_config,
            repository=agent_runtime_repository,
            task_store=task_store,
            context_compactor=context_compactor,
            video_agent_repository=video_agent_repository,
            video_agent_entrypoint=video_agent_entrypoint,
            video_agent_executor=video_agent_runtime.executor,
            video_agent_runner=video_agent_runner,
            operation_repository=agent_runtime_repository,
            video_agent_operation_recovery=video_agent_operation_recovery,
            conversation_router=ConversationRouteService(
                budget_policy_provider=ContextBudgetPolicyProvider(
                    agent_runtime_config.context_budget,
                ),
            ),
            # 只有完整V2核心与独立Runner同时就绪时才接管新视频对话。
            primary_execution_intents=("video",) if video_agent_runner is not None else (),
        )
        logger.info(
            "PixelFlow Agent Runtime initialised: mode=%s rollout=%s primary_execution_intents=%s video_agent_ready=%s jianying_ready=%s",
            agent_runtime_config.mode,
            agent_runtime_config.new_conversation_rollout_percent,
            sorted(
                app.state.pixelflow_agent_runtime_service.primary_execution_intents,
            ),
            video_agent_runtime.ready,
            video_agent_runtime.optional_capabilities.get(
                "jianying_package",
                False,
            ),
        )

        from pixelflow.tracing import configure_trace_sink

        conversation_trace_store = app.state.pixelflow_task_store

        async def _write_conversation_trace_event(conversation_id: str, event: str, data: dict, user_id: str | None) -> None:
            await conversation_trace_store.append_trace_event(conversation_id, event, data, user_id=user_id)

        configure_trace_sink(_write_conversation_trace_event)

        try:
            yield
        finally:
            pixelflow_agent_runtime_service = getattr(
                app.state,
                "pixelflow_agent_runtime_service",
                None,
            )
            if pixelflow_agent_runtime_service is not None:
                await pixelflow_agent_runtime_service.aclose()
                logger.info("PixelFlow Agent Runtime closed")
            pixelflow_video_agent_operation_recovery = getattr(
                app.state,
                "pixelflow_video_agent_operation_recovery",
                None,
            )
            if pixelflow_video_agent_operation_recovery is not None:
                await pixelflow_video_agent_operation_recovery.aclose()
                logger.info("PixelFlow VideoAgent Operation恢复Worker已关闭")
            pixelflow_jianying_draft_service = getattr(app.state, "pixelflow_jianying_draft_service", None)
            if pixelflow_jianying_draft_service is not None:
                await pixelflow_jianying_draft_service.aclose()
                logger.info("PixelFlow Jianying draft service closed")
            if content_app_provider_client is not None:
                await content_app_provider_client.aclose()
                logger.info("PixelFlow reference analysis Provider client closed")
            pixelflow_mysql_engine = getattr(app.state, "pixelflow_mysql_engine", None)
            if pixelflow_mysql_engine is not None:
                await pixelflow_mysql_engine.dispose()
                logger.info("PixelFlow MySQL task store closed")
            pixelflow_preference_mysql_engine = getattr(app.state, "pixelflow_preference_mysql_engine", None)
            if pixelflow_preference_mysql_engine is not None:
                await pixelflow_preference_mysql_engine.dispose()
                logger.info("PixelFlow MySQL preference store closed")
            pixelflow_power_mem_service = getattr(app.state, "pixelflow_power_mem_service", None)
            if pixelflow_power_mem_service is not None:
                await pixelflow_power_mem_service.aclose()
                logger.info("PixelFlow semantic memory closed")
            configure_trace_sink(None)

    logger.info("Shutting down API Gateway")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。

    返回已挂载中间件、router 和生命周期处理器的 FastAPI 实例。
    """
    # create_app 可能被测试直接调用；这里保持幂等加载，确保 GatewayConfig 从 YAML 取值。
    load_profile_config()

    config = get_gateway_config()
    docs_url = "/agent/docs" if config.enable_docs else None
    redoc_url = "/agent/redoc" if config.enable_docs else None
    openapi_url = "/agent/openapi.json" if config.enable_docs else None

    app = FastAPI(
        title="PixelFlow Agent API Gateway",
        description="""
## PixelFlow Agent API Gateway

PixelFlow 是电商带货短视频生成 AI Agent 平台。这个接口文档由 FastAPI
自动生成，底层是 OpenAPI 3，功能上类似 Java 项目里的 Knife4j / Swagger 页面。

### 主要入口

- **PixelFlow Agent Flow**: `/agent/flows`，创建生成流程、查询状态、订阅 SSE、确认 Brief/片段/剪辑/QC
- **Auth**: 所有非公开接口使用 content-app 的 `Authorization: Bearer <token>`
- **Agent Runtime**: `/agent/threads`、`/agent/runs`，DeerFlow/LangGraph 兼容运行时接口
- **Tools**: `/agent/models`、`/agent/mcp`、`/agent/skills`、`/agent/memory`

### 访问说明

开发环境默认端口是 `8001`。启动后访问 `/agent/docs` 查看 Swagger UI，
访问 `/agent/redoc` 查看 ReDoc，访问 `/agent/openapi.json` 获取原始 OpenAPI JSON。
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
                "name": "pixelflow-flows",
                "description": "PixelFlow e-commerce video Agent flow API, progress events, and explainable timeline",
            },
            {
                "name": "pixelflow-conversations",
                "description": "PixelFlow conversation history, pagination, and workflow resume API",
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

    # 已废弃 pixelflow 自有 cookie 登录体系，因此不再挂 CSRF 中间件。
    # 浏览器请求必须显式携带 Authorization header，和 content-app 保持一致。

    # CORS：统一 nginx 入口默认同源。前后端分离部署时，浏览器来源必须显式写入
    # Gateway allowlist，浏览器才允许把 Authorization header 发给 pixelflow。
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
    # 模型 API：/agent/models。
    app.include_router(models.router)

    # MCP API：/agent/mcp。
    app.include_router(mcp.router)

    # Memory API：/agent/memory。
    app.include_router(memory.router)

    # Skills API：/agent/skills。
    app.include_router(skills.router)

    # Artifacts API：/agent/threads/{thread_id}/artifacts。
    app.include_router(artifacts.router)

    # Uploads API：/agent/threads/{thread_id}/uploads。
    app.include_router(uploads.router)

    # Thread 管理 API：/agent/threads/{thread_id}。
    app.include_router(threads.router)

    # 自定义 Agents API：/agent/agents。
    app.include_router(agents.router)

    # Suggestions API：/agent/threads/{thread_id}/suggestions。
    app.include_router(suggestions.router)

    # Assistants 兼容 API：LangGraph Platform stub。
    app.include_router(assistants_compat.router)

    # Auth API：只保留 /agent/auth/me，用于查看 content-app 当前用户。
    app.include_router(auth.router)

    # Feedback API：/agent/threads/{thread_id}/runs/{run_id}/feedback。
    app.include_router(feedback.router)

    # Thread Runs API：兼容 LangGraph Platform 的 runs 生命周期。
    app.include_router(thread_runs.router)

    # Stateless Runs API：无需预先存在 thread 的 stream/wait。
    app.include_router(runs.router)

    # PixelFlow Agent 工作流 API：/agent/flows。
    app.include_router(pixelflow_tasks.router)

    # PixelFlow 采集表单和创意方向 API：/agent/flows/intake。
    app.include_router(pixelflow_intake.router)

    # PixelFlow 策划 plan.md API：/agent/flows/planning。
    app.include_router(pixelflow_planning.router)

    # PixelFlow 图片生成准备 API：/agent/flows/image。
    app.include_router(pixelflow_image.router)

    # PixelFlow 智能 PPT 生成 API：/agent/flows/ppt。
    app.include_router(pixelflow_ppt.router)

    # P0-5：旧 /agent/flows/video* 与剪映 Job HTTP 路由模块已物理删除。

    # PixelFlow 对话历史 API：/agent/conversations。
    app.include_router(pixelflow_conversations.router)

    # PixelFlow 结构化偏好 API：/agent/users/{user_id}/preferences。
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
