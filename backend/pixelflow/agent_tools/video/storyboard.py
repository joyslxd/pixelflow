"""脚本与分镜准备 Tool：只写入权威 Workspace，不触发计费 Provider。"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from pixelflow.video.contracts import VideoToolResult

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

MAX_STORYBOARD_SCENE_COUNT = 120
MAX_SCENE_DURATION_SEC = 30
MIN_SCENE_DURATION_SEC = 4


class StoryboardSceneInput(BaseModel):
    """一个待写入 Workspace 的可执行分镜。"""

    model_config = ConfigDict(extra="allow", frozen=True)

    scene_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=10_000)
    duration_sec: int = Field(ge=MIN_SCENE_DURATION_SEC, le=MAX_SCENE_DURATION_SEC)
    title: str | None = Field(default=None, max_length=256)
    storyline: str | None = Field(default=None, max_length=4_000)
    narration: str | None = Field(default=None, max_length=4_000)
    onscreen_text: str | None = Field(default=None, max_length=2_000)
    shot_type: str | None = Field(default=None, max_length=256)
    camera_movement: str | None = Field(default=None, max_length=512)


class PrepareScenePackagesInput(BaseModel):
    """脚本与分镜一次性准备；长片总时长由业务计划和 Provider 能力决定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(min_length=1, max_length=8_000)
    scenes: tuple[StoryboardSceneInput, ...] = Field(
        min_length=1,
        max_length=MAX_STORYBOARD_SCENE_COUNT,
    )

    @model_validator(mode="after")
    def validate_duration_and_ids(self) -> PrepareScenePackagesInput:
        scene_ids = [scene.scene_id.strip() for scene in self.scenes]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValueError("分镜 scene_id 不能重复")
        return self


class PrepareScenePackagesTool:
    """将已确认的脚本和分镜设计原子写入当前 Workspace。"""

    spec = VideoToolSpec(
        name="prepare_scene_packages",
        description="写入脚本和分镜包；单镜最长 30 秒，长片生成由 M06 批次拆分，完成后再请求生成确认。",
        input_model=PrepareScenePackagesInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=(
            "script",
            "scenes",
            "scene_packages",
            "scenes_replace",
            "dirty_scene_ids",
        ),
        model_observation_keys=("scene_count", "total_duration_sec", "workspace_revision_required"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            request = PrepareScenePackagesInput.model_validate(dict(arguments))
        except Exception as exc:
            raise VideoToolValidationError("脚本或分镜参数无效") from exc

        previous_script = context.workspace.payload.get("script")
        script_data = dict(previous_script) if isinstance(previous_script, Mapping) else {}
        script_data.update({"content": request.script.strip(), "status": "已编辑"})
        scenes: list[dict[str, JsonValue]] = []
        for scene in request.scenes:
            item = scene.model_dump(mode="json", exclude_none=True)
            item.update({"edit_status": "待生成", "generation_jobs": [], "variants": []})
            scenes.append(item)
        scene_ids = [str(item["scene_id"]) for item in scenes]
        total = sum(int(item["duration_sec"]) for item in scenes)
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已准备 {len(scenes)} 个分镜，总时长 {total} 秒；长片将按 M06 批次拆分，下一步可请求生成确认。",
            workspace_patch={
                "script": script_data,
                "scenes": scenes,
                "scene_packages": scenes,
                "scenes_replace": True,
                "dirty_scene_ids": scene_ids,
            },
            model_observation={
                "scene_count": len(scenes),
                "total_duration_sec": total,
                "workspace_revision_required": True,
            },
        )


class CreateStoryboardTool(PrepareScenePackagesTool):
    """prepare_scene_packages 的语义别名，便于模型按自然语言选择工具名。"""

    spec = VideoToolSpec(
        **{
            **PrepareScenePackagesTool.spec.__dict__,
            "name": "create_storyboard",
            "description": "创建或覆盖当前项目分镜；单镜最长 30 秒，长片生成由 M06 批次拆分。",
        }
    )


__all__ = [
    "CreateStoryboardTool",
    "MAX_SCENE_DURATION_SEC",
    "MAX_STORYBOARD_SCENE_COUNT",
    "PrepareScenePackagesInput",
    "PrepareScenePackagesTool",
    "StoryboardSceneInput",
]
