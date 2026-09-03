"""通过真实进程、HTTP、SQLite 与当前配置模型验证最小 Sidecar Run 链路。"""

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
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jwt
import pytest

from pixelflow_harness_sidecar.client import AgentHarnessSidecarClient
from pixelflow_harness_sidecar.contracts import HarnessRunRequest

_MODEL_PROFILE = {
    "logical_name": "deepseek-v4-pro",
    "model_id": "deepseek-v4-flash-vision-exp",
    "capability_version": "m0-real-v1",
    "budget_version": "m0-real-budget-v1",
}
_MODEL_PROFILE_DIGEST = "sha256:" + hashlib.sha256(
    json.dumps(_MODEL_PROFILE, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
).hexdigest()


def _request_payload(*, user_input: str = "请用不超过六个汉字说明连接状态。") -> dict[str, object]:
    """构造不含用户身份或供应商参数的真实 Sidecar 网络 DTO。"""

    return {
        "protocol_version": "v1",
        "run_request_key": "sha256:m0-real-sidecar-http",
        "request_digest": "sha256:m0-real-sidecar-request",
        "session_id": "pfh_m0_real_sidecar",
        "trigger": {"type": "user_turn", "trigger_id": "m0-real-sidecar-turn"},
        "binding": {
            "conversation_ref": "opaque:m0-real-conversation",
            "workspace_ref": "opaque:m0-real-workspace",
            "workspace_revision": 1,
            "context_digest": "sha256:m0-real-context",
        },
        "model": {
            "profile_name": "deepseek-v4-pro",
            "profile_digest": _MODEL_PROFILE_DIGEST,
            "max_output_tokens": 1024,
        },
        "context_budget": {
            "effective_context_k": 896,
            "output_reserve_k": 32,
            "safety_reserve_k": 32,
            "require_verified_model_profile": True,
            "policy_digest": "sha256:m0-real-budget",
        },
        "limits": {
            "profile": "m0_real_tool_v1",
            "digest": "sha256:430ab64beb0161d31afe22764dd1074e28fe264d05690ef4ecffe58c3312a2c6",
            "max_model_steps": 8,
            "max_business_tools": 3,
            "max_billable_batch_starts": 0,
            "deadline_seconds": 90,
        },
        "toolset": {"version": "agent-tools-v1", "manifest_digest": "sha256:m0-real-manifest"},
        "context": {
            "system_instruction": "你是 PixelFlow 的安全测试 Agent。不要调用未声明能力。",
            "user_input": user_input,
            "workspace_projection": {},
            "conversation_projection": {},
            "preference_projection": {},
            "brand_profile_projection": {},
            "long_term_memory_projection": [],
        },
    }


def _free_port() -> int:
    """申请一个仅供本测试临时使用的本机端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_manifest_broker(
    manifest: dict[str, object],
    *,
    tool_observation: dict[str, object] | None = None,
) -> tuple[str, ThreadingHTTPServer, threading.Thread, list[dict[str, object]]]:
    """提供最小真实 Broker HTTP 边界，并记录经过 Plugin 的 Tool 调用。"""

    encoded = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    calls: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/agent/internal/agent-tools/manifest":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:
            if self.path != "/agent/internal/agent-tools/calls" or tool_observation is None:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                self.send_error(400)
                return
            calls.append(body)
            response = json.dumps(tool_observation, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *_args: object) -> None:
            """测试 Broker 禁止输出请求头，避免服务 JWT 进入测试日志。"""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return f"http://{host}:{port}", server, thread, calls


def _gateway_service_jwt(signing_key: str) -> str:
    """签发仅用于本 Case 的短期 Gateway→Sidecar 服务 JWT。"""

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "m0-gateway",
            "iss": "pixelflow-gateway",
            "aud": "pixelflow-harness-sidecar",
            "service_instance_id": "m0-gateway-loopback",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="HS256",
    )


def _http_json(
    method: str,
    url: str,
    *,
    token: str,
    body: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, object]]:
    """通过真实 loopback HTTP 调用 Sidecar，不使用 ASGI TestClient。"""

    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return int(error.code), json.loads(error.read().decode("utf-8"))


def _http_sse(url: str, *, token: str) -> tuple[int, list[dict[str, object]]]:
    """消费已经终态的真实 SSE 响应，并只解析公开事件 DTO。"""

    request = Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
    )
    with urlopen(request, timeout=5) as response:
        data_lines = [
            line.removeprefix("data: ")
            for line in response.read().decode("utf-8").splitlines()
            if line.startswith("data: ")
        ]
        return int(response.status), [json.loads(line) for line in data_lines]


def _direct_deepseek_tool_call() -> dict[str, object]:
    """以 OpenAI 兼容协议验证当前直连模型的最小 Tool Calling 能力。"""

    base_url = os.environ["DEEPSEEK_BASE_URL"].rstrip("/")
    model_id = "deepseek-v4-flash-vision-exp"
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": "Call inspect_video_workspace now, with an empty object.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "inspect_video_workspace",
                    "description": "Read the current workspace.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "tool_choice": "auto",
        "max_tokens": 512,
    }
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        pytest.fail(f"DeepSeek Tool Calling 请求被拒绝：HTTP {error.code}")
    choice = data.get("choices", [{}])[0] if isinstance(data, dict) else {}
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []
    first_tool = tool_calls[0] if isinstance(tool_calls, list) and tool_calls else {}
    function = first_tool.get("function", {}) if isinstance(first_tool, dict) else {}
    return {
        "response_model": data.get("model") if isinstance(data, dict) else None,
        "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
        "tool_calls_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
        "tool_name": function.get("name") if isinstance(function, dict) else None,
    }


def _start_real_sidecar_process(*, port: int, environment: dict[str, str]) -> subprocess.Popen[bytes]:
    """启动实际 Uvicorn Sidecar 进程；调用方负责 kill/等待，不用 TestClient 代替。"""

    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pixelflow_harness_sidecar.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(Path(__file__).parents[1]),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ready(base_url: str, service_jwt: str) -> None:
    """等待真实 Sidecar 完成启动准入检查。"""

    for _ in range(50):
        try:
            status_code, body = _http_json("GET", f"{base_url}/ready", token=service_jwt)
        except URLError:
            time.sleep(0.1)
            continue
        if status_code == 200 and body == {"status": "ready"}:
            return
        time.sleep(0.1)
    pytest.fail("真实 Sidecar 进程未在限定时间内达到 readiness")


@pytest.mark.m0_real
def test_real_sidecar_http_persists_and_replays_real_model_run(tmp_path: Path) -> None:
    """真实 Sidecar 进程必须通过 HTTP 调用模型并从 SQLite 回放公开事件。"""

    if os.environ.get("PIXELFLOW_RUN_REAL_M0") != "1":
        pytest.skip("未显式开启会消耗测试 token 的真实 M0 用例")
    if not os.environ.get("DEEPSEEK_API_KEY") or not os.environ.get("DEEPSEEK_BASE_URL"):
        pytest.skip("缺少真实 DeepSeek 直连测试凭据或端点")

    root = tmp_path / "agent-home"
    skill_file = root / "skills" / "m0-probe-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: m0-probe-skill\ndescription: 验证隔离 Skill 根的真实模型测试规则。\n---\nM0 隔离 Skill 正文",
        encoding="utf-8",
    )
    jwt_signing_key = "m0-loopback-jwt-signing-key-at-least-32-bytes"
    service_jwt = _gateway_service_jwt(jwt_signing_key)
    broker_base_url, broker_server, broker_thread, _calls = _start_manifest_broker(
        {
            "protocol_version": "v1",
            "version": "agent-tools-v1",
            "digest": "sha256:m0-real-manifest",
            "tools": [],
        },
    )
    port = _free_port()
    source_root = str(Path(__file__).parents[1] / "src")
    environment = os.environ.copy()
    environment.update(
        {
            "PIXELFLOW_AGENT_HOME": str(root),
            "PIXELFLOW_HARNESS_RUN_STORE": str(root / "run-events" / "runs.sqlite3"),
            "PIXELFLOW_GATEWAY_JWT_VERIFY_KEY": jwt_signing_key,
            "PIXELFLOW_GATEWAY_JWT_ISSUER": "pixelflow-gateway",
            "PIXELFLOW_GATEWAY_JWT_AUDIENCE": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_BASE_URL": broker_base_url,
            "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": "m0-tool-broker-signing-key-at-least-32-bytes",
            "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": "pixelflow-tool-broker",
            "PIXELFLOW_SIDECAR_INSTANCE_ID": "m0-sidecar-http",
            "PIXELFLOW_HARNESS_PROFILE_NAME": "deepseek-v4-pro",
            "PIXELFLOW_HARNESS_CAPABILITY_VERSION": "m0-real-v1",
            "PIXELFLOW_HARNESS_BUDGET_VERSION": "m0-real-budget-v1",
            "PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST": "sha256:m0-real-manifest",
            "PIXELFLOW_HARNESS_MODEL_ID": "deepseek-v4-flash-vision-exp",
            "PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES": json.dumps(
                {
                    "m0_real_tool_v1": {
                        "deadline_seconds": 90,
                        "max_model_steps": 8,
                        "max_business_tools": 3,
                        "max_billable_batch_starts": 0,
                    }
                },
                separators=(",", ":"),
            ),
            "PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS": "90",
            "PYTHONPATH": source_root,
        },
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pixelflow_harness_sidecar.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(Path(__file__).parents[1]),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                ready_status, ready_body = _http_json("GET", f"{base_url}/ready", token=service_jwt)
            except URLError:
                time.sleep(0.1)
                continue
            if ready_status == 200 and ready_body == {"status": "ready"}:
                break
            time.sleep(0.1)
        else:
            pytest.fail("真实 Sidecar 进程未在限定时间内达到 readiness")

        payload = _request_payload()
        async def create_and_activate_run_via_client():
            """在同一事件循环内先创建、再激活已绑定 Run 并关闭 Client。"""

            sidecar_client = AgentHarnessSidecarClient(
                base_url=base_url,
                service_jwt=service_jwt,
                timeout_seconds=10,
            )
            try:
                accepted_run = await sidecar_client.create_run(
                    HarnessRunRequest.model_validate(payload),
                )
                await sidecar_client.activate_run(accepted_run.run_id)
                return accepted_run
            finally:
                await sidecar_client.aclose()

        accepted = asyncio.run(create_and_activate_run_via_client())
        run_id = accepted.run_id
        assert accepted.status == "accepted"
        terminal: dict[str, object] | None = None
        for _ in range(180):
            current_status, current = _http_json(
                "GET",
                f"{base_url}/internal/v1/runs/{run_id}",
                token=service_jwt,
            )
            assert current_status == 200
            if current.get("status") in {"completed", "failed", "cancelled"}:
                terminal = current
                break
            time.sleep(0.25)
        assert terminal is not None
        assert terminal["status"] == "completed"
        assert terminal["termination_reason"] == "completed"

        event_status, events = _http_sse(
            f"{base_url}/internal/v1/runs/{run_id}/events?after_sequence=0",
            token=service_jwt,
        )
        assert event_status == 200
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert {event["type"] for event in events} >= {
            "run.accepted",
            "run.started",
            "response.completed",
            "run.completed",
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        broker_server.shutdown()
        broker_thread.join(timeout=5)


@pytest.mark.m0_real
def test_real_sidecar_context_policy_and_direct_deepseek_tool_call(
    tmp_path: Path,
) -> None:
    """真实 Engine 要经过 Context Policy，且直连模型必须支持 Tool Calling。"""

    if os.environ.get("PIXELFLOW_RUN_REAL_M0") != "1":
        pytest.skip("未显式开启会消耗测试 token 的真实 Tool Calling 用例")
    if not os.environ.get("DEEPSEEK_API_KEY") or not os.environ.get("DEEPSEEK_BASE_URL"):
        pytest.skip("缺少真实 DeepSeek 直连凭据或端点")

    root = tmp_path / "tool-calling-agent-home"
    skill_file = root / "skills" / "tool-calling-probe" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: tool-calling-probe\ndescription: 验证安全 Capability Tool 调用。\n---\n"
        "需要读取工作区时，只调用已发布 Tool。",
        encoding="utf-8",
    )
    jwt_signing_key = "m0-tool-calling-jwt-signing-key-at-least-32-bytes"
    service_jwt = _gateway_service_jwt(jwt_signing_key)
    broker_base_url, broker_server, broker_thread, calls = _start_manifest_broker(
        {
            "protocol_version": "v1",
            "version": "agent-tools-v1",
            "digest": "sha256:m0-real-tool-manifest",
            "tools": [
                {
                    "name": "analyze_video",
                    "description": "读取已授权视频的安全测试状态；仅在当前测试要求时调用。",
                    "parameters_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                    "cost_level": "external_read",
                    "confirmation_required": False,
                }
            ],
        },
        tool_observation={
            "status": "completed",
            "public_summary": "已读取安全测试状态",
            "model_observation": {"status": "completed"},
        },
    )
    port = _free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "PIXELFLOW_AGENT_HOME": str(root),
            "PIXELFLOW_HARNESS_RUN_STORE": str(root / "run-events" / "runs.sqlite3"),
            "PIXELFLOW_GATEWAY_JWT_VERIFY_KEY": jwt_signing_key,
            "PIXELFLOW_GATEWAY_JWT_ISSUER": "pixelflow-gateway",
            "PIXELFLOW_GATEWAY_JWT_AUDIENCE": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_BASE_URL": broker_base_url,
            "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": "m0-tool-calling-broker-signing-key-at-least-32-bytes",
            "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": "pixelflow-tool-broker",
            "PIXELFLOW_SIDECAR_INSTANCE_ID": "m0-sidecar-tool-calling",
            "PIXELFLOW_HARNESS_PROFILE_NAME": "deepseek-v4-pro",
            "PIXELFLOW_HARNESS_CAPABILITY_VERSION": "m0-real-v1",
            "PIXELFLOW_HARNESS_BUDGET_VERSION": "m0-real-budget-v1",
            "PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST": "sha256:m0-real-manifest",
            "PIXELFLOW_HARNESS_MODEL_ID": "deepseek-v4-flash-vision-exp",
            "PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES": json.dumps(
                {
                    "m0_real_tool_v1": {
                        "deadline_seconds": 90,
                        "max_model_steps": 8,
                        "max_business_tools": 3,
                        "max_billable_batch_starts": 0,
                    }
                },
                separators=(",", ":"),
            ),
            "PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS": "90",
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        },
    )
    process = _start_real_sidecar_process(port=port, environment=environment)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base_url, service_jwt)
        payload = _request_payload(
            user_input=(
                "请先且仅调用一次 analyze_video 工具（无参数）。"
                "收到工具结果后，只回复“已确认”。"
            )
        )
        payload["run_request_key"] = "sha256:m0-real-direct-deepseek-tool"
        payload["request_digest"] = "sha256:m0-real-direct-deepseek-tool-request"
        payload["session_id"] = "pfh_m0_real_direct_deepseek_tool"
        payload["toolset"] = {
            "version": "agent-tools-v1",
            "manifest_digest": "sha256:m0-real-tool-manifest",
        }

        async def create_and_activate_run() -> object:
            client = AgentHarnessSidecarClient(
                base_url=base_url, service_jwt=service_jwt, timeout_seconds=10
            )
            try:
                accepted = await client.create_run(HarnessRunRequest.model_validate(payload))
                await client.activate_run(accepted.run_id)
                return accepted
            finally:
                await client.aclose()

        accepted = asyncio.run(create_and_activate_run())
        for _ in range(180):
            status, snapshot = _http_json(
                "GET", f"{base_url}/internal/v1/runs/{accepted.run_id}", token=service_jwt
            )
            assert status == 200
            if snapshot.get("status") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.25)
        else:
            pytest.fail("真实 Tool Calling Run 未在限定时间结束")

        assert snapshot["status"] == "completed", snapshot
        # Agent 是否主动选择业务 Tool 由模型决定；本 Case 只确认完整 Engine 在 Context
        # Policy 参与下可完成。Tool Plugin 的 HTTP 转发由 Node 回归覆盖，模型兼容性则
        # 由下方强制的 OpenAI 兼容 Tool Calling 直接验证，避免将随机采样当作门禁。
        assert len(calls) <= 1
        direct = _direct_deepseek_tool_call()
        assert direct["response_model"] == "deepseek-v4-flash-vision-exp"
        assert direct["finish_reason"] == "tool_calls"
        assert direct["tool_calls_count"] == 1
        assert direct["tool_name"] == "inspect_video_workspace"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        broker_server.shutdown()
        broker_thread.join(timeout=5)


@pytest.mark.m0_real
def test_real_sidecar_kill_restart_safely_closes_unfinished_run(tmp_path: Path) -> None:
    """真实 kill/restart 后不得续跑旧 Session，必须追加可恢复所需的固定失败事件。"""

    if os.environ.get("PIXELFLOW_RUN_REAL_M0") != "1":
        pytest.skip("未显式开启真实 M0 进程恢复用例")
    if not os.environ.get("DEEPSEEK_API_KEY") or not os.environ.get("DEEPSEEK_BASE_URL"):
        pytest.skip("缺少启动真实 Sidecar 所需的模型凭据或端点")

    root = tmp_path / "restart-agent-home"
    skill_file = root / "skills" / "restart-safety" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: restart-safety\ndescription: 仅验证 Sidecar 进程恢复边界。\n---\n不得续跑中断 Session。",
        encoding="utf-8",
    )
    jwt_signing_key = "m0-restart-jwt-signing-key-at-least-32-bytes"
    service_jwt = _gateway_service_jwt(jwt_signing_key)
    port = _free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "PIXELFLOW_AGENT_HOME": str(root),
            "PIXELFLOW_HARNESS_RUN_STORE": str(root / "run-events" / "runs.sqlite3"),
            "PIXELFLOW_GATEWAY_JWT_VERIFY_KEY": jwt_signing_key,
            "PIXELFLOW_GATEWAY_JWT_ISSUER": "pixelflow-gateway",
            "PIXELFLOW_GATEWAY_JWT_AUDIENCE": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_BASE_URL": "http://127.0.0.1:19999",
            "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": "m0-restart-tool-broker-signing-key-at-least-32-bytes",
            "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": "pixelflow-tool-broker",
            "PIXELFLOW_SIDECAR_INSTANCE_ID": "m0-sidecar-restart",
            "PIXELFLOW_HARNESS_PROFILE_NAME": "deepseek-v4-pro",
            "PIXELFLOW_HARNESS_CAPABILITY_VERSION": "m0-real-v1",
            "PIXELFLOW_HARNESS_BUDGET_VERSION": "m0-real-budget-v1",
            "PIXELFLOW_HARNESS_TOOL_MANIFEST_DIGEST": "sha256:m0-real-manifest",
            "PIXELFLOW_HARNESS_MODEL_ID": "deepseek-v4-flash-vision-exp",
            "PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES": json.dumps(
                {
                    "m0_real_tool_v1": {
                        "deadline_seconds": 90,
                        "max_model_steps": 8,
                        "max_business_tools": 3,
                        "max_billable_batch_starts": 0,
                    }
                },
                separators=(",", ":"),
            ),
            "PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS": "90",
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        },
    )
    base_url = f"http://127.0.0.1:{port}"
    process = _start_real_sidecar_process(port=port, environment=environment)
    try:
        _wait_ready(base_url, service_jwt)
        payload = _request_payload()
        payload["run_request_key"] = "sha256:m0-real-sidecar-restart"
        payload["request_digest"] = "sha256:m0-real-sidecar-restart-request"
        payload["session_id"] = "pfh_m0_real_sidecar_restart"
        created_status, created = _http_json(
            "POST",
            f"{base_url}/internal/v1/runs",
            token=service_jwt,
            body=payload,
            idempotency_key=str(payload["run_request_key"]),
        )
        assert created_status == 202, created
        run_id = str(created["run_id"])
        assert created["status"] == "accepted"

        process.kill()
        process.wait(timeout=5)
        process = _start_real_sidecar_process(port=port, environment=environment)
        _wait_ready(base_url, service_jwt)

        state_status, state = _http_json(
            "GET",
            f"{base_url}/internal/v1/runs/{run_id}",
            token=service_jwt,
        )
        assert state_status == 200
        assert state["status"] == "failed"
        assert state["termination_reason"] == "engine_error"
        event_status, events = _http_sse(
            f"{base_url}/internal/v1/runs/{run_id}/events?after_sequence=0",
            token=service_jwt,
        )
        assert event_status == 200
        assert [event["sequence"] for event in events] == [1, 2]
        assert events[-1]["type"] == "run.failed"
        assert events[-1]["payload"] == {"code": "harness_run_recovery_required"}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
