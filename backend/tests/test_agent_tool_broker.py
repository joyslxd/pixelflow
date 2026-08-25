"""验证 Gateway Tool Broker 使用真实 SQLite Workspace 与 Run binding。"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
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
from pixelflow.platform.persistence import Base
from pixelflow.video.contracts import VideoWorkspace
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
