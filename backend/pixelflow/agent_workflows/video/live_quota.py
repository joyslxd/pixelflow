"""把 External Job quota 事件投影为视频 Workflow 与人工中断。"""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError

from pixelflow.agent_runtime.contracts import (
    AgentEvent,
    AgentEventType,
    ExternalJobRef,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.contracts.base import ContractModel
from pixelflow.agent_runtime.jobs import (
    OperationQuotaEventPayload,
    OperationQuotaState,
    build_operation_quota_event_id,
)
from pixelflow.agent_runtime.persistence import StoredAgentInterrupt
from pixelflow.agent_runtime.persistence.repositories import OperationRecord
from pixelflow.agent_runtime.ports import OperationConflictError

from .live_handler import video_interrupt_occurrence_id
from .state_codec import (
    VideoWorkflowStateEnvelope,
    decode_video_workflow_state,
    encode_video_workflow_state,
    project_video_workflow_state,
)


class VideoOperationQuotaProjection(ContractModel):
    """同一 quota Event 对应的完整视频投影目标。"""

    workflow_state: VideoWorkflowStateEnvelope
    workflow: WorkflowRecord
    open_interrupt: StoredAgentInterrupt | None = None
    close_interrupt_revision: int | None = Field(default=None, ge=1)


class VideoOperationQuotaProjectionService:
    """纯构造 quota Event 的唯一视频投影，不执行持久化。"""

    def build(
        self,
        *,
        user_id: str,
        envelope: VideoWorkflowStateEnvelope,
        operation: OperationRecord,
        quota_event: AgentEvent,
    ) -> VideoOperationQuotaProjection:
        """严格绑定事件、Operation 和信封后生成稳定目标。"""

        normalized_event = strict_quota_agent_event(quota_event)
        try:
            payload = OperationQuotaEventPayload.model_validate(
                normalized_event.payload
            )
        except ValidationError:
            raise OperationConflictError("quota Event 合同不合法") from None
        _validate_event_identity(
            user_id=user_id,
            envelope=envelope,
            operation=operation,
            quota_event=normalized_event,
            payload=payload,
        )
        state = decode_video_workflow_state(envelope)
        _validate_pending_operation_identity(state, operation, payload)
        next_envelope = encode_video_workflow_state(
            user_id=user_id,
            state=state,
            workflow_version=envelope.workflow_version + 1,
            last_turn_id=envelope.last_turn_id,
            last_action_key=normalized_event.event_id,
        )
        workflow = project_video_workflow_state(state)
        if payload.quota_state is OperationQuotaState.PAUSED:
            paused_workflow = workflow.model_copy(
                update={
                    "status": WorkflowStatus.PAUSED_QUOTA,
                    "updated_at": normalized_event.occurred_at,
                }
            )
            return VideoOperationQuotaProjection(
                workflow_state=next_envelope,
                workflow=paused_workflow,
                open_interrupt=_quota_authorization_interrupt(
                    user_id=user_id,
                    envelope=next_envelope,
                    operation=operation,
                    event=normalized_event,
                    payload=payload,
                    workflow=paused_workflow,
                ),
            )
        return VideoOperationQuotaProjection(
            workflow_state=next_envelope,
            workflow=workflow,
            close_interrupt_revision=payload.quota_pause_revision,
        )


def strict_quota_agent_event(value: AgentEvent) -> AgentEvent:
    """按基础 DTO 重建事件，并拒绝子类通过任意序列化注入额外字段。"""

    try:
        document = value.model_dump(mode="json", serialize_as_any=True)
        return AgentEvent.model_validate(document)
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise OperationConflictError("quota Event 合同不合法") from None


def _validate_event_identity(
    *,
    user_id: str,
    envelope: VideoWorkflowStateEnvelope,
    operation: OperationRecord,
    quota_event: AgentEvent,
    payload: OperationQuotaEventPayload,
) -> None:
    expected_event_id = build_operation_quota_event_id(
        operation.job_id,
        payload.quota_pause_revision,
        payload.quota_state,
    )
    if (
        not isinstance(user_id, str)
        or not user_id
        or envelope.user_id != user_id
        or quota_event.type
        is not AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED
        or quota_event.event_id != expected_event_id
        or quota_event.conversation_id != operation.conversation_id
        or envelope.conversation_id != operation.conversation_id
        or envelope.workflow_id != operation.workflow_id
        or payload.job_id != operation.job_id
        or payload.workflow_id != operation.workflow_id
        or payload.stage != operation.stage
        or payload.stage_version != operation.stage_version
        or payload.attempt != operation.attempt
        or payload.quota_pause_revision != operation.quota_pause_revision
    ):
        raise OperationConflictError("quota 事件与视频 Workflow 身份不一致")


def _validate_pending_operation_identity(
    state: Any,
    operation: OperationRecord,
    payload: OperationQuotaEventPayload,
) -> None:
    pending_items: list[ExternalJobRef] = []
    many = getattr(state, "pending_operations", None)
    if isinstance(many, list):
        pending_items.extend(
            ExternalJobRef.model_validate(item) for item in many
        )
    single = getattr(state, "pending_operation", None)
    if single is not None:
        pending_items.append(ExternalJobRef.model_validate(single))
    matches = [item for item in pending_items if item.job_id == operation.job_id]
    if len(matches) != 1:
        raise OperationConflictError("视频状态缺少唯一 pending Operation")
    pending = matches[0]
    if (
        pending.workflow_id != operation.workflow_id
        or pending.stage != operation.stage
        or pending.attempt != operation.attempt
        or getattr(state, "workflow_id", None) != operation.workflow_id
        or getattr(state, "conversation_id", None) != operation.conversation_id
        or payload.stage_version != operation.stage_version
    ):
        raise OperationConflictError("视频 pending Operation 身份不一致")


def _quota_authorization_interrupt(
    *,
    user_id: str,
    envelope: VideoWorkflowStateEnvelope,
    operation: OperationRecord,
    event: AgentEvent,
    payload: OperationQuotaEventPayload,
    workflow: WorkflowRecord,
) -> StoredAgentInterrupt:
    reason_code = "authorization_required"
    return StoredAgentInterrupt(
        interrupt_id=video_interrupt_occurrence_id(
            turn_id=envelope.last_turn_id,
            reason_code=reason_code,
            workflow=workflow,
            workflow_version=envelope.workflow_version,
        ),
        conversation_id=envelope.conversation_id,
        workflow_id=envelope.workflow_id,
        turn_id=envelope.last_turn_id,
        kind="authorization_required",
        reason_code=reason_code,
        payload={
            "workflow_id": operation.workflow_id,
            "stage": operation.stage,
            "authorization_action": {
                "action": "retry_failed",
                "intent": "video",
                "workflow_id": operation.workflow_id,
                "stage": operation.stage,
                "artifact_ref": None,
                "patch": {
                    "job_id": operation.job_id,
                    "quota_pause_revision": payload.quota_pause_revision,
                },
            },
        },
        opened_at=event.occurred_at,
        user_id=user_id,
        thread_id=f"quota-paused:{event.event_id}",
        checkpoint_ns="root",
    )


__all__ = [
    "VideoOperationQuotaProjection",
    "VideoOperationQuotaProjectionService",
    "strict_quota_agent_event",
]
