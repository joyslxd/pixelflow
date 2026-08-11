"""脚本 Skill 单阶段大模型超时收口。"""

from __future__ import annotations

import asyncio

import pytest

from pixelflow.video_agent.tools.registry import VideoToolValidationError
from pixelflow.video_agent.tools import script_skill_pipeline as pipeline


@pytest.mark.asyncio
async def test_generate_stage_markdown_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowModel:
        async def ainvoke(self, _messages):  # noqa: ANN001, ARG002
            await asyncio.sleep(60)
            return type("Msg", (), {"content": "should-not-return"})()

    monkeypatch.setattr(pipeline, "create_chat_model", lambda **_kwargs: SlowModel())
    monkeypatch.setattr(pipeline, "SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(VideoToolValidationError, match="超时"):
        await pipeline._generate_stage_markdown(
            stage="compliance",
            user_story="用户成稿",
            prior={},
        )
