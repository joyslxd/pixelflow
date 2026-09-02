"""锁住通用底座指令与视频领域选 Tool 边界。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pixelflow.agent_harness.system_instruction import (
    PIXELFLOW_AGENT_SYSTEM_INSTRUCTION,
    compose_system_instruction,
)
from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.registry import VideoToolRegistry
from pixelflow.agent_tools.video.scene import PatchSceneTool
from pixelflow.agent_tools.video.storyboard import (
    CreateStoryboardTool,
    PrepareScenePackagesTool,
    ReviseStoryboardTool,
)
from pixelflow.video.contracts import VideoWorkspace

_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills" / "skills"
_VIDEO_DOMAIN_MARKERS = (
    "patch_scene",
    "prepare_scene_packages",
    "create_storyboard",
    "revise_storyboard",
    "Seedance",
    "asset_registry",
    "reference_asset_ids",
    "generate_scenes",
    "set_video_generation_contract",
    "分镜",
    "视频 Agent",
)


def test_base_instruction_has_no_video_domain_tools_or_dto_fields() -> None:
    text = PIXELFLOW_AGENT_SYSTEM_INSTRUCTION
    assert text.startswith("你是 PixelFlow Agent")
    for marker in _VIDEO_DOMAIN_MARKERS:
        assert marker not in text


def test_all_harness_triggers_reuse_the_same_base_instruction() -> None:
    for trigger in (
        "user_turn",
        "confirmation_resume",
        "authorization_resume",
        "form_resume",
        "run_recovery",
    ):
        composed = compose_system_instruction(trigger)
        assert composed.startswith(PIXELFLOW_AGENT_SYSTEM_INSTRUCTION)
        if trigger == "user_turn":
            assert composed == PIXELFLOW_AGENT_SYSTEM_INSTRUCTION
            continue
        assert "本轮触发约束" in composed
        for marker in _VIDEO_DOMAIN_MARKERS:
            assert marker not in composed


def test_write_tool_descriptions_contrast_first_write_and_local_revision() -> None:
    prepare = PrepareScenePackagesTool.spec.description
    create = CreateStoryboardTool.spec.description
    revise = ReviseStoryboardTool.spec.description
    patch = PatchSceneTool.spec.description

    assert "尚无分镜" in prepare
    assert "replace_existing=true" in prepare
    assert "patch_scene" in prepare
    assert "尚不存在的分镜" in create
    assert "replace_existing=true" in create
    assert "已有分镜" in revise
    assert "prepare_scene_packages" in revise
    assert "第 N 段" in patch or "第 N 镜" in patch
    assert "prepare_scene_packages" in patch


def test_orchestration_and_authoring_skills_forbid_reprepare_on_local_edit() -> None:
    orchestration = (_SKILLS_ROOT / "pixelflow-video-orchestration" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    authoring = (_SKILLS_ROOT / "video-script-authoring" / "SKILL.md").read_text(encoding="utf-8")

    assert "patch_scene" in orchestration
    assert "replace_existing=true" in orchestration
    assert "set_video_generation_contract" in orchestration
    assert "compose_or_export_video" in orchestration
    assert "最新一份" in orchestration
    assert "不要为改第 N 段再输出整包 `prepare_scene_packages`" in authoring


def _prepare_arguments(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "script": "产品展示",
        "asset_registry": [
            {
                "asset_id": "image-1",
                "kind": "character",
                "role": "女主",
                "generation_prompt": "厨房中的女主角设定图",
            }
        ],
        "scenes": [
            {
                "scene_id": "scene_01",
                "prompt": "女主打开冰箱",
                "duration_sec": 8,
                "reference_asset_ids": ["image-1"],
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_prepare_rejects_existing_storyboard_without_replace_existing() -> None:
    result = await VideoToolRegistry((PrepareScenePackagesTool(),)).execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-existing-scenes",
                conversation_id="conversation-existing-scenes",
                payload={
                    "scenes": [
                        {"scene_id": "scene_01", "prompt": "旧镜头", "duration_sec": 8},
                    ]
                },
            ),
        ),
        "prepare_scene_packages",
        _prepare_arguments(),
    )

    assert result.public_summary == (
        "工作区已有分镜；局部修订请使用 patch_scene 或 revise_storyboard。"
        "仅当用户明确要求整份重建时，才能设置 replace_existing=true"
    )


@pytest.mark.asyncio
async def test_prepare_replaces_existing_storyboard_when_flag_set() -> None:
    result = await PrepareScenePackagesTool().execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-replace-scenes",
                conversation_id="conversation-replace-scenes",
                payload={
                    "scenes": [
                        {"scene_id": "old_scene", "prompt": "旧镜头", "duration_sec": 8},
                    ]
                },
            ),
        ),
        _prepare_arguments(replace_existing=True),
    )

    assert result.workspace_patch["scenes_replace"] is True
    assert result.workspace_patch["scenes"][0]["scene_id"] == "scene_01"
