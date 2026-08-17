"""生成 token → Thought 进度回调：只发短阶段文案，不灌全文。"""

from __future__ import annotations

import pytest

from pixelflow.video_agent.tools.script_skill_pipeline import (
    IMPORT_STRUCTURE_PROGRESS_MILESTONES,
    make_generation_progress_on_token,
)


@pytest.mark.asyncio
async def test_generation_progress_emits_milestones_not_body() -> None:
    events: list[tuple[str, str]] = []

    async def emit_progress(message: str, *, phase: str) -> None:
        events.append((phase, message))

    on_token = make_generation_progress_on_token(
        emit_progress,
        phase="import_structure_extract",
        milestones=IMPORT_STRUCTURE_PROGRESS_MILESTONES,
        heartbeat_every_chars=10_000,
        heartbeat_message="拆解仍在进行…",
    )

    body = (
        "## 角色/场景/道具设定\n\n### 安然\n很长的角色设定正文不应出现在进度里。\n\n"
        "## 场景设定\n\n办公室剪辑间……\n\n"
        "## 道具与产品设定\n\n氧气防晒……\n\n"
        "## 剧本正文\n\n| 时间 | 景别 |\n| 0-10秒 | 近景 |\n"
    )
    await on_token(body)

    assert all(phase == "import_structure_extract" for phase, _ in events)
    messages = [msg for _, msg in events]
    assert "正在整理角色设定…" in messages
    assert "正在整理场景设定…" in messages
    assert "正在整理道具与产品设定…" in messages
    assert "正在整理分镜表…" in messages
    assert "正在写入镜头列表…" in messages
    joined = "\n".join(messages)
    assert "安然" not in joined
    assert "氧气防晒" not in joined
    assert "很长的角色设定正文" not in joined


@pytest.mark.asyncio
async def test_generation_progress_heartbeat_without_body() -> None:
    events: list[str] = []

    async def emit_progress(message: str, *, phase: str) -> None:
        events.append(message)

    on_token = make_generation_progress_on_token(
        emit_progress,
        phase="skill_episode_stream",
        milestones=(),
        heartbeat_every_chars=20,
        heartbeat_message="生成剧本正文 /episode仍在生成…",
    )

    await on_token("这是一段会触发心跳的生成正文内容啊再补几个字")
    assert events == ["生成剧本正文 /episode仍在生成…"]
