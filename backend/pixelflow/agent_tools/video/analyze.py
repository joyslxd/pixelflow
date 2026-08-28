"""提交已有视频到 Content-App 拆解接口的 Harness Tool。"""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.capabilities.video_understanding.port import VideoUnderstandingPort
from pixelflow.video.contracts import VideoToolResult

from .contracts import VideoToolContext, VideoToolCostLevel, VideoToolIdempotencyMode, VideoToolRecoveryMode, VideoToolSpec


class AnalyzeVideoInput(BaseModel):
    """视频地址由 Workspace Artifact 投影注入，模型只提交地址引用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    video_url: str = Field(min_length=1, max_length=2_000)
    project_id: int | None = Field(default=None, ge=1)


class AnalyzeVideoTool:
    """非计费视频拆解 Tool；任务身份只作为安全 Observation 返回。"""

    spec = VideoToolSpec(
        name="analyze_video",
        description="提交一个已授权视频进行镜头、字幕、音频和叙事结构拆解；不会生成或修改视频。",
        input_model=AnalyzeVideoInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=(),
        model_observation_keys=("task_id", "status", "parent_generation_dialog_id"),
    )

    def __init__(self, port: VideoUnderstandingPort) -> None:
        self._port = port

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        parsed = self.spec.input_model.model_validate(arguments)
        result = await self._port.analyze(
            {"video_url": parsed.video_url},
            authorization=(context.credential.authorization if context.credential is not None else ""),
            project_id=parsed.project_id,
        )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=result.public_summary,
            model_observation={
                "task_id": result.task_id,
                "status": result.status,
                "parent_generation_dialog_id": result.parent_generation_dialog_id,
            },
        )


__all__ = ["AnalyzeVideoInput", "AnalyzeVideoTool"]
