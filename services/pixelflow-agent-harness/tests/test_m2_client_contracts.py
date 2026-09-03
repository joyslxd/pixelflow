"""验证 M2 Sidecar Client 对不可信网络响应失败关闭且不自动重试。"""

from __future__ import annotations

import json

import httpx
import pytest

from pixelflow_harness_sidecar.client import (
    AgentHarnessSidecarClient,
    AgentHarnessSidecarClientError,
)
from pixelflow_harness_sidecar.contracts import HarnessRunRequest


def _request() -> HarnessRunRequest:
    """构造不含身份、凭据和 Provider 参数的稳定请求。"""

    return HarnessRunRequest.model_validate(
        {
            "protocol_version": "v1",
            "run_request_key": "sha256:m2-client-request",
            "request_digest": "sha256:m2-client-digest",
            "session_id": "pfh_m2_client",
            "trigger": {"type": "user_turn", "trigger_id": "m2-client-turn"},
            "binding": {
                "conversation_ref": "opaque:m2-client-conversation",
                "workspace_ref": "opaque:m2-client-workspace",
                "workspace_revision": 1,
                "context_digest": "sha256:m2-client-context",
            },
            "model": {
                "profile_name": "m2-client-model",
                "profile_digest": "sha256:m2-client-model-digest",
                "max_output_tokens": 32,
            },
            "context_budget": {
                "effective_context_k": 896,
                "output_reserve_k": 32,
                "safety_reserve_k": 32,
                "require_verified_model_profile": True,
                "policy_digest": "sha256:m2-client-budget",
            },
            "limits": {"max_model_steps": 8, "max_business_tools": 3, "deadline_seconds": 90},
            "toolset": {"version": "agent-tools-v1", "manifest_digest": "sha256:m2-client-manifest"},
            "context": {
                "system_instruction": "执行 M2 Client 合同测试。",
                "user_input": "不发送真实模型请求。",
                "workspace_projection": {},
                "conversation_projection": {},
                "preference_projection": {},
                "brand_profile_projection": {},
                "long_term_memory_projection": [],
            },
        }
    )


@pytest.mark.asyncio
async def test_create_run_does_not_retry_unknown_sidecar_5xx() -> None:
    """创建请求遇到未知 5xx 只能失败，不能自动重发可能已接受的 Run。"""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert request.url.path == "/internal/v1/runs"
        return httpx.Response(503, json={"detail": {"code": "unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = AgentHarnessSidecarClient(
            base_url="http://127.0.0.1:8090",
            service_jwt="m2-test-service-jwt",
            client=transport,
        )
        with pytest.raises(AgentHarnessSidecarClientError, match="拒绝或不可用"):
            await client.create_run(_request())

    assert request_count == 1


@pytest.mark.asyncio
async def test_sse_out_of_order_event_fails_closed() -> None:
    """客户端收到重复或乱序 sequence 时不得继续投影后续事件。"""

    first = {
        "protocol_version": "v1",
        "run_id": "hrun_m2_client",
        "event_id": "hevt_m2_client_1",
        "sequence": 1,
        "type": "run.accepted",
        "occurred_at": "2026-08-25T00:00:00Z",
        "payload": {"status": "accepted"},
    }
    duplicate = {**first, "event_id": "hevt_m2_client_2"}
    body = "".join(
        f"data: {json.dumps(event)}\n\n"
        for event in (first, duplicate)
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = AgentHarnessSidecarClient(
            base_url="http://127.0.0.1:8090",
            service_jwt="m2-test-service-jwt",
            client=transport,
        )
        iterator = client.stream_events("hrun_m2_client", after_sequence=0)
        first_event = await anext(iterator)
        assert first_event.sequence == 1
        with pytest.raises(AgentHarnessSidecarClientError, match="序列"):
            await anext(iterator)
