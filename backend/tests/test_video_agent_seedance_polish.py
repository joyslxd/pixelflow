"""polish_seedance_shot_prompts Tool 合同测试。"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, patch

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools.registry import VideoToolContext, VideoToolValidationError
from pixelflow.video_agent.tools.seedance_polish import (
    PolishSeedanceShotPromptsTool,
    build_polish_human_prompt,
    build_polish_system_prompt,
    resolve_polish_source_markdown,
)


def _workspace(payload: dict) -> VideoWorkspace:
    return VideoWorkspace(
        workspace_id="ws-polish",
        conversation_id="conv-polish",
        revision=1,
        payload=payload,
    )


def test_resolve_polish_source_prefers_pre_polish() -> None:
    text, label = resolve_polish_source_markdown(
        {
            "script_pipeline": {
                "episode": {
                    "content": "已润色稿",
                    "pre_polish_content": "文学原稿 镜头1",
                }
            }
        }
    )
    assert text == "文学原稿 镜头1"
    assert label == "episode.pre_polish"


def test_resolve_polish_source_falls_back_to_episode() -> None:
    text, label = resolve_polish_source_markdown(
        {"script_pipeline": {"episode": {"content": "镜头1 0-10秒"}}}
    )
    assert "镜头1" in text
    assert label == "episode"


def test_build_polish_system_prompt_loads_seedance_skill() -> None:
    prompt = build_polish_system_prompt()
    assert "Seedance" in prompt
    assert "PixelFlow 分镜执行合同" in prompt
    assert "@character-" in prompt or "@asset_id" in prompt


def test_build_polish_human_prompt_includes_source_and_focus() -> None:
    human = build_polish_human_prompt(
        source_markdown="0-10秒: 画面：安然看手机",
        characters_markdown="## 角色设定\n### 安然",
        focus="强调防晒瓶特写",
        video_model="seedance-2.0",
    )
    assert "安然看手机" in human
    assert "安然" in human
    assert "强调防晒瓶特写" in human
    assert "seedance-2.0" in human


@pytest.mark.asyncio
async def test_polish_tool_requires_episode() -> None:
    tool = PolishSeedanceShotPromptsTool()
    context = VideoToolContext(
        user_id="user-1",
        workspace=_workspace({}),
    )
    with pytest.raises(VideoToolValidationError, match="可润色的剧本正文"):
        await tool.execute(context, {})


@pytest.mark.asyncio
async def test_polish_tool_writes_episode_and_keeps_pre_polish() -> None:
    tool = PolishSeedanceShotPromptsTool()
    context = VideoToolContext(
        user_id="user-1",
        workspace=_workspace(
            {
                "script_pipeline": {
                    "characters": {"content": "## 角色设定\n### 安然"},
                    "episode": {
                        "stage": "episode",
                        "content": "0-10秒: 画面：@安然 看手机",
                    },
                },
                "creation_contract": {"video_model": "seedance-2.0"},
            }
        ),
    )
    polished = (
        "0-10秒: 地点：@办公室；主体：@安然；动作：看手机；"
        "景别：近景；运镜：缓推；光影：窗光；声音：安然轻声自语；收束：接下镜。"
    )
    with patch(
        "pixelflow.video_agent.tools.seedance_polish._generate_polished_markdown",
        new=AsyncMock(return_value=polished),
    ):
        result = await tool.execute(context, {"focus": ""})

    assert "润色" in result.public_summary
    patch_payload = result.workspace_patch or {}
    episode = patch_payload["script_pipeline"]["episode"]
    assert episode["seedance_polished"] is True
    assert episode["pre_polish_content"] == "0-10秒: 画面：@安然 看手机"
    assert "地点：" in str(episode["content"])
    assert patch_payload["script"]["seedance_polished"] is True


@pytest.mark.asyncio
async def test_polish_tool_replays_same_fingerprint() -> None:
    tool = PolishSeedanceShotPromptsTool()
    source = "0-10秒: 画面：@安然 看手机"
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "source": source,
                "characters": "",
                "focus": "",
                "video_model": "",
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    context = VideoToolContext(
        user_id="user-1",
        workspace=_workspace(
            {
                "script_pipeline": {
                    "episode": {
                        "content": "已润色",
                        "pre_polish_content": source,
                        "seedance_polished": True,
                        "polish_fingerprint": fingerprint,
                        "change_summary": "已复用测试",
                        "artifact_ref": "artifact:test",
                    }
                }
            }
        ),
    )

    with patch(
        "pixelflow.video_agent.tools.seedance_polish._generate_polished_markdown",
        new=AsyncMock(side_effect=AssertionError("不应再次调模型")),
    ) as mocked:
        result = await tool.execute(context, {})
    mocked.assert_not_awaited()
    assert result.public_summary == "已复用测试"
    assert not result.workspace_patch
