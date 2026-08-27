"""真实验证认证用户经 Gateway 公共 Harness Turn 入口调用 Sidecar。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.routers import internal_agent_tools, pixelflow_conversations
from pixelflow.agent_control_plane.persistence import SQLCompactionQueueRepository
from pixelflow.agent_control_plane.persistence.models import PixelFlowAgentHarnessToolCallRow
from pixelflow.agent_harness import AgentHarnessSidecarClient
from pixelflow.agent_harness.projector import HarnessRunProjector
from pixelflow.agent_harness.recovery import HarnessRecoveryService
from pixelflow.agent_tools import AgentToolBroker, SQLAgentToolRepository
from pixelflow.platform.persistence import Base
from pixelflow.tasks import PixelFlowConversationRecord, SQLPixelFlowTaskStore
from pixelflow.video.contracts import AgentPlan, AgentPlanStatus, VideoWorkspace
from pixelflow.video.workspace import SQLVideoAgentRepository
from tests.test_m0_real_gateway_sidecar_tool import _service_jwt


def _free_port() -> int:
    """申请仅供隔离真实进程使用的 loopback 端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _sidecar_python_command(python: Path) -> list[str]:
    """macOS ARM64 强制原生 Runtime；Linux 使用其当前架构的锁定 wheel。"""

    if sys.platform == "darwin":
        return ["/usr/bin/arch", "-arm64", str(python)]
    return [str(python)]


@pytest.mark.m0_real
@pytest.mark.m4_real
def test_real_authenticated_public_harness_turn_and_sse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实用户鉴权、公开 Turn、SSE 与 Tool Call 必须穿过同一 Gateway。"""

    if os.environ.get("PIXELFLOW_RUN_REAL_M0") != "1":
        pytest.skip("未显式开启会消耗测试 token 的真实 M0 用例")
    model_api_key = os.environ.get("DEEPSEEK_API_KEY")
    model_base_url = os.environ.get("DEEPSEEK_BASE_URL")
    user_authorization = os.environ.get("PIXELFLOW_REAL_BORGRISE_AUTHORIZATION")
    if not model_api_key or not model_base_url or not user_authorization:
        pytest.skip("缺少模型凭据、模型端点或真实 Borgrise 用户 Authorization")
    try:
        token = user_authorization.removeprefix("Bearer ").strip()
        owner = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})["sub"]
    except (jwt.PyJWTError, KeyError, TypeError):
        pytest.skip("真实 Borgrise Authorization 未携带可用用户主体")
    if not isinstance(owner, str) or not owner.strip():
        pytest.skip("真实 Borgrise Authorization 未携带可用用户主体")
    limit_profiles = {
        "video_interactive_v1": {"deadline_seconds": 180, "max_model_steps": 12, "max_business_tools": 6, "max_billable_batch_starts": 1},
        "operation_resume_v1": {"deadline_seconds": 150, "max_model_steps": 10, "max_business_tools": 5, "max_billable_batch_starts": 1},
        "confirmation_resume_v1": {"deadline_seconds": 150, "max_model_steps": 10, "max_business_tools": 5, "max_billable_batch_starts": 1},
        "run_recovery_v1": {"deadline_seconds": 90, "max_model_steps": 6, "max_business_tools": 3, "max_billable_batch_starts": 0},
    }
    profile_digest = "sha256:" + hashlib.sha256(
        json.dumps({"profile": "deepseek-v4-pro"}, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    monkeypatch.setenv("PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES", json.dumps(limit_profiles))

    async def prepare_gateway() -> tuple[object, SQLPixelFlowTaskStore, SQLAgentToolRepository, SQLVideoAgentRepository, SQLCompactionQueueRepository]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm0-public-turn.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        store = SQLPixelFlowTaskStore(session_factory)
        await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="m0-public-conversation",
                user_id=owner,
                title="M0 真实公开 Turn",
            ),
        )
        video_repository = SQLVideoAgentRepository(session_factory)
        now = datetime.now(UTC)
        await video_repository.create_workspace(
            owner,
            VideoWorkspace(
                workspace_id="m0-public-workspace",
                conversation_id="m0-public-conversation",
                revision=1,
                payload={
                    "script": {"content": "白色陶瓷杯展示短视频脚本", "status": "已生成"},
                    "assets": [{"asset_id": "cup-reference"}],
                    "scenes": [{"scene_id": "scene-1"}, {"scene_id": "scene-2"}],
                },
                created_at=now,
                updated_at=now,
            ),
        )
        await video_repository.save_plan(
            owner,
            AgentPlan(
                plan_id="m4-public-plan",
                workspace_id="m0-public-workspace",
                conversation_id="m0-public-conversation",
                status=AgentPlanStatus.RUNNING,
                public_goal="完成陶瓷杯视频方案",
                created_at=now,
                updated_at=now,
            ),
            [],
        )
        return (
            engine,
            store,
            SQLAgentToolRepository(session_factory),
            video_repository,
            SQLCompactionQueueRepository(session_factory),
        )

    engine, store, repository, video_repository, event_repository = asyncio.run(prepare_gateway())
    broker_jwt_key = "m0-public-tool-broker-jwt-signing-key-at-least-32-bytes"
    gateway_jwt_key = "m0-public-gateway-sidecar-jwt-signing-key-at-least-32-bytes"
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_VERIFY_KEY", broker_jwt_key)
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_ISSUER", "pixelflow-harness-sidecar")
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE", "pixelflow-tool-broker")

    gateway_app = FastAPI()
    gateway_app.state.pixelflow_task_store = store
    gateway_app.state.pixelflow_harness_video_repository = video_repository
    gateway_app.state.pixelflow_agent_tool_broker = AgentToolBroker(repository, video_repository)
    gateway_app.state.pixelflow_harness_run_projector = HarnessRunProjector(
        binding_repository=repository,
        event_repository=event_repository,
        task_store=store,
    )
    gateway_app.add_middleware(AuthMiddleware)
    gateway_app.include_router(internal_agent_tools.router)
    gateway_app.include_router(pixelflow_conversations.router)
    gateway_port = _free_port()
    gateway_server = uvicorn.Server(
        uvicorn.Config(gateway_app, host="127.0.0.1", port=gateway_port, log_level="warning"),
    )
    gateway_thread = threading.Thread(target=gateway_server.run, daemon=True)
    gateway_thread.start()

    sidecar_port = _free_port()
    agent_home = tmp_path / "sidecar-home"
    skill_file = agent_home / "skills" / "workspace-inspection" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: workspace-inspection\ndescription: 用户询问当前视频工作区的脚本、素材或分镜时使用。\nuser-invocable: false\n---\n"
        "用户要求检查视频方案时，必须依次调用 inspect_video_workspace、inspect_script、inspect_video_plan 和 inspect_scene 获取证据，再基于 Tool 返回的安全摘要回答。",
        encoding="utf-8",
    )
    sidecar_root = Path(__file__).parents[2] / "services/pixelflow-agent-harness"
    environment = os.environ.copy()
    environment.update(
        {
            "PIXELFLOW_AGENT_HOME": str(agent_home),
            "PIXELFLOW_HARNESS_RUN_STORE": str(agent_home / "run-events" / "runs.sqlite3"),
            "PIXELFLOW_GATEWAY_JWT_VERIFY_KEY": gateway_jwt_key,
            "PIXELFLOW_GATEWAY_JWT_ISSUER": "pixelflow-gateway",
            "PIXELFLOW_GATEWAY_JWT_AUDIENCE": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_BASE_URL": f"http://127.0.0.1:{gateway_port}",
            "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": broker_jwt_key,
            "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": "pixelflow-tool-broker",
            "PIXELFLOW_SIDECAR_INSTANCE_ID": "m0-public-sidecar",
            "PIXELFLOW_HARNESS_MODEL_PROFILE": "deepseek-v4-pro",
            "PIXELFLOW_HARNESS_MODEL_PROFILE_DIGEST": profile_digest,
            "PIXELFLOW_HARNESS_MODEL_ID": "deepseek-v4-pro-ga-260813",
            "PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS": "90",
            "PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES": json.dumps(limit_profiles),
            "PYTHONPATH": str(sidecar_root / "src"),
        },
    )
    sidecar_process = subprocess.Popen(
        [
            *_sidecar_python_command(sidecar_root / ".venv/bin/python"),
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
    gateway_app.state.pixelflow_harness_run_bridge = AgentHarnessSidecarClient(
        base_url=sidecar_url,
        gateway_jwt_signing_key=gateway_jwt_key,
        gateway_instance_id="m0-public-gateway",
        repository=repository,
        timeout_seconds=10,
    )
    try:
        with httpx.Client(timeout=90) as http_client:
            for _ in range(50):
                try:
                    ready = http_client.get(
                        f"{sidecar_url}/ready",
                        headers={"Authorization": f"Bearer {_service_jwt(gateway_jwt_key)}"},
                    )
                except httpx.ConnectError:
                    time.sleep(0.1)
                    continue
                if ready.status_code == 200:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("真实 Sidecar 未在限定时间达到 readiness")

            response = http_client.post(
                f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-public-conversation/harness-turns/start",
                headers={"Authorization": user_authorization},
                json={
                    "client_input_id": "01447dc1-0dfb-4e70-98fa-cba6e48cfb7d",
                    "workspace_id": "m0-public-workspace",
                    "expected_workspace_revision": 1,
                    "content": "请检查当前视频方案：读取工作区、脚本、计划和 scene-1，并给出简短结论。",
                    "max_output_tokens": 192,
                },
            )
            assert response.status_code == 200, response.text
            run = response.json()
            assert run["status"] == "accepted"
            stream_url = (
                f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-public-conversation/"
                f"harness-runs/{run['run_id']}/events"
            )
            interrupted_event: dict[str, object] | None = None
            with http_client.stream(
                "GET",
                f"{stream_url}?after_sequence=0",
                headers={"Authorization": user_authorization, "Accept": "text/event-stream"},
            ) as interrupted_stream:
                assert interrupted_stream.status_code == 200
                for line in interrupted_stream.iter_lines():
                    if line.startswith("data: "):
                        interrupted_event = json.loads(line.removeprefix("data: "))
                        break
            assert interrupted_event is not None
            resumed_stream = http_client.get(
                f"{stream_url}?after_sequence={interrupted_event['sequence']}",
                headers={"Authorization": user_authorization, "Accept": "text/event-stream"},
            )
            assert resumed_stream.status_code == 200, resumed_stream.text
            resumed_events = [
                json.loads(line.removeprefix("data: "))
                for line in resumed_stream.text.splitlines()
                if line.startswith("data: ")
            ]
            events = [interrupted_event, *resumed_events]
            assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))

        previous_bridge = gateway_app.state.pixelflow_harness_run_bridge
        asyncio.run(previous_bridge.aclose())
        gateway_server.should_exit = True
        gateway_thread.join(timeout=5)
        assert not gateway_thread.is_alive()

        restarted_gateway_app = FastAPI()
        restarted_gateway_app.state.pixelflow_task_store = store
        restarted_gateway_app.state.pixelflow_harness_video_repository = video_repository
        restarted_gateway_app.state.pixelflow_agent_tool_broker = AgentToolBroker(repository, video_repository)
        restarted_gateway_app.state.pixelflow_harness_run_projector = HarnessRunProjector(
            binding_repository=repository,
            event_repository=event_repository,
            task_store=store,
        )
        restarted_gateway_app.add_middleware(AuthMiddleware)
        restarted_gateway_app.include_router(internal_agent_tools.router)
        restarted_gateway_app.include_router(pixelflow_conversations.router)
        restarted_gateway_app.state.pixelflow_harness_run_bridge = AgentHarnessSidecarClient(
            base_url=sidecar_url,
            gateway_jwt_signing_key=gateway_jwt_key,
            gateway_instance_id="m0-public-gateway-restarted",
            repository=repository,
            timeout_seconds=10,
        )
        gateway_app = restarted_gateway_app
        gateway_server = uvicorn.Server(
            uvicorn.Config(gateway_app, host="127.0.0.1", port=gateway_port, log_level="warning"),
        )
        gateway_thread = threading.Thread(target=gateway_server.run, daemon=True)
        gateway_thread.start()
        with httpx.Client(timeout=30) as restarted_client:
            for _ in range(50):
                try:
                    snapshot = restarted_client.get(
                        f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-public-conversation/harness-runs/{run['run_id']}/snapshot",
                        headers={"Authorization": user_authorization},
                    )
                except httpx.ConnectError:
                    time.sleep(0.1)
                    continue
                if snapshot.status_code == 200:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("重启后的 Gateway 未在限定时间恢复公开 Snapshot")
            assert snapshot.json()["status"] == "completed"
            assert snapshot.json()["last_sequence"] == events[-1]["sequence"]
            assert [message["role"] for message in snapshot.json()["messages"]] == ["user", "assistant"]
            replayed_stream = restarted_client.get(
                f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-public-conversation/harness-runs/{run['run_id']}/events?after_sequence=0",
                headers={"Authorization": user_authorization, "Accept": "text/event-stream"},
            )
            assert replayed_stream.status_code == 200, replayed_stream.text
            replayed_events = [
                json.loads(line.removeprefix("data: "))
                for line in replayed_stream.text.splitlines()
                if line.startswith("data: ")
            ]
        assert replayed_events == events

        with httpx.Client(timeout=30) as http_client:
            snapshot = http_client.get(
                f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-public-conversation/harness-runs/{run['run_id']}/snapshot",
                headers={"Authorization": user_authorization},
            )
            assert snapshot.status_code == 200, snapshot.text
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert {event["type"] for event in events} >= {
            "run.state_changed",
            "agent.tool.completed",
            "agent.thinking.delta",
            "agent.response.completed",
        }
        assert snapshot.json()["status"] == "completed"
        assert snapshot.json()["last_sequence"] == events[-1]["sequence"]
        assert [message["role"] for message in snapshot.json()["messages"]] == ["user", "assistant"]

        async def assert_persistence() -> None:
            messages = await store.list_conversation_messages(
                "m0-public-conversation",
                user_id=owner,
            )
            assert [message.role for message in messages] == ["user", "assistant"]
            assert messages[1].payload.get("harness_run_id") == run["run_id"]
            async with repository._session_factory() as session:  # noqa: SLF001 - 验证真实 SQL Tool Call 已落库。
                tool_call = await session.scalar(
                    select(PixelFlowAgentHarnessToolCallRow).where(
                        PixelFlowAgentHarnessToolCallRow.run_id == run["run_id"],
                    ),
                )
                assert tool_call is not None
                tool_names = {
                    row.tool_name
                    for row in (await session.scalars(
                        select(PixelFlowAgentHarnessToolCallRow).where(
                            PixelFlowAgentHarnessToolCallRow.run_id == run["run_id"],
                        ),
                    )).all()
                }
                assert {"inspect_video_workspace", "inspect_script", "inspect_video_plan", "inspect_scene"}.issubset(tool_names)

        asyncio.run(assert_persistence())
    finally:
        bridge = gateway_app.state.pixelflow_harness_run_bridge
        asyncio.run(bridge.aclose())
        sidecar_process.terminate()
        try:
            sidecar_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sidecar_process.kill()
            sidecar_process.wait(timeout=5)
        gateway_server.should_exit = True
        gateway_thread.join(timeout=5)
        asyncio.run(engine.dispose())


@pytest.mark.m0_real
def test_real_public_run_recovery_after_sidecar_kill_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 Sidecar 重启后，Gateway 必须创建新 run_recovery 而非续跑旧 Session。"""

    if os.environ.get("PIXELFLOW_RUN_REAL_M0") != "1":
        pytest.skip("未显式开启会消耗测试 token 的真实 M0 用例")
    if not os.environ.get("DEEPSEEK_API_KEY") or not os.environ.get("DEEPSEEK_BASE_URL"):
        pytest.skip("缺少真实 Ark 模型测试凭据或端点")
    user_authorization = os.environ.get("PIXELFLOW_REAL_BORGRISE_AUTHORIZATION")
    if not user_authorization:
        pytest.skip("缺少真实 Borgrise 用户 Authorization")
    try:
        token = user_authorization.removeprefix("Bearer ").strip()
        owner = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})["sub"]
    except (jwt.PyJWTError, KeyError, TypeError):
        pytest.skip("真实 Borgrise Authorization 未携带可用用户主体")
    if not isinstance(owner, str) or not owner.strip():
        pytest.skip("真实 Borgrise Authorization 未携带可用用户主体")

    async def prepare_gateway() -> tuple[object, SQLPixelFlowTaskStore, SQLAgentToolRepository, SQLVideoAgentRepository, SQLCompactionQueueRepository]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm0-public-recovery.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        store = SQLPixelFlowTaskStore(session_factory)
        await store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="m0-recovery-conversation",
                user_id=owner,
                title="M0 真实恢复 Turn",
            ),
        )
        video_repository = SQLVideoAgentRepository(session_factory)
        now = datetime.now(UTC)
        await video_repository.create_workspace(
            owner,
            VideoWorkspace(
                workspace_id="m0-recovery-workspace",
                conversation_id="m0-recovery-conversation",
                revision=1,
                payload={
                    "script": "白色陶瓷杯展示短视频脚本",
                    "assets": [{"asset_id": "cup-reference"}],
                    "scenes": [{"scene_id": "scene-1"}, {"scene_id": "scene-2"}],
                },
                created_at=now,
                updated_at=now,
            ),
        )
        return (
            engine,
            store,
            SQLAgentToolRepository(session_factory),
            video_repository,
            SQLCompactionQueueRepository(session_factory),
        )

    engine, store, repository, video_repository, event_repository = asyncio.run(prepare_gateway())
    broker_jwt_key = "m0-recovery-tool-broker-jwt-signing-key-at-least-32-bytes"
    gateway_jwt_key = "m0-recovery-gateway-sidecar-jwt-signing-key-at-least-32-bytes"
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_VERIFY_KEY", broker_jwt_key)
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_ISSUER", "pixelflow-harness-sidecar")
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE", "pixelflow-tool-broker")
    gateway_app = FastAPI()
    gateway_app.state.pixelflow_task_store = store
    gateway_app.state.pixelflow_harness_video_repository = video_repository
    gateway_app.state.pixelflow_agent_tool_broker = AgentToolBroker(repository, video_repository)
    gateway_app.state.pixelflow_harness_run_projector = HarnessRunProjector(
        binding_repository=repository,
        event_repository=event_repository,
        task_store=store,
    )
    gateway_app.state.pixelflow_harness_recovery_service = HarnessRecoveryService(
        binding_repository=repository,
        task_store=store,
        video_repository=video_repository,
    )
    gateway_app.add_middleware(AuthMiddleware)
    gateway_app.include_router(internal_agent_tools.router)
    gateway_app.include_router(pixelflow_conversations.router)
    gateway_port = _free_port()
    gateway_server = uvicorn.Server(
        uvicorn.Config(gateway_app, host="127.0.0.1", port=gateway_port, log_level="warning"),
    )
    gateway_thread = threading.Thread(target=gateway_server.run, daemon=True)
    gateway_thread.start()

    sidecar_port = _free_port()
    agent_home = tmp_path / "sidecar-home"
    skill_file = agent_home / "skills" / "workspace-inspection" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: workspace-inspection\ndescription: 用户询问当前视频工作区的脚本、素材或分镜时使用。\nuser-invocable: false\n---\n"
        "用户询问当前工作区事实时，必须先调用 inspect_video_workspace 获取证据，再基于 Tool 返回的安全摘要回答。",
        encoding="utf-8",
    )
    sidecar_root = Path(__file__).parents[2] / "services/pixelflow-agent-harness"
    environment = os.environ.copy()
    environment.update(
        {
            "PIXELFLOW_AGENT_HOME": str(agent_home),
            "PIXELFLOW_HARNESS_RUN_STORE": str(agent_home / "run-events" / "runs.sqlite3"),
            "PIXELFLOW_GATEWAY_JWT_VERIFY_KEY": gateway_jwt_key,
            "PIXELFLOW_GATEWAY_JWT_ISSUER": "pixelflow-gateway",
            "PIXELFLOW_GATEWAY_JWT_AUDIENCE": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_BASE_URL": f"http://127.0.0.1:{gateway_port}",
            "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": broker_jwt_key,
            "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": "pixelflow-tool-broker",
            "PIXELFLOW_SIDECAR_INSTANCE_ID": "m0-public-recovery-sidecar",
            "PIXELFLOW_HARNESS_MODEL_PROFILE": "deepseek-v4-pro",
            "PIXELFLOW_HARNESS_MODEL_ID": "deepseek-v4-pro-ga-260813",
            "PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS": "90",
            "PYTHONPATH": str(sidecar_root / "src"),
        },
    )

    def start_sidecar() -> subprocess.Popen[bytes]:
        """以官方 ARM64 Runtime 启动真实 Sidecar，并复用同一 SQLite Run Store。"""

        return subprocess.Popen(
            [
                *_sidecar_python_command(sidecar_root / ".venv/bin/python"),
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

    sidecar_process = start_sidecar()
    sidecar_url = f"http://127.0.0.1:{sidecar_port}"
    gateway_app.state.pixelflow_harness_run_bridge = AgentHarnessSidecarClient(
        base_url=sidecar_url,
        gateway_jwt_signing_key=gateway_jwt_key,
        gateway_instance_id="m0-public-recovery-gateway",
        repository=repository,
        timeout_seconds=10,
    )
    try:
        with httpx.Client(timeout=90) as http_client:
            for _ in range(50):
                try:
                    ready = http_client.get(
                        f"{sidecar_url}/ready",
                        headers={"Authorization": f"Bearer {_service_jwt(gateway_jwt_key)}"},
                    )
                except httpx.ConnectError:
                    time.sleep(0.1)
                    continue
                if ready.status_code == 200:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("真实 Sidecar 未在限定时间达到 readiness")

            created = http_client.post(
                f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-recovery-conversation/harness-turns/start",
                headers={"Authorization": user_authorization},
                json={
                    "client_input_id": "41b3422a-e765-48dc-a3a3-79f6ab3b3d81",
                    "workspace_id": "m0-recovery-workspace",
                    "expected_workspace_revision": 1,
                    "content": "请读取当前视频工作区，告诉我脚本、素材和分镜数量。",
                    "max_output_tokens": 192,
                },
            )
            assert created.status_code == 200, created.text
            original_run_id = created.json()["run_id"]

            sidecar_process.kill()
            sidecar_process.wait(timeout=5)
            sidecar_process = start_sidecar()
            for _ in range(50):
                try:
                    ready = http_client.get(
                        f"{sidecar_url}/ready",
                        headers={"Authorization": f"Bearer {_service_jwt(gateway_jwt_key)}"},
                    )
                except httpx.ConnectError:
                    time.sleep(0.1)
                    continue
                if ready.status_code == 200:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("重启后的真实 Sidecar 未在限定时间达到 readiness")
            time.sleep(0.3)

            failed_events = http_client.get(
                f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-recovery-conversation/harness-runs/{original_run_id}/events?after_sequence=0",
                headers={"Authorization": user_authorization, "Accept": "text/event-stream"},
            )
            assert failed_events.status_code == 200, failed_events.text
            original_events = [
                json.loads(line.removeprefix("data: "))
                for line in failed_events.text.splitlines()
                if line.startswith("data: ")
            ]
            assert original_events[-1]["payload"] == {
                "status": "failed",
                "code": "harness_run_recovery_required",
            }

            recovered = http_client.post(
                f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-recovery-conversation/harness-runs/{original_run_id}/recover",
                headers={"Authorization": user_authorization},
            )
            assert recovered.status_code == 200, recovered.text
            recovery_payload = recovered.json()
            recovery_run_id = recovery_payload["recovery_run_id"]
            assert recovery_run_id != original_run_id

            recovery_events_response = http_client.get(
                f"http://127.0.0.1:{gateway_port}/agent/conversations/m0-recovery-conversation/harness-runs/{recovery_run_id}/events?after_sequence=0",
                headers={"Authorization": user_authorization, "Accept": "text/event-stream"},
            )
            assert recovery_events_response.status_code == 200, recovery_events_response.text
            recovery_events = [
                json.loads(line.removeprefix("data: "))
                for line in recovery_events_response.text.splitlines()
                if line.startswith("data: ")
            ]
        assert {event["type"] for event in recovery_events} >= {
            "run.state_changed",
            "agent.tool.completed",
            "agent.response.completed",
        }
        record = asyncio.run(repository.get_or_create_recovery_event(original_run_id))
        assert record.status == "created"
        assert record.recovery_run_id == recovery_run_id
    finally:
        bridge = gateway_app.state.pixelflow_harness_run_bridge
        asyncio.run(bridge.aclose())
        if sidecar_process.poll() is None:
            sidecar_process.terminate()
            try:
                sidecar_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sidecar_process.kill()
                sidecar_process.wait(timeout=5)
        gateway_server.should_exit = True
        gateway_thread.join(timeout=5)
        asyncio.run(engine.dispose())
