"""创意与生产约束的多版本 Workspace Tool。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.video.contracts import VideoToolResult
from pixelflow.video.workspace.payload import migrate_workspace_payload

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)


class InspectCreativeBriefInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreativeBriefOptionPatch(BaseModel):
    """创意版本的可增量字段；未提供字段不会覆盖旧内容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=256)
    concept: str | None = Field(default=None, max_length=4_000)
    audience: str | None = Field(default=None, max_length=2_000)
    platform: str | None = Field(default=None, max_length=128)
    aspect_ratio: str | None = Field(default=None, max_length=32)
    target_duration_sec: int | None = Field(default=None, ge=1, le=3_600)
    audio: str | None = Field(default=None, max_length=2_000)
    cta: str | None = Field(default=None, max_length=2_000)
    status: Literal["draft", "selected", "archived"] | None = None


class SelectCreativeOptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: str = Field(min_length=1, max_length=128)


class InspectCreativeBriefTool:
    spec = VideoToolSpec(
        name="inspect_creative_brief",
        description="读取当前 Workspace 的创意约束和已保存的多个创意版本。",
        input_model=InspectCreativeBriefInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
        model_observation_keys=("active_option_id", "options"),
    )

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        del arguments
        brief = _brief(context.workspace.payload)
        options = _options(brief)
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"当前有 {len(options)} 个创意版本，当前选中：{brief.get('active_option_id') or '未选择'}。",
            model_observation={"active_option_id": brief.get("active_option_id"), "options": options[:12]},
        )


class UpdateCreativeBriefTool:
    spec = VideoToolSpec(
        name="update_creative_brief",
        description="增量修改或新增创意版本；只更新创意约束，不自动改分镜或触发生成。",
        input_model=CreativeBriefOptionPatch,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("creative_brief",),
        model_observation_keys=("active_option_id", "option_id", "option_status"),
    )

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        request = CreativeBriefOptionPatch.model_validate(dict(arguments))
        brief = _brief(context.workspace.payload)
        options = _options(brief)
        by_id = {str(item.get("option_id")): dict(item) for item in options}
        option = by_id.get(request.option_id, {"option_id": request.option_id, "status": "draft"})
        option.update(request.model_dump(mode="json", exclude_none=True))
        option.setdefault("status", "draft")
        by_id[request.option_id] = option
        active_id = str(brief.get("active_option_id") or "").strip() or None
        next_brief = {**brief, "options": list(by_id.values())}
        if active_id is not None:
            next_brief["active_option_id"] = active_id
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"创意版本 {request.option_id} 已保存，可继续修改或选择其他版本。",
            workspace_patch={"creative_brief": next_brief},
            model_observation={
                "active_option_id": next_brief.get("active_option_id"),
                "option_id": request.option_id,
                "option_status": option.get("status"),
            },
        )


class SelectCreativeOptionTool:
    spec = VideoToolSpec(
        name="select_creative_option",
        description="选择当前创意版本；不自动创建分镜、不自动生成资产。",
        input_model=SelectCreativeOptionInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("creative_brief",),
        model_observation_keys=("active_option_id", "option_status"),
    )

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        request = SelectCreativeOptionInput.model_validate(dict(arguments))
        brief = _brief(context.workspace.payload)
        options = _options(brief)
        selected = next((item for item in options if item.get("option_id") == request.option_id), None)
        if selected is None:
            raise VideoToolValidationError("创意版本不存在")
        next_options = [
            {**item, "status": "selected" if item.get("option_id") == request.option_id else ("draft" if item.get("status") == "selected" else item.get("status", "draft"))}
            for item in options
        ]
        next_brief = {**brief, "active_option_id": request.option_id, "options": next_options}
        for key in ("title", "concept", "audience", "platform", "aspect_ratio", "target_duration_sec", "audio", "cta"):
            if key in selected:
                next_brief[key] = selected[key]
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已选择创意版本 {request.option_id}；分镜和资产仍需 Agent 根据上下文决定是否更新。",
            workspace_patch={"creative_brief": next_brief},
            model_observation={"active_option_id": request.option_id, "option_status": "selected"},
        )


def _brief(payload: Mapping[str, object]) -> dict[str, object]:
    migrated = migrate_workspace_payload(payload)
    value = migrated.get("creative_brief")
    return dict(value) if isinstance(value, Mapping) else {}


def _options(brief: Mapping[str, object]) -> list[dict[str, object]]:
    value = brief.get("options")
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


__all__ = [
    "CreativeBriefOptionPatch",
    "InspectCreativeBriefInput",
    "InspectCreativeBriefTool",
    "SelectCreativeOptionInput",
    "SelectCreativeOptionTool",
    "UpdateCreativeBriefTool",
]
