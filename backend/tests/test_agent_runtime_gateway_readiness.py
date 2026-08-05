from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway import pixelflow_agent_runtime as live_runtime_module
from app.gateway.pixelflow_agent_live_capabilities import (
    make_pixelflow_agent_live_capabilities,
)
from app.gateway.pixelflow_agent_live_providers import (
    VIDEO_LIVE_HANDLER_NOT_READY,
    make_video_live_provider_adapters,
)
from app.gateway.pixelflow_agent_runtime import (
    GatewayWorkflowRegistry,
    make_pixelflow_agent_live_runtime,
)
from deerflow.config.app_config import AppConfig
from deerflow.persistence.base import Base
from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.context import ModelContextProfile
from pixelflow.agent_runtime.contracts import WorkflowKind
from pixelflow.agent_runtime.jobs import ProviderJobOutcome
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_SUPPORT_TABLES,
    AGENT_RUNTIME_TABLES,
    MemoryVideoRuntimeRepository,
    SQLVideoRuntimeRepository,
    VideoRuntimeRepository,
)
from pixelflow.agent_runtime.service import AgentRuntimeService
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowTaskStore,
    SQLPixelFlowTaskStore,
)
from pixelflow.tasks.model import (
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
)

RepositoryKind = Literal["memory", "sql"]
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _SceneAssetSkill:
    async def reference_image(self, **_kwargs: Any) -> dict[str, object]:
        return {}

    async def text_to_image(self, **_kwargs: Any) -> dict[str, object]:
        return {}


class _Model:
    async def ainvoke(self, _messages: Any) -> str:
        return "{}"


class _PowerMemService:
    async def search(self, **_kwargs: Any) -> list[Any]:
        return []

    async def record(self, **_kwargs: Any) -> bool:
        return True


class _ExistingJobService:
    def __init__(self) -> None:
        self.start_calls = 0
        self.status_calls = 0

    async def start(
        self,
        request: Mapping[str, object],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        del request, authorization, idempotency_key
        self.start_calls += 1
        return {"job_id": "provider-job", "status": "running"}

    async def status(self, provider_job_id: str) -> dict[str, object]:
        self.status_calls += 1
        return {"job_id": provider_job_id, "status": "running"}


@asynccontextmanager
async def _repository(
    kind: RepositoryKind,
) -> AsyncIterator[tuple[VideoRuntimeRepository, PixelFlowTaskStore]]:
    if kind == "memory":
        task_store = MemoryPixelFlowTaskStore()
        yield MemoryVideoRuntimeRepository(task_store=task_store), task_store
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=(
                    AGENT_RUNTIME_TABLES
                    + AGENT_RUNTIME_SUPPORT_TABLES
                    + (
                        PixelFlowConversationRow.__table__,
                        PixelFlowConversationMessageRow.__table__,
                    )
                ),
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    task_store = SQLPixelFlowTaskStore(session_factory)
    try:
        yield (
            SQLVideoRuntimeRepository(session_factory, task_store=task_store),
            task_store,
        )
    finally:
        await engine.dispose()


def _config(*enabled_intents: str) -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        mode="primary",
        enabled_intents=enabled_intents,
        new_conversation_rollout_percent=100,
        context_compaction_enabled=True,
    )


def _profiles() -> dict[str, ModelContextProfile]:
    return {
        "deepseek-v4-pro": ModelContextProfile(
            model_name="deepseek-v4-pro",
            max_context_tokens=1_000_000,
            max_output_tokens=32 * 1024,
            tokenizer_strategy="verified_gateway_test",
            verified_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            source="Gateway readiness 测试档案",
        )
    }


def _capabilities() -> Any:
    return make_pixelflow_agent_live_capabilities(
        model_factory=lambda *_args, **_kwargs: _Model(),
        scene_asset_skill_factory=_SceneAssetSkill,
        power_mem_service=_PowerMemService(),
        clock=_Clock(),
        model_name="deepseek-v4-pro",
    )


def _providers() -> tuple[Any, tuple[_ExistingJobService, ...]]:
    services = tuple(_ExistingJobService() for _index in range(4))
    return (
        make_video_live_provider_adapters(
            generate_scene_video=services[0],
            merge_video=services[1],
            quality_review=services[2],
            jianying_draft=services[3],
        ),
        services,
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_gateway_registers_same_real_video_handler_only_when_ready(
    kind: RepositoryKind,
) -> None:
    app = FastAPI()
    providers, services = _providers()
    async with _repository(kind) as (repository, task_store):
        async with make_pixelflow_agent_live_runtime(
            app,
            config=_config("video"),
            repository=repository,
            task_store=task_store,
            checkpointer=InMemorySaver(),
            capabilities=_capabilities(),
            providers=providers,
            model_name="deepseek-v4-pro",
            model_profiles=_profiles(),
            memory_search=_PowerMemService(),
            clock=_Clock(),
        ) as runtime:
            assert runtime.ready is True
            assert runtime.repository is repository
            assert isinstance(runtime.registry, GatewayWorkflowRegistry)
            assert runtime.registry.resolve(WorkflowKind.VIDEO) is runtime.video_handler
            assert runtime.graph_runtime is not None
            assert runtime.graph_runtime.registry is runtime.registry
            assert app.state.pixelflow_agent_graph_runtime is runtime.graph_runtime
            assert app.state.pixelflow_agent_live_runtime is runtime
            assert runtime.registered_intents == frozenset({"video"})
            assert runtime.primary_execution_intents == frozenset({"video"})
            assert runtime.executor is not None
            assert runtime.operation_recovery is not None
            snapshot = runtime.status_snapshot()
            assert snapshot["ready"] is True
            assert snapshot["registered_intents"] == ["video"]
            assert snapshot["reason_code"] is None
            assert snapshot["executor"]["started"] is True
            assert snapshot["operation_recovery"]["started"] is True
            assert "provider-job" not in json.dumps(snapshot)

    assert all(service.start_calls == 0 for service in services)
    assert all(service.status_calls == 0 for service in services)
    assert runtime.closed is True
    assert not hasattr(app.state, "pixelflow_agent_live_runtime")
    assert not hasattr(app.state, "pixelflow_agent_graph_runtime")
    await asyncio.sleep(0)
    worker_names = {
        task.get_name()
        for task in asyncio.all_tasks()
        if not task.done()
    }
    assert "supervisor-turn-scan" not in worker_names
    assert not any(name.startswith("operation-recovery:") for name in worker_names)


@pytest.mark.parametrize(
    "missing",
    ["quota_handler", "graph", "providers", "repository"],
)
@pytest.mark.asyncio
async def test_gateway_keeps_video_on_v2_when_quota_recovery_is_incomplete(
    missing: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任一配额恢复依赖缺失时不得注册 live video 或启动恢复任务。"""

    app = FastAPI()
    providers, _services = _providers()
    live_repository: VideoRuntimeRepository | None

    if missing == "quota_handler":
        class _UnavailableQuotaHandler:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("测试模拟 quota handler 不可用")

        monkeypatch.setattr(
            live_runtime_module,
            "VideoOperationQuotaStateHandler",
            _UnavailableQuotaHandler,
            raising=False,
        )
    elif missing == "graph":
        @asynccontextmanager
        async def _unavailable_graph(*_args: Any, **_kwargs: Any):
            raise RuntimeError("测试模拟 Graph 不可用")
            yield

        monkeypatch.setattr(
            live_runtime_module,
            "make_pixelflow_agent_graph_runtime",
            _unavailable_graph,
        )
    elif missing == "providers":
        providers = make_video_live_provider_adapters()

    async with _repository("memory") as (repository, task_store):
        live_repository = None if missing == "repository" else repository
        async with make_pixelflow_agent_live_runtime(
            app,
            config=_config("video"),
            repository=live_repository,
            task_store=task_store,
            checkpointer=InMemorySaver(),
            capabilities=_capabilities(),
            providers=providers,
            model_name="deepseek-v4-pro",
            model_profiles=_profiles(),
            memory_search=_PowerMemService(),
            clock=_Clock(),
        ) as runtime:
            assert runtime.ready is False
            assert runtime.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
            assert runtime.registered_intents == frozenset()
            assert runtime.primary_execution_intents == frozenset()
            assert runtime.registry is None
            assert runtime.video_handler is None
            assert runtime.graph_runtime is None
            assert runtime.executor is None
            assert runtime.operation_recovery is None
            assert runtime.quota_handler is None

    assert runtime.closed is True
    assert not hasattr(app.state, "pixelflow_agent_live_runtime")
    assert not hasattr(app.state, "pixelflow_agent_graph_runtime")
    await asyncio.sleep(0)
    worker_names = {
        task.get_name()
        for task in asyncio.all_tasks()
        if not task.done()
    }
    assert "supervisor-turn-scan" not in worker_names
    assert not any(name.startswith("operation-recovery:") for name in worker_names)


@pytest.mark.asyncio
async def test_gateway_wires_quota_and_completion_to_same_live_graph() -> None:
    """就绪 Gateway 的完成与配额处理器必须共享 Graph、Repository 与 Bridge。"""

    app = FastAPI()
    providers, _services = _providers()
    async with _repository("memory") as (repository, task_store):
        async with make_pixelflow_agent_live_runtime(
            app,
            config=_config("video"),
            repository=repository,
            task_store=task_store,
            checkpointer=InMemorySaver(),
            capabilities=_capabilities(),
            providers=providers,
            model_name="deepseek-v4-pro",
            model_profiles=_profiles(),
            memory_search=_PowerMemService(),
            clock=_Clock(),
        ) as runtime:
            assert runtime.ready is True
            assert runtime.registered_intents == frozenset({"video"})
            assert runtime.primary_execution_intents == frozenset({"video"})
            assert runtime.operation_recovery is not None
            assert runtime.graph_runtime is not None
            assert runtime.quota_handler is not None
            assert (
                runtime.operation_recovery.quota_resumer
                is runtime.quota_handler
            )
            with pytest.raises(AttributeError):
                setattr(runtime.operation_recovery, "quota_resumer", object())
            completion_handler = runtime.operation_recovery._resumer
            assert completion_handler._graph is runtime.graph_runtime.graph
            assert runtime.quota_handler._graph is runtime.graph_runtime.graph
            assert completion_handler._repository is repository
            assert runtime.quota_handler._repository is repository
            assert completion_handler._operations is runtime.quota_handler._operations
            assert runtime.quota_handler._external_job_observer is runtime.executor
            before = runtime.executor.metrics_snapshot()["external_job_states"]
            runtime.quota_handler._observe_external_job_state(
                ProviderJobOutcome.PAUSED_QUOTA,
            )
            runtime.quota_handler._observe_external_job_state(
                ProviderJobOutcome.POLLING,
            )
            after = runtime.executor.metrics_snapshot()["external_job_states"]
            assert after["paused_quota"] == before["paused_quota"] + 1
            assert after["polling"] == before["polling"] + 1


@pytest.mark.asyncio
async def test_primary_intents_are_configured_and_registered_intersection() -> None:
    app = FastAPI()
    providers, _services = _providers()
    async with _repository("memory") as (repository, task_store):
        async with make_pixelflow_agent_live_runtime(
            app,
            config=_config("image"),
            repository=repository,
            task_store=task_store,
            checkpointer=InMemorySaver(),
            capabilities=_capabilities(),
            providers=providers,
            model_name="deepseek-v4-pro",
            model_profiles=_profiles(),
            memory_search=_PowerMemService(),
            clock=_Clock(),
        ) as runtime:
            service = AgentRuntimeService(
                config=_config("image"),
                repository=repository,
                task_store=task_store,
                turn_executor=runtime.executor,
                video_repository=repository,
                primary_execution_intents=runtime.primary_execution_intents,
            )

            assert runtime.registered_intents == frozenset({"video"})
            assert runtime.primary_execution_intents == frozenset()
            assert service.primary_execution_intents == frozenset()


@pytest.mark.parametrize(
    "missing",
    ["capabilities", "providers", "model_profile", "repository"],
)
@pytest.mark.asyncio
async def test_gateway_fails_closed_without_half_handler_or_empty_graph(
    missing: str,
) -> None:
    app = FastAPI()
    providers, _services = _providers()
    capabilities = _capabilities()
    if missing == "providers":
        providers = make_video_live_provider_adapters()
    elif missing == "capabilities":
        capabilities = make_pixelflow_agent_live_capabilities(
            model_factory=None,
            scene_asset_skill_factory=None,
            power_mem_service=None,
            clock=None,
            model_name="",
        )
    async with _repository("memory") as (repository, task_store):
        live_repository = None if missing == "repository" else repository
        model_profiles = {} if missing == "model_profile" else _profiles()
        async with make_pixelflow_agent_live_runtime(
            app,
            config=_config("video"),
            repository=live_repository,
            task_store=task_store,
            checkpointer=InMemorySaver(),
            capabilities=capabilities,
            providers=providers,
            model_name="deepseek-v4-pro",
            model_profiles=model_profiles,
            memory_search=_PowerMemService(),
            clock=_Clock(),
        ) as runtime:
            assert runtime.ready is False
            assert runtime.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
            assert runtime.registered_intents == frozenset()
            assert runtime.primary_execution_intents == frozenset()
            assert runtime.registry is None
            assert runtime.video_handler is None
            assert runtime.graph_runtime is None
            assert runtime.executor is None
            assert runtime.operation_recovery is None
            assert not hasattr(app.state, "pixelflow_agent_graph_runtime")
            assert runtime.status_snapshot() == {
                "ready": False,
                "registered_intents": [],
                "reason_code": VIDEO_LIVE_HANDLER_NOT_READY,
                "executor": {"started": False, "metrics": None},
                "operation_recovery": {"started": False},
                "closed": False,
            }


@pytest.mark.asyncio
async def test_real_gateway_lifespan_routes_video_to_v2_entrypoint_without_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0 首计划不依赖 Provider，缺 Provider 时仍可进入 V2 VideoAgent。"""

    from app.gateway import app as gateway_app

    startup_config = AppConfig.model_validate(
        {
            "log_level": "info",
            "models": [
                {
                    "name": "deepseek-v4-pro",
                    "use": "langchain_openai:ChatOpenAI",
                    "model": "gateway-lifespan-test",
                    "api_key": "sk-fake-not-used",
                    "base_url": "https://example.invalid/v1",
                    "context_profile": {
                        "max_context_tokens": 1_000_000,
                        "max_output_tokens": 32 * 1024,
                        "tokenizer_strategy": "verified_gateway_test",
                        "verified_at": "2026-01-01T00:00:00+00:00",
                        "source": "Gateway lifespan 测试档案",
                    },
                }
            ],
            "sandbox": {
                "use": "deerflow.sandbox.local:LocalSandboxProvider",
            },
            "database": {"backend": "memory"},
            "run_events": {"backend": "memory"},
        }
    )
    monkeypatch.setattr(gateway_app, "load_profile_config", lambda: None)
    monkeypatch.setattr(gateway_app, "get_app_config", lambda: startup_config)
    monkeypatch.setattr(
        gateway_app,
        "validate_agent_runtime_startup_config",
        lambda: _config("video"),
    )
    monkeypatch.setenv("PIXELFLOW_SEMANTIC_MEMORY_ENABLED", "false")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "false")
    monkeypatch.delenv("PIXELFLOW_MYSQL_URL", raising=False)
    application = gateway_app.create_app()

    async with application.router.lifespan_context(application):
        live_runtime = application.state.pixelflow_agent_live_runtime
        service = application.state.pixelflow_agent_runtime_service
        assignment = service.assignment_for_new_conversation(
            {},
            initial_intent="video",
        )

        assert live_runtime.ready is False
        assert live_runtime.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
        assert live_runtime.graph_runtime is None
        assert not hasattr(application.state, "pixelflow_agent_graph_runtime")
        assert service.primary_execution_intents == frozenset({"video"})
        assert assignment.orchestration_mode.value == "supervisor_v1"
        assert assignment.context["__agent_runtime"][
            "primary_execution_ready"
        ] is True


@pytest.mark.asyncio
async def test_real_gateway_lifespan_keeps_r1_v2_available_without_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空模型列表只关闭 live 能力，不得阻断既有 R1/v2 Gateway。"""

    from app.gateway import app as gateway_app

    startup_config = AppConfig.model_validate(
        {
            "log_level": "info",
            "models": [],
            "sandbox": {
                "use": "deerflow.sandbox.local:LocalSandboxProvider",
            },
            "database": {"backend": "memory"},
            "run_events": {"backend": "memory"},
        }
    )
    monkeypatch.setattr(gateway_app, "load_profile_config", lambda: None)
    monkeypatch.setattr(gateway_app, "get_app_config", lambda: startup_config)
    monkeypatch.setattr(
        gateway_app,
        "validate_agent_runtime_startup_config",
        lambda: AgentRuntimeConfig(mode="off"),
    )
    monkeypatch.setenv("PIXELFLOW_SEMANTIC_MEMORY_ENABLED", "false")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "false")
    monkeypatch.delenv("PIXELFLOW_MYSQL_URL", raising=False)
    application = gateway_app.create_app()

    async with application.router.lifespan_context(application):
        live_runtime = application.state.pixelflow_agent_live_runtime
        service = application.state.pixelflow_agent_runtime_service
        assignment = service.assignment_for_new_conversation(
            {},
            initial_intent="video",
        )

        assert live_runtime.ready is False
        assert live_runtime.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
        assert live_runtime.registered_intents == frozenset()
        assert live_runtime.primary_execution_intents == frozenset()
        assert live_runtime.graph_runtime is None
        assert live_runtime.executor is None
        assert live_runtime.operation_recovery is None
        assert not hasattr(application.state, "pixelflow_agent_graph_runtime")
        assert service.primary_execution_intents == frozenset()
        assert assignment.orchestration_mode.value == "frontend_v2"

        worker_names = {
            task.get_name()
            for task in asyncio.all_tasks()
            if not task.done()
        }
        assert "supervisor-turn-scan" not in worker_names
        assert not any(name.startswith("operation-recovery:") for name in worker_names)
