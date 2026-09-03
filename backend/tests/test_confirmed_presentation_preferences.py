"""验证已确认的演示偏好只经 Gateway Outbox 写入长期记忆。"""

from __future__ import annotations

import pytest

from pixelflow.agent_tools.catalog import runtime_video_tool_registry
from pixelflow.agent_tools.video.confirmed_preferences import (
    SaveConfirmedPresentationPreferencesTool,
)
from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.video.contracts import VideoWorkspace


class _PreferenceStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def update(self, user_id: str, patch: dict[str, object]) -> object:
        self.calls.append((user_id, patch))
        return object()


class _LongTermMemory:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def write_background(self, *, user_id: str, content: str, category: str, write_key: str) -> None:
        self.calls.append(
            {
                "user_id": user_id,
                "content": content,
                "category": category,
                "write_key": write_key,
            }
        )


@pytest.mark.asyncio
async def test_confirmed_presentation_preferences_update_local_store_and_enqueue_mem0() -> None:
    """已确认的四类演示偏好更新本地权威记录，并以稳定身份异步写入 Mem0。"""

    preference_store = _PreferenceStore()
    memory = _LongTermMemory()
    tool = SaveConfirmedPresentationPreferencesTool(
        preference_store=preference_store,
        long_term_memory_service=memory,
    )
    context = VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(workspace_id="workspace-1", conversation_id="conversation-1"),
        run_id="hrun_1234567890abcdef",
        tool_call_id="tool-call-1",
    )

    result = await tool.execute(
        context,
        {
            "brand_preference": "美的高端家电",
            "template_preference": "现代极简商务",
            "language_style": "专业但简洁",
            "preferred_page_count": 12,
        },
    )

    assert tool.spec.confirmation_required is True
    assert result.public_summary == "已保存你确认的演示偏好，长期记忆将在后台同步。"
    assert preference_store.calls == [
        (
            "user-1",
            {
                "style_preferences": {
                    "brand_preference": "美的高端家电",
                    "presentation_template": "现代极简商务",
                    "presentation_language_style": "专业但简洁",
                },
                "defaults": {"presentation_page_count": 12},
            },
        )
    ]
    assert memory.calls == [
        {
            "user_id": "user-1",
            "content": "经用户确认的演示偏好：品牌偏好=美的高端家电；模板偏好=现代极简商务；语言风格=专业但简洁；常用页数=12。",
            "category": "confirmed_presentation_preference",
            "write_key": "mem0-presentation-preference:hrun_1234567890abcdef:tool-call-1",
        }
    ]


def test_confirmed_presentation_preferences_are_published_with_confirmation() -> None:
    """启动期 Manifest 必须公开该 Tool，并要求用户确认后才允许执行。"""

    tool = runtime_video_tool_registry().resolve("save_confirmed_presentation_preferences")

    assert tool is not None
    assert tool.spec.confirmation_required is True
    assert tool.spec.workspace_mutations == ()
