"""视频生产合同 Tool：以独立原子写入冻结已选择的 Provider 参数。"""

from __future__ import annotations

from collections.abc import Mapping

from pixelflow.video.contracts import VideoToolResult
from pixelflow.video.workspace.payload import WorkspaceCreationContract

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)


class SetVideoGenerationContractTool:
    """保存实际生成路由，保持创意规划与付费 Provider 解耦。"""

    spec = VideoToolSpec(
        name="set_video_generation_contract",
        description=(
            "原子写入即将用于视频生成的完整生产合同（video_model、video_ratio、"
            "video_size、video_sound），不生成视频也不计费。仅在 Agent 已根据当前工作区、"
            "用户确认和可用能力选定参数后调用；不得写入 Authorization、价格或 Provider 原始配置。"
        ),
        input_model=WorkspaceCreationContract,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("creation_contract",),
        model_observation_keys=("creation_contract_ready", "workspace_revision_required"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            contract = WorkspaceCreationContract.model_validate(dict(arguments))
        except Exception as exc:
            raise VideoToolValidationError("视频生产合同参数无效") from exc
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="已冻结本次视频生成的模型、画幅、分辨率和声音参数，尚未开始生成。",
            workspace_patch={"creation_contract": contract.model_dump(mode="json")},
            model_observation={
                "creation_contract_ready": True,
                "workspace_revision_required": True,
            },
        )


__all__ = ["SetVideoGenerationContractTool"]
