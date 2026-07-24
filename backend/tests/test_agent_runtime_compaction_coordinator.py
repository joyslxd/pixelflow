"""M04.3 四阈值上下文压缩 Coordinator 测试。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pixelflow.agent_runtime.context import (
    CompactionSegment,
    CompactionStageRequest,
    CompactionStageResult,
    ContextBudgetPolicy,
    ContextCompactionCoordinator,
    ContextCompactionRequest,
    ContextCompactionResult,
    ModelContextProfile,
    TokenMeter,
)
from pixelflow.agent_runtime.context.compaction import (
    CompactionExecutionError,
    CompactionValidationError,
)

_NOW = datetime(2026, 7, 24, 23, 45, tzinfo=UTC)
_SUMMARY_MODEL_NAME = "verified-summary-model"


def _budget(percentage: int):
    return TokenMeter().measure(
        estimated_input_tokens=percentage,
        profile=ModelContextProfile(
            model_name="verified-test-model",
            max_context_tokens=200,
            max_output_tokens=50,
            tokenizer_strategy="provider_usage",
            verified_at=datetime(2026, 7, 24, tzinfo=UTC),
            source="M04.3 测试档案",
        ),
        policy=ContextBudgetPolicy(
            effective_context_cap_tokens=200,
            output_reserve_tokens=50,
            safety_reserve_tokens=50,
        ),
    )


def _summary_profile(
    *,
    model_name: str = _SUMMARY_MODEL_NAME,
    max_context_tokens: int = 150_000,
    max_output_tokens: int = 10_000,
    verified_at: datetime | None = _NOW,
    expires_at: datetime | None = None,
) -> ModelContextProfile:
    return ModelContextProfile(
        model_name=model_name,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        tokenizer_strategy="provider_usage",
        verified_at=verified_at,
        expires_at=expires_at,
        source="M04.3 摘要模型测试档案",
    )


class _FakeStageExecutor:
    def __init__(
        self,
        responses: Mapping[str, Sequence[int | Exception]],
    ) -> None:
        self.responses = {action: list(values) for action, values in responses.items()}
        self.requests: list[CompactionStageRequest] = []

    async def execute(
        self,
        request: CompactionStageRequest,
    ) -> CompactionStageResult:
        self.requests.append(request)
        values = self.responses.get(request.action)
        if not values:
            raise AssertionError(f"没有为 {request.action} 准备 fake 结果")
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return CompactionStageResult(estimated_input_tokens=value)


def _coordinator(
    executor: _FakeStageExecutor,
    *,
    model_profiles: Mapping[str, ModelContextProfile] | None = None,
) -> ContextCompactionCoordinator:
    profiles = {_SUMMARY_MODEL_NAME: _summary_profile()} if model_profiles is None else model_profiles
    return ContextCompactionCoordinator(
        executor=executor,
        summary_model_name=_SUMMARY_MODEL_NAME,
        model_profiles=profiles,
        clock=lambda: _NOW,
    )


def _request(
    percentage: int,
    *,
    incremental_segments: tuple[CompactionSegment, ...] | None = None,
    workflow_summary_segments: tuple[CompactionSegment, ...] | None = None,
) -> ContextCompactionRequest:
    return ContextCompactionRequest(
        conversation_id="conv-m04-3",
        budget_report=_budget(percentage),
        incremental_segments=(incremental_segments if incremental_segments is not None else (CompactionSegment(segment_id="msg-1", estimated_tokens=10),)),
        workflow_summary_segments=(workflow_summary_segments if workflow_summary_segments is not None else (CompactionSegment(segment_id="wf-1", estimated_tokens=10),)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("percentage", "responses", "expected_actions"),
    [
        (59, {}, []),
        (60, {"externalize_payloads": [44]}, ["externalize_payloads"]),
        (71, {"externalize_payloads": [44]}, ["externalize_payloads"]),
        (
            72,
            {
                "externalize_payloads": [70],
                "incremental_summary": [44],
            },
            ["externalize_payloads", "incremental_summary"],
        ),
        (
            84,
            {
                "externalize_payloads": [70],
                "incremental_summary": [44],
            },
            ["externalize_payloads", "incremental_summary"],
        ),
        (
            85,
            {
                "externalize_payloads": [80],
                "incremental_summary": [70],
                "hierarchical_summary": [44],
            },
            [
                "externalize_payloads",
                "incremental_summary",
                "hierarchical_summary",
            ],
        ),
        (
            91,
            {
                "externalize_payloads": [80],
                "incremental_summary": [70],
                "hierarchical_summary": [44],
            },
            [
                "externalize_payloads",
                "incremental_summary",
                "hierarchical_summary",
            ],
        ),
        (
            92,
            {
                "externalize_payloads": [90],
                "incremental_summary": [80],
                "hierarchical_summary": [70],
                "hard_gate_summary": [44],
            },
            [
                "externalize_payloads",
                "incremental_summary",
                "hierarchical_summary",
                "hard_gate_summary",
            ],
        ),
    ],
)
async def test_coordinator_uses_exact_threshold_boundaries(
    percentage: int,
    responses: Mapping[str, Sequence[int | Exception]],
    expected_actions: list[str],
) -> None:
    executor = _FakeStageExecutor(responses)

    result = await _coordinator(executor).coordinate(_request(percentage))

    assert [request.action for request in executor.requests] == expected_actions
    assert result.initial_budget_report.compaction_level == _budget(percentage).compaction_level
    if percentage < 60:
        assert result.status == "not_required"
        assert result.final_budget_report.estimated_input_tokens == percentage
    else:
        assert result.status == "target_reached"
        assert result.final_budget_report.estimated_input_tokens == 44
    assert result.model_invocation_allowed is True


@pytest.mark.asyncio
async def test_coordinator_requires_strictly_less_than_forty_five_percent() -> None:
    executor = _FakeStageExecutor(
        {
            "externalize_payloads": [70],
            "incremental_summary": [45],
        }
    )

    result = await _coordinator(executor).coordinate(_request(72))

    assert result.target_input_tokens == 44
    assert result.final_budget_report.estimated_input_tokens == 45
    assert result.status == "target_not_reached"
    assert result.model_invocation_allowed is True


@pytest.mark.asyncio
async def test_coordinator_stops_as_soon_as_strict_target_is_reached() -> None:
    executor = _FakeStageExecutor(
        {
            "externalize_payloads": [44],
            "incremental_summary": [10],
        }
    )

    result = await _coordinator(executor).coordinate(_request(84))

    assert [request.action for request in executor.requests] == ["externalize_payloads"]
    assert result.status == "target_reached"
    assert result.final_budget_report.utilization == 0.44


@pytest.mark.asyncio
async def test_coordinator_chunks_oversized_total_input_in_source_order() -> None:
    executor = _FakeStageExecutor(
        {
            "externalize_payloads": [80],
            "incremental_summary": [70, 44],
        }
    )
    segments = tuple(
        CompactionSegment(
            segment_id=f"msg-{index}",
            estimated_tokens=40_000,
        )
        for index in range(1, 4)
    )

    result = await _coordinator(executor).coordinate(
        _request(
            84,
            incremental_segments=segments,
        )
    )

    incremental_requests = [request for request in executor.requests if request.action == "incremental_summary"]
    assert [[segment.segment_id for segment in request.batch.segments] for request in incremental_requests if request.batch is not None] == [["msg-1", "msg-2"], ["msg-3"]]
    assert [request.batch.estimated_tokens for request in incremental_requests if request.batch is not None] == [80_000, 40_000]
    assert [(request.batch.batch_index, request.batch.batch_count) for request in incremental_requests if request.batch is not None] == [(1, 2), (2, 2)]
    assert result.status == "target_reached"


@pytest.mark.asyncio
async def test_coordinator_hierarchically_compacts_workflow_summaries() -> None:
    executor = _FakeStageExecutor(
        {
            "externalize_payloads": [84],
            "incremental_summary": [80],
            "hierarchical_summary": [60, 44],
        }
    )
    workflow_segments = (
        CompactionSegment(segment_id="wf-video", estimated_tokens=50_000),
        CompactionSegment(segment_id="wf-ppt", estimated_tokens=50_000),
    )

    result = await _coordinator(executor).coordinate(
        _request(
            85,
            workflow_summary_segments=workflow_segments,
        )
    )

    assert [request.action for request in executor.requests] == [
        "externalize_payloads",
        "incremental_summary",
        "hierarchical_summary",
        "hierarchical_summary",
    ]
    hierarchical_requests = executor.requests[2:]
    assert [request.batch.scope for request in hierarchical_requests if request.batch is not None] == ["workflow_summaries", "workflow_summaries"]
    assert [request.batch.segments[0].segment_id for request in hierarchical_requests if request.batch is not None] == ["wf-video", "wf-ppt"]
    assert result.status == "target_reached"


@pytest.mark.asyncio
async def test_coordinator_rejects_single_segment_larger_than_summary_budget() -> None:
    executor = _FakeStageExecutor({"externalize_payloads": [70]})

    with pytest.raises(CompactionValidationError, match="单段"):
        await _coordinator(executor).coordinate(
            _request(
                72,
                incremental_segments=(
                    CompactionSegment(
                        segment_id="msg-too-large",
                        estimated_tokens=90_849,
                    ),
                ),
            )
        )

    assert executor.requests == []


@pytest.mark.asyncio
async def test_hard_gate_failure_uses_minimal_safe_context() -> None:
    executor = _FakeStageExecutor(
        {
            "externalize_payloads": [RuntimeError("敏感 provider 原始错误")],
            "minimal_safe_context": [80],
        }
    )

    result = await _coordinator(executor).coordinate(_request(92))

    assert [request.action for request in executor.requests] == [
        "externalize_payloads",
        "minimal_safe_context",
    ]
    assert result.status == "minimal_safe_context"
    assert result.final_budget_report.estimated_input_tokens == 80
    assert result.model_invocation_allowed is True
    assert result.pause_reason is None


@pytest.mark.asyncio
async def test_hard_gate_failure_pauses_when_minimal_context_cannot_be_proven() -> None:
    executor = _FakeStageExecutor(
        {
            "externalize_payloads": [RuntimeError("第一次失败")],
            "minimal_safe_context": [RuntimeError("第二次失败")],
        }
    )

    result = await _coordinator(executor).coordinate(_request(92))

    assert result.status == "paused"
    assert result.model_invocation_allowed is False
    assert result.pause_reason == "hard_gate_compaction_failed"
    assert result.final_budget_report.estimated_input_tokens == 92
    assert "第一次失败" not in result.model_dump_json()
    assert "第二次失败" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_hard_gate_pauses_when_minimal_context_does_not_reduce_tokens() -> None:
    executor = _FakeStageExecutor(
        {
            "externalize_payloads": [RuntimeError("压缩失败")],
            "minimal_safe_context": [92],
        }
    )

    result = await _coordinator(executor).coordinate(_request(92))

    assert result.status == "paused"
    assert result.model_invocation_allowed is False
    assert result.attempts == ()


@pytest.mark.asyncio
async def test_coordinator_rejects_forged_compaction_level() -> None:
    executor = _FakeStageExecutor({})
    forged = _budget(92).model_copy(update={"compaction_level": 0})
    request = _request(92).model_copy(update={"budget_report": forged})

    with pytest.raises(CompactionValidationError, match="compaction_level"):
        await _coordinator(executor).coordinate(request)

    assert executor.requests == []


@pytest.mark.asyncio
async def test_coordinator_rejects_stage_that_increases_context_size() -> None:
    executor = _FakeStageExecutor({"externalize_payloads": [61]})

    with pytest.raises(CompactionExecutionError, match="不得增加"):
        await _coordinator(executor).coordinate(_request(60))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_profiles",
    [
        {},
        {
            _SUMMARY_MODEL_NAME: _summary_profile(
                max_context_tokens=500_000,
                verified_at=datetime(2026, 1, 1, tzinfo=UTC),
                expires_at=datetime(2026, 7, 1, tzinfo=UTC),
            )
        },
    ],
)
async def test_summary_chunks_use_conservative_profile_when_verified_profile_is_unavailable(
    model_profiles: Mapping[str, ModelContextProfile],
) -> None:
    executor = _FakeStageExecutor({"externalize_payloads": [70]})
    request = _request(
        72,
        incremental_segments=(
            CompactionSegment(
                segment_id="msg-over-conservative-budget",
                estimated_tokens=80_000,
            ),
        ),
    )

    with pytest.raises(CompactionValidationError, match="单段"):
        await _coordinator(
            executor,
            model_profiles=model_profiles,
        ).coordinate(request)

    assert executor.requests == []


def test_compaction_request_rejects_caller_supplied_summary_chunk_limit() -> None:
    payload = _request(72).model_dump(mode="python")
    payload["summary_chunk_limit_tokens"] = 1_000_000

    with pytest.raises(ValidationError, match="summary_chunk_limit_tokens"):
        ContextCompactionRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_hard_gate_summary_planning_failure_uses_minimal_safe_context() -> None:
    executor = _FakeStageExecutor({"minimal_safe_context": [80]})

    result = await _coordinator(executor).coordinate(
        _request(
            92,
            incremental_segments=(
                CompactionSegment(
                    segment_id="msg-too-large-at-hard-gate",
                    estimated_tokens=90_849,
                ),
            ),
        )
    )

    assert [request.action for request in executor.requests] == ["minimal_safe_context"]
    assert result.status == "minimal_safe_context"
    assert result.model_invocation_allowed is True


def test_compaction_request_rejects_business_context() -> None:
    payload = _request(72).model_dump(mode="python")
    payload["business_context"] = {
        "creation_contract": {"video_duration_sec": 30},
        "pending_action": "generate_video",
    }

    with pytest.raises(ValidationError, match="business_context"):
        ContextCompactionRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "conversation_id": "conv-1",
            "action": "incremental_summary",
            "target_input_tokens": 44,
            "current_estimated_input_tokens": 72,
            "batch": None,
        },
        {
            "conversation_id": "conv-1",
            "action": "externalize_payloads",
            "target_input_tokens": 44,
            "current_estimated_input_tokens": 72,
            "batch": {
                "scope": "messages",
                "batch_index": 1,
                "batch_count": 1,
                "segments": [
                    {
                        "segment_id": "msg-1",
                        "estimated_tokens": 10,
                    }
                ],
            },
        },
        {
            "conversation_id": "conv-1",
            "action": "hierarchical_summary",
            "target_input_tokens": 44,
            "current_estimated_input_tokens": 85,
            "batch": {
                "scope": "messages",
                "batch_index": 1,
                "batch_count": 1,
                "segments": [
                    {
                        "segment_id": "wf-1",
                        "estimated_tokens": 10,
                    }
                ],
            },
        },
    ],
)
def test_compaction_stage_request_rejects_action_batch_mismatch(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="batch"):
        CompactionStageRequest.model_validate(payload)


def test_compaction_batch_rejects_index_after_batch_count() -> None:
    payload = {
        "scope": "messages",
        "batch_index": 2,
        "batch_count": 1,
        "segments": [
            {
                "segment_id": "msg-1",
                "estimated_tokens": 10,
            }
        ],
    }

    with pytest.raises(ValidationError, match="batch_index"):
        from pixelflow.agent_runtime.context import CompactionBatch

        CompactionBatch.model_validate(payload)


def test_compaction_result_rejects_false_target_reached_status() -> None:
    payload = {
        "status": "target_reached",
        "initial_budget_report": _budget(72),
        "final_budget_report": _budget(45),
        "target_input_tokens": 44,
        "attempts": [],
        "model_invocation_allowed": True,
        "pause_reason": None,
    }

    with pytest.raises(ValidationError, match="target_reached"):
        ContextCompactionResult.model_validate(payload)
