"""Task 11历史V1视频Workflow只读归档测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.contracts import (
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.persistence import MemoryCompactionQueueRepository
from pixelflow.agent_runtime.service import (
    AgentRuntimeService,
    AgentRuntimeVideoWorkflowRetirementError,
)
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowConversationRecord,
)

NOW = datetime(2026, 8, 6, 18, 0, tzinfo=UTC)


async def _runtime() -> tuple[
    AgentRuntimeService,
    MemoryCompactionQueueRepository,
]:
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="assist",
            enabled_intents=(),
            new_conversation_rollout_percent=100,
            context_compaction_enabled=True,
        ),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    assignment = service.assignment_for_new_conversation({})
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-retired-v1",
            user_id="user-retired-v1",
            context=assignment.context,
        )
    )
    return service, repository


def _workflow(
    *,
    kind: WorkflowKind = WorkflowKind.VIDEO,
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id="workflow-retired-v1",
        conversation_id="conversation-retired-v1",
        kind=kind,
        status=WorkflowStatus.RUNNING,
        current_stage="generate_scene_video",
        stage_version=4,
        creation_contract_snapshot={
            "private_prompt": "历史状态不得进入归档响应",
        },
        latest_artifact_refs=[
            "artifact:retired-video-1",
            "https://signed.example.invalid/private.mp4?token=secret",
            "artifact:retired-video-1",
        ],
        context_version=8,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_historical_v1_video_resume_returns_read_only_retirement() -> None:
    """恢复历史V1只能返回安全归档摘要，不能修改Workflow或产生事件。"""

    service, repository = await _runtime()
    original = await repository.create_workflow(
        "user-retired-v1",
        _workflow(),
    )

    result = await service.resume_workflow(
        user_id="user-retired-v1",
        conversation_id="conversation-retired-v1",
        workflow_id=original.workflow_id,
    )

    assert result.code == "video_workflow_retired"
    assert result.workflow_id == original.workflow_id
    assert result.created_at == NOW
    assert result.artifact_refs == ["artifact:retired-video-1"]
    assert await repository.get_workflow(
        "user-retired-v1",
        original.workflow_id,
    ) == original
    assert await repository.list_events(
        "user-retired-v1",
        original.conversation_id,
    ) == []
    assert "private_prompt" not in result.model_dump_json()
    assert "signed.example.invalid" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_retired_workflow_lookup_keeps_owner_and_kind_isolation() -> None:
    service, repository = await _runtime()
    await repository.create_workflow(
        "user-retired-v1",
        _workflow(kind=WorkflowKind.IMAGE),
    )

    with pytest.raises(AgentRuntimeVideoWorkflowRetirementError):
        await service.resume_workflow(
            user_id="user-retired-v1",
            conversation_id="conversation-retired-v1",
            workflow_id="workflow-retired-v1",
        )
    with pytest.raises(LookupError):
        await service.resume_workflow(
            user_id="other-user",
            conversation_id="conversation-retired-v1",
            workflow_id="workflow-retired-v1",
        )
