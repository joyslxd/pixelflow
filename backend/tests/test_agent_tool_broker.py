"""验证 Gateway Tool Broker 使用真实 SQLite Workspace 与 Run binding。"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import jwt
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.routers import internal_agent_tools
from pixelflow.agent_tools import AgentToolBroker, SQLAgentToolRepository
from pixelflow.agent_tools.contracts import ToolCallRequest
from pixelflow.agent_tools.manifest import manifest
from pixelflow.agent_tools.repository import RunBinding
from pixelflow.agent_tools.video.credential_store import TransientRunCredentialStore
from pixelflow.agent_tools.video.delivery import ComposeOrExportVideoTool
from pixelflow.agent_tools.video.registry import VideoToolRegistry
from pixelflow.platform.persistence import Base
from pixelflow.video.contracts import AgentPlan, AgentPlanStatus, VideoWorkspace
from pixelflow.video.workspace import SQLVideoAgentRepository


def _free_port() -> int:
    """申请仅供真实 loopback Gateway 测试使用的临时端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _sidecar_service_jwt(signing_key: str) -> str:
    """签发仅用于 loopback Broker Case 的短期 Sidecar 服务 JWT。"""

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "m0-sidecar",
            "iss": "pixelflow-harness-sidecar",
            "aud": "pixelflow-tool-broker",
            "service_instance_id": "m0-sidecar-loopback",
            "iat": now,
            "exp": now.timestamp() + 300,
        },
        signing_key,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_inspect_tool_reads_bound_workspace_from_sqlite_and_replays_observation(tmp_path) -> None:
    """Tool 只能按 Run binding 读取真实 SQL Workspace，并稳定回放同一 Call。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-tools.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    video_repository = SQLVideoAgentRepository(session_factory)
    repository = SQLAgentToolRepository(session_factory)
    now = datetime.now(UTC)
    workspace = await video_repository.create_workspace(
        "m0-tool-user",
        VideoWorkspace(
            workspace_id="m0-tool-workspace",
            conversation_id="m0-tool-conversation",
            revision=1,
            payload={"script": "测试脚本", "assets": [], "scenes": []},
            created_at=now,
            updated_at=now,
        ),
    )
    current_manifest = manifest()
    binding = RunBinding(
        run_id="hrun_m0_tool_broker",
        session_id="pfh_m0_tool_broker",
        user_id="m0-tool-user",
        conversation_id="m0-tool-conversation",
        workspace_id=workspace.workspace_id,
        workspace_revision=workspace.revision,
        context_digest="sha256:" + "1" * 64,
        toolset_version="agent-tools-v1",
        tool_manifest_digest=current_manifest.digest,
        request_digest="sha256:" + "2" * 64,
    )
    await repository.register_run_binding(binding)
    broker = AgentToolBroker(repository, video_repository)
    request = ToolCallRequest(
        protocol_version="v1",
        run_id=binding.run_id,
        session_id=binding.session_id,
        tool_call_id="m0-inspect-call",
        tool_name="inspect_video_workspace",
        arguments={},
        expected_workspace_revision=workspace.revision,
        context_digest=binding.context_digest,
        toolset_version=binding.toolset_version,
    )
    try:
        idempotency_key = repository.tool_call_key(
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
        )
        first = await broker.call(request, idempotency_key=idempotency_key)
        replay = await broker.call(request, idempotency_key=idempotency_key)
    finally:
        await engine.dispose()

    assert first.status == "completed"
    assert first.model_observation["workspace_revision"] == 1
    assert replay == first


@pytest.mark.asyncio
async def test_confirmation_tool_is_ledgered_and_never_executes_provider_before_user_response(tmp_path) -> None:
    """M5 首次计费调用只能生成稳定确认挂起，未确认前不得触发 Operation Port。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-tools-confirmation.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    video_repository = SQLVideoAgentRepository(session_factory)
    repository = SQLAgentToolRepository(session_factory)
    now = datetime.now(UTC)
    workspace = await video_repository.create_workspace(
        "m5-tool-user",
        VideoWorkspace(
            workspace_id="m5-tool-workspace",
            conversation_id="m5-tool-conversation",
            revision=1,
            payload={"scenes": []},
            created_at=now,
            updated_at=now,
        ),
    )
    binding = RunBinding(
        run_id="hrun_m5_confirmation",
        session_id="pfh_m5_confirmation",
        user_id="m5-tool-user",
        conversation_id=workspace.conversation_id,
        workspace_id=workspace.workspace_id,
        workspace_revision=workspace.revision,
        context_digest="sha256:" + "a" * 64,
        toolset_version="agent-tools-v1",
        tool_manifest_digest=manifest().digest,
        request_digest="sha256:" + "b" * 64,
    )
    await repository.register_run_binding(binding)
    request = ToolCallRequest(
        protocol_version="v1",
        run_id=binding.run_id,
        session_id=binding.session_id,
        tool_call_id="m5-confirmation-call",
        tool_name="compose_or_export_video",
        arguments={"output_type": "mp4"},
        expected_workspace_revision=workspace.revision,
        context_digest=binding.context_digest,
        toolset_version=binding.toolset_version,
    )
    credential_store = TransientRunCredentialStore()
    broker = AgentToolBroker(
        repository,
        video_repository,
        video_tools=VideoToolRegistry((ComposeOrExportVideoTool(),)),
        credential_store=credential_store,
    )
    try:
        key = repository.tool_call_key(run_id=request.run_id, tool_call_id=request.tool_call_id)
        first = await broker.call(request, idempotency_key=key)
        replay = await broker.call(request, idempotency_key=key)
        interrupt_id = str(first.suspension["interrupt_id"])
        confirmed = await repository.respond_interrupt(
            interrupt_id=interrupt_id,
            binding=binding,
            client_response_id="b8bd2c37-4c1a-4e0d-8299-8dc091cc6b43",
            expected_workspace_revision=1,
        )
        resumed_binding = replace(
            binding,
            run_id="hrun_m5_confirmation_resume",
            session_id="pfh_m5_confirmation_resume",
        )
        await repository.register_run_binding(resumed_binding)
        await repository.bind_interrupt_resume_run(
            interrupt_id=interrupt_id,
            client_response_id=confirmed.response_id or "",
            resumed_run_id=resumed_binding.run_id,
        )
        await credential_store.put(
            run_id=resumed_binding.run_id,
            authorization="Bearer test-only-authorization",
        )
        resumed = await broker.call(
            request.model_copy(update={"run_id": resumed_binding.run_id, "session_id": resumed_binding.session_id, "tool_call_id": "m5-confirmed-call"}),
            idempotency_key=repository.tool_call_key(run_id=resumed_binding.run_id, tool_call_id="m5-confirmed-call"),
        )
    finally:
        await credential_store.aclose()
        await engine.dispose()

    assert first.status == replay.status == "awaiting_confirmation"
    assert first == replay
    assert first.suspension == {
        "kind": "awaiting_confirmation",
        "tool_name": "compose_or_export_video",
        "interrupt_id": "hint_" + hashlib.sha256(
            f"v1:harness-interrupt:{key}".encode(),
        ).hexdigest()[:32],
    }
    assert resumed.status == "completed"
    assert resumed.status != "awaiting_confirmation"


@pytest.mark.asyncio
async def test_patch_scene_tool_is_exposed_to_harness_and_replay_does_not_repeat_mutation(tmp_path) -> None:
    """Harness 可调用已迁入的非计费编辑 Tool，重放只返回首次 Observation。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-tools-patch.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    video_repository = SQLVideoAgentRepository(session_factory)
    repository = SQLAgentToolRepository(session_factory)
    now = datetime.now(UTC)
    workspace = await video_repository.create_workspace(
        "m0-tool-user",
        VideoWorkspace(
            workspace_id="m0-tool-patch-workspace",
            conversation_id="m0-tool-patch-conversation",
            revision=1,
            payload={
                "scenes": [{"scene_id": "scene-1", "title": "旧镜头", "prompt": "旧描述"}],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    binding = RunBinding(
        run_id="hrun_m0_tool_patch",
        session_id="pfh_m0_tool_patch",
        user_id="m0-tool-user",
        conversation_id=workspace.conversation_id,
        workspace_id=workspace.workspace_id,
        workspace_revision=workspace.revision,
        context_digest="sha256:" + "5" * 64,
        toolset_version="agent-tools-v1",
        tool_manifest_digest=manifest().digest,
        request_digest="sha256:" + "6" * 64,
    )
    await repository.register_run_binding(binding)
    request = ToolCallRequest(
        protocol_version="v1",
        run_id=binding.run_id,
        session_id=binding.session_id,
        tool_call_id="m0-patch-scene-call",
        tool_name="patch_scene",
        arguments={"scene_id": "scene-1", "patch": {"title": "新镜头"}},
        expected_workspace_revision=1,
        context_digest=binding.context_digest,
        toolset_version=binding.toolset_version,
    )
    try:
        broker = AgentToolBroker(repository, video_repository)
        idempotency_key = repository.tool_call_key(
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
        )
        first = await broker.call(request, idempotency_key=idempotency_key)
        replay = await broker.call(request, idempotency_key=idempotency_key)
        current = await video_repository.get_workspace("m0-tool-user", workspace.workspace_id)
    finally:
        await engine.dispose()

    assert {item["name"] for item in manifest().tools} >= {
        "inspect_video_workspace",
        "inspect_scene",
        "patch_scene",
        "replace_scene_asset",
    }
    assert first.status == replay.status == "completed"
    assert first == replay
    assert first.model_observation["workspace_revision"] == 2
    assert current is not None
    assert current.revision == 2
    assert current.payload["scenes"][0]["title"] == "新镜头"


@pytest.mark.asyncio
async def test_script_and_plan_tools_use_distinct_workspace_and_plan_revisions(tmp_path) -> None:
    """四个非计费 Tool 只公开白名单 observation，Plan 不会误写 Workspace。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-tools-script-plan.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    video_repository = SQLVideoAgentRepository(session_factory)
    repository = SQLAgentToolRepository(session_factory)
    now = datetime.now(UTC)
    workspace = await video_repository.create_workspace(
        "script-plan-user",
        VideoWorkspace(
            workspace_id="script-plan-workspace",
            conversation_id="script-plan-conversation",
            revision=1,
            payload={"script": {"content": "原始脚本", "status": "已生成"}},
            created_at=now,
            updated_at=now,
        ),
    )
    plan = await video_repository.save_plan(
        "script-plan-user",
        AgentPlan(
            plan_id="script-plan-id",
            workspace_id=workspace.workspace_id,
            conversation_id=workspace.conversation_id,
            status=AgentPlanStatus.RUNNING,
            public_goal="初始目标",
            created_at=now,
            updated_at=now,
        ),
        [],
    )

    async def call_tool(
        *,
        name: str,
        arguments: dict[str, object],
        run_suffix: str,
        workspace_revision: int,
    ):
        binding = RunBinding(
            run_id=f"hrun_script_plan_{run_suffix}",
            session_id=f"pfh_script_plan_{run_suffix}",
            user_id="script-plan-user",
            conversation_id=workspace.conversation_id,
            workspace_id=workspace.workspace_id,
            workspace_revision=workspace_revision,
            context_digest="sha256:" + run_suffix[0] * 64,
            toolset_version="agent-tools-v1",
            tool_manifest_digest=manifest().digest,
            request_digest="sha256:" + "9" * 64,
        )
        await repository.register_run_binding(binding)
        request = ToolCallRequest(
            protocol_version="v1",
            run_id=binding.run_id,
            session_id=binding.session_id,
            tool_call_id=f"script-plan-call-{run_suffix}",
            tool_name=name,
            arguments=arguments,
            expected_workspace_revision=workspace_revision,
            context_digest=binding.context_digest,
            toolset_version=binding.toolset_version,
        )
        broker = AgentToolBroker(repository, video_repository)
        return await broker.call(
            request,
            idempotency_key=repository.tool_call_key(
                run_id=request.run_id,
                tool_call_id=request.tool_call_id,
            ),
        )

    try:
        inspected_script = await call_tool(
            name="inspect_script", arguments={}, run_suffix="a", workspace_revision=1,
        )
        updated_script = await call_tool(
            name="update_script", arguments={"content": "修订脚本"}, run_suffix="b", workspace_revision=1,
        )
        inspected_plan = await call_tool(
            name="inspect_video_plan", arguments={"plan_id": plan.plan_id}, run_suffix="c", workspace_revision=2,
        )
        updated_plan = await call_tool(
            name="update_video_plan",
            arguments={
                "plan_id": plan.plan_id,
                "expected_plan_revision": plan.revision,
                "public_goal": "修订目标",
            },
            run_suffix="d",
            workspace_revision=2,
        )
        current_workspace = await video_repository.get_workspace("script-plan-user", workspace.workspace_id)
        current_plan = await video_repository.get_plan("script-plan-user", plan.plan_id)
    finally:
        await engine.dispose()

    assert inspected_script.model_observation["script_preview"] == "原始脚本"
    assert updated_script.model_observation["workspace_revision"] == 2
    assert inspected_plan.model_observation["plan_revision"] == 1
    assert updated_plan.model_observation["plan_revision"] == 2
    assert current_workspace is not None and current_workspace.revision == 2
    assert current_plan is not None and current_plan.revision == 2
    assert current_plan.public_goal == "修订目标"


@pytest.mark.asyncio
async def test_concurrent_tool_call_is_claimed_before_workspace_mutation(tmp_path) -> None:
    """同一 Tool Call 并发抵达时，后到请求只能看到执行中状态，绝不能再次写 Workspace。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-tools-concurrent.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    video_repository = SQLVideoAgentRepository(session_factory)
    repository = SQLAgentToolRepository(session_factory)
    now = datetime.now(UTC)
    workspace = await video_repository.create_workspace(
        "m3-tool-user",
        VideoWorkspace(
            workspace_id="m3-tool-workspace",
            conversation_id="m3-tool-conversation",
            revision=1,
            payload={"scenes": [{"scene_id": "scene-1", "title": "旧镜头"}]},
            created_at=now,
            updated_at=now,
        ),
    )
    binding = RunBinding(
        run_id="hrun_m3_concurrent",
        session_id="pfh_m3_concurrent",
        user_id="m3-tool-user",
        conversation_id=workspace.conversation_id,
        workspace_id=workspace.workspace_id,
        workspace_revision=1,
        context_digest="sha256:" + "7" * 64,
        toolset_version="agent-tools-v1",
        tool_manifest_digest=manifest().digest,
        request_digest="sha256:" + "8" * 64,
    )
    await repository.register_run_binding(binding)
    request = ToolCallRequest(
        protocol_version="v1",
        run_id=binding.run_id,
        session_id=binding.session_id,
        tool_call_id="m3-concurrent-patch",
        tool_name="patch_scene",
        arguments={"scene_id": "scene-1", "patch": {"title": "新镜头"}},
        expected_workspace_revision=1,
        context_digest=binding.context_digest,
        toolset_version=binding.toolset_version,
    )
    broker = AgentToolBroker(repository, video_repository)
    original_execute = broker._executor.execute_tool_call  # noqa: SLF001 - 固定并发领取边界。
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_execute(**kwargs):
        entered.set()
        await release.wait()
        return await original_execute(**kwargs)

    broker._executor.execute_tool_call = delayed_execute  # type: ignore[method-assign]  # noqa: SLF001
    idempotency_key = repository.tool_call_key(run_id=request.run_id, tool_call_id=request.tool_call_id)
    try:
        first_task = asyncio.create_task(broker.call(request, idempotency_key=idempotency_key))
        await entered.wait()
        second = await broker.call(request, idempotency_key=idempotency_key)
        release.set()
        first = await first_task
        current = await video_repository.get_workspace(binding.user_id, binding.workspace_id)
    finally:
        await engine.dispose()

    assert first.status == "completed"
    assert second.status == "failed"
    assert second.model_observation == {"code": "tool_call_in_progress"}
    assert current is not None
    assert current.revision == 2


@pytest.mark.asyncio
async def test_recovery_event_is_persisted_once_and_recovery_run_cannot_drift(tmp_path) -> None:
    """同一原 Run 的恢复事件与后继 Run 必须在真实 SQLite 中保持唯一且不可漂移。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-tools-recovery.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SQLAgentToolRepository(session_factory)
    try:
        first = await repository.get_or_create_recovery_event("hrun_m0_recovery_source")
        replay = await repository.get_or_create_recovery_event("hrun_m0_recovery_source")
        created = await repository.bind_recovery_run(
            original_run_id="hrun_m0_recovery_source",
            recovery_run_id="hrun_m0_recovery_target",
        )
        bound_replay = await repository.bind_recovery_run(
            original_run_id="hrun_m0_recovery_source",
            recovery_run_id="hrun_m0_recovery_target",
        )
    finally:
        await engine.dispose()

    assert first.status == replay.status == "pending"
    assert first.recovery_event_id == replay.recovery_event_id
    assert created.status == bound_replay.status == "created"
    assert created.recovery_run_id == bound_replay.recovery_run_id == "hrun_m0_recovery_target"


def test_internal_router_requires_service_identity_and_reads_real_sql_workspace(tmp_path, monkeypatch) -> None:
    """正式 Router 经 loopback HTTP 调用真实 Broker，不接受缺失服务身份的请求。"""

    async def setup() -> tuple[object, AgentToolBroker, ToolCallRequest]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-tools-http.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        video_repository = SQLVideoAgentRepository(session_factory)
        now = datetime.now(UTC)
        workspace = await video_repository.create_workspace(
            "m0-http-user",
            VideoWorkspace(
                workspace_id="m0-http-workspace",
                conversation_id="m0-http-conversation",
                revision=1,
                payload={"script": "真实 HTTP 测试脚本", "assets": [], "scenes": []},
                created_at=now,
                updated_at=now,
            ),
        )
        repository = SQLAgentToolRepository(session_factory)
        binding = RunBinding(
            run_id="hrun_m0_http_broker",
            session_id="pfh_m0_http_broker",
            user_id="m0-http-user",
            conversation_id="m0-http-conversation",
            workspace_id=workspace.workspace_id,
            workspace_revision=workspace.revision,
            context_digest="sha256:" + "3" * 64,
            toolset_version="agent-tools-v1",
            tool_manifest_digest=manifest().digest,
            request_digest="sha256:" + "4" * 64,
        )
        await repository.register_run_binding(binding)
        request = ToolCallRequest(
            protocol_version="v1",
            run_id=binding.run_id,
            session_id=binding.session_id,
            tool_call_id="m0-http-inspect-call",
            tool_name="inspect_video_workspace",
            arguments={},
            expected_workspace_revision=1,
            context_digest=binding.context_digest,
            toolset_version=binding.toolset_version,
        )
        return engine, AgentToolBroker(repository, video_repository), request

    engine, broker, call_request = asyncio.run(setup())
    jwt_signing_key = "m0-tool-broker-jwt-signing-key-at-least-32-bytes"
    service_jwt = _sidecar_service_jwt(jwt_signing_key)
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_VERIFY_KEY", jwt_signing_key)
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_ISSUER", "pixelflow-harness-sidecar")
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE", "pixelflow-tool-broker")
    app = FastAPI()
    app.state.pixelflow_agent_tool_broker = broker
    app.add_middleware(AuthMiddleware)
    app.include_router(internal_agent_tools.router)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=5) as client:
            for _ in range(50):
                try:
                    manifest_response = client.get(
                        f"{base_url}/agent/internal/agent-tools/manifest",
                        headers={"Authorization": f"Bearer {service_jwt}"},
                    )
                except httpx.ConnectError:
                    time.sleep(0.1)
                    continue
                if manifest_response.status_code == 200:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("内部 Gateway Router 未在限定时间内启动")
            assert client.get(f"{base_url}/agent/internal/agent-tools/manifest").status_code == 401
            assert manifest_response.json()["digest"] == manifest().digest
            rejected = client.post(
                f"{base_url}/agent/internal/agent-tools/calls",
                headers={
                    "Authorization": f"Bearer {service_jwt}",
                    "Idempotency-Key": "incorrect-key",
                },
                json=call_request.model_dump(mode="json"),
            )
            assert rejected.status_code == 409
            response = client.post(
                f"{base_url}/agent/internal/agent-tools/calls",
                headers={
                    "Authorization": f"Bearer {service_jwt}",
                    "Idempotency-Key": SQLAgentToolRepository.tool_call_key(
                        run_id=call_request.run_id,
                        tool_call_id=call_request.tool_call_id,
                    ),
                },
                json=call_request.model_dump(mode="json"),
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "completed"
            assert payload["model_observation"]["workspace_revision"] == 1
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        asyncio.run(engine.dispose())
