# ruff: noqa: E402

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.gateway.profile_config import load_profile_config
from pixelflow.platform.config import GatewayRuntimeSettings

# 在导入 Gateway Router 前加载 PixelFlow profile YAML，确保启动配置只来自当前环境。
load_profile_config()

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.config import get_gateway_config
from app.gateway.csrf_middleware import get_configured_cors_origins
from app.gateway.routers import (
    auth,
    internal_agent_tools,
    long_term_memory,
    pixelflow_conversations,
)

# 默认日志配置；lifespan 会根据当前 profile YAML 的 log_level 覆盖。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def _bind_run_credential_grant(
    credential_store: object,
    *,
    run_id: str,
    grant_id: str | None,
) -> None:
    """Sidecar activate 前将确认请求的瞬时凭据转给唯一 Run；无票据的 Run 不受影响。"""

    if grant_id is None:
        return
    from pixelflow.agent_tools.video.credential_store import TransientRunCredentialStore

    if not isinstance(credential_store, TransientRunCredentialStore):
        raise RuntimeError("Gateway 未装配瞬时 Run 凭据仓")
    await credential_store.bind_grant(grant_id=grant_id, run_id=run_id)


class _GatewayClock:
    """为 Gateway 内同一组 live 组件提供统一的 UTC 时间。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI 应用生命周期处理器。"""

    # lifespan 入口再调用一次，保证测试或特殊 ASGI 加载路径也已经完成 profile 初始化。
    load_profile_config()

    try:
        startup_config = GatewayRuntimeSettings.from_env()
        logger.setLevel(startup_config.log_level)
        logger.info("Configuration loaded successfully")
    except Exception as e:
        error_msg = f"Failed to load configuration during gateway startup: {e}"
        logger.exception(error_msg)
        raise RuntimeError(error_msg) from e
    config = get_gateway_config()
    logger.info(f"Starting API Gateway on {config.host}:{config.port}")

    # M1：仅启动 PixelFlow 自有持久化、Tool Broker 与 Sidecar Client。
    if True:
        from pixelflow.long_term_memory import (
            LongTermMemoryService,
            VolcengineMem0Adapter,
            load_long_term_memory_config_from_env,
        )
        from pixelflow.platform.persistence import (
            close_engine as close_pixelflow_engine,
        )
        from pixelflow.platform.persistence import (
            ensure_schema,
        )
        from pixelflow.platform.persistence import (
            get_engine as get_pixelflow_engine,
        )
        from pixelflow.platform.persistence import (
            get_session_factory as get_pixelflow_session_factory,
        )
        from pixelflow.platform.persistence import (
            init_engine as init_pixelflow_engine,
        )
        from pixelflow.tasks import MemoryPixelFlowTaskStore, SQLPixelFlowTaskStore

        pixelflow_mysql_url = os.environ.get("PIXELFLOW_MYSQL_URL", "").strip()
        database_config = startup_config
        if pixelflow_mysql_url:
            persistence_engine = None
        elif database_config.database_backend == "memory":
            persistence_engine = None
        else:
            await init_pixelflow_engine(
                backend=database_config.database_backend,
                url=database_config.database_url,
                echo=database_config.database_echo_sql,
                pool_size=database_config.database_pool_size,
                sqlite_dir=(database_config.database_sqlite_dir if database_config.database_backend == "sqlite" else ""),
            )
            persistence_engine = get_pixelflow_engine()

        # PixelFlow 自有引擎必须先创建本领域表，再注入同一会话工厂给 Repository。
        if persistence_engine is not None:
            from pixelflow.agent_tools.repository import ensure_sql_agent_tool_schema
            from pixelflow.preferences.model import PixelFlowUserPreferenceRow
            from pixelflow.tasks import ensure_sql_conversation_schema

            await ensure_schema(persistence_engine)
            await ensure_sql_conversation_schema(persistence_engine)
            await ensure_sql_agent_tool_schema(persistence_engine)
            # 用途：创建结构化用户偏好表；影响：Harness Context 首次读取偏好时不会因缺表中断新 Run。
            async with persistence_engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: PixelFlowUserPreferenceRow.metadata.create_all(
                        sync_connection,
                        tables=[PixelFlowUserPreferenceRow.__table__],
                    ),
                )

        if pixelflow_mysql_url:
            from pixelflow.preferences.mysql import make_mysql_preference_store
            from pixelflow.tasks.mysql import make_mysql_task_store

            app.state.pixelflow_task_store, app.state.pixelflow_mysql_engine = await make_mysql_task_store(pixelflow_mysql_url)
            app.state.pixelflow_preference_store, app.state.pixelflow_preference_mysql_engine = await make_mysql_preference_store(pixelflow_mysql_url)
            logger.info("PixelFlow task store initialised: mysql")
        else:
            sf = get_pixelflow_session_factory()
            from pixelflow.preferences import MemoryUserPreferenceStore, SQLUserPreferenceStore

            app.state.pixelflow_task_store = SQLPixelFlowTaskStore(sf) if sf is not None else MemoryPixelFlowTaskStore()
            app.state.pixelflow_preference_store = SQLUserPreferenceStore(sf) if sf is not None else MemoryUserPreferenceStore()
            logger.info("PixelFlow task store initialised: %s", "sql" if sf is not None else "memory")

        from pixelflow.agent_control_plane.persistence import (
            MemoryCompactionQueueRepository,
            SQLCompactionQueueRepository,
        )
        from pixelflow.video.workspace import (
            MemoryVideoAgentRepository,
            SQLVideoAgentRepository,
        )

        task_store = app.state.pixelflow_task_store
        memory_outbox_session_factory = getattr(task_store, "session_factory", None)
        long_term_memory_config = load_long_term_memory_config_from_env()
        long_term_memory_adapter = VolcengineMem0Adapter(long_term_memory_config)
        if memory_outbox_session_factory is not None:
            from pixelflow.long_term_memory.outbox import (
                MemoryWriteWorker,
                SQLWriteOutbox,
            )

            memory_write_outbox = SQLWriteOutbox(memory_outbox_session_factory)
            memory_write_worker = MemoryWriteWorker(
                memory_write_outbox,
                long_term_memory_adapter,
                worker_id=f"gateway-memory:{os.getpid()}",
            )
            await memory_write_worker.start()
            app.state.pixelflow_long_term_memory_write_worker = memory_write_worker
        else:
            memory_write_outbox = None
            app.state.pixelflow_long_term_memory_write_worker = None
        app.state.pixelflow_long_term_memory_service = LongTermMemoryService(
            long_term_memory_adapter,
            long_term_memory_config,
            outbox=memory_write_outbox,
        )
        logger.info("PixelFlow long-term memory initialised enabled=%s", long_term_memory_config.available)
        if isinstance(task_store, SQLPixelFlowTaskStore):
            agent_runtime_repository = SQLCompactionQueueRepository(
                task_store.session_factory,
            )
            app.state.pixelflow_agent_runtime_repository = agent_runtime_repository
            video_agent_repository = SQLVideoAgentRepository(
                task_store.session_factory,
            )
            app.state.pixelflow_harness_video_repository = video_agent_repository
            from pixelflow.agent_tools import AgentToolBroker, SQLAgentToolRepository
            from pixelflow.agent_tools.catalog import runtime_video_tool_registry
            from pixelflow.agent_tools.manifest import manifest
            from pixelflow.agent_tools.video.credential_store import (
                TransientBatchCredentialStore,
                TransientRunCredentialStore,
            )
            from pixelflow.capabilities.image_generation import (
                ContentAppImageGenerationAdapter,
                ContentAppImageProviderSettings,
            )
            from pixelflow.capabilities.video_generation.providers import (
                ContentAppVideoGenerationProvider,
                ContentAppVideoProviderSettings,
            )
            from pixelflow.operations.jobs.batch_callback import (
                OperationBatchTerminalCallback,
                OperationBatchTerminalWorker,
            )
            from pixelflow.operations.jobs.batch_repository import (
                SQLOperationBatchRepository,
            )

            agent_tool_repository = SQLAgentToolRepository(task_store.session_factory)
            batch_repository = SQLOperationBatchRepository(task_store.session_factory)
            app.state.pixelflow_agent_tool_repository = agent_tool_repository
            # 用途：M06 子 Operation 完成后聚合唯一批次 Outbox；影响：子项不会直接恢复 Harness Run。
            app.state.pixelflow_operation_batch_repository = batch_repository
            async def _wake_batch_dispatcher() -> None:
                dispatcher_worker = getattr(
                    app.state,
                    "pixelflow_operation_batch_dispatcher_worker",
                    None,
                )
                if dispatcher_worker is not None:
                    dispatcher_worker.wake()

            batch_terminal_callback = OperationBatchTerminalCallback(
                batch_repository=batch_repository,
                video_repository=video_agent_repository,
                on_child_terminal=_wake_batch_dispatcher,
            )
            app.state.pixelflow_operation_batch_terminal_callback = batch_terminal_callback
            batch_terminal_worker = OperationBatchTerminalWorker(
                operation_repository=agent_runtime_repository,
                callback=batch_terminal_callback,
                worker_id=f"gateway-operation-batch-terminal:{os.getpid()}",
            )
            await batch_terminal_worker.start()
            app.state.pixelflow_operation_batch_terminal_worker = batch_terminal_worker
            credential_store = TransientRunCredentialStore()
            app.state.pixelflow_transient_run_credential_store = credential_store
            batch_credential_store = TransientBatchCredentialStore()
            app.state.pixelflow_transient_batch_credential_store = (
                batch_credential_store
            )
            provider_settings = ContentAppVideoProviderSettings.from_env()
            video_provider = (
                ContentAppVideoGenerationProvider(provider_settings)
                if provider_settings is not None
                else None
            )
            app.state.pixelflow_video_generation_provider = video_provider
            image_provider_settings = ContentAppImageProviderSettings.from_env()
            image_provider = (
                ContentAppImageGenerationAdapter(image_provider_settings)
                if image_provider_settings is not None
                else None
            )
            app.state.pixelflow_image_generation_provider = image_provider
            app.state.pixelflow_image_batch_dispatcher_worker = None
            from pixelflow.capabilities.video_understanding import ContentAppVideoUnderstandingAdapter
            video_understanding_adapter = (
                ContentAppVideoUnderstandingAdapter(base_url=provider_settings.base_url)
                if provider_settings is not None
                and os.environ.get("PIXELFLOW_VIDEO_UNDERSTANDING_ENABLED", "").strip().lower()
                in {"1", "true", "yes", "on"}
                else None
            )
            if video_provider is not None:
                from pixelflow.video.adapters.operations import (
                    M06SceneGenerationBatchDispatcher,
                    M06SceneGenerationBatchDispatcherWorker,
                    M06SceneGenerationBatchOperationPort,
                    M06SceneGenerationOperationPort,
                )

                scene_operation_port = M06SceneGenerationOperationPort(
                    repository=agent_runtime_repository,
                    adapter=video_provider.as_operation_adapter(),
                    lease_owner=f"gateway-m06-video:{os.getpid()}",
                    provider_request_transformer=video_provider.prepare_operation_request,
                )
                scene_batch_dispatcher = M06SceneGenerationBatchDispatcher(
                    batch_repository=batch_repository,
                    operation_port=scene_operation_port,
                )
                scene_batch_port = M06SceneGenerationBatchOperationPort(
                    batch_repository=batch_repository,
                    dispatcher=scene_batch_dispatcher,
                    credential_store=batch_credential_store,
                )
                batch_dispatcher_worker = M06SceneGenerationBatchDispatcherWorker(
                    batch_repository=batch_repository,
                    video_repository=video_agent_repository,
                    dispatcher=scene_batch_dispatcher,
                    credential_store=batch_credential_store,
                    worker_id=f"gateway-operation-batch-dispatch:{os.getpid()}",
                )
                await batch_dispatcher_worker.start()
                app.state.pixelflow_operation_batch_dispatcher_worker = (
                    batch_dispatcher_worker
                )
                video_tools = runtime_video_tool_registry(
                    plan_repository=video_agent_repository,
                    scene_generation_batch_operation_port=scene_batch_port,
                    video_understanding_port=video_understanding_adapter,
                    operation_batch_repository=batch_repository,
                )
            else:
                app.state.pixelflow_operation_batch_dispatcher_worker = None
                video_tools = runtime_video_tool_registry(
                    plan_repository=video_agent_repository,
                    video_understanding_port=video_understanding_adapter,
                    operation_batch_repository=batch_repository,
                )
            if image_provider is not None:
                from pixelflow.video.adapters.operations import (
                    M06ImageGenerationBatchDispatcher,
                    M06ImageGenerationBatchDispatcherWorker,
                    M06ImageGenerationBatchOperationPort,
                    M06ImageGenerationOperationPort,
                )
                image_operation_port = M06ImageGenerationOperationPort(
                    repository=agent_runtime_repository,
                    adapter=image_provider.as_operation_adapter(),
                    lease_owner=f"gateway-m06-image:{os.getpid()}",
                )
                image_batch_dispatcher = M06ImageGenerationBatchDispatcher(
                    batch_repository=batch_repository,
                    operation_port=image_operation_port,
                )
                image_batch_port = M06ImageGenerationBatchOperationPort(
                    batch_repository=batch_repository,
                    credential_store=batch_credential_store,
                )
                image_dispatcher_worker = M06ImageGenerationBatchDispatcherWorker(
                    batch_repository=batch_repository,
                    video_repository=video_agent_repository,
                    dispatcher=image_batch_dispatcher,
                    credential_store=batch_credential_store,
                    worker_id=f"gateway-m06-image-dispatch:{os.getpid()}",
                )
                await image_dispatcher_worker.start()
                app.state.pixelflow_image_batch_dispatcher_worker = image_dispatcher_worker
                video_tools = runtime_video_tool_registry(
                    plan_repository=video_agent_repository,
                    scene_generation_batch_operation_port=scene_batch_port if video_provider is not None else None,
                    video_understanding_port=video_understanding_adapter,
                    image_generation_batch_operation_port=image_batch_port,
                    operation_batch_repository=batch_repository,
                )
            else:
                app.state.pixelflow_image_batch_dispatcher_worker = None
            tool_manifest = manifest(video_tools)
            app.state.pixelflow_agent_tool_broker = AgentToolBroker(
                agent_tool_repository,
                video_agent_repository,
                video_tools=video_tools,
                credential_store=credential_store,
                manifest_snapshot=tool_manifest,
            )
            if video_provider is not None or image_provider is not None:
                from pixelflow.operations.jobs import (
                    MappingProviderJobAdapterResolver,
                    OperationRecoveryRuntime,
                )

                async def _is_video_batch_operation(user_id: str, operation: object) -> bool:
                    job_id = getattr(operation, "job_id", None)
                    conversation_id = getattr(operation, "conversation_id", None)
                    if not isinstance(job_id, str) or not isinstance(conversation_id, str):
                        return False
                    return (
                        await batch_repository.get_batch_for_child_job(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            job_id=job_id,
                        )
                    ) is not None

                operation_recovery_runtime = OperationRecoveryRuntime(
                    agent_runtime_repository,
                    resolver=MappingProviderJobAdapterResolver(
                        {
                            **({"generate_scene:*": video_provider.as_operation_adapter()} if video_provider is not None else {}),
                            **({"generate_image_asset:*": image_provider.as_operation_adapter()} if image_provider is not None else {}),
                        }
                    ),
                    resumer=batch_terminal_callback,
                    worker_id=f"gateway-m06-video:{os.getpid()}",
                    candidate_filter=_is_video_batch_operation,
                )
                await operation_recovery_runtime.start()
                app.state.pixelflow_m06_operation_recovery_runtime = (
                    operation_recovery_runtime
                )
            else:
                app.state.pixelflow_m06_operation_recovery_runtime = None
            from pixelflow.platform import HarnessSidecarSettings

            harness_settings = HarnessSidecarSettings.from_env()
            if harness_settings is not None:
                from pixelflow.agent_harness import AgentHarnessSidecarClient
                from pixelflow.agent_harness.projector import HarnessRunProjector

                binding_repository = agent_tool_repository
                from pixelflow.agent_harness.admission import SQLHarnessAdmissionRepository

                admission_repository = SQLHarnessAdmissionRepository(
                    task_store.session_factory,
                )
                await admission_repository.initialize(
                    initial_open=True,
                    updated_by=harness_settings.instance_id,
                )
                app.state.pixelflow_harness_admission_repository = admission_repository
                app.state.pixelflow_harness_run_bridge = AgentHarnessSidecarClient(
                    base_url=harness_settings.base_url,
                    gateway_jwt_signing_key=harness_settings.jwt_signing_key,
                    gateway_instance_id=harness_settings.instance_id,
                    repository=binding_repository,
                    timeout_seconds=harness_settings.request_timeout_seconds,
                    manifest_provider=lambda: tool_manifest,
                    on_run_bound=lambda run_id, request: _bind_run_credential_grant(
                        credential_store,
                        run_id=run_id,
                        grant_id=request.transient_credential_grant_id,
                    ),
                )
                app.state.pixelflow_harness_run_projector = HarnessRunProjector(
                    binding_repository=binding_repository,
                    event_repository=agent_runtime_repository,
                    task_store=task_store,
                    video_repository=video_agent_repository,
                )
                from pixelflow.agent_control_plane.run_bridge import AgentRunBridge

                app.state.pixelflow_agent_run_bridge = AgentRunBridge(
                    harness=app.state.pixelflow_harness_run_bridge,
                    projector=app.state.pixelflow_harness_run_projector,
                )
                from pixelflow.agent_harness.recovery import HarnessRecoveryService

                app.state.pixelflow_harness_recovery_service = HarnessRecoveryService(
                    binding_repository=binding_repository,
                    task_store=task_store,
                    video_repository=video_agent_repository,
                )
                from pixelflow.agent_harness.operation_batch_resume import (
                    GatewayOperationBatchResumePort,
                    OperationBatchResumeWorker,
                )
                batch_resume_worker = OperationBatchResumeWorker(
                    repository=batch_repository,
                    resume_port=GatewayOperationBatchResumePort(
                        task_store=task_store,
                        video_repository=video_agent_repository,
                        bridge=app.state.pixelflow_agent_run_bridge,
                    ),
                    worker_id=f"gateway-operation-batch:{harness_settings.instance_id}",
                )
                await batch_resume_worker.start()
                app.state.pixelflow_operation_batch_resume_worker = batch_resume_worker
            else:
                app.state.pixelflow_harness_run_bridge = None
                app.state.pixelflow_harness_run_projector = None
                app.state.pixelflow_harness_recovery_service = None
                app.state.pixelflow_harness_admission_repository = None
                app.state.pixelflow_agent_run_bridge = None
                app.state.pixelflow_operation_batch_resume_worker = None
        elif isinstance(task_store, MemoryPixelFlowTaskStore):
            agent_runtime_repository = MemoryCompactionQueueRepository()
            app.state.pixelflow_agent_runtime_repository = agent_runtime_repository
            video_agent_repository = MemoryVideoAgentRepository(
                event_repository=agent_runtime_repository,
            )
            app.state.pixelflow_agent_tool_broker = None
            app.state.pixelflow_harness_run_bridge = None
            app.state.pixelflow_harness_video_repository = None
            app.state.pixelflow_harness_run_projector = None
            app.state.pixelflow_harness_recovery_service = None
            app.state.pixelflow_harness_admission_repository = None
            app.state.pixelflow_operation_batch_resume_worker = None
            app.state.pixelflow_operation_batch_repository = None
            app.state.pixelflow_operation_batch_terminal_callback = None
            app.state.pixelflow_operation_batch_terminal_worker = None
            app.state.pixelflow_transient_run_credential_store = None
            app.state.pixelflow_transient_batch_credential_store = None
            app.state.pixelflow_operation_batch_dispatcher_worker = None
            app.state.pixelflow_video_generation_provider = None
            app.state.pixelflow_image_generation_provider = None
            app.state.pixelflow_image_batch_dispatcher_worker = None
            app.state.pixelflow_m06_operation_recovery_runtime = None
        else:
            # MySQL 对话 Store 尚无同事务 Runtime Repository，保持 R1 压缩并固定关闭V2执行。
            agent_runtime_repository = MemoryCompactionQueueRepository()
            app.state.pixelflow_agent_runtime_repository = agent_runtime_repository
            video_agent_repository = None
            app.state.pixelflow_agent_tool_broker = None
            app.state.pixelflow_harness_run_bridge = None
            app.state.pixelflow_harness_video_repository = None
            app.state.pixelflow_harness_run_projector = None
            app.state.pixelflow_harness_recovery_service = None
            app.state.pixelflow_harness_admission_repository = None
            app.state.pixelflow_operation_batch_resume_worker = None
            app.state.pixelflow_operation_batch_repository = None
            app.state.pixelflow_operation_batch_terminal_callback = None
            app.state.pixelflow_operation_batch_terminal_worker = None
            app.state.pixelflow_transient_run_credential_store = None
            app.state.pixelflow_transient_batch_credential_store = None
            app.state.pixelflow_operation_batch_dispatcher_worker = None
            app.state.pixelflow_video_generation_provider = None
            app.state.pixelflow_image_generation_provider = None
            app.state.pixelflow_image_batch_dispatcher_worker = None
            app.state.pixelflow_m06_operation_recovery_runtime = None
        # M1：旧 VideoAgent、Native Invoker 与其恢复 Worker 已删除；Run 仅经 Harness Sidecar。

        from pixelflow.tracing import configure_trace_sink

        conversation_trace_store = app.state.pixelflow_task_store

        async def _write_conversation_trace_event(conversation_id: str, event: str, data: dict, user_id: str | None) -> None:
            await conversation_trace_store.append_trace_event(conversation_id, event, data, user_id=user_id)

        configure_trace_sink(_write_conversation_trace_event)

        try:
            yield
        finally:
            operation_recovery_runtime = getattr(
                app.state,
                "pixelflow_m06_operation_recovery_runtime",
                None,
            )
            if operation_recovery_runtime is not None:
                await operation_recovery_runtime.aclose()
                logger.info("PixelFlow M06 Video Operation Recovery Runtime closed")
            operation_batch_terminal_worker = getattr(
                app.state,
                "pixelflow_operation_batch_terminal_worker",
                None,
            )
            if operation_batch_terminal_worker is not None:
                await operation_batch_terminal_worker.aclose()
                logger.info("PixelFlow OperationBatch Terminal Worker closed")
            operation_batch_dispatcher_worker = getattr(
                app.state,
                "pixelflow_operation_batch_dispatcher_worker",
                None,
            )
            if operation_batch_dispatcher_worker is not None:
                await operation_batch_dispatcher_worker.aclose()
                logger.info("PixelFlow OperationBatch Dispatcher Worker closed")
            pixelflow_harness_run_projector = getattr(
                app.state,
                "pixelflow_harness_run_projector",
                None,
            )
            if pixelflow_harness_run_projector is not None:
                await pixelflow_harness_run_projector.aclose()
                logger.info("PixelFlow Harness Event Projector closed")
            operation_batch_resume_worker = getattr(
                app.state,
                "pixelflow_operation_batch_resume_worker",
                None,
            )
            if operation_batch_resume_worker is not None:
                await operation_batch_resume_worker.aclose()
                logger.info("PixelFlow OperationBatch Resume Worker closed")
            transient_run_credential_store = getattr(
                app.state,
                "pixelflow_transient_run_credential_store",
                None,
            )
            if transient_run_credential_store is not None:
                await transient_run_credential_store.aclose()
            transient_batch_credential_store = getattr(
                app.state,
                "pixelflow_transient_batch_credential_store",
                None,
            )
            if transient_batch_credential_store is not None:
                await transient_batch_credential_store.aclose()
            video_generation_provider = getattr(
                app.state,
                "pixelflow_video_generation_provider",
                None,
            )
            if video_generation_provider is not None:
                await video_generation_provider.aclose()
            image_batch_dispatcher_worker = getattr(
                app.state,
                "pixelflow_image_batch_dispatcher_worker",
                None,
            )
            if image_batch_dispatcher_worker is not None:
                await image_batch_dispatcher_worker.aclose()
                logger.info("PixelFlow Image OperationBatch Dispatcher Worker closed")
            image_generation_provider = getattr(
                app.state,
                "pixelflow_image_generation_provider",
                None,
            )
            if image_generation_provider is not None:
                await image_generation_provider.aclose()
            pixelflow_harness_run_bridge = getattr(
                app.state,
                "pixelflow_harness_run_bridge",
                None,
            )
            if pixelflow_harness_run_bridge is not None:
                await pixelflow_harness_run_bridge.aclose()
                logger.info("PixelFlow Harness Sidecar Client closed")
            pixelflow_mysql_engine = getattr(app.state, "pixelflow_mysql_engine", None)
            if pixelflow_mysql_engine is not None:
                await pixelflow_mysql_engine.dispose()
                logger.info("PixelFlow MySQL task store closed")
            pixelflow_preference_mysql_engine = getattr(app.state, "pixelflow_preference_mysql_engine", None)
            if pixelflow_preference_mysql_engine is not None:
                await pixelflow_preference_mysql_engine.dispose()
                logger.info("PixelFlow MySQL preference store closed")
            long_term_memory_service = getattr(
                app.state,
                "pixelflow_long_term_memory_service",
                None,
            )
            if long_term_memory_service is not None:
                await long_term_memory_service.aclose()
            memory_write_worker = getattr(
                app.state,
                "pixelflow_long_term_memory_write_worker",
                None,
            )
            if memory_write_worker is not None:
                await memory_write_worker.aclose()
            await close_pixelflow_engine()
            logger.info("PixelFlow persistence engine closed")
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

- **Conversations**: 创建对话、提交 Harness Turn、读取 Snapshot 和 SSE。
- **Harness Tool Broker**: 只供 Sidecar 调用的受控业务 Tool 接口。
- **Auth**: 所有非公开接口使用 content-app 的 `Authorization: Bearer <token>`。

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
            {"name": "pixelflow-conversations", "description": "Harness 会话、Run、Snapshot 与 SSE"},
            {"name": "internal-agent-tools", "description": "仅供 Sidecar 调用的受控 Tool Broker"},
            {"name": "auth", "description": "当前 content-app 用户身份"},
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
    # Auth API：只保留 /agent/auth/me，用于查看 content-app 当前用户。
    app.include_router(auth.router)

    # 长期记忆人工重放 API：只允许 owner 重新排队自己的 manual_review 写入。
    app.include_router(long_term_memory.router)

    # PixelFlow 对话与 Harness Run API：/agent/conversations。
    app.include_router(pixelflow_conversations.router)

    # 仅 Sidecar 服务身份可调用的 Capability Tool Broker：/agent/internal/agent-tools。
    app.include_router(internal_agent_tools.router)

    @app.get("/live", tags=["health"])
    async def liveness_check() -> dict[str, str]:
        """返回 Gateway 进程存活状态，不读取数据库、Sidecar 或用户身份。"""

        return {"status": "live", "service": "pixelflow-gateway"}

    @app.get("/ready", tags=["health"])
    async def readiness_check() -> dict[str, str]:
        """确认 Harness Run Bridge 已在生命周期中装配，未就绪时禁止流量进入。"""

        if getattr(app.state, "pixelflow_harness_run_bridge", None) is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "harness_bridge_unavailable"},
            )
        return {"status": "ready", "service": "pixelflow-gateway"}

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        """保留既有兼容健康检查端点；新编排应使用 /live 与 /ready。"""

        return await liveness_check()

    return app


# 供 uvicorn 导入的应用实例。
app = create_app()
