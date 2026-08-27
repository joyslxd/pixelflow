"""验证 Harness Runtime 故障只投影安全诊断字段。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixelflow_harness_sidecar.contracts import HarnessRunRequest
from pixelflow_harness_sidecar.deepseek_engine import (
    HarnessExecutionDiagnostic,
    HarnessExecutionError,
    HarnessProjectionError,
    _execution_diagnostic,
    _project_harness_result,
)
from pixelflow_harness_sidecar.event_store import SqliteRunEventStore
from pixelflow_harness_sidecar.run_service import RunService
from pixelflow_harness_sidecar.skill_snapshot import snapshot_skill_root


def _request() -> HarnessRunRequest:
    """构造不包含真实用户内容的固定 Run 请求。"""

    return HarnessRunRequest.model_validate(
        {
            "protocol_version": "v1",
            "run_request_key": "sha256:m1-runtime-diagnostic-run",
            "request_digest": "sha256:m1-runtime-diagnostic-request",
            "session_id": "pfh_m1_runtime_diagnostic",
            "trigger": {"type": "user_turn", "trigger_id": "m1-runtime-diagnostic-turn"},
            "binding": {
                "conversation_ref": "opaque:m1-runtime-diagnostic-conversation",
                "workspace_ref": "opaque:m1-runtime-diagnostic-workspace",
                "workspace_revision": 1,
                "context_digest": "sha256:m1-runtime-diagnostic-context",
            },
            "model": {
                "profile_name": "m1-runtime-diagnostic-model",
                "profile_digest": "sha256:m1-runtime-diagnostic-model",
                "max_output_tokens": 32,
            },
            "context_budget": {
                "effective_context_k": 896,
                "output_reserve_k": 32,
                "safety_reserve_k": 32,
                "require_verified_model_profile": True,
                "policy_digest": "sha256:m1-runtime-diagnostic-budget",
            },
            "limits": {"max_model_steps": 8, "max_business_tools": 3, "deadline_seconds": 90},
            "toolset": {"version": "agent-tools-v1", "manifest_digest": "sha256:m1-runtime-diagnostic-tools"},
            "context": {
                "system_instruction": "执行受控测试。",
                "user_input": "验证安全失败投影。",
                "workspace_projection": {},
                "conversation_projection": {},
                "preference_projection": {},
                "brand_profile_projection": {},
                "long_term_memory_projection": [],
            },
        }
    )


class _TimeoutEngine:
    """构造模型执行超时，不依赖真实模型或 Runtime 子进程。"""

    engine_id = "m1-runtime-diagnostic-engine"
    engine_version = "v1"

    def __init__(self, skill_root: Path) -> None:
        skill_file = skill_root / "m1-diagnostic" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("---\ndescription: M1 安全诊断测试。\n---\n测试正文", encoding="utf-8")
        self._snapshot = snapshot_skill_root(skill_root)

    def snapshot_skills(self):
        """返回固定 Skill 快照。"""

        return self._snapshot

    async def execute(self, *_args):
        """抛出不包含底层错误正文的结构化超时。"""

        await asyncio.sleep(0)
        raise HarnessExecutionError(
            HarnessExecutionDiagnostic(
                exception_type="TimeoutError",
                failure_phase="model_execution",
                failure_reason=None,
                timeout_phase="model_execution",
            )
        )


@pytest.mark.asyncio
async def test_runtime_timeout_only_projects_safe_diagnostic_fields(tmp_path: Path) -> None:
    """SSE 失败事件必须保留类型与阶段，同时不包含底层异常文本。"""

    service = RunService(
        SqliteRunEventStore(tmp_path / "runs.sqlite3"),
        _TimeoutEngine(tmp_path / "skills"),
    )
    try:
        created = await service.create_run(_request())
        await service.activate_run(created.run_id)
        for _ in range(20):
            state = await service.get_run(created.run_id)
            if state is not None and state.status.value == "failed":
                break
            await asyncio.sleep(0)
        events = await service.events_after(created.run_id, 0)

        assert events[-1].type == "run.failed"
        assert events[-1].payload == {
            "code": "engine_execution_failed",
            "exception_type": "TimeoutError",
            "failure_phase": "model_execution",
            "failure_reason": None,
            "timeout_phase": "model_execution",
        }
        assert "底层" not in str(events[-1].payload)
    finally:
        await service.aclose()


def test_non_timeout_runtime_error_also_projects_failure_phase() -> None:
    """非超时异常同样必须记录固定失败阶段，且不误标记为超时。"""

    diagnostic = _execution_diagnostic(RuntimeError("不会投影的原始异常"), "model_execution")

    assert diagnostic.exception_type == "RuntimeError"
    assert diagnostic.failure_phase == "model_execution"
    assert diagnostic.failure_reason is None
    assert diagnostic.timeout_phase is None


def test_result_projection_does_not_depend_on_harness_private_sequence() -> None:
    """PixelFlow 公开序号由 Event Store 生成，不能依赖 SDK 内部事件序号。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[
                {"type": "assistant/message", "data": {}},
                {"type": "tool/call", "data": {"name": "inspect_video_workspace"}},
            ],
            final_response=" 已完成 ",
            finish_reason="completed",
        )
    )

    assert projected.final_response == "已完成"
    assert projected.finish_reason == "completed"
    assert projected.tool_names == ("inspect_video_workspace",)


@pytest.mark.parametrize("runtime_finish_reason", ["completed", "complete", "stop", "end_turn"])
def test_result_projection_normalizes_known_runtime_completion_reasons(
    runtime_finish_reason: str,
) -> None:
    """Provider 正常结束值必须统一为 Sidecar 的 completed，未知值仍不能放行。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[],
            final_response="已完成",
            finish_reason=runtime_finish_reason,
        )
    )

    assert projected.finish_reason == "completed"


def test_result_projection_keeps_unknown_runtime_finish_reason() -> None:
    """未知 Runtime 结束值不得被误判为成功，RunService 应保持失败关闭。"""

    projected = _project_harness_result(
        SimpleNamespace(events=[], final_response="已完成", finish_reason="unexpected_reason")
    )

    assert projected.finish_reason == "unexpected_reason"


def test_result_projection_failure_uses_fixed_reason_code() -> None:
    """投影失败只能暴露固定原因码，不能包含 SDK 的异常正文。"""

    with pytest.raises(HarnessProjectionError, match="Harness 结果投影失败") as captured:
        _project_harness_result(SimpleNamespace(events=[], final_response="", finish_reason="completed"))

    diagnostic = _execution_diagnostic(captured.value, "result_projection")
    assert diagnostic.failure_reason == "final_response_missing"


def test_result_projection_uses_text_delta_only_when_final_message_missing() -> None:
    """流式回退只能读取公开文本增量，不得拼入 reasoning 或工具内容。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[
                {"type": "assistant/chunk", "data": {"chunk": {"type": "reasoning-delta", "delta": "机密"}}},
                {"type": "assistant/chunk", "data": {"chunk": {"type": "text-delta", "delta": "公开"}}},
                {"type": "assistant/chunk", "data": {"chunk": {"type": "usage", "text": "计量"}}},
                {"type": "assistant/chunk", "data": {"chunk": {"type": "text-delta", "data": {"text": "回复"}}}},
            ],
            final_response="",
            finish_reason="completed",
        )
    )

    assert projected.final_response == "公开回复"
