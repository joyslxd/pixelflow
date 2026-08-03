"""把真实 PixelFlow Supervisor、视频 Handler 与恢复 Worker 绑定到 Gateway。"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from langgraph.types import Checkpointer

from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.context import (
    ContextAssembler,
    ContextBudgetPolicyProvider,
    ModelContextProfile,
    RepositoryContextSnapshotSource,
)
from pixelflow.agent_runtime.contracts import WorkflowKind
from pixelflow.agent_runtime.executor import SupervisorTurnExecutor
from pixelflow.agent_runtime.graph import (
    AgentRuntimeGraphComposition,
    WorkflowCommandHandler,
    WorkflowRegistry,
    compose_agent_runtime_graph,
)
from pixelflow.agent_runtime.graph.registry import LiveWorkflowCommandHandler
from pixelflow.agent_runtime.jobs import OperationRecoveryRuntime
from pixelflow.agent_runtime.persistence import VideoRuntimeRepository
from pixelflow.agent_runtime.supervisor import (
    DecisionValidator,
    DeterministicTargetResolver,
    LLMActionClassifier,
    SupervisorDecisionService,
)
from pixelflow.agent_workflows.video.live_handler import VideoLiveWorkflowHandler
from pixelflow.agent_workflows.video.live_operations import (
    TransientCredentialVault,
    VideoLiveOperationBridge,
    VideoOperationAdapterResolver,
    VideoOperationCompletionHandler,
    VideoOperationQuotaStateHandler,
)
from pixelflow.tasks import PixelFlowTaskStore

from .pixelflow_agent_live_capabilities import GatewayVideoLiveCapabilities
from .pixelflow_agent_live_providers import (
    VIDEO_LIVE_HANDLER_NOT_READY,
    VideoLiveProviderAdapters,
)

logger = logging.getLogger(__name__)
_GRAPH_STATE_ATTRIBUTE = "pixelflow_agent_graph_runtime"
_LIVE_STATE_ATTRIBUTE = "pixelflow_agent_live_runtime"
_SUPERVISOR_BUDGET_NODE = "supervisor_decision"


class GatewayWorkflowRegistry(WorkflowRegistry):
    """生产注册表只保存显式安装的业务 Handler，不提供隐式 fake。"""

    def __init__(
        self,
        handlers: Mapping[
            WorkflowKind,
            WorkflowCommandHandler | LiveWorkflowCommandHandler,
        ],
    ) -> None:
        self._handlers = MappingProxyType(dict(handlers))

    def resolve(
        self,
        kind: WorkflowKind,
    ) -> WorkflowCommandHandler | LiveWorkflowCommandHandler:
        handler = self._handlers.get(kind)
        if handler is None:
            value = getattr(kind, "value", "unknown")
            raise LookupError(f"Gateway 未注册 {value} Workflow 处理器")
        return handler


@dataclass(slots=True)
class PixelFlowAgentLiveRuntime:
    """聚合一次 Gateway live 装配及其安全就绪状态。"""

    config: AgentRuntimeConfig
    repository: VideoRuntimeRepository | None
    registry: GatewayWorkflowRegistry | None = None
    video_handler: WorkflowCommandHandler | LiveWorkflowCommandHandler | None = None
    graph_runtime: AgentRuntimeGraphComposition | None = None
    executor: SupervisorTurnExecutor | None = None
    operation_recovery: OperationRecoveryRuntime | None = None
    quota_handler: VideoOperationQuotaStateHandler | None = None
    reason_code: str | None = VIDEO_LIVE_HANDLER_NOT_READY
    registered_intents: frozenset[str] = frozenset()
    primary_execution_intents: frozenset[str] = frozenset()
    _executor_started: bool = field(default=False, repr=False)
    _recovery_started: bool = field(default=False, repr=False)
    _closed: bool = field(default=False, repr=False)

    @property
    def ready(self) -> bool:
        return (
            self.reason_code is None
            and self.registry is not None
            and self.video_handler is not None
            and self.graph_runtime is not None
            and self.executor is not None
            and self.operation_recovery is not None
            and self.quota_handler is not None
            and self._executor_started
            and self._recovery_started
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def status_snapshot(self) -> dict[str, Any]:
        """只返回有限状态、聚合指标和固定原因，不暴露业务或供应商标识。"""

        return {
            "ready": self.ready,
            "registered_intents": sorted(self.registered_intents),
            "reason_code": self.reason_code,
            "executor": {
                "started": self._executor_started,
                "metrics": (
                    self.executor.metrics_snapshot()
                    if self.executor is not None
                    else None
                ),
            },
            "operation_recovery": {"started": self._recovery_started},
            "closed": self._closed,
        }


@asynccontextmanager
async def make_pixelflow_agent_graph_runtime(
    app: FastAPI,
    *,
    checkpointer: Checkpointer,
    registry: WorkflowRegistry,
) -> AsyncGenerator[AgentRuntimeGraphComposition, None]:
    """挂载使用真实注册表的图，并在共享 checkpointer 关闭前移除引用。"""

    if getattr(app.state, _GRAPH_STATE_ATTRIBUTE, None) is not None:
        raise RuntimeError("PixelFlow Agent 图运行时不可重复挂载")
    if registry is None:
        raise ValueError("Gateway Agent 图必须显式提供 WorkflowRegistry")

    runtime = compose_agent_runtime_graph(
        registry=registry,
        checkpointer=checkpointer,
    )
    setattr(app.state, _GRAPH_STATE_ATTRIBUTE, runtime)
    try:
        yield runtime
    finally:
        await runtime.aclose()
        if getattr(app.state, _GRAPH_STATE_ATTRIBUTE, None) is runtime:
            delattr(app.state, _GRAPH_STATE_ATTRIBUTE)


@asynccontextmanager
async def make_pixelflow_agent_live_runtime(
    app: FastAPI,
    *,
    config: AgentRuntimeConfig,
    repository: VideoRuntimeRepository | None,
    task_store: PixelFlowTaskStore,
    checkpointer: Checkpointer,
    capabilities: GatewayVideoLiveCapabilities,
    providers: VideoLiveProviderAdapters,
    model_name: str,
    model_profiles: Mapping[str, ModelContextProfile],
    memory_search: Any,
    clock: Any,
) -> AsyncGenerator[PixelFlowAgentLiveRuntime, None]:
    """按全有或全无规则启动 Registry、Executor 和 Operation 恢复。"""

    if getattr(app.state, _LIVE_STATE_ATTRIBUTE, None) is not None:
        raise RuntimeError("PixelFlow Agent live Runtime 不可重复挂载")
    runtime = PixelFlowAgentLiveRuntime(
        config=AgentRuntimeConfig.model_validate(config.model_dump()),
        repository=repository,
    )
    setattr(app.state, _LIVE_STATE_ATTRIBUTE, runtime)
    stack = AsyncExitStack()
    try:
        if repository is not None and capabilities.ready and providers.ready:
            await _assemble_ready_runtime(
                app,
                runtime=runtime,
                stack=stack,
                task_store=task_store,
                checkpointer=checkpointer,
                capabilities=capabilities,
                providers=providers,
                model_name=model_name,
                model_profiles=model_profiles,
                memory_search=memory_search,
                clock=clock,
            )
        yield runtime
    finally:
        try:
            await _close_live_runtime(runtime)
        finally:
            try:
                await stack.aclose()
            finally:
                runtime._closed = True
                if getattr(app.state, _LIVE_STATE_ATTRIBUTE, None) is runtime:
                    delattr(app.state, _LIVE_STATE_ATTRIBUTE)


async def _assemble_ready_runtime(
    app: FastAPI,
    *,
    runtime: PixelFlowAgentLiveRuntime,
    stack: AsyncExitStack,
    task_store: PixelFlowTaskStore,
    checkpointer: Checkpointer,
    capabilities: GatewayVideoLiveCapabilities,
    providers: VideoLiveProviderAdapters,
    model_name: str,
    model_profiles: Mapping[str, ModelContextProfile],
    memory_search: Any,
    clock: Any,
) -> None:
    """先完整构造依赖，再启动后台 Worker；任一步失败都回到固定降级态。"""

    graph_runtime: AgentRuntimeGraphComposition | None = None
    executor: SupervisorTurnExecutor | None = None
    recovery: OperationRecoveryRuntime | None = None
    quota_handler: VideoOperationQuotaStateHandler | None = None
    try:
        if (
            runtime.repository is None
            or capabilities.capabilities is None
            or capabilities.decision_model is None
            or capabilities.answer_port is None
            or not callable(getattr(clock, "now", None))
            or not callable(getattr(memory_search, "search", None))
        ):
            return
        normalized_model_name = model_name.strip()
        profiles = dict(model_profiles)
        budget_provider = ContextBudgetPolicyProvider(runtime.config.context_budget)
        budget_provider.resolve_model_profile(
            normalized_model_name,
            profiles,
            now=clock.now(),
        )
        worker_suffix = uuid4().hex
        credential_vault = TransientCredentialVault()
        operation_bridge = VideoLiveOperationBridge(
            repository=runtime.repository,
            resolver=VideoOperationAdapterResolver(providers.adapters),
            lease_owner=f"gateway-video-start:{worker_suffix}",
            clock=clock,
        )
        base_handler = VideoLiveWorkflowHandler(
            repository=runtime.repository,
            capabilities=capabilities.capabilities,
            credential_provider=credential_vault,
            clock=clock,
            operation_port=operation_bridge,
        )
        scoped_handler = capabilities.scope_handler(base_handler)
        registry = GatewayWorkflowRegistry(
            {WorkflowKind.VIDEO: scoped_handler},
        )
        graph_runtime = await stack.enter_async_context(
            make_pixelflow_agent_graph_runtime(
                app,
                checkpointer=checkpointer,
                registry=registry,
            )
        )
        context_assembler = ContextAssembler(
            source=RepositoryContextSnapshotSource(
                task_store=task_store,
                repository=runtime.repository,
            ),
            model_name=normalized_model_name,
            model_profiles=profiles,
            budget_node=_SUPERVISOR_BUDGET_NODE,
            memory_search=memory_search,
            clock=clock.now,
            budget_policy_provider=budget_provider,
        )
        decision_service = SupervisorDecisionService(
            resolver=DeterministicTargetResolver(),
            classifier=LLMActionClassifier(capabilities.decision_model),
            validator=DecisionValidator(),
            context_assembler=context_assembler,
            answer_port=capabilities.answer_port,
        )
        executor = SupervisorTurnExecutor(
            repository=runtime.repository,
            task_store=task_store,
            decision_service=decision_service,
            graph=graph_runtime.graph,
            credential_vault=credential_vault,
            clock=clock.now,
            worker_id=f"gateway-supervisor:{worker_suffix}",
        )
        completion_handler = VideoOperationCompletionHandler(
            repository=runtime.repository,
            operations=operation_bridge,
            clock=clock,
            graph=graph_runtime.graph,
            external_job_observer=executor,
        )
        quota_handler = VideoOperationQuotaStateHandler(
            repository=runtime.repository,
            operations=operation_bridge,
            clock=clock,
            graph=graph_runtime.graph,
        )
        recovery = operation_bridge.build_recovery_runtime(
            resumer=completion_handler,
            worker_id=f"gateway-video-recovery:{worker_suffix}",
            quota_resumer=quota_handler,
        )
        await executor.start()
        runtime._executor_started = True
        await recovery.start()
        runtime._recovery_started = True
    except Exception as error:  # noqa: BLE001 - readiness 对外只暴露固定原因码
        logger.warning(
            "PixelFlow Agent live Runtime 构造失败：exception_type=%s",
            type(error).__name__,
        )
        await _close_component_safely(recovery, label="operation_recovery")
        await _close_component_safely(executor, label="turn_executor")
        runtime._executor_started = False
        runtime._recovery_started = False
        await _close_component_safely(stack, label="graph_runtime")
        return

    runtime.registry = registry
    runtime.video_handler = scoped_handler
    runtime.graph_runtime = graph_runtime
    runtime.executor = executor
    runtime.operation_recovery = recovery
    runtime.quota_handler = quota_handler
    runtime.reason_code = None
    runtime.registered_intents = frozenset({"video"})
    runtime.primary_execution_intents = frozenset(
        set(runtime.config.enabled_intents) & runtime.registered_intents
    )


async def _close_live_runtime(runtime: PixelFlowAgentLiveRuntime) -> None:
    """按恢复 Worker、Turn Executor、Graph 的逆序关闭进程内资源。"""

    if runtime.operation_recovery is not None:
        await _close_component_safely(
            runtime.operation_recovery,
            label="operation_recovery",
        )
        runtime._recovery_started = False
    if runtime.executor is not None:
        await _close_component_safely(
            runtime.executor,
            label="turn_executor",
        )
        runtime._executor_started = False


async def _close_component_safely(component: Any | None, *, label: str) -> None:
    """关闭 live 局部资源，异常只保留固定组件名与类型。"""

    if component is None:
        return
    try:
        await component.aclose()
    except Exception as error:  # noqa: BLE001 - 关闭异常不得中断 R1 生命周期
        logger.warning(
            "PixelFlow Agent live 组件关闭失败：component=%s exception_type=%s",
            label,
            type(error).__name__,
        )


__all__ = [
    "GatewayWorkflowRegistry",
    "PixelFlowAgentLiveRuntime",
    "make_pixelflow_agent_graph_runtime",
    "make_pixelflow_agent_live_runtime",
]
