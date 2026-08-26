"""通过真实 Sidecar 进程验证 M2 取消 HTTP 协议，不调用模型或 Provider。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jwt
import pytest


def _free_port() -> int:
    """申请仅供本测试进程使用的 loopback 端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _service_jwt(signing_key: str) -> str:
    """签发不含用户身份的短期 Gateway 服务凭据。"""

    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "m2-gateway",
            "iss": "pixelflow-gateway",
            "aud": "pixelflow-harness-sidecar",
            "service_instance_id": "m2-loopback",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="HS256",
    )


def _payload() -> dict[str, object]:
    """构造不含用户正文、授权或 Provider 参数的合法创建请求。"""

    return {
        "protocol_version": "v1",
        "run_request_key": "sha256:m2-http-cancel",
        "request_digest": "sha256:m2-http-cancel-request",
        "session_id": "pfh_m2_http_cancel",
        "trigger": {"type": "user_turn", "trigger_id": "m2-http-turn"},
        "binding": {
            "conversation_ref": "opaque:m2-http-conversation",
            "workspace_ref": "opaque:m2-http-workspace",
            "workspace_revision": 1,
            "context_digest": "sha256:m2-http-context",
        },
        "model": {
            "profile_name": "deepseek-v4-pro",
            "profile_digest": "sha256:m2-http-model",
            "max_output_tokens": 32,
        },
        "context_budget": {
            "effective_context_k": 896,
            "output_reserve_k": 32,
            "safety_reserve_k": 32,
            "require_verified_model_profile": True,
            "policy_digest": "sha256:m2-http-budget",
        },
        "limits": {"max_model_steps": 8, "max_business_tools": 3, "deadline_seconds": 90},
        "toolset": {"version": "agent-tools-v1", "manifest_digest": "sha256:m2-http-manifest"},
        "context": {
            "system_instruction": "执行 M2 HTTP 协议测试。",
            "user_input": "不激活模型。",
            "workspace_projection": {},
            "conversation_projection": {},
            "preference_projection": {},
            "brand_profile_projection": {},
            "long_term_memory_projection": [],
        },
    }


def _http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, object]]:
    """经真实 HTTP 边界调用 Sidecar，错误体只用于固定错误码断言。"""

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - 固定为本机测试进程地址。
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return int(error.code), json.loads(error.read().decode("utf-8"))


def _wait_live(base_url: str) -> None:
    """等待实际 Uvicorn Sidecar 进程接受连接。"""

    for _ in range(50):
        try:
            status, body = _http_json("GET", f"{base_url}/live")
        except URLError:
            time.sleep(0.1)
            continue
        if status == 200 and body == {"status": "live"}:
            return
        time.sleep(0.1)
    pytest.fail("M2 Sidecar 测试进程未启动")


def _start_sidecar_process(*, port: int, environment: dict[str, str]) -> subprocess.Popen[bytes]:
    """启动真实 Uvicorn Sidecar 进程，供 HTTP 与重启演练复用。"""

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


def test_cancel_endpoint_is_authenticated_idempotent_and_replayable(tmp_path: Path) -> None:
    """取消 accepted Run 必须经真实 HTTP、SQLite 与 SSE 留下唯一审计终态。"""

    skill_file = tmp_path / "agent-home" / "skills" / "m2-http" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\ndescription: M2 HTTP 测试。\n---\n测试正文", encoding="utf-8")
    signing_key = "m2-sidecar-http-jwt-signing-key-at-least-32-bytes"
    port = _free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "PIXELFLOW_AGENT_HOME": str(tmp_path / "agent-home"),
            "PIXELFLOW_HARNESS_RUN_STORE": str(tmp_path / "runs.sqlite3"),
            "PIXELFLOW_GATEWAY_JWT_VERIFY_KEY": signing_key,
            "PIXELFLOW_GATEWAY_JWT_ISSUER": "pixelflow-gateway",
            "PIXELFLOW_GATEWAY_JWT_AUDIENCE": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_BASE_URL": "http://127.0.0.1:19999",
            "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": "m2-tool-broker-signing-key-at-least-32-bytes",
            "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": "pixelflow-tool-broker",
            "PIXELFLOW_SIDECAR_INSTANCE_ID": "m2-sidecar-http",
            "DEEPSEEK_API_KEY": "test-only-not-used",
            "DEEPSEEK_BASE_URL": "https://example.invalid",
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        },
    )
    process = _start_sidecar_process(port=port, environment=environment)
    base_url = f"http://127.0.0.1:{port}"
    token = _service_jwt(signing_key)
    try:
        _wait_live(base_url)
        missing_auth_status, missing_auth_body = _http_json(
            "POST",
            f"{base_url}/internal/v1/runs/not-a-run/cancel",
        )
        assert missing_auth_status == 401
        assert missing_auth_body == {"detail": {"code": "service_authentication_failed"}}

        payload = _payload()
        create_status, created = _http_json(
            "POST",
            f"{base_url}/internal/v1/runs",
            token=token,
            body=payload,
            idempotency_key=str(payload["run_request_key"]),
        )
        assert create_status == 202
        run_id = str(created["run_id"])

        replay_status, replay = _http_json(
            "POST",
            f"{base_url}/internal/v1/runs",
            token=token,
            body=payload,
            idempotency_key=str(payload["run_request_key"]),
        )
        assert replay_status == 202
        assert replay["run_id"] == run_id

        conflict_payload = dict(payload)
        conflict_payload["request_digest"] = "sha256:m2-http-cancel-request-conflict"
        conflict_status, conflict_body = _http_json(
            "POST",
            f"{base_url}/internal/v1/runs",
            token=token,
            body=conflict_payload,
            idempotency_key=str(payload["run_request_key"]),
        )
        assert conflict_status == 409
        assert conflict_body == {"detail": {"code": "run_request_conflict"}}

        first_status, first = _http_json(
            "POST", f"{base_url}/internal/v1/runs/{run_id}/cancel", token=token,
        )
        second_status, second = _http_json(
            "POST", f"{base_url}/internal/v1/runs/{run_id}/cancel", token=token,
        )
        assert first_status == second_status == 200
        assert first == second
        assert first["status"] == "cancelled"
        assert first["termination_reason"] == "cancelled"

        events_request = Request(
            f"{base_url}/internal/v1/runs/{run_id}/events?after_sequence=0",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        )
        with urlopen(events_request, timeout=5) as response:  # noqa: S310 - 固定为本机测试进程地址。
            events = [
                json.loads(line.removeprefix("data: "))
                for line in response.read().decode("utf-8").splitlines()
                if line.startswith("data: ")
            ]
        assert [event["type"] for event in events] == ["run.accepted", "run.cancelled"]
        assert [event["sequence"] for event in events] == [1, 2]

        cursor_status, cursor_body = _http_json(
            "GET",
            f"{base_url}/internal/v1/runs/{run_id}/events?after_sequence=3",
            token=token,
        )
        assert cursor_status == 422
        assert cursor_body == {"detail": {"code": "after_sequence_unknown"}}

        missing_run_status, missing_run_body = _http_json(
            "POST",
            f"{base_url}/internal/v1/runs/hrun_0123456789abcdef0123456789abcdef/cancel",
            token=token,
        )
        assert missing_run_status == 404
        assert missing_run_body == {"detail": {"code": "run_not_found"}}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_kill_restart_closes_unactivated_run_with_auditable_event(tmp_path: Path) -> None:
    """kill -9 后不得续跑旧 Session，重启 Sidecar 必须写入稳定失败终态。"""

    skill_file = tmp_path / "agent-home" / "skills" / "m2-restart" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\ndescription: M2 重启测试。\n---\n测试正文", encoding="utf-8")
    signing_key = "m2-sidecar-restart-jwt-signing-key-at-least-32-bytes"
    port = _free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "PIXELFLOW_AGENT_HOME": str(tmp_path / "agent-home"),
            "PIXELFLOW_HARNESS_RUN_STORE": str(tmp_path / "runs.sqlite3"),
            "PIXELFLOW_GATEWAY_JWT_VERIFY_KEY": signing_key,
            "PIXELFLOW_GATEWAY_JWT_ISSUER": "pixelflow-gateway",
            "PIXELFLOW_GATEWAY_JWT_AUDIENCE": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_BASE_URL": "http://127.0.0.1:19999",
            "PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY": "m2-restart-tool-broker-signing-key-at-least-32-bytes",
            "PIXELFLOW_TOOL_BROKER_JWT_ISSUER": "pixelflow-harness-sidecar",
            "PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE": "pixelflow-tool-broker",
            "PIXELFLOW_SIDECAR_INSTANCE_ID": "m2-sidecar-restart",
            "DEEPSEEK_API_KEY": "test-only-not-used",
            "DEEPSEEK_BASE_URL": "https://example.invalid",
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        },
    )
    base_url = f"http://127.0.0.1:{port}"
    token = _service_jwt(signing_key)
    process = _start_sidecar_process(port=port, environment=environment)
    try:
        _wait_live(base_url)
        payload = _payload()
        payload["run_request_key"] = "sha256:m2-restart"
        payload["request_digest"] = "sha256:m2-restart-request"
        payload["session_id"] = "pfh_m2_restart"
        created_status, created = _http_json(
            "POST",
            f"{base_url}/internal/v1/runs",
            token=token,
            body=payload,
            idempotency_key=str(payload["run_request_key"]),
        )
        assert created_status == 202
        run_id = str(created["run_id"])

        process.kill()
        process.wait(timeout=5)
        process = _start_sidecar_process(port=port, environment=environment)
        _wait_live(base_url)

        state_status, state = _http_json(
            "GET", f"{base_url}/internal/v1/runs/{run_id}", token=token,
        )
        assert state_status == 200
        assert state["status"] == "failed"
        assert state["termination_reason"] == "engine_error"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
