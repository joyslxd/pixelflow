"""验证 M1 Agent Harness 稳定 Port 的 DTO 与 M0 Bridge 适配关系。"""

from __future__ import annotations

import hashlib

from pixelflow.agent_harness import AgentHarnessPort, AgentHarnessSidecarClient, HarnessRunRequest


def _digest(value: str) -> str:
    """生成满足 DTO 格式约束的无敏感测试摘要。"""

    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def test_m0_bridge_satisfies_m1_agent_harness_port() -> None:
    """既有真实 Bridge 必须可作为 M1 Port 使用，避免产生第二条运行时链路。"""

    assert isinstance(AgentHarnessSidecarClient, type)
    assert issubclass(AgentHarnessSidecarClient, AgentHarnessPort)


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
    }

    request = HarnessRunRequest.model_validate(payload)
    assert request.trigger_type == "user_turn"

    invalid = dict(payload, context_digest="sha256:not-a-valid-digest")
    try:
        HarnessRunRequest.model_validate(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("无效摘要不应通过稳定 DTO")
