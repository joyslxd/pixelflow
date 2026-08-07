from __future__ import annotations

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import VideoToolContext
from pixelflow.video_agent.tools.script import BrainstormScriptTool, ImportScriptTool

MATURE_SCRIPT = """# 夏日保温杯短视频

## 视频规格

- 时长：15 秒
- 画幅：9:16

## 镜头脚本

1. 前 3 秒展示冰块落入杯中，旁白介绍长效保冷。
2. 中段展示通勤携带和防漏测试。
3. 结尾展示商品并引导立即购买。
"""


def _context(payload: dict | None = None) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload=payload or {},
        ),
    )


@pytest.mark.asyncio
async def test_import_script_creates_ready_version_without_plan_review() -> None:
    result = await ImportScriptTool().execute(
        _context(),
        {"markdown": MATURE_SCRIPT},
    )

    script = result.workspace_patch["script"]
    assert script["source"] == "user_import"
    assert script["version"] == 1
    assert script["status"] == "ready"
    assert script["review_required"] is False
    assert script["missing_requirements"] == []
    assert result.requires_confirmation is False
    assert result.artifact_refs == (script["artifact_ref"],)
    assert result.workspace_patch["script_versions"] == [script]


@pytest.mark.asyncio
async def test_import_script_replay_reuses_same_version() -> None:
    tool = ImportScriptTool()
    first = await tool.execute(_context(), {"markdown": MATURE_SCRIPT})
    replay = await tool.execute(
        _context(first.workspace_patch),
        {"markdown": MATURE_SCRIPT},
    )

    assert replay.workspace_patch == {}
    assert replay.artifact_refs == first.artifact_refs


class FakeVideoDomainAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def brainstorm_script(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "# 创意脚本草稿\n\n1. 用反差开场。\n2. 展示商品卖点。\n3. 引导购买。"


@pytest.mark.asyncio
async def test_brainstorm_script_appends_versioned_draft_only() -> None:
    imported = await ImportScriptTool().execute(
        _context(),
        {"markdown": MATURE_SCRIPT},
    )
    adapter = FakeVideoDomainAdapter()
    result = await BrainstormScriptTool(adapter=adapter).execute(
        _context(imported.workspace_patch),
        {
            "product_info": {"product_name": "保温杯"},
            "video_params": {"duration_sec": 15, "ratio": "9:16"},
            "creative_direction": "通勤反差",
        },
    )

    script = result.workspace_patch["script"]
    assert script["source"] == "agent_brainstorm"
    assert script["version"] == 2
    assert script["status"] == "draft"
    assert script["review_required"] is True
    assert len(result.workspace_patch["script_versions"]) == 2
    assert result.requires_confirmation is False
    assert adapter.calls[0]["creative_direction"] == "通勤反差"
