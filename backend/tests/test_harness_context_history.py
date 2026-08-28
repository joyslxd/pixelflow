"""验证独立 Harness Run 仍能接收当前对话最近已确认事实。"""

from __future__ import annotations

from app.gateway.routers.pixelflow_conversations import _harness_context_history
from pixelflow.tasks import PixelFlowConversationMessageRecord


def _message(role: str, content: str, message_id: str) -> PixelFlowConversationMessageRecord:
    """构造不含用户身份或受保护 payload 的公开会话消息。"""

    return PixelFlowConversationMessageRecord(
        message_id=message_id,
        conversation_id="context-conversation",
        user_id="context-user",
        role=role,
        content=content,
        payload={"不应进入投影": "内部字段"},
    )


def test_harness_context_history_keeps_recent_public_turns_in_order() -> None:
    """新 Run 必须看到最近用户确认和助手结论，不复制 system 或 payload。"""

    history = _harness_context_history(
        [
            _message("system", "系统内部消息", "system"),
            _message("user", "型号为 M20，投放抖音。", "user-1"),
            _message("assistant", "已确认画幅为 16:9。", "assistant-1"),
            _message("user", "从零开始，不需要参考图。", "user-2"),
        ]
    )

    assert history == [
        {"role": "user", "content": "型号为 M20，投放抖音。"},
        {"role": "assistant", "content": "已确认画幅为 16:9。"},
        {"role": "user", "content": "从零开始，不需要参考图。"},
    ]


def test_harness_context_history_prioritizes_newest_messages_with_bounded_body() -> None:
    """超长历史不能淹没新确认；上限内仍保留角色和稳定顺序。"""

    history = _harness_context_history(
        [_message("user", "a" * 7_000, "older"), _message("assistant", "最新确认", "newer")]
    )

    assert history[-1] == {"role": "assistant", "content": "最新确认"}
    assert len(history[0]["content"]) == 6_000
