"""提供 M06 OperationBatch 的受限只读查询 Tool。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.operations.jobs.batch_repository import OperationBatchRecord
from pixelflow.video.contracts import VideoToolResult

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)


class InspectOperationBatchInput(BaseModel):
    """只允许查询调用方已获得的稳定批次身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(pattern=r"^operation-batch-[a-f0-9]{32}$")


class OperationBatchReadPort(Protocol):
    """隔离 Tool 与 M06 Repository，类似只读 Repository 接口。"""

    async def get_batch(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workspace_id: str,
        batch_id: str,
    ) -> OperationBatchRecord | None: ...


class InspectOperationBatchTool:
    """回读当前 Workspace 的图片或视频批次进度，不暴露 Provider 原文。"""

    spec = VideoToolSpec(
        name="inspect_operation_batch",
        description="查询当前 Workspace 指定 M06 图片或视频生成批次的子任务状态；只返回计数、资产或镜头身份和安全 Job 引用。",
        input_model=InspectOperationBatchInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
        model_observation_keys=("batch_id", "status", "total", "queued", "polling", "succeeded", "failed", "children"),
    )

    def __init__(self, *, batch_repository: OperationBatchReadPort | None = None) -> None:
        self._batches = batch_repository

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = InspectOperationBatchInput.model_validate(dict(arguments))
        if self._batches is None:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="批次查询能力当前未装配，请通过工作区状态确认生成进度。",
                model_observation={"batch_id": request.batch_id, "status": "unavailable"},
            )
        batch = await self._batches.get_batch(
            user_id=context.user_id,
            conversation_id=context.workspace.conversation_id,
            workspace_id=context.workspace.workspace_id,
            batch_id=request.batch_id,
        )
        if batch is None:
            raise VideoToolValidationError("当前 Workspace 中不存在该生成批次")
        counts = {status: 0 for status in ("queued", "starting", "polling", "succeeded", "failed", "timeout", "expired")}
        children: list[dict[str, object]] = []
        for child in batch.children:
            counts[child.status] += 1
            children.append(
                {
                    "item_id": child.scene_id,
                    "variant_index": child.variant_index,
                    "status": child.status,
                    "job_id": child.job_id,
                }
            )
        failed = counts["failed"] + counts["timeout"] + counts["expired"]
        polling = counts["starting"] + counts["polling"]
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=(
                f"批次 {batch.batch_id}：共 {len(batch.children)} 项，已完成 {counts['succeeded']} 项，"
                f"进行中 {polling} 项，排队 {counts['queued']} 项，失败 {failed} 项。"
            ),
            model_observation={
                "batch_id": batch.batch_id,
                "status": batch.status,
                "total": len(batch.children),
                "queued": counts["queued"],
                "polling": polling,
                "succeeded": counts["succeeded"],
                "failed": failed,
                "children": children,
            },
        )


__all__ = ["InspectOperationBatchInput", "InspectOperationBatchTool", "OperationBatchReadPort"]
