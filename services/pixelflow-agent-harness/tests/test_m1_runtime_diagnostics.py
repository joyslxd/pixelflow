"""验证 Harness Runtime 故障只投影安全诊断字段。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepseek_harness.errors import JsonRpcError

from pixelflow_harness_sidecar.contracts import HarnessRunRequest
from pixelflow_harness_sidecar.deepseek_engine import (
    DeepSeekEngineResult,
    HarnessExecutionDiagnostic,
    HarnessExecutionError,
    HarnessProjectionError,
    _build_model_input,
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

    def validate_request(self, _request) -> None:
        """测试替身只注入超时；真实请求校验由 DeepSeek Engine 覆盖。"""

    async def execute(self, *_args, **_kwargs):
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


class _OutputLimitWithoutResponseEngine(_TimeoutEngine):
    """构造只完成 reasoning 且达到输出上限的安全可恢复失败。"""

    async def execute(self, *_args, **_kwargs):
        await asyncio.sleep(0)
        raise HarnessExecutionError(
            HarnessExecutionDiagnostic(
                exception_type="HarnessProjectionError",
                failure_phase="result_projection",
                failure_reason="max_output_tokens_without_public_response",
                timeout_phase=None,
            )
        )


class _DeadlineEngine(_TimeoutEngine):
    """构造当前 Runtime 的安全 deadline 结束原因。"""

    async def execute(self, *_args, **_kwargs):
        await asyncio.sleep(0)
        return DeepSeekEngineResult(
            final_response="已完成工具阶段的公开摘要",
            finish_reason="deadline_exceeded",
            tool_names=("generate_image_assets",),
            tool_results_seen=True,
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


def test_jsonrpc_error_projects_allowlisted_fields_without_message_or_data() -> None:
    """JSON-RPC 失败只公开白名单字段，禁止泄露供应商 message/data。"""

    error = JsonRpcError(
        -32602,
        "secret provider prompt and token",
        {"type": "invalid_params", "category": "invalid_request", "secret": "do-not-leak"},
    )
    diagnostic = _execution_diagnostic(error, "model_execution")

    assert diagnostic.failure_code == "-32602"
    assert diagnostic.failure_type == "invalid_params"
    assert diagnostic.failure_category == "invalid_request"
    assert diagnostic.failure_reason == "invalid_request"
    assert "secret" not in str(diagnostic)


def test_jsonrpc_error_unknown_fields_fail_closed() -> None:
    """未知 JSON-RPC code/category 不得成为公开错误原因。"""

    diagnostic = _execution_diagnostic(
        JsonRpcError(49001, "private detail", {"type": "private_type", "category": "private_category"}),
        "model_execution",
    )

    assert diagnostic.failure_code is None
    assert diagnostic.failure_type == "private_type"
    assert diagnostic.failure_category is None
    assert diagnostic.failure_reason is None


@pytest.mark.asyncio
async def test_output_limit_without_public_response_is_recoverable(tmp_path: Path) -> None:
    """模型只输出 reasoning 时保留恢复入口，而不是向浏览器暴露不可恢复失败。"""

    service = RunService(
        SqliteRunEventStore(tmp_path / "runs.sqlite3"),
        _OutputLimitWithoutResponseEngine(tmp_path / "skills"),
    )
    try:
        created = await service.create_run(_request())
        await service.activate_run(created.run_id)
        for _ in range(20):
            state = await service.get_run(created.run_id)
            if state is not None and state.status.value == "failed":
                break
            await asyncio.sleep(0)
        state = await service.get_run(created.run_id)
        events = await service.events_after(created.run_id, 0)

        assert state is not None
        assert state.termination_reason.value == "max_output_tokens"
        assert events[-1].type == "run.failed"
        assert events[-1].payload == {"code": "harness_run_recovery_required"}
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_runtime_deadline_reason_is_persisted_without_generic_engine_error(tmp_path: Path) -> None:
    """Runtime deadline 只产生固定结束原因，并保留已完成 Tool 的公开事件。"""

    service = RunService(
        SqliteRunEventStore(tmp_path / "runs.sqlite3"),
        _DeadlineEngine(tmp_path / "skills"),
    )
    try:
        created = await service.create_run(_request())
        await service.activate_run(created.run_id)
        for _ in range(20):
            state = await service.get_run(created.run_id)
            if state is not None and state.status.value == "failed":
                break
            await asyncio.sleep(0)
        state = await service.get_run(created.run_id)
        events = await service.events_after(created.run_id, 0)

        assert state is not None
        assert state.termination_reason.value == "deadline_exceeded"
        assert {event.type for event in events} >= {"tool.completed", "run.failed"}
        assert events[-1].payload == {"code": "deadline_exceeded"}
    finally:
        await service.aclose()


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


def test_result_projection_recognizes_tool_call_content_blocks_and_deduplicates_names() -> None:
    """当前 Runtime 同时发出 assistant/message 与 tool/call 时，Tool 名称只公开一次。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[
                {
                    "type": "assistant/message",
                    "data": {
                        "message": {
                            "content": [
                                {"type": "reasoning", "text": "不得公开"},
                                {"type": "tool-call", "name": "generate_image_assets"},
                            ]
                        }
                    },
                },
                {"type": "tool/call", "data": {"callId": "call_01", "name": "generate_image_assets"}},
            ],
            final_response="已提交生成任务",
            finish_reason="completed",
        )
    )

    assert projected.tool_names == ("generate_image_assets",)


def test_result_projection_normalizes_runtime_error_deadline_reason() -> None:
    """Runtime 的 turn/end.reason.error.code 必须投影为固定 deadline 结束原因。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[
                {
                    "type": "turn/end",
                    "data": {
                        "reason": {
                            "kind": "error",
                            "error": {"code": "deadline_exceeded", "message": "不得公开"},
                        }
                    },
                }
            ],
            final_response="已输出安全摘要",
            finish_reason="error",
        )
    )

    assert projected.finish_reason == "deadline_exceeded"


def test_result_projection_normalizes_policy_error_from_runtime_tool_result() -> None:
    """Runtime 将 Policy 错误放在 isError ToolResult 文本时，仍只公开固定错误码。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[
                {
                    "type": "tool/result",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "tool-result",
                                    "isError": True,
                                    "content": [{"type": "text", "text": "Error: deadline_exceeded\n"}],
                                }
                            ]
                        }
                    },
                },
                {"type": "turn/end", "data": {"reason": {"kind": "error", "error": {"code": "UNKNOWN"}}}},
            ],
            final_response="已输出安全摘要",
            finish_reason="error",
        )
    )

    assert projected.finish_reason == "deadline_exceeded"


def test_model_input_includes_gateway_frozen_conversation_and_workspace_context() -> None:
    """独立 Sidecar Session 必须接收 Gateway 组装的多轮事实，而非仅当前用户句子。"""

    request = _request()
    context = request.context.model_copy(
        update={
            "conversation_projection": {
                "recent_messages": [
                    {"role": "user", "content": "型号为 M20，画幅为 16:9。"},
                    {"role": "assistant", "content": "已确认使用家庭温情风格。"},
                ]
            },
            "workspace_projection": {"revision": 3, "script_status": "已确认"},
        }
    )

    model_input = _build_model_input(request.model_copy(update={"context": context}))

    assert "【冻结上下文】" in model_input
    assert "型号为 M20，画幅为 16:9。" in model_input
    assert "家庭温情风格" in model_input
    assert '"revision":3' in model_input
    assert model_input.index("型号为 M20") < model_input.index("【用户请求】")


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


def test_output_limit_without_public_response_uses_recovery_reason() -> None:
    """仅输出上限且无公开文本可走 Gateway 的幂等恢复合同。"""

    with pytest.raises(HarnessProjectionError) as captured:
        _project_harness_result(
            SimpleNamespace(events=[], final_response="", finish_reason="max-tokens")
        )

    assert captured.value.reason_code == "max_output_tokens_without_public_response"


def test_result_projection_uses_text_delta_only_when_final_message_missing() -> None:
    """流式回退只能读取公开文本增量，不得拼入 reasoning 或工具内容。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[
                {"type": "assistant/chunk", "data": {"chunk": {"type": "reasoning-delta", "delta": "机密"}}},
                {"type": "assistant/chunk", "data": {"chunk": {"type": "text-delta", "delta": "公开"}}},
                {"type": "assistant/chunk", "data": {"chunk": {"type": "text-chunks", "chunks": ["文本", {"text": "块"}]}}},
                {"type": "assistant/chunk", "data": {"chunk": {"type": "usage", "text": "计量"}}},
                {"type": "assistant/chunk", "data": {"chunk": {"type": "text-delta", "data": {"text": "回复"}}}},
            ],
            final_response="",
            finish_reason="completed",
        )
    )

    assert projected.final_response == "公开文本块回复"


def test_result_projection_accepts_deployed_runtime_response_field() -> None:
    """已部署 Harness 用 response 而非 final_response 时仍只投影其显式文本。"""

    projected = _project_harness_result(
        SimpleNamespace(events=[], response="已兼容最终回复", finish_reason="completed")
    )

    assert projected.final_response == "已兼容最终回复"


def test_result_projection_uses_public_final_message_from_notification() -> None:
    """通知流的最终 message 与 run() 返回事件同样受公开文本白名单约束。"""

    projected = _project_harness_result(
        SimpleNamespace(events=[], final_response="", finish_reason="completed"),
        notification_events=[
            {"type": "assistant/message", "data": {"message": {"content": [{"type": "text", "text": "通知最终回复"}]}}},
        ],
    )

    assert projected.final_response == "通知最终回复"


def test_result_projection_uses_public_final_message_when_chunks_absent() -> None:
    """Runtime 仅在最终消息携带文本时，仍必须投影公开文本且排除 reasoning 块。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[
                {
                    "type": "assistant/message",
                    "data": {
                        "message": {
                            "content": [
                                {"type": "reasoning", "text": "不得公开"},
                                {"type": "text", "text": "最终回复"},
                            ]
                        }
                    },
                }
            ],
            final_response="",
            finish_reason="completed",
        )
    )

    assert projected.final_response == "最终回复"


def test_result_projection_detects_confirmation_inside_runtime_tool_result_message() -> None:
    """新版 Runtime 将 Plugin Observation 封装为 ToolResultMessage，仍须优先挂起。"""

    projected = _project_harness_result(
        SimpleNamespace(
            events=[
                {
                    "type": "tool/result",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "tool-result",
                                    "toolCallId": "call_01",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": (
                                                '{"status":"awaiting_confirmation",'
                                                '"suspension":{"kind":"awaiting_confirmation",'
                                                '"interrupt_id":"interrupt_01"}}'
                                            ),
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                }
            ],
            final_response="",
            finish_reason="error",
        )
    )

    assert projected.finish_reason == "suspended"
    assert projected.suspension_kind == "awaiting_confirmation"
    assert projected.suspension_interrupt_id == "interrupt_01"


def test_result_projection_ignores_unstructured_tool_result_text() -> None:
    """普通 Tool 文本不得被猜测为业务挂起状态。"""

    with pytest.raises(HarnessProjectionError, match="Harness 结果投影失败"):
        _project_harness_result(
            SimpleNamespace(
                events=[
                    {
                        "type": "tool/result",
                        "data": {
                            "message": {
                                "content": [
                                    {
                                        "type": "tool-result",
                                        "content": [{"type": "text", "text": "等待确认"}],
                                    }
                                ]
                            }
                        },
                    }
                ],
                final_response="",
                finish_reason="error",
            )
        )
