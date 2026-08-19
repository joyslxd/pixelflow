"""确认等待中剥离重复计费 tool_calls。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from pixelflow.video_agent.middleware.tool_gateway import (
    VideoConfirmationAwaitMiddleware,
    strip_repeat_while_awaiting_confirmation,
)


def test_strip_repeat_compose_while_awaiting_confirmation() -> None:
    pending = ToolMessage(
        content=json.dumps(
            {
                "tool_name": "compose_or_export_video",
                "requires_confirmation": True,
                "public_summary": "请确认",
            },
            ensure_ascii=False,
        ),
        tool_call_id="call-1",
        name="compose_or_export_video",
    )
    retry = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "compose_or_export_video",
                "args": {"output_type": "mp4"},
                "id": "call-2",
                "type": "tool_call",
            }
        ],
    )
    update = strip_repeat_while_awaiting_confirmation(
        {
            "messages": [
                HumanMessage(content="合并视频吧"),
                pending,
                retry,
            ]
        }
    )
    assert update is not None
    stripped = update["messages"][0]
    assert isinstance(stripped, AIMessage)
    assert stripped.tool_calls == []
    assert "确认单已发出" in str(stripped.content)


def test_strip_repeat_compose_after_soft_failure() -> None:
    failed = ToolMessage(
        content=json.dumps(
            {
                "tool_name": "compose_or_export_video",
                "requires_confirmation": False,
                "public_summary": "视频交付合并失败，请稍后重试或检查分镜视频后重新发起",
            },
            ensure_ascii=False,
        ),
        tool_call_id="call-1",
        name="compose_or_export_video",
    )
    retry = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "compose_or_export_video",
                "args": {"output_type": "mp4"},
                "id": "call-2",
                "type": "tool_call",
            }
        ],
    )
    update = strip_repeat_while_awaiting_confirmation(
        {"messages": [HumanMessage(content="合并视频吧"), failed, retry]}
    )
    assert update is not None
    assert update["messages"][0].tool_calls == []
    assert "已失败" in str(update["messages"][0].content)


def test_confirmation_await_middleware_after_model() -> None:
    mw = VideoConfirmationAwaitMiddleware()
    pending = ToolMessage(
        content=json.dumps(
            {
                "tool_name": "compose_or_export_video",
                "requires_confirmation": True,
            }
        ),
        tool_call_id="c1",
        name="compose_or_export_video",
    )
    state = {
        "messages": [
            pending,
            AIMessage(
                content="再合并一次",
                tool_calls=[
                    {
                        "name": "compose_or_export_video",
                        "args": {"output_type": "mp4"},
                        "id": "c2",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    }
    out = mw.after_model(state, runtime=None)  # type: ignore[arg-type]
    assert out is not None
    assert out["messages"][0].tool_calls == []


def test_confirmation_await_strips_all_follow_up_tools_after_first_confirmation() -> None:
    """任一确认单已发出后，本 Turn 必须结束，不得再探查或发第二张确认单。"""

    pending = ToolMessage(
        content=json.dumps(
            {
                "tool_name": "generate_scenes",
                "requires_confirmation": True,
                "confirmation_id": "confirm-generate",
            }
        ),
        tool_call_id="generate-call",
        name="generate_scenes",
    )
    follow_up = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "inspect_video_workspace",
                "args": {},
                "id": "inspect-call",
                "type": "tool_call",
            },
            {
                "name": "compose_or_export_video",
                "args": {"output_type": "mp4"},
                "id": "compose-call",
                "type": "tool_call",
            },
        ],
    )

    update = strip_repeat_while_awaiting_confirmation(
        {"messages": [HumanMessage(content="生成全部分镜视频"), pending, follow_up]}
    )

    assert update is not None
    stripped = update["messages"][0]
    assert isinstance(stripped, AIMessage)
    assert stripped.tool_calls == []
    assert "确认单已发出" in str(stripped.content)
