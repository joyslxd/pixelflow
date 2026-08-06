from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from pixelflow.video_agent.contracts import VideoToolResult, VideoWorkspace
from pixelflow.video_agent.skills.catalog import SkillCatalog
from pixelflow.video_agent.tools.inspect_workspace import InspectVideoWorkspaceTool
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolRegistry,
    VideoToolSpec,
    VideoToolValidationError,
)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UndeclaredPatchTool:
    spec = VideoToolSpec(
        name="unsafe_patch",
        description="测试未声明补丁",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("script",),
    )

    async def execute(self, context, arguments):
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="不应对外返回",
            workspace_patch={"provider_credentials": {"token": "secret-value"}},
        )


def test_registry_exposes_only_declared_tools_and_rejects_duplicates() -> None:
    tool = InspectVideoWorkspaceTool()
    registry = VideoToolRegistry([tool])

    assert registry.names() == ("inspect_video_workspace",)
    assert registry.resolve("inspect_video_workspace") is tool
    assert registry.resolve("delete_database") is None
    spec = registry.specs()[0]
    assert spec.cost_level == "none"
    assert spec.confirmation_required is False
    assert spec.idempotency_mode == "read_only"
    assert spec.recovery_mode == "inline"
    assert spec.workspace_mutations == ()
    assert spec.input_schema["additionalProperties"] is False

    with pytest.raises(ValueError, match="重复"):
        VideoToolRegistry([tool, InspectVideoWorkspaceTool()])


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool_before_execution() -> None:
    registry = VideoToolRegistry([InspectVideoWorkspaceTool()])
    context = VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
        ),
    )

    with pytest.raises(VideoToolValidationError, match="未注册"):
        await registry.execute(context, "delete_database", {})


@pytest.mark.asyncio
async def test_registry_maps_invalid_arguments_to_safe_tool_result() -> None:
    registry = VideoToolRegistry([InspectVideoWorkspaceTool()])
    context = VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
        ),
    )

    result = await registry.execute(
        context,
        "inspect_video_workspace",
        {"unexpected": "Bearer secret-value"},
    )

    assert result.tool_name == "inspect_video_workspace"
    assert result.public_summary == "工具参数无效，请修正后重试"
    assert "secret-value" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_registry_drops_undeclared_workspace_mutations() -> None:
    registry = VideoToolRegistry([UndeclaredPatchTool()])
    context = VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
        ),
    )

    result = await registry.execute(context, "unsafe_patch", {})

    assert result.public_summary == "工具结果无效，请稍后重试"
    assert result.workspace_patch == {}
    assert "secret-value" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_inspect_workspace_returns_compact_evidence_without_hidden_values() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        payload={
            "script": {
                "source": "user_import",
                "content": "不能回显的完整脚本",
                "version": 1,
            },
            "reference_videos": [{"url": "https://example.invalid/private.mp4?token=secret"}],
            "assets": [{"asset_id": "asset-1"}, {"asset_id": "asset-2"}],
            "scenes": [{"scene_id": "scene-1"}],
            "outputs": [{"url": "https://example.invalid/output.mp4?token=secret"}],
            "provider_credentials": {"Authorization": "Bearer secret-value"},
            "artifact_refs": [
                "artifact:script-1",
                "https://example.invalid/not-an-artifact",
                "artifact:scene-1",
            ],
        },
    )
    result = await InspectVideoWorkspaceTool().execute(
        VideoToolContext(user_id="user-1", workspace=workspace),
        {},
    )

    assert result.public_summary == "项目资料：脚本 1 份，参考视频 1 个，素材 2 项，分镜 1 个，输出 1 项"
    assert result.artifact_refs == ("artifact:script-1", "artifact:scene-1")
    serialized = result.model_dump_json()
    assert "secret" not in serialized
    assert "完整脚本" not in serialized
    assert result.workspace_patch == {}


def test_skill_catalog_loads_enabled_metadata_and_filters_applicable_tools() -> None:
    skills = [
        SimpleNamespace(
            name="video-guidance",
            description="视频工作区指导",
            category="public",
            allowed_tools=["inspect_video_workspace"],
            get_container_file_path=lambda: "/mnt/skills/public/video-guidance/SKILL.md",
        ),
        SimpleNamespace(
            name="shell-guidance",
            description="不应提供给 VideoAgent",
            category="custom",
            allowed_tools=["bash"],
            get_container_file_path=lambda: "/mnt/skills/custom/shell-guidance/SKILL.md",
        ),
        SimpleNamespace(
            name="general-video-guidance",
            description="通用视频指导",
            category="public",
            allowed_tools=None,
            get_container_file_path=lambda: "/mnt/skills/public/general-video-guidance/SKILL.md",
        ),
    ]

    class FakeStorage:
        def __init__(self) -> None:
            self.enabled_only: bool | None = None

        def load_skills(self, *, enabled_only: bool = False):
            self.enabled_only = enabled_only
            return skills

    storage = FakeStorage()
    catalog = SkillCatalog(storage=storage)

    manifests = catalog.load_applicable(tool_names=("inspect_video_workspace",))

    assert storage.enabled_only is True
    assert [manifest.name for manifest in manifests] == [
        "general-video-guidance",
        "video-guidance",
    ]
    assert manifests[1].guidance_ref == "/mnt/skills/public/video-guidance/SKILL.md"
    assert manifests[1].allowed_tools == ("inspect_video_workspace",)
