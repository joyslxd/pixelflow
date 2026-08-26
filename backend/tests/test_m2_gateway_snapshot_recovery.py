"""以真实 Gateway/Sidecar 进程验证 M2 Snapshot、SSE 与 Gateway 重启恢复。"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.gateway.auth.models import User
from app.gateway.routers import pixelflow_conversations
from pixelflow.agent_control_plane.persistence import SQLCompactionQueueRepository
from pixelflow.agent_control_plane.run_bridge import AgentRunBridge
from pixelflow.agent_harness import AgentHarnessSidecarClient, HarnessRunRequest
from pixelflow.agent_harness.projector import HarnessRunProjector
from pixelflow.agent_tools import SQLAgentToolRepository
from pixelflow.agent_tools.manifest import manifest
from pixelflow.agent_tools.repository import RunBinding
from pixelflow.platform.persistence import Base
from pixelflow.tasks import PixelFlowConversationRecord, SQLPixelFlowTaskStore


def _free_port() -> int:
    """申请仅供本测试使用的 loopback 端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _service_jwt(signing_key: str) -> str:
    """签发不含最终用户身份的 Gateway→Sidecar 短期服务 JWT。"""

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "m2-gateway",
            "iss": "pixelflow-gateway",
            "aud": "pixelflow-harness-sidecar",
            "service_instance_id": "m2-gateway-loopback",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="HS256",
    )


def _wait_http(client: httpx.Client, url: str) -> httpx.Response:
    """等待真实 Uvicorn 进程对外提供 HTTP 服务。"""

    for _ in range(50):
        try:
            response = client.get(url)
        except httpx.ConnectError:
            time.sleep(0.1)
            continue
        if response.status_code < 500:
            return response
        time.sleep(0.1)
    pytest.fail(f"进程未在限定时间就绪：{url}")


def _start_gateway(app: FastAPI, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    """启动真实 Gateway HTTP 进程边界。"""

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


class _M2StubAuthMiddleware(BaseHTTPMiddleware):
    """仅为本地 Gateway 进程演练写入已认证用户，不替代生产认证测试。"""

    def __init__(self, app: ASGIApp, owner: User) -> None:
        super().__init__(app)
        self._owner = owner

    async def dispatch(self, request, call_next):
        request.state.user = self._owner
        return await call_next(request)


def test_gateway_restart_reprojects_cancelled_run_snapshot_and_sse(tmp_path: Path) -> None:
    """Gateway 重启后从 Sidecar Event Store 重投影，不重复写 Outbox 或丢失取消终态。"""

    owner = User(
        email="m2-gateway@example.com",
        password_hash="x",
        system_role="user",
        id=uuid4(),
    )

    async def prepare_storage():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.sqlite3'}")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        store = SQLPixelFlowTaskStore(factory)
        await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="m2-gateway-conversation",
                user_id=str(owner.id),
                title="M2 Gateway 重启测试",
            ),
        )
        return engine, store, SQLAgentToolRepository(factory), SQLCompactionQueueRepository(factory)

    engine, store, binding_repository, event_repository = asyncio.run(prepare_storage())
    gateway_key = "m2-gateway-sidecar-jwt-signing-key-at-least-32-bytes"
    sidecar_port = _free_port()
    agent_home = tmp_path / "sidecar-home"
    skill_file = agent_home / "skills" / "m2-gateway" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\ndescription: M2 Gateway 重启测试。\n---\n测试正文", encoding="utf-8")
    sidecar_root = Path(__file__).parents[2] / "services/pixelflow-agent-harness"
    environment = {
        **os.environ,
        "PIXELFLOW_AGENT_HOME": str(agent_home),
        "PIXELFLOW_HARNESS_RUN_STORE": str(agent_home / "runs.sqlite3"),
        "PIXELFLOW_GATEWAY_JWT_VERIFY_KEY": gateway_key,
        "PIXELFLOW_GATEWAY_JWT_ISSUER": "pixelflow-gateway",
        "PIXELFLOW_GATEWAY_JWT_AUDIENCE": "pixelflow-harness-sidecar",
        "PIXELFLOW_TOOL_BROKER_BASE_URL": "http://127.0.0.1:19999",
        "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": "m2-gateway-tool-broker-key-at-least-32-bytes",
        "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": "pixelflow-harness-sidecar",
        "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": "pixelflow-tool-broker",
        "PIXELFLOW_SIDECAR_INSTANCE_ID": "m2-gateway-sidecar",
        "DEEPSEEK_API_KEY": "test-only-not-used",
        "DEEPSEEK_BASE_URL": "https://example.invalid",
        "PYTHONPATH": str(sidecar_root / "src"),
    }
    sidecar_process = subprocess.Popen(
        [
            "/usr/bin/arch",
            "-arm64",
            str(sidecar_root / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "pixelflow_harness_sidecar.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(sidecar_port),
            "--log-level",
            "warning",
        ],
        cwd=sidecar_root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sidecar_url = f"http://127.0.0.1:{sidecar_port}"
    gateway_server: uvicorn.Server | None = None
    gateway_thread: threading.Thread | None = None
    bridge: AgentHarnessSidecarClient | None = None
    try:
        request = HarnessRunRequest(
            user_id=str(owner.id),
            conversation_id="m2-gateway-conversation",
            workspace_id="m2-gateway-workspace",
            workspace_revision=1,
            trigger_id="m2-gateway-turn",
            user_input="不激活模型。",
            system_instruction="执行 M2 Gateway 测试。",
            context_digest="sha256:" + "1" * 64,
            model_profile_digest="sha256:" + "2" * 64,
            context_budget_digest="sha256:" + "3" * 64,
            run_limits_digest="sha256:" + "4" * 64,
        )
        sidecar_request = AgentHarnessSidecarClient._sidecar_request(request, manifest().digest)  # noqa: SLF001 - 构造真实稳定 HTTP DTO。
        with httpx.Client(timeout=5) as client:
            _wait_http(client, f"{sidecar_url}/live")
            created = client.post(
                f"{sidecar_url}/internal/v1/runs",
                headers={
                    "Authorization": f"Bearer {_service_jwt(gateway_key)}",
                    "Idempotency-Key": str(sidecar_request["run_request_key"]),
                },
                json=sidecar_request,
            )
        assert created.status_code == 202, created.text
        run_id = str(created.json()["run_id"])
        asyncio.run(
            binding_repository.register_run_binding(
                RunBinding(
                    run_id=run_id,
                    session_id=str(sidecar_request["session_id"]),
                    user_id=str(owner.id),
                    conversation_id="m2-gateway-conversation",
                    workspace_id="m2-gateway-workspace",
                    workspace_revision=1,
                    context_digest=request.context_digest,
                    toolset_version="agent-tools-v1",
                    tool_manifest_digest=manifest().digest,
                    request_digest=str(sidecar_request["request_digest"]),
                ),
            )
        )

        def build_gateway(instance_id: str):
            app = FastAPI()
            app.add_middleware(_M2StubAuthMiddleware, owner=owner)
            projector = HarnessRunProjector(
                binding_repository=binding_repository,
                event_repository=event_repository,
                task_store=store,
            )
            app.state.pixelflow_task_store = store
            app.state.pixelflow_harness_run_projector = projector
            client = AgentHarnessSidecarClient(
                base_url=sidecar_url,
                gateway_jwt_signing_key=gateway_key,
                gateway_instance_id=instance_id,
                repository=binding_repository,
            )
            app.state.pixelflow_harness_run_bridge = client
            app.state.pixelflow_agent_run_bridge = AgentRunBridge(harness=client, projector=projector)
            app.include_router(pixelflow_conversations.router)
            return app, client

        gateway_port = _free_port()
        gateway_app, bridge = build_gateway("m2-gateway-first")
        gateway_server, gateway_thread = _start_gateway(gateway_app, gateway_port)
        base_url = f"http://127.0.0.1:{gateway_port}/agent/conversations/m2-gateway-conversation/harness-runs/{run_id}"
        with httpx.Client(timeout=10, headers={"Authorization": "Bearer local-test"}) as client:
            _wait_http(client, f"{base_url}/snapshot")
            cancelled = client.post(f"{base_url}/cancel")
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["status"] == "cancelled"
            for _ in range(50):
                snapshot = client.get(f"{base_url}/snapshot")
                if snapshot.status_code == 200 and snapshot.json()["status"] == "cancelled":
                    break
                time.sleep(0.1)
            else:
                pytest.fail("取消后 Gateway 未投影 cancelled Snapshot")
            first_snapshot = snapshot.json()
            replay = client.get(f"{base_url}/events?after_sequence=0")
            assert replay.status_code == 200, replay.text
            first_events = [line for line in replay.text.splitlines() if line.startswith("data: ")]
        assert first_snapshot["last_sequence"] == 2
        assert len(first_events) == 2

        asyncio.run(bridge.aclose())
        gateway_server.should_exit = True
        gateway_thread.join(timeout=5)
        assert not gateway_thread.is_alive()

        restarted_app, bridge = build_gateway("m2-gateway-restarted")
        gateway_server, gateway_thread = _start_gateway(restarted_app, gateway_port)
        with httpx.Client(timeout=10, headers={"Authorization": "Bearer local-test"}) as client:
            for _ in range(50):
                snapshot = client.get(f"{base_url}/snapshot")
                if snapshot.status_code == 200 and snapshot.json()["status"] == "cancelled":
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Gateway 重启后未恢复 cancelled Snapshot")
            replay = client.get(f"{base_url}/events?after_sequence=0")
            assert replay.status_code == 200, replay.text
            restarted_events = [line for line in replay.text.splitlines() if line.startswith("data: ")]
        assert snapshot.json()["last_sequence"] == 2
        assert restarted_events == first_events
    finally:
        if bridge is not None:
            asyncio.run(bridge.aclose())
        if gateway_server is not None:
            gateway_server.should_exit = True
        if gateway_thread is not None:
            gateway_thread.join(timeout=5)
        if sidecar_process.poll() is None:
            sidecar_process.terminate()
            try:
                sidecar_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sidecar_process.kill()
                sidecar_process.wait(timeout=5)
        asyncio.run(engine.dispose())
