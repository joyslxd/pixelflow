"""验证 M1 Agent Harness 稳定 Port 的 DTO 与 M0 Bridge 适配关系。"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from pixelflow.agent_harness import (
    AgentHarnessPort,
    AgentHarnessSidecarClient,
    GatewayHarnessSidecarError,
    HarnessRunRequest,
)
from pixelflow.agent_harness.contracts import HarnessRunEvent
from pixelflow.agent_harness.projector import HarnessRunProjector


def _digest(value: str) -> str:
    """生成满足 DTO 格式约束的无敏感测试摘要。"""

    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def test_m0_bridge_satisfies_m1_agent_harness_port() -> None:
    """既有真实 Bridge 必须可作为 M1 Port 使用，避免产生第二条运行时链路。"""

    assert isinstance(AgentHarnessSidecarClient, type)
    assert issubclass(AgentHarnessSidecarClient, AgentHarnessPort)


@pytest.mark.asyncio
async def test_gateway_bridge_only_allows_fixed_compose_sidecar_endpoint() -> None:
    """Docker 内网明文 HTTP 只能使用部署文件声明的固定服务名和端口。"""

    kwargs = {
        "gateway_jwt_signing_key": "k" * 32,
        "gateway_instance_id": "gateway-test",
        "repository": object(),
    }
    allowed = AgentHarnessSidecarClient(
        base_url="http://harness-sidecar:8090",
        **kwargs,
    )
    assert allowed is not None
    await allowed.aclose()
    with pytest.raises(ValueError, match="地址"):
        AgentHarnessSidecarClient(base_url="http://harness-sidecar:8091", **kwargs)


@pytest.mark.asyncio
async def test_gateway_does_not_retry_unknown_sidecar_create_failure() -> None:
    """Sidecar 创建请求返回 5xx 时，Gateway 不得自动重发未知状态的 Run。"""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert request.url.path == "/internal/v1/runs"
        return httpx.Response(503, json={"detail": {"code": "unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        bridge = AgentHarnessSidecarClient(
            base_url="http://127.0.0.1:8090",
            gateway_jwt_signing_key="k" * 32,
            gateway_instance_id="gateway-test",
            repository=object(),
            client=transport,
        )
        request = HarnessRunRequest(
            user_id="m2-user",
            conversation_id="m2-conversation",
            workspace_id="m2-workspace",
            workspace_revision=1,
            trigger_id="m2-turn",
            user_input="测试 M2 网络失败。",
            system_instruction="执行受控测试。",
            context_digest=_digest("context"),
            model_profile_digest=_digest("model"),
                context_budget_digest=_digest("budget"),
                run_limits_digest=_digest("limits"),
                limit_profile="video_interactive_v1",
                max_model_steps=12,
                max_business_tools=6,
                max_billable_batch_starts=1,
                deadline_seconds=180,
        )
        with pytest.raises(GatewayHarnessSidecarError, match="拒绝 Run 创建"):
            await bridge.create_and_bind(request)

    assert request_count == 1


@pytest.mark.asyncio
async def test_transient_credential_grant_is_bound_before_activation_and_never_sent_to_sidecar() -> None:
    """确认授权票据只留在 Gateway 内存，绑定完成后才激活 Sidecar Run。"""

    calls: list[str] = []
    bound: list[tuple[str, str | None]] = []

    class Repository:
        async def register_run_binding(self, binding):
            calls.append(f"binding:{binding.run_id}")
            return binding

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/internal/v1/runs":
            assert b"credential-grant" not in request.content
            return httpx.Response(
                202,
                json={
                    "protocol_version": "v1",
                    "run_id": "hrun_" + "a" * 32,
                    "status": "created",
                    "termination_reason": None,
                    "engine_id": "test",
                    "engine_version": "v1",
                    "skill_catalog_digest": _digest("skills"),
                    "accepted_at": "2026-08-27T00:00:00+00:00",
                    "completed_at": None,
                },
            )
        assert request.url.path == "/internal/v1/runs/hrun_" + "a" * 32 + "/activate"
        assert bound == [("hrun_" + "a" * 32, "credential-grant:test")]
        return httpx.Response(
            200,
            json={
                "protocol_version": "v1",
                "run_id": "hrun_" + "a" * 32,
                "status": "running",
                "termination_reason": None,
                "engine_id": "test",
                "engine_version": "v1",
                "skill_catalog_digest": _digest("skills"),
                "accepted_at": "2026-08-27T00:00:00+00:00",
                "completed_at": None,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        bridge = AgentHarnessSidecarClient(
            base_url="http://127.0.0.1:8090",
            gateway_jwt_signing_key="k" * 32,
            gateway_instance_id="gateway-test",
            repository=Repository(),
            client=transport,
            on_run_bound=lambda run_id, request: _record_grant(bound, run_id, request.transient_credential_grant_id),
        )
        request = HarnessRunRequest(
            user_id="m5-user",
            conversation_id="m5-conversation",
            workspace_id="m5-workspace",
            workspace_revision=1,
            trigger_id="confirmation",
            trigger_type="confirmation_resume",
            user_input="用户已确认。",
            system_instruction="执行受控操作。",
            context_digest=_digest("context"),
            model_profile_digest=_digest("model"),
            context_budget_digest=_digest("budget"),
            run_limits_digest=_digest("limits"),
            limit_profile="confirmation_resume_v1",
            max_model_steps=10,
            max_business_tools=5,
            max_billable_batch_starts=1,
            deadline_seconds=150,
            transient_credential_grant_id="credential-grant:test",
        )
        await bridge.create_and_bind(request)

    assert calls == ["/internal/v1/runs", "binding:hrun_" + "a" * 32, "/internal/v1/runs/hrun_" + "a" * 32 + "/activate"]


async def _record_grant(
    values: list[tuple[str, str | None]],
    run_id: str,
    grant_id: str | None,
) -> None:
    values.append((run_id, grant_id))


def test_harness_run_request_rejects_unknown_or_invalid_digest() -> None:
    """稳定请求 DTO 必须拒绝未知字段和未冻结的摘要。"""

    payload = {
        "user_id": "m1-user",
        "conversation_id": "m1-conversation",
        "workspace_id": "m1-workspace",
        "workspace_revision": 1,
        "trigger_id": "m1-turn",
        "user_input": "读取工作区",
        "system_instruction": "遵循受控 Tool。",
        "context_digest": _digest("context"),
        "model_profile_digest": _digest("model"),
        "context_budget_digest": _digest("budget"),
            "run_limits_digest": _digest("limits"),
            "limit_profile": "video_interactive_v1",
            "max_model_steps": 12,
            "max_business_tools": 6,
            "max_billable_batch_starts": 1,
            "deadline_seconds": 180,
    }

    request = HarnessRunRequest.model_validate(payload)
    assert request.trigger_type == "user_turn"

    authorization_resume = HarnessRunRequest.model_validate(
        {**payload, "trigger_type": "authorization_resume"},
    )
    assert authorization_resume.trigger_type == "authorization_resume"

    invalid = dict(payload, context_digest="sha256:not-a-valid-digest")
    try:
        HarnessRunRequest.model_validate(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("无效摘要不应通过稳定 DTO")


def test_confirmation_suspension_projects_interrupt_id_for_public_host() -> None:
    """浏览器必须从公开 Run 事件取得确认 API 所需的中断身份。"""

    event_type, payload = HarnessRunProjector._public_event(  # noqa: SLF001 - 锁定公开投影合同。
        HarnessRunEvent(
            run_id="hrun_" + "c" * 32,
            event_id="hevt_" + "d" * 32,
            sequence=3,
            type="run.suspended",
            occurred_at="2026-08-27T00:00:00Z",
            payload={
                "status": "suspended_confirmation",
                "interrupt_id": "interrupt-confirmation-1",
            },
        )
    )

    assert event_type.value == "run.state_changed"
    assert payload == {
        "status": "suspended_confirmation",
        "interrupt_id": "interrupt-confirmation-1",
    }
