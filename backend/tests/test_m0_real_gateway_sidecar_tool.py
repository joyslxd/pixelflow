"""真实验证 Gateway Run binding、Sidecar Plugin 与 Tool Broker 的最小 M0 链路。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.routers import internal_agent_tools
from pixelflow.agent_control_plane.persistence.models import PixelFlowAgentHarnessToolCallRow
from pixelflow.agent_harness import AgentHarnessSidecarClient, HarnessRunRequest
from pixelflow.agent_tools import AgentToolBroker, SQLAgentToolRepository
from pixelflow.platform.persistence import Base
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace import SQLVideoAgentRepository


def _free_port() -> int:
    """申请仅用于本 Case 的 loopback 端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _service_jwt(signing_key: str) -> str:
    """为查询 Sidecar 公开状态签发短期 Gateway 服务 JWT。"""

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "m0-gateway",
            "iss": "pixelflow-gateway",
            "aud": "pixelflow-harness-sidecar",
            "service_instance_id": "m0-gateway-e2e",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="HS256",
    )


def _sha256(label: str) -> str:
    """生成符合稳定协议格式的测试摘要，不包含用户正文。"""

    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


@pytest.mark.m0_real
def test_real_gateway_binding_sidecar_plugin_and_tool_broker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模型必须加载真实 Skill 后自主调用 Plugin，经 Broker 读取真实 SQL Workspace。"""

    if os.environ.get("PIXELFLOW_RUN_REAL_M0") != "1":
        pytest.skip("未显式开启会消耗测试 token 的真实 M0 用例")
    if not os.environ.get("DEEPSEEK_API_KEY") or not os.environ.get("DEEPSEEK_BASE_URL"):
        pytest.skip("缺少真实 Ark 模型测试凭据或端点")

    async def prepare_gateway() -> tuple[object, async_sessionmaker, SQLAgentToolRepository, SQLVideoAgentRepository, HarnessRunRequest]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm0-gateway.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        video_repository = SQLVideoAgentRepository(session_factory)
        now = datetime.now(UTC)
        await video_repository.create_workspace(
            "m0-e2e-user",
            VideoWorkspace(
                workspace_id="m0-e2e-workspace",
                conversation_id="m0-e2e-conversation",
                revision=1,
                payload={
                    "script": "展示白色陶瓷杯的三镜头短视频脚本",
                    "assets": [{"asset_id": "cup-reference"}],
                    "scenes": [{"scene_id": "scene-1"}, {"scene_id": "scene-2"}],
                },
                created_at=now,
                updated_at=now,
            ),
        )
        repository = SQLAgentToolRepository(session_factory)
        request = HarnessRunRequest(
            user_id="m0-e2e-user",
            conversation_id="m0-e2e-conversation",
            workspace_id="m0-e2e-workspace",
            workspace_revision=1,
            trigger_id="m0-e2e-inspect-turn",
            user_input="请读取当前视频工作区，告诉我脚本、素材和分镜数量。",
            system_instruction="你是 PixelFlow 视频 Agent。涉及当前工作区事实时不得猜测，必须遵循相关 Skill 的指令并使用受控 Tool 获取证据。",
            context_digest=_sha256("m0-e2e-context"),
            model_profile_digest=_sha256("m0-e2e-model-profile"),
            context_budget_digest=_sha256("m0-e2e-budget"),
            run_limits_digest=_sha256("m0-e2e-limits"),
            max_output_tokens=192,
        )
        return engine, session_factory, repository, video_repository, request

    engine, session_factory, repository, video_repository, run_request = asyncio.run(prepare_gateway())
    broker_jwt_key = "m0-tool-broker-jwt-signing-key-at-least-32-bytes"
    gateway_jwt_key = "m0-gateway-sidecar-jwt-signing-key-at-least-32-bytes"
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_VERIFY_KEY", broker_jwt_key)
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_ISSUER", "pixelflow-harness-sidecar")
    monkeypatch.setenv("PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE", "pixelflow-tool-broker")
    gateway_app = FastAPI()
    gateway_app.state.pixelflow_agent_tool_broker = AgentToolBroker(
        repository,
        video_repository,
    )
    gateway_app.add_middleware(AuthMiddleware)
    gateway_app.include_router(internal_agent_tools.router)
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
        "---\nname: workspace-inspection\ndescription: 当用户询问当前视频项目、脚本、素材、分镜或生成状态时使用。\nuser-invocable: false\n---\n"
        "当用户询问当前工作区事实时，必须先调用 inspect_video_workspace 获取证据，再基于 Tool 返回的安全摘要回答。不得猜测、编造或读取其他路径。",
        encoding="utf-8",
    )
    sidecar_root = Path(__file__).parents[2] / "services/pixelflow-agent-harness"
    sidecar_python = sidecar_root / ".venv/bin/python"
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
            "PIXELFLOW_SIDECAR_INSTANCE_ID": "m0-sidecar-e2e",
            "PIXELFLOW_HARNESS_MODEL_PROFILE": "deepseek-v4-pro",
            "PIXELFLOW_HARNESS_MODEL_ID": "deepseek-v4-pro-ga-260813",
            "PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS": "90",
            "PYTHONPATH": str(sidecar_root / "src"),
        },
    )
    sidecar_stderr_path = tmp_path / "sidecar-startup.stderr"
    with sidecar_stderr_path.open("w", encoding="utf-8") as sidecar_stderr:
        sidecar_process = subprocess.Popen(
            [
                "/usr/bin/arch",
                "-arm64",
                str(sidecar_python),
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
            stderr=sidecar_stderr,
        )
    sidecar_url = f"http://127.0.0.1:{sidecar_port}"
    try:
        with httpx.Client(timeout=5) as http_client:
            readiness_status: int | None = None
            readiness_code: str | None = None
            for _ in range(50):
                try:
                    ready = http_client.get(
                        f"{sidecar_url}/ready",
                        headers={"Authorization": f"Bearer {_service_jwt(gateway_jwt_key)}"},
                    )
                except httpx.ConnectError:
                    time.sleep(0.1)
                    continue
                readiness_status = ready.status_code
                detail = ready.json().get("detail") if ready.headers.get("content-type", "").startswith("application/json") else None
                readiness_code = detail.get("code") if isinstance(detail, dict) and isinstance(detail.get("code"), str) else None
                if ready.status_code == 200:
                    break
                time.sleep(0.1)
            else:
                startup_error = sidecar_stderr_path.read_text(encoding="utf-8")[-1_000:]
                pytest.fail(f"真实 Sidecar 未在限定时间达到 readiness：status={readiness_status} code={readiness_code} startup={startup_error}")

        async def create_and_bind() -> object:
            async with httpx.AsyncClient(timeout=10) as client:
                bridge = AgentHarnessSidecarClient(
                    base_url=sidecar_url,
                    gateway_jwt_signing_key=gateway_jwt_key,
                    gateway_instance_id="m0-gateway-e2e",
                    repository=repository,
                    client=client,
                )
                return await bridge.create_and_bind(run_request)

        run = asyncio.run(create_and_bind())
        assert run.status == "accepted"

        terminal: dict[str, object] | None = None
        events: list[dict[str, object]] = []
        with httpx.Client(timeout=10) as http_client:
            for _ in range(240):
                response = http_client.get(
                    f"{sidecar_url}/internal/v1/runs/{run.run_id}",
                    headers={"Authorization": f"Bearer {_service_jwt(gateway_jwt_key)}"},
                )
                assert response.status_code == 200
                current = response.json()
                if current["status"] in {"completed", "failed", "cancelled"}:
                    terminal = current
                    break
                time.sleep(0.25)
            stream = http_client.get(
                f"{sidecar_url}/internal/v1/runs/{run.run_id}/events?after_sequence=0",
                headers={"Authorization": f"Bearer {_service_jwt(gateway_jwt_key)}", "Accept": "text/event-stream"},
            )
            assert stream.status_code == 200
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
        assert terminal is not None
        assert terminal["status"] == "completed"
        tool_events = [event for event in events if event["type"] == "tool.completed"]
        assert {event["payload"]["tool_name"] for event in tool_events} >= {"skill", "inspect_video_workspace"}
        async def read_observation() -> dict[str, object]:
            async with session_factory() as session:
                row = await session.scalar(select(PixelFlowAgentHarnessToolCallRow).where(PixelFlowAgentHarnessToolCallRow.run_id == run.run_id))
                assert row is not None
                return dict(row.response_json)
        observation = asyncio.run(read_observation())
        assert observation["status"] == "completed"
        assert observation["model_observation"]["code"] == "workspace_inspected"
    finally:
        sidecar_process.terminate()
        try:
            sidecar_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sidecar_process.kill()
            sidecar_process.wait(timeout=5)
        gateway_server.should_exit = True
        gateway_thread.join(timeout=5)
        asyncio.run(engine.dispose())
